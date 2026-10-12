"""Writes the dated archive snapshot and the current showtimes.json.

Layout (relative to the output root):

    showtimes.json              same content as today's snapshot
    archive/YYYY/MM/DD.json     one snapshot per Vancouver calendar day

The archive is append-only: a snapshot for a past date is never rewritten.
Today's snapshot is updated by later runs on the same day, venue by venue:

* a venue that succeeds replaces its showtimes with the new ones;
* a venue that fails is marked failed, but keeps any showtimes an earlier
  successful run captured today (so a bad evening run can't erase the morning's data);
* a venue not attempted in this run (e.g. `run --venue x`) is left as it was.

Every venue attempted that day has an entry under "venues" saying whether its
latest attempt succeeded and, if not, why ("error" = the scraper raised;
"no_showtimes" = it ran but found nothing).
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

from .schema import VANCOUVER, Showtime

SCHEMA_VERSION = 1
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
        if r.ok:
            kept, scraped_at = r.showtimes, attempted_at
        else:
            # Keep whatever an earlier run today captured for this venue.
            kept = prev_showtimes.get(r.venue_id, [])
            scraped_at = prev_venues.get(r.venue_id, {}).get("showtimes_scraped_at") if kept else None
        venues[r.venue_id] = {
            "status": STATUS_OK if r.ok else STATUS_FAILED,
            "failure": r.failure,
            "error": r.error,
            "attempted_at": attempted_at,
            "showtimes_scraped_at": scraped_at,
            "showtime_count": len(kept),
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


def write_run(root: Path, results: list[VenueResult], now: datetime) -> tuple[Path, Path, dict[str, Any]]:
    """Merge this run into today's snapshot and update showtimes.json.

    Returns (snapshot path, current path, payload written).
    """
    today = now.astimezone(VANCOUVER).date()
    existing = snapshot_path(root, today)
    previous = json.loads(existing.read_text("utf-8")) if existing.exists() else None
    payload = build_payload(results, now, previous)
    snap = write_snapshot(root, today, payload, today=today)
    current = root / CURRENT_FILENAME
    _write_json(current, payload)
    return snap, current, payload


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
