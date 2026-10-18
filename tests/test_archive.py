from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from kinolocator import archive
from kinolocator.archive import ArchiveError, VenueResult
from kinolocator.schema import VANCOUVER, Skipped

from .test_schema import make

# 2026-10-13 01:00 UTC is still 2026-10-12 in Vancouver.
EVENING_RUN = datetime(2026, 10, 13, 1, 0, tzinfo=timezone.utc)
MORNING_RUN = datetime(2026, 10, 12, 13, 17, tzinfo=timezone.utc)
NEXT_DAY_RUN = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 20, 19, 0, tzinfo=VANCOUVER)


def rio(**overrides):
    return make(**{"venue_id": "rio_theatre", "venue_name": "Rio Theatre", "title": "Repo Man", **overrides})


def ok(*showtimes, venue_id="cinematheque", skipped=()):
    return VenueResult(venue_id, list(showtimes), skipped=list(skipped))


def error(venue_id="cinematheque", msg="CinemathequeParseError: no #eventCalendar"):
    return VenueResult(venue_id, error=msg)


def empty(venue_id="cinematheque"):
    return VenueResult(venue_id, [])


def read(path):
    return json.loads(path.read_text())


def test_write_run_paths_use_vancouver_date(tmp_path):
    w = archive.write_run(tmp_path, [ok(make())], EVENING_RUN)
    assert w.snapshot_path == tmp_path / "archive" / "2026" / "10" / "12.json"
    assert w.current_path == tmp_path / "showtimes.json"
    assert read(w.snapshot_path) == w.snapshot and read(w.current_path) == w.current


def test_payload_contents(tmp_path):
    later = make(title="Kwaidan", start=LATER)
    w = archive.write_run(tmp_path, [ok(later, make())], EVENING_RUN)
    data = read(w.snapshot_path)
    assert data["schema_version"] == 2
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
            "skipped": [],
        }
    }
    assert [s["title"] for s in data["showtimes"]] == ["Harakiri", "Kwaidan"]  # sorted by start
    assert archive.load_showtimes(w.snapshot_path) == [make(), later]


def test_current_matches_snapshot_plus_freshness_when_all_ok(tmp_path):
    w = archive.write_run(tmp_path, [ok(make())], EVENING_RUN)
    venue = read(w.current_path)["venues"]["cinematheque"]
    assert venue["stale"] is False
    assert venue["last_confirmed_at"] == EVENING_RUN.isoformat()
    assert {k: v for k, v in venue.items() if k not in ("stale", "last_confirmed_at")} == w.snapshot["venues"]["cinematheque"]
    assert read(w.current_path)["showtimes"] == w.snapshot["showtimes"]


# --- skipped listings ------------------------------------------------------


def test_skipped_listings_are_recorded(tmp_path):
    skips = [
        Skipped("Comedy Night", "event tagged Live Event (not a film)", LATER, "https://x/event/c/"),
        Skipped("VIMFF: Reel Rock", "event tagged neither Film Event nor Live Event", LATER, None, True),
        Skipped("VIFF 2026", "third-party rental"),
    ]
    w = archive.write_run(tmp_path, [ok(make(), skipped=skips)], EVENING_RUN)
    assert read(w.snapshot_path)["venues"]["cinematheque"]["skipped"] == [
        {"title": "Comedy Night", "reason": "event tagged Live Event (not a film)",
         "start": "2026-10-20T19:00:00-07:00", "url": "https://x/event/c/", "needs_review": False},
        {"title": "VIMFF: Reel Rock", "reason": "event tagged neither Film Event nor Live Event",
         "start": "2026-10-20T19:00:00-07:00", "url": None, "needs_review": True},
        {"title": "VIFF 2026", "reason": "third-party rental", "start": None, "url": None, "needs_review": False},
    ]


def test_failed_rerun_keeps_skipped_from_the_run_whose_data_is_kept(tmp_path):
    archive.write_run(tmp_path, [ok(make(), skipped=[Skipped("Comedy", "live")])], MORNING_RUN)
    w = archive.write_run(tmp_path, [error()], EVENING_RUN)
    assert [k["title"] for k in w.snapshot["venues"]["cinematheque"]["skipped"]] == ["Comedy"]


# --- failures are recorded, good venues still written ----------------------


def test_failed_venue_is_recorded_alongside_successful_ones(tmp_path):
    w = archive.write_run(tmp_path, [error(), ok(rio(), venue_id="rio_theatre")], EVENING_RUN)
    data = read(w.snapshot_path)
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
        "skipped": [],
    }
    assert list(archive.failed_venues(data)) == ["cinematheque"]


def test_error_and_no_showtimes_are_distinguished(tmp_path):
    w = archive.write_run(tmp_path, [error(), empty("rio_theatre")], EVENING_RUN)
    assert w.snapshot["venues"]["cinematheque"]["failure"] == "error"
    assert w.snapshot["venues"]["rio_theatre"]["failure"] == "no_showtimes"
    assert w.snapshot["venues"]["rio_theatre"]["error"] is None
    assert w.snapshot["showtime_count"] == 0


def test_all_failed_still_writes_a_snapshot(tmp_path):
    w = archive.write_run(tmp_path, [error()], EVENING_RUN)
    assert read(w.snapshot_path)["venues"]["cinematheque"]["status"] == "failed"


# --- same-day merging (archive) --------------------------------------------


def test_same_day_success_replaces_venue_showtimes(tmp_path):
    archive.write_run(tmp_path, [ok(make())], MORNING_RUN)
    w = archive.write_run(tmp_path, [ok(make(), make(title="Pulse"))], EVENING_RUN)
    assert w.snapshot["showtime_count"] == 2
    assert w.snapshot["venues"]["cinematheque"]["showtimes_scraped_at"] == EVENING_RUN.isoformat()


def test_same_day_failure_keeps_earlier_good_data(tmp_path):
    archive.write_run(tmp_path, [ok(make()), ok(rio(), venue_id="rio_theatre")], MORNING_RUN)
    w = archive.write_run(tmp_path, [error(), ok(rio(), venue_id="rio_theatre")], EVENING_RUN)
    data = read(w.snapshot_path)
    assert {s["venue_id"] for s in data["showtimes"]} == {"cinematheque", "rio_theatre"}
    cine = data["venues"]["cinematheque"]
    assert cine["status"] == "failed" and cine["failure"] == "error"
    assert cine["attempted_at"] == EVENING_RUN.isoformat()
    assert cine["showtimes_scraped_at"] == MORNING_RUN.isoformat()  # data is from the morning
    assert cine["showtime_count"] == 1
    # Shown in showtimes.json too, flagged stale as of the morning.
    current = read(w.current_path)["venues"]["cinematheque"]
    assert current["stale"] is True and current["last_confirmed_at"] == MORNING_RUN.isoformat()


def test_repeated_same_day_failures_keep_original_timestamp(tmp_path):
    archive.write_run(tmp_path, [ok(make())], MORNING_RUN)
    archive.write_run(tmp_path, [empty()], datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc))
    w = archive.write_run(tmp_path, [error()], EVENING_RUN)
    cine = w.snapshot["venues"]["cinematheque"]
    assert cine["failure"] == "error"
    assert cine["showtimes_scraped_at"] == MORNING_RUN.isoformat()
    assert cine["showtime_count"] == 1


def test_venues_not_attempted_are_left_alone(tmp_path):
    archive.write_run(tmp_path, [ok(make()), ok(rio(), venue_id="rio_theatre")], MORNING_RUN)
    w = archive.write_run(tmp_path, [ok(rio(title="Akira"), venue_id="rio_theatre")], EVENING_RUN)
    assert sorted(s["title"] for s in w.snapshot["showtimes"]) == ["Akira", "Harakiri"]
    assert w.snapshot["venues"]["cinematheque"]["attempted_at"] == MORNING_RUN.isoformat()


# --- stale fallback (showtimes.json only) ----------------------------------


def test_new_day_failure_falls_back_in_current_but_not_in_archive(tmp_path):
    upcoming = make(title="Kwaidan", start=LATER)
    archive.write_run(tmp_path, [ok(make(), upcoming), ok(rio(start=LATER), venue_id="rio_theatre")], EVENING_RUN)
    w = archive.write_run(tmp_path, [error(), ok(rio(title="Akira", start=LATER), venue_id="rio_theatre")], NEXT_DAY_RUN)

    snap = read(w.snapshot_path)
    assert w.snapshot_path.name == "13.json"
    assert [s["venue_id"] for s in snap["showtimes"]] == ["rio_theatre"]  # archive: as observed
    assert snap["venues"]["cinematheque"]["showtime_count"] == 0
    assert "stale" not in snap["venues"]["cinematheque"]

    current = read(w.current_path)
    cine = current["venues"]["cinematheque"]
    assert cine["status"] == "failed" and cine["failure"] == "error"
    assert cine["stale"] is True
    assert cine["last_confirmed_at"] == EVENING_RUN.isoformat()
    assert cine["showtime_count"] == 1  # yesterday's Harakiri has passed; only Kwaidan carried
    assert [(s["venue_id"], s["title"]) for s in current["showtimes"]] == [
        ("cinematheque", "Kwaidan"), ("rio_theatre", "Akira")
    ]
    assert current["showtime_count"] == 2
    assert current["venues"]["rio_theatre"]["stale"] is False


def test_stale_data_keeps_original_confirmation_time_across_days(tmp_path):
    upcoming = make(title="Kwaidan", start=LATER)
    archive.write_run(tmp_path, [ok(upcoming)], EVENING_RUN)
    archive.write_run(tmp_path, [error()], NEXT_DAY_RUN)
    w = archive.write_run(tmp_path, [empty()], datetime(2026, 10, 14, 20, 0, tzinfo=timezone.utc))
    cine = w.current["venues"]["cinematheque"]
    assert cine["failure"] == "no_showtimes"
    assert cine["stale"] is True and cine["last_confirmed_at"] == EVENING_RUN.isoformat()
    assert [s["title"] for s in w.current["showtimes"]] == ["Kwaidan"]


def test_recovery_clears_stale(tmp_path):
    archive.write_run(tmp_path, [ok(make(title="Kwaidan", start=LATER))], EVENING_RUN)
    archive.write_run(tmp_path, [error()], NEXT_DAY_RUN)
    w = archive.write_run(tmp_path, [ok(make(title="Pulse", start=LATER))], datetime(2026, 10, 14, 20, 0, tzinfo=timezone.utc))
    cine = w.current["venues"]["cinematheque"]
    assert cine["stale"] is False and cine["status"] == "ok"
    assert [s["title"] for s in w.current["showtimes"]] == ["Pulse"]


def test_failure_with_nothing_upcoming_to_carry(tmp_path):
    archive.write_run(tmp_path, [ok(make())], EVENING_RUN)  # Harakiri on the 12th
    w = archive.write_run(tmp_path, [error()], NEXT_DAY_RUN)
    cine = w.current["venues"]["cinematheque"]
    assert cine["stale"] is False and cine["showtime_count"] == 0
    assert cine["last_confirmed_at"] == EVENING_RUN.isoformat()
    assert w.current["showtimes"] == []


def test_failure_with_no_history(tmp_path):
    w = archive.write_run(tmp_path, [error()], EVENING_RUN)
    cine = w.current["venues"]["cinematheque"]
    assert cine["stale"] is False and cine["last_confirmed_at"] is None


def test_previous_schema_v1_current_is_readable(tmp_path):
    # showtimes.json as written before stale tracking existed (no last_confirmed_at).
    v1 = archive.build_payload([ok(make(title="Kwaidan", start=LATER))], EVENING_RUN)
    for v in v1["venues"].values():
        v.pop("skipped")
    (tmp_path / "showtimes.json").write_text(json.dumps(v1))
    w = archive.write_run(tmp_path, [error()], NEXT_DAY_RUN)
    assert w.current["venues"]["cinematheque"]["last_confirmed_at"] == EVENING_RUN.isoformat()
    assert w.current["showtime_count"] == 1


# --- append-only -----------------------------------------------------------


def test_past_snapshot_is_never_overwritten(tmp_path):
    w = archive.write_run(tmp_path, [ok(make())], EVENING_RUN)
    before = w.snapshot_path.read_text()
    with pytest.raises(ArchiveError, match="append-only"):
        archive.write_snapshot(tmp_path, date(2026, 10, 12), {"x": 1}, today=date(2026, 10, 13))
    assert w.snapshot_path.read_text() == before


def test_next_day_run_adds_new_file(tmp_path):
    first = archive.write_run(tmp_path, [ok(make())], EVENING_RUN)
    second = archive.write_run(tmp_path, [ok(make(title="Pulse"))], NEXT_DAY_RUN)
    assert second.snapshot_path.name == "13.json" and first.snapshot_path.exists()
    assert read(first.snapshot_path)["showtimes"][0]["title"] == "Harakiri"
    assert read(second.current_path)["showtimes"][0]["title"] == "Pulse"


def test_future_snapshot_rejected(tmp_path):
    with pytest.raises(ArchiveError, match="future"):
        archive.write_snapshot(tmp_path, date(2026, 10, 14), {}, today=date(2026, 10, 13))


def test_no_temp_files_left_behind(tmp_path):
    w = archive.write_run(tmp_path, [ok(make())], EVENING_RUN)
    assert [p.name for p in w.snapshot_path.parent.iterdir()] == ["12.json"]
