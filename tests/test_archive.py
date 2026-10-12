from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from kinolocator import archive
from kinolocator.archive import ArchiveError

from .test_schema import make

# 2026-10-13 01:00 UTC is still 2026-10-12 in Vancouver.
EVENING_RUN = datetime(2026, 10, 13, 1, 0, tzinfo=timezone.utc)


def test_write_run_paths_use_vancouver_date(tmp_path):
    snap, current = archive.write_run(tmp_path, [make()], EVENING_RUN)
    assert snap == tmp_path / "archive" / "2026" / "10" / "12.json"
    assert current == tmp_path / "showtimes.json"
    assert snap.read_text() == current.read_text()


def test_payload_contents(tmp_path):
    later = make(title="Kwaidan", start=datetime(2026, 10, 20, 19, 0, tzinfo=timezone.utc))
    _, current = archive.write_run(tmp_path, [later, make()], EVENING_RUN)
    data = json.loads(current.read_text())
    assert data["date"] == "2026-10-12"
    assert data["showtime_count"] == 2
    assert data["venues"] == {"cinematheque": 2}
    assert [s["title"] for s in data["showtimes"]] == ["Harakiri", "Kwaidan"]  # sorted by start
    assert archive.load_showtimes(current) == [make(), later]


def test_same_day_rerun_replaces_todays_snapshot(tmp_path):
    archive.write_run(tmp_path, [make()], EVENING_RUN)
    snap, _ = archive.write_run(tmp_path, [make(), make(title="Pulse")], EVENING_RUN)
    assert json.loads(snap.read_text())["showtime_count"] == 2


def test_past_snapshot_is_never_overwritten(tmp_path):
    snap, _ = archive.write_run(tmp_path, [make()], EVENING_RUN)
    before = snap.read_text()
    with pytest.raises(ArchiveError, match="append-only"):
        archive.write_snapshot(tmp_path, date(2026, 10, 12), {"x": 1}, today=date(2026, 10, 13))
    assert snap.read_text() == before


def test_next_day_run_adds_new_file(tmp_path):
    first, _ = archive.write_run(tmp_path, [make()], EVENING_RUN)
    second, current = archive.write_run(tmp_path, [make(title="Pulse")], datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc))
    assert second.name == "13.json" and first.exists()
    assert json.loads(first.read_text())["showtimes"][0]["title"] == "Harakiri"
    assert json.loads(current.read_text())["showtimes"][0]["title"] == "Pulse"


def test_future_snapshot_rejected(tmp_path):
    with pytest.raises(ArchiveError, match="future"):
        archive.write_snapshot(tmp_path, date(2026, 10, 14), {}, today=date(2026, 10, 13))


def test_no_temp_files_left_behind(tmp_path):
    snap, _ = archive.write_run(tmp_path, [make()], EVENING_RUN)
    assert [p.name for p in snap.parent.iterdir()] == ["12.json"]
