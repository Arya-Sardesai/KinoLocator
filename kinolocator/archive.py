"""Writes the dated archive snapshot and the current showtimes.json.

Layout (relative to the output root):

    archive/YYYY/MM/DD.json     one snapshot per Vancouver calendar day, as observed
    showtimes.json              today's snapshot plus stale fallbacks (below)

The archive is append-only: a snapshot for a past date is never rewritten.
Today's snapshot is updated by later runs on the same day, venue by venue:

* a venue that succeeds replaces its showtimes with the new ones;
* a venue that fails is marked failed, but keeps any showtimes an earlier
  successful run captured today (so a bad evening run can't erase the morning's data);
* a venue not attempted in this run (e.g. `run --venue x`) is left as it was.

Every venue attempted that day has an entry under "venues" saying whether its
latest attempt succeeded and, if not, why ("error" = the scraper raised;
"no_showtimes" = it ran but found nothing), plus the listings it deliberately
skipped (non-film events etc.).

showtimes.json is what consumers read, so it never loses a venue just because
its scraper broke: when a venue failed and today's snapshot has no showtimes
for it, the venue's showtimes from the previous showtimes.json are carried
over (upcoming ones only) and its entry is marked "stale": true with
"last_confirmed_at" = when that data was last scraped successfully. The
archive never receives carried-over data.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .schema import VANCOUVER, Showtime, Skipped

SCHEMA_VERSION = 3  # 3: added Showtime.original_title (nullable)
CURRENT_FILENAME = "showtimes.json"
ARCHIVE_DIRNAME = "archive"

STATUS_OK = "ok"
STATUS_FAILED = "failed"
FAILURE_ERROR = "error"
FAILURE_NO_SHOWTIMES = "no_showtimes"


class ArchiveError(RuntimeError):
    pass


@dataclass
class VenueResult:
    """Outcome of running one venue's scraper."""

    venue_id: str
    showtimes: list[Showtime] = field(default_factory=list)
    error: str | None = None
    """Exception summary if the scraper raised."""
    skipped: list[Skipped] = field(default_factory=list)

    @property
    def failure(self) -> str | None:
        if self.error is not None:
            return FAILURE_ERROR
        if not self.showtimes:
            return FAILURE_NO_SHOWTIMES
        return None

    @property
    def ok(self) -> bool:
        return self.failure is None


def snapshot_path(root: Path, day: date) -> Path:
    return root / ARCHIVE_DIRNAME / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}.json"


def build_payload(
    results: list[VenueResult], now: datetime, previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Snapshot for this run, merged venue-by-venue into `previous` (today's
    existing snapshot, if any)."""
    attempted_at = now.isoformat()
    prev_venues: dict[str, dict[str, Any]] = previous["venues"] if previous else {}
    prev_showtimes: dict[str, list[Showtime]] = defaultdict(list)
    for d in previous["showtimes"] if previous else []:
        prev_showtimes[d["venue_id"]].append(Showtime.from_dict(d))

    venues = {vid: entry for vid, entry in prev_venues.items()}
    showtimes = {vid: prev_showtimes[vid] for vid in prev_venues}
    for r in results:
        prev_entry = prev_venues.get(r.venue_id, {})
        if r.ok:
            kept, scraped_at = r.showtimes, attempted_at
            skipped = [k.to_dict() for k in r.skipped]
        else:
            # Keep whatever an earlier run today captured for this venue.
            kept = prev_showtimes.get(r.venue_id, [])
            scraped_at = prev_entry.get("showtimes_scraped_at") if kept else None
            skipped = prev_entry.get("skipped", []) if kept else [k.to_dict() for k in r.skipped]
        venues[r.venue_id] = {
            "status": STATUS_OK if r.ok else STATUS_FAILED,
            "failure": r.failure,
            "error": r.error,
            "attempted_at": attempted_at,
            "showtimes_scraped_at": scraped_at,
            "showtime_count": len(kept),
            "skipped": skipped,
        }
        showtimes[r.venue_id] = kept

    ordered = sorted((s for group in showtimes.values() for s in group), key=lambda s: s.sort_key)
    return {
        "schema_version": SCHEMA_VERSION,
        "date": now.astimezone(VANCOUVER).date().isoformat(),
        "generated_at": attempted_at,
        "showtime_count": len(ordered),
        "venues": dict(sorted(venues.items())),
        "showtimes": [s.to_dict() for s in ordered],
    }


def build_current(snapshot: dict[str, Any], previous_current: dict[str, Any] | None) -> dict[str, Any]:
    """showtimes.json: the snapshot, with failed venues that have no data
    today falling back to their showtimes in the previous showtimes.json."""
    today = date.fromisoformat(snapshot["date"])
    prev_venues: dict[str, dict[str, Any]] = previous_current["venues"] if previous_current else {}
    prev_showtimes: dict[str, list[Showtime]] = defaultdict(list)
    for d in previous_current["showtimes"] if previous_current else []:
        prev_showtimes[d["venue_id"]].append(Showtime.from_dict(d))

    showtimes = [Showtime.from_dict(d) for d in snapshot["showtimes"]]
    venues = {}
    for vid, entry in snapshot["venues"].items():
        entry = dict(entry)
        has_data = entry["showtime_count"] > 0
        last_confirmed = entry["showtimes_scraped_at"]
        # Data for a failed venue predates its latest attempt, whichever source it came from.
        stale = entry["status"] != STATUS_OK and has_data
        if entry["status"] != STATUS_OK and not has_data:
            prev = prev_venues.get(vid, {})
            prev_confirmed = prev.get("last_confirmed_at") or prev.get("showtimes_scraped_at")
            carried = [s for s in prev_showtimes.get(vid, []) if s.start.date() >= today]
            if carried and prev_confirmed:
                showtimes.extend(carried)
                entry["showtime_count"] = len(carried)
                stale, last_confirmed = True, prev_confirmed
            elif prev_confirmed:
                last_confirmed = prev_confirmed  # nothing upcoming left to show
        entry["stale"] = stale
        entry["last_confirmed_at"] = last_confirmed
        venues[vid] = entry

    ordered = sorted(showtimes, key=lambda s: s.sort_key)
    return {
        **snapshot,
        "showtime_count": len(ordered),
        "venues": venues,
        "showtimes": [s.to_dict() for s in ordered],
    }


def failed_venues(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {vid: v for vid, v in payload["venues"].items() if v["status"] != STATUS_OK}


def write_snapshot(root: Path, day: date, payload: dict[str, Any], *, today: date) -> Path:
    """Write the snapshot for `day`, refusing to touch an existing past date."""
    path = snapshot_path(root, day)
    if day > today:
        raise ArchiveError(f"refusing to write snapshot for future date {day} (today is {today})")
    if day < today and path.exists():
        raise ArchiveError(f"archive is append-only: {path} already exists for past date {day}")
    _write_json(path, payload)
    return path


@dataclass
class Written:
    snapshot_path: Path
    current_path: Path
    snapshot: dict[str, Any]
    current: dict[str, Any]


def write_run(root: Path, results: list[VenueResult], now: datetime) -> Written:
    """Merge this run into today's snapshot and rebuild showtimes.json."""
    today = now.astimezone(VANCOUVER).date()
    snap_path = snapshot_path(root, today)
    current_path = root / CURRENT_FILENAME
    previous_snapshot = _read_json(snap_path)
    previous_current = _read_json(current_path)

    snapshot = build_payload(results, now, previous_snapshot)
    current = build_current(snapshot, previous_current)
    write_snapshot(root, today, snapshot, today=today)
    _write_json(current_path, current)
    return Written(snap_path, current_path, snapshot, current)


def _read_json(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text("utf-8")) if path.exists() else None


def load_showtimes(path: Path) -> list[Showtime]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Showtime.from_dict(d) for d in data["showtimes"]]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    # Write-then-rename so an interrupted run never leaves a truncated file.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
