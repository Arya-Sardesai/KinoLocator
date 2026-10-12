"""Writes the dated archive snapshot and the current showtimes.json.

Layout (relative to the output root):

    showtimes.json              latest run, overwritten every run
    archive/YYYY/MM/DD.json     one snapshot per Vancouver calendar day

The archive is append-only: a snapshot for a past date is never rewritten.
Today's snapshot may be replaced by a later run on the same day, so each date
ends up holding the last successful run of that day.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from .schema import VANCOUVER, Showtime

CURRENT_FILENAME = "showtimes.json"
ARCHIVE_DIRNAME = "archive"


class ArchiveError(RuntimeError):
    pass


def snapshot_path(root: Path, day: date) -> Path:
    return root / ARCHIVE_DIRNAME / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}.json"


def build_payload(showtimes: Iterable[Showtime], generated_at: datetime) -> dict[str, Any]:
    ordered = sorted(showtimes, key=lambda s: s.sort_key)
    return {
        "date": generated_at.astimezone(VANCOUVER).date().isoformat(),
        "generated_at": generated_at.isoformat(),
        "showtime_count": len(ordered),
        "venues": dict(sorted(Counter(s.venue_id for s in ordered).items())),
        "showtimes": [s.to_dict() for s in ordered],
    }


def write_snapshot(root: Path, day: date, payload: dict[str, Any], *, today: date) -> Path:
    """Write the snapshot for `day`, refusing to touch an existing past date."""
    path = snapshot_path(root, day)
    if day > today:
        raise ArchiveError(f"refusing to write snapshot for future date {day} (today is {today})")
    if day < today and path.exists():
        raise ArchiveError(f"archive is append-only: {path} already exists for past date {day}")
    _write_json(path, payload)
    return path


def write_run(root: Path, showtimes: Iterable[Showtime], now: datetime) -> tuple[Path, Path]:
    """Write today's snapshot and the current showtimes.json. Returns both paths."""
    today = now.astimezone(VANCOUVER).date()
    payload = build_payload(showtimes, now)
    snap = write_snapshot(root, today, payload, today=today)
    current = root / CURRENT_FILENAME
    _write_json(current, payload)
    return snap, current


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
