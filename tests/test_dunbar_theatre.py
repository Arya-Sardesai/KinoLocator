"""Dunbar Theatre: Veezi sessions pages, recorded 2026-10-20 02:02 UTC (19:02 in Vancouver)."""

from __future__ import annotations

from datetime import datetime

import pytest

from kinolocator.overrides import Override
from kinolocator.schema import VANCOUVER, FilmHints
from kinolocator.scrapers.dunbar_theatre import (
    DunbarTheatre,
    VeeziParseError,
    parse_sessions,
    sessions_url,
    title_language,
)

FIXTURES = "dunbar_theatre"
MAIN = sessions_url("sap55z6c6aj6b96yshhtj7vyw4")
SPECIAL = sessions_url("b2fkxrvna0ydd3hyn7wv85791c")


@pytest.fixture
def scraped(replay):
    session = replay(FIXTURES)
    scraper = DunbarTheatre(session, now=session.recorded_at)
    return scraper.scrape(), scraper, session


def at(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=VANCOUVER)


def page(session, url):
    return (session.fixture_dir / session.responses[url]).read_text()


def test_scrape_counts(scraped):
    showtimes, scraper, session = scraped
    assert len(showtimes) == 36
    assert {s.title for s in showtimes} == {"Primetime", "Wildwood", "Jailer 2 (Tamil)", "Jailer 2 (Telugu)"}
    assert session.requested == [MAIN, SPECIAL]


def test_film_fields(scraped):
    showtimes, _, _ = scraped
    [first] = [s for s in showtimes if s.title == "Primetime" and s.start == at(2026, 10, 20, 16, 0)]
    assert first.venue_id == "dunbar_theatre" and first.municipality == "Vancouver"
    assert first.ticket_url == "https://ticketing.uswest.veezi.com/purchase/8443?siteToken=sap55z6c6aj6b96yshhtj7vyw4"
    # Veezi states no language, format or price on the sessions page.
    assert first.screening_language is None and first.format is None and first.regular_price is None


def test_language_versions_from_titles(scraped):
    showtimes, _, _ = scraped
    [tamil] = [s for s in showtimes if s.title == "Jailer 2 (Tamil)"]
    [telugu] = [s for s in showtimes if s.title == "Jailer 2 (Telugu)"]
    assert tamil.start == at(2026, 10, 19, 21, 30) and tamil.screening_language == ["ta"]
    assert telugu.start == at(2026, 10, 23, 21, 45) and telugu.screening_language == ["te"]
    # The Telugu version is a dub: the screened language says nothing about the original.
    assert tamil.original_language is None and telugu.original_language is None


@pytest.mark.parametrize(
    ("title", "code"),
    [
        ("Jailer 2 (Tamil)", "ta"),
        ("Jailer 2 (Telugu)", "te"),
        ("Pushpa 3 (Hindi) ", "hi"),
        ("Wildwood", None),
        ("Dune: Part Three (Advance Screening)", None),
        ("Jailer (Tamil) 2", None),  # only a trailing aside counts
    ],
)
def test_title_language(title, code, caplog):
    assert title_language(title) == code
    assert "unknown language name" not in caplog.text  # probing an aside is quiet


def test_wall_clock_kept_after_the_time_change(scraped):
    # Veezi sends "2026-11-01T13:00:00-08:00" (old tz data) for a session the
    # page shows at 1:00 PM; BC is at UTC-7 from 2026-11-01, so it is 13:00-07:00.
    showtimes, _, session = scraped
    assert '"startDate":"2026-11-01T13:00:00-08:00"' in page(session, MAIN)
    starts = {s.start for s in showtimes if s.title == "Wildwood"}
    assert at(2026, 11, 1, 13, 0) in starts
    assert at(2026, 11, 1, 13, 0).isoformat() == "2026-11-01T13:00:00-07:00"


def test_past_sessions_dropped(scraped):
    showtimes, _, _ = scraped
    assert min(s.start for s in showtimes).date().isoformat() == "2026-10-19"


def test_fundraiser_flagged_for_review(scraped):
    _, scraper, _ = scraped
    [skip] = scraper.skipped
    assert skip.title == "Fundraiser - Wild Cats Soft Ball"
    assert skip.needs_review and skip.key == "special:ST00000106"
    assert skip.start == at(2026, 11, 29, 10, 0)


def test_override_decides_the_fundraiser(replay):
    session = replay(FIXTURES)
    scraper = DunbarTheatre(
        session, now=session.recorded_at,
        overrides={"special:ST00000106": Override(True, "Fundraiser", "screening of a family film")},
    )
    showtimes = scraper.scrape()
    assert len(showtimes) == 37 and not scraper.skipped


def test_every_listing_is_a_showtime_or_a_recorded_skip(scraped):
    showtimes, scraper, session = scraped
    listed = parse_sessions(page(session, MAIN)) + parse_sessions(page(session, SPECIAL))
    today = session.recorded_at.astimezone(VANCOUVER).date()
    upcoming = {(s.title, s.start) for s in listed if s.start.date() >= today}
    assert upcoming == {(s.title, s.start) for s in showtimes} | {(k.title, k.start) for k in scraper.skipped}


def test_runtime_hints(scraped):
    _, scraper, _ = scraped
    assert scraper.hints["Primetime"] == FilmHints(runtime=110)
    assert scraper.hints["Jailer 2 (Tamil)"] == FilmHints(runtime=150)


def test_json_ld_and_page_must_agree(replay):
    session = replay(FIXTURES)
    html = page(session, SPECIAL).replace("<time>9:30 PM</time>", "<time>8:30 PM</time>")
    with pytest.raises(VeeziParseError, match="JSON-LD says 21:30 but the page shows 20:30"):
        parse_sessions(html)


def test_layout_change_raises():
    with pytest.raises(VeeziParseError, match="no #sessionsByFilmConent"):
        parse_sessions("<html><body><p>Under maintenance</p></body></html>")
