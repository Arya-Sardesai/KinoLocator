from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from kinolocator import archive
from kinolocator.archive import ArchiveError, VenueResult

from .test_schema import make

# 2026-10-13 01:00 UTC is still 2026-10-12 in Vancouver.
EVENING_RUN = datetime(2026, 10, 13, 1, 0, tzinfo=timezone.utc)
MORNING_RUN = datetime(2026, 10, 12, 13, 17, tzinfo=timezone.utc)
NEXT_DAY_RUN = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)


def rio(**overrides):
    return make(**{"venue_id": "rio_theatre", "venue_name": "Rio Theatre", "title": "Repo Man", **overrides})


def ok(*showtimes, venue_id="cinematheque"):
    return VenueResult(venue_id, list(showtimes))


def error(venue_id="cinematheque", msg="CinemathequeParseError: no #eventCalendar"):
    return VenueResult(venue_id, error=msg)


def empty(venue_id="cinematheque"):
    return VenueResult(venue_id, [])


def read(path):
    return json.loads(path.read_text())


def test_write_run_paths_use_vancouver_date(tmp_path):
    snap, current, _ = archive.write_run(tmp_path, [ok(make())], EVENING_RUN)
    assert snap == tmp_path / "archive" / "2026" / "10" / "12.json"
    assert current == tmp_path / "showtimes.json"
    assert snap.read_text() == current.read_text()


def test_payload_contents(tmp_path):
    later = make(title="Kwaidan", start=datetime(2026, 10, 20, 19, 0, tzinfo=timezone.utc))
    _, current, _ = archive.write_run(tmp_path, [ok(later, make())], EVENING_RUN)
    data = read(current)
    assert data["schema_version"] == 1
    assert data["date"] == "2026-10-12"
    assert data["showtime_count"] == 2
    assert data["venues"] == {
        "cinematheque": {
            "status": "ok",
            "failure": None,
            "error": None,
            "attempted_at": EVENING_RUN.isoformat(),
            "showtimes_scraped_at": EVENING_RUN.isoformat(),
            "showtime_count": 2,
        }
    }
    assert [s["title"] for s in data["showtimes"]] == ["Harakiri", "Kwaidan"]  # sorted by start
    assert archive.load_showtimes(current) == [make(), later]


# --- failures are recorded, good venues still written ----------------------


def test_failed_venue_is_recorded_alongside_successful_ones(tmp_path):
    snap, _, payload = archive.write_run(tmp_path, [error(), ok(rio(), venue_id="rio_theatre")], EVENING_RUN)
    data = read(snap)
    assert data == payload
    assert data["showtime_count"] == 1
    assert [s["venue_id"] for s in data["showtimes"]] == ["rio_theatre"]
    assert data["venues"]["rio_theatre"]["status"] == "ok"
    assert data["venues"]["cinematheque"] == {
        "status": "failed",
        "failure": "error",
        "error": "CinemathequeParseError: no #eventCalendar",
        "attempted_at": EVENING_RUN.isoformat(),
        "showtimes_scraped_at": None,
        "showtime_count": 0,
    }
    assert list(archive.failed_venues(data)) == ["cinematheque"]


def test_error_and_no_showtimes_are_distinguished(tmp_path):
    _, _, payload = archive.write_run(tmp_path, [error(), empty("rio_theatre")], EVENING_RUN)
    assert payload["venues"]["cinematheque"]["failure"] == "error"
    assert payload["venues"]["rio_theatre"]["failure"] == "no_showtimes"
    assert payload["venues"]["rio_theatre"]["error"] is None
    assert payload["showtime_count"] == 0


def test_all_failed_still_writes_a_snapshot(tmp_path):
    snap, _, _ = archive.write_run(tmp_path, [error()], EVENING_RUN)
    assert read(snap)["venues"]["cinematheque"]["status"] == "failed"


# --- same-day merging ------------------------------------------------------


def test_same_day_success_replaces_venue_showtimes(tmp_path):
    archive.write_run(tmp_path, [ok(make())], MORNING_RUN)
    snap, _, _ = archive.write_run(tmp_path, [ok(make(), make(title="Pulse"))], EVENING_RUN)
    data = read(snap)
    assert data["showtime_count"] == 2
    assert data["venues"]["cinematheque"]["showtimes_scraped_at"] == EVENING_RUN.isoformat()


def test_same_day_failure_keeps_earlier_good_data(tmp_path):
    archive.write_run(tmp_path, [ok(make()), ok(rio(), venue_id="rio_theatre")], MORNING_RUN)
    snap, current, _ = archive.write_run(tmp_path, [error(), ok(rio(), venue_id="rio_theatre")], EVENING_RUN)
    data = read(snap)
    assert {s["venue_id"] for s in data["showtimes"]} == {"cinematheque", "rio_theatre"}
    cine = data["venues"]["cinematheque"]
    assert cine["status"] == "failed" and cine["failure"] == "error"
    assert cine["attempted_at"] == EVENING_RUN.isoformat()
    assert cine["showtimes_scraped_at"] == MORNING_RUN.isoformat()  # data is from the morning
    assert cine["showtime_count"] == 1
    assert read(current) == data


def test_repeated_same_day_failures_keep_original_timestamp(tmp_path):
    archive.write_run(tmp_path, [ok(make())], MORNING_RUN)
    archive.write_run(tmp_path, [empty()], datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc))
    snap, _, _ = archive.write_run(tmp_path, [error()], EVENING_RUN)
    cine = read(snap)["venues"]["cinematheque"]
    assert cine["failure"] == "error"
    assert cine["showtimes_scraped_at"] == MORNING_RUN.isoformat()
    assert cine["showtime_count"] == 1


def test_venues_not_attempted_are_left_alone(tmp_path):
    archive.write_run(tmp_path, [ok(make()), ok(rio(), venue_id="rio_theatre")], MORNING_RUN)
    snap, _, _ = archive.write_run(tmp_path, [ok(rio(title="Akira"), venue_id="rio_theatre")], EVENING_RUN)
    data = read(snap)
    assert sorted(s["title"] for s in data["showtimes"]) == ["Akira", "Harakiri"]
    assert data["venues"]["cinematheque"]["attempted_at"] == MORNING_RUN.isoformat()


def test_failure_on_new_day_does_not_carry_yesterdays_data(tmp_path):
    archive.write_run(tmp_path, [ok(make())], EVENING_RUN)
    snap, current, _ = archive.write_run(tmp_path, [error()], NEXT_DAY_RUN)
    assert snap.name == "13.json"
    data = read(snap)
    assert data["showtimes"] == [] and data["venues"]["cinematheque"]["showtime_count"] == 0
    assert read(current) == data


# --- append-only -----------------------------------------------------------


def test_past_snapshot_is_never_overwritten(tmp_path):
    snap, _, _ = archive.write_run(tmp_path, [ok(make())], EVENING_RUN)
    before = snap.read_text()
    with pytest.raises(ArchiveError, match="append-only"):
        archive.write_snapshot(tmp_path, date(2026, 10, 12), {"x": 1}, today=date(2026, 10, 13))
    assert snap.read_text() == before


def test_next_day_run_adds_new_file(tmp_path):
    first, _, _ = archive.write_run(tmp_path, [ok(make())], EVENING_RUN)
    second, current, _ = archive.write_run(tmp_path, [ok(make(title="Pulse"))], NEXT_DAY_RUN)
    assert second.name == "13.json" and first.exists()
    assert read(first)["showtimes"][0]["title"] == "Harakiri"
    assert read(current)["showtimes"][0]["title"] == "Pulse"


def test_future_snapshot_rejected(tmp_path):
    with pytest.raises(ArchiveError, match="future"):
        archive.write_snapshot(tmp_path, date(2026, 10, 14), {}, today=date(2026, 10, 13))


def test_no_temp_files_left_behind(tmp_path):
    snap, _, _ = archive.write_run(tmp_path, [ok(make())], EVENING_RUN)
    assert [p.name for p in snap.parent.iterdir()] == ["12.json"]
