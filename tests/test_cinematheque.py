"""Cinematheque scraper against responses recorded from thecinematheque.ca on
2026-10-12 (see tests/fixtures/cinematheque/manifest.json). A failure here means
the code changed, not the website; re-record with `python -m kinolocator record
cinematheque` only when the site itself has changed."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from kinolocator.schema import VANCOUVER, PriceConfidence
from kinolocator.scrapers.cinematheque import (
    CALENDAR_URL,
    RATES_URL,
    Cinematheque,
    CinemathequeParseError,
    parse_calendar,
    parse_film_page,
    parse_general_price,
    parse_time,
)

TICKETS = "https://tickets.thecinematheque.ca/websales/pages/ticketsearchcriteria.aspx?evtinfo={}~c720b4d8-2524-4617-94b4-09d7b2ffa465&"


def page(fixture_dir, name):
    return (fixture_dir / "cinematheque" / name).read_text("utf-8")


@pytest.fixture
def showtimes(replay):
    session = replay("cinematheque")
    return Cinematheque(session, now=session.recorded_at).scrape()


def find(showtimes, title, start):
    matches = [s for s in showtimes if s.title == title and s.start == start]
    assert len(matches) == 1, f"expected one {title} at {start}, got {matches}"
    return matches[0]


# --- calendar --------------------------------------------------------------


def test_calendar_lists_screenings_only(fixture_dir):
    entries = parse_calendar(page(fixture_dir, "films_calendar.html"))
    assert len(entries) == 40
    titles = {e.title for e in entries}
    # programRental / programEvent entries are not screenings
    assert "VIFF 2026: Vancouver International Film Festival" not in titles
    assert "Annual General Meeting Notice 2026" not in titles
    assert all(e.start.tzinfo is VANCOUVER for e in entries)


def test_calendar_entry_fields(fixture_dir):
    entries = parse_calendar(page(fixture_dir, "films_calendar.html"))
    harakiri = [e for e in entries if e.title == "Harakiri"]
    assert [e.start for e in harakiri] == [
        datetime(2026, 10, 12, 18, 0, tzinfo=VANCOUVER),
        datetime(2026, 10, 14, 19, 45, tzinfo=VANCOUVER),
        datetime(2026, 10, 16, 21, 0, tzinfo=VANCOUVER),
    ]
    assert harakiri[0].film_url == "https://thecinematheque.ca/films/2026/harakiri"
    assert harakiri[0].ticket_url == TICKETS.format(572716)


def test_calendar_am_time(fixture_dir):
    entries = parse_calendar(page(fixture_dir, "films_calendar.html"))
    yf = next(e for e in entries if e.title == "Young Frankenstein")
    assert yf.start == datetime(2026, 10, 18, 10, 30, tzinfo=VANCOUVER)


def test_calendar_layout_change_is_an_error():
    with pytest.raises(CinemathequeParseError):
        parse_calendar("<html><body><main><p>Calendar coming soon</p></main></body></html>")


@pytest.mark.parametrize(
    ("text", "meridiem", "expected"),
    [("12:00", "pm", time(12, 0)), ("12:15", "am", time(0, 15)), ("9:05", "am", time(9, 5)),
     ("8:40", "pm", time(20, 40))],
)
def test_parse_time(text, meridiem, expected):
    assert parse_time(text, meridiem) == expected


@pytest.mark.parametrize(("text", "meridiem"), [("8:40", ""), ("13:00", "pm"), ("TBA", "pm")])
def test_parse_time_rejects_garbage(text, meridiem):
    with pytest.raises(CinemathequeParseError):
        parse_time(text, meridiem)


# --- film pages ------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "fmt", "languages", "subtitles"),
    [
        ("films_2026_harakiri.html", "35mm", ["ja"], ["en"]),
        ("films_2026_trouble-every-day.html", "DCP", ["fr", "en"], ["en"]),
        ("films_2026_replica.html", "DCP", ["zh", "en"], ["en"]),
        ("films_2026_past-future-continuous.html", "DCP", ["fa"], ["en"]),
        ("films_2026_daughters-darkness.html", "DCP", ["en"], []),
        ("films_2026_color-correction.html", "35mm", ["xx"], []),  # "Silent"
        ("films_2026_begotten.html", "DCP", ["xx"], []),  # "No dialogue"
        ("films_2026_young-frankenstein.html", "DCP", None, None),  # no language line
        ("films_2026_rylan-friday.html", None, None, None),  # no format listed
    ],
)
def test_film_page(fixture_dir, name, fmt, languages, subtitles):
    details = parse_film_page(page(fixture_dir, name))
    assert details.format == fmt
    assert details.languages == languages
    assert details.subtitles == subtitles


def test_free_admission_callout(fixture_dir):
    assert parse_film_page(page(fixture_dir, "films_2026_hello-destroyer.html")).free_admission
    assert not parse_film_page(page(fixture_dir, "films_2026_harakiri.html")).free_admission


def test_film_page_layout_change_is_an_error():
    with pytest.raises(CinemathequeParseError):
        parse_film_page("<html><body><main><h1>Harakiri</h1></main></body></html>")


def test_general_price(fixture_dir):
    assert parse_general_price(page(fixture_dir, "about_visit.html")) == 15.0
    assert parse_general_price("<main>Tickets at the door</main>") is None


# --- full scrape -----------------------------------------------------------


def test_scrape_end_to_end(showtimes):
    assert len(showtimes) == 40
    assert {s.venue_id for s in showtimes} == {"cinematheque"}
    assert {s.venue_name for s in showtimes} == {"The Cinematheque"}
    assert {s.municipality for s in showtimes} == {"Vancouver"}
    assert all(s.start.date() >= date(2026, 10, 12) for s in showtimes)  # yesterday dropped
    assert all(s.ticket_url and s.ticket_url.startswith("https://tickets.thecinematheque.ca/") for s in showtimes)


def test_scrape_combines_calendar_and_film_page(showtimes):
    s = find(showtimes, "Harakiri", datetime(2026, 10, 12, 18, 0, tzinfo=VANCOUVER))
    assert s.start.isoformat() == "2026-10-12T18:00:00-07:00"
    assert s.original_language == "ja"
    assert s.screening_language == ["ja"]
    assert s.subtitles == ["en"]
    assert s.format == "35mm"
    assert s.ticket_url == TICKETS.format(572716)
    assert s.regular_price == 15.0
    assert s.price_confidence is PriceConfidence.ESTIMATED
    assert s.tmdb_id is None


def test_scrape_free_screening_has_scraped_zero_price(showtimes):
    s = find(showtimes, "Hello Destroyer", datetime(2026, 10, 27, 19, 0, tzinfo=VANCOUVER))
    assert s.regular_price == 0.0
    assert s.price_confidence is PriceConfidence.SCRAPED


def test_scrape_uses_vancouver_offset_after_2026_time_change(showtimes):
    # BC stopped changing clocks on 2026-11-01 and stays at UTC-7 year round.
    # If this fails with -08:00, the tz database is out of date (pip install -U tzdata).
    s = find(showtimes, "Cameron Winter at Carnegie Hall", datetime(2026, 12, 11, 21, 0, tzinfo=VANCOUVER))
    assert s.start.isoformat() == "2026-12-11T21:00:00-07:00"


def test_scrape_fetches_each_film_page_once(replay):
    session = replay("cinematheque")
    Cinematheque(session, now=session.recorded_at).scrape()
    assert session.requested[0] == CALENDAR_URL
    assert RATES_URL in session.requested
    assert len(session.requested) == len(set(session.requested))


def test_scrape_survives_one_missing_film_page(replay):
    session = replay("cinematheque")
    del session.responses["https://thecinematheque.ca/films/2026/pulse"]
    showtimes = Cinematheque(session, now=session.recorded_at).scrape()
    pulse = [s for s in showtimes if s.title == "Pulse"]
    assert len(pulse) == 4 and all(s.format is None for s in pulse)


def test_scrape_fails_when_most_film_pages_break(replay):
    session = replay("cinematheque")
    for url in list(session.responses):
        if "/films/2026/" in url:
            del session.responses[url]
    with pytest.raises(CinemathequeParseError):
        Cinematheque(session, now=session.recorded_at).scrape()
