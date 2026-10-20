"""Park Theatre scraper against Barker API responses recorded from
www.theparktheatre.ca on 2026-10-18 (tests/fixtures/park_theatre/manifest.json).
The parsing and classification rules are shared with the Rio (_barker.py) and
unit-tested in test_rio_theatre.py; these tests pin the Park's own data."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from kinolocator.overrides import Override, load_overrides
from kinolocator.schema import VANCOUVER, FilmHints, PriceConfidence
from kinolocator.languages import language_code
from kinolocator.scrapers._barker import Listing, parse_language_terms, parse_listings, parse_posts
from kinolocator.scrapers.park_theatre import ParkTheatre

FIXTURES = "park_theatre"


def fixture_text(fixture_dir, prefix):
    [path] = (fixture_dir / FIXTURES).glob(prefix + "*")
    return path.read_text("utf-8")


@pytest.fixture
def scraped(replay):
    session = replay(FIXTURES)
    scraper = ParkTheatre(session, now=session.recorded_at)
    return scraper.scrape(), scraper.skipped, session


def find(showtimes, title, start):
    [match] = [s for s in showtimes if s.title == title and s.start == start]
    return match


def by_title(showtimes, title):
    return [s for s in showtimes if s.title == title]


# --- parsing ---------------------------------------------------------------


def test_parse_listings(fixture_dir):
    listings = parse_listings(fixture_text(fixture_dir, "wp-json_barker_v1_listings"))
    assert len(listings) == 65
    assert {x.kind for x in listings} == {"movie", "event"}
    assert listings[0] == Listing(
        post_id=54680,
        kind="movie",
        title="Tony",
        url="https://www.theparktheatre.ca/movie/tony/",
        start=datetime(2026, 10, 18, 13, 15, tzinfo=VANCOUVER),
        ticket_url="https://tickets.theparktheatre.ca/events/45549-tony",
    )


def test_parse_listings_titles_and_missing_ticket_links(fixture_dir):
    listings = parse_listings(fixture_text(fixture_dir, "wp-json_barker_v1_listings"))
    titles = {x.title for x in listings}
    assert "Menopause: Coming in Hot (Filmmakers in Attendance for Q&A)" in titles
    assert not any("&#" in t for t in titles)
    # An empty tickets_link becomes None rather than "".
    assert sum(x.ticket_url is None for x in listings) == 3


def test_parse_movie_posts(fixture_dir):
    posts = parse_posts(fixture_text(fixture_dir, "wp-json_wp_v2_barker_movie"), with_languages=True)
    assert len(posts) == 16
    assert posts[54100].tags == {"coming-soon", "film-event"}  # Dune 70mm
    assert posts[54680].tags == frozenset()  # Tony: a movie post needs no tag
    assert posts[54501].language_ids == ()  # Motorcycle Film Fest: no language terms


def test_language_terms_resolve(fixture_dir, caplog):
    names = parse_language_terms(fixture_text(fixture_dir, "wp-json_wp_v2_barker-movie-language"))
    # Fatherland's class_list shows "polski" *and* "p": the latter is TMDB's
    # "Pусский" (Latin P) with its Cyrillic bytes stripped from the slug.
    assert "Pусский" in names.values()
    assert all(language_code(n) for n in names.values())
    assert "unknown language" not in caplog.text


def test_parse_event_posts(fixture_dir):
    posts = parse_posts(fixture_text(fixture_dir, "wp-json_wp_v2_barker_event"))
    assert posts[52646].tags == {"film-event"}  # cereal cartoon party
    assert posts[53985].tags == frozenset()  # Inspire Film Fest: untagged
    assert posts[52836].tags == frozenset()  # Private Event: untagged


def test_description_prices(fixture_dir):
    movies = parse_posts(fixture_text(fixture_dir, "wp-json_wp_v2_barker_movie"))
    events = parse_posts(fixture_text(fixture_dir, "wp-json_wp_v2_barker_event"))
    assert movies[54501].price == 35.0  # "Tickets $35+ fees"
    assert movies[54809].price == 0.0  # "a FREE (!) Halloween screening"
    assert events[52646].price == 23.0  # "Adults $23 Kids Pricing (13 and under) $15"
    assert movies[54594].price is None  # "donation of $10 or more" is not a ticket price
    assert movies[54100].price is None


# --- full scrape -----------------------------------------------------------


def test_scrape_counts(scraped):
    showtimes, skipped, _ = scraped
    assert len(showtimes) == 63
    assert len(skipped) == 2


def test_every_listing_is_a_showtime_or_a_recorded_skip(scraped, fixture_dir):
    showtimes, skipped, _ = scraped
    listings = parse_listings(fixture_text(fixture_dir, "wp-json_barker_v1_listings"))
    produced = sorted([(s.title, s.start) for s in showtimes] + [(k.title, k.start) for k in skipped])
    assert produced == sorted((x.title, x.start) for x in listings)
    assert {s.venue_id for s in showtimes} == {"park_theatre"}
    assert all(s.start.tzinfo is not None for s in showtimes)


def test_scrape_film_fields(scraped):
    showtimes, _, _ = scraped
    tony = find(showtimes, "Tony", datetime(2026, 10, 18, 13, 15, tzinfo=VANCOUVER))
    assert tony.start.isoformat() == "2026-10-18T13:15:00-07:00"
    assert tony.ticket_url == "https://tickets.theparktheatre.ca/events/45549-tony"
    assert tony.original_language == "en" and tony.screening_language == ["en"]
    assert tony.subtitles is None and tony.format is None
    # No rate card at the Park, so a film without a stated price has none.
    assert (tony.regular_price, tony.price_confidence) == (None, None)


def test_scrape_format_from_title(scraped):
    showtimes, _, _ = scraped
    dune70 = by_title(showtimes, "Dune: Part Three (Presented in 70mm)")
    assert len(dune70) == 22
    assert {s.format for s in dune70} == {"70mm"}
    # The title is kept as listed; only the format is extracted.
    assert all(s.title.endswith("(Presented in 70mm)") for s in dune70)
    # Other bracketed asides are not formats.
    for title in ("Dune: Part Three (Advance Screenings)", "The Fly (40th Anniversary Screening)"):
        assert {s.format for s in by_title(showtimes, title)} == {None}
    assert {s.format for s in showtimes} == {None, "70mm"}


def test_scrape_scraped_prices(scraped):
    showtimes, _, _ = scraped
    prices = {
        "The Motorcycle Film Fest": 35.0,
        "Beetlejuice (FREE Halloween Screening!)": 0.0,
        "The Saturday Morning All-You-Can-Eat Cereal Cartoon Party!": 23.0,
    }
    for title, price in prices.items():
        [s] = by_title(showtimes, title)
        assert (s.regular_price, s.price_confidence) == (price, PriceConfidence.SCRAPED)
    assert [s for s in showtimes if s.regular_price is not None and s.title not in prices] == []


def test_scrape_languages(scraped, caplog):
    showtimes, _, _ = scraped
    assert "unknown language" not in caplog.text and "no name for language term" not in caplog.text
    altered = by_title(showtimes, "Altered States")
    assert [s.screening_language for s in altered] == [["en", "es"]]  # "Español"
    assert [s.screening_language for s in by_title(showtimes, "The Motorcycle Film Fest")] == [None]
    [fatherland] = by_title(showtimes, "Fatherland")
    assert fatherland.screening_language == ["de", "en", "fr", "pl", "ru"]
    assert fatherland.original_language is None  # several spoken languages: ambiguous
    devils = by_title(showtimes, "Ken Russell's 'The Devils'")
    assert len(devils) == 8
    assert all(s.screening_language == ["en", "la"] and s.original_language is None for s in devils)


def test_scrape_includes_film_events(scraped):
    showtimes, _, _ = scraped
    # An event post tagged Film Event.
    assert len(by_title(showtimes, "The Saturday Morning All-You-Can-Eat Cereal Cartoon Party!")) == 1


def test_scrape_flags_untagged_events_for_review(scraped):
    _, skipped, _ = scraped
    assert sorted(k.title for k in skipped if k.needs_review) == ["Inspire Film Fest", "Private Event"]
    inspire = next(k for k in skipped if k.title == "Inspire Film Fest")
    assert inspire.reason == "event tagged neither Film Event nor Live Event"
    assert inspire.url.startswith("https://www.theparktheatre.ca/event/")
    assert inspire.key == "53985"
    assert all(k.key and k.key.isdigit() for k in skipped)


def test_overrides_resolve_review_listings(replay):
    session = replay(FIXTURES)
    overrides = {"53985": Override(True, note="shorts festival"), "54680": Override(False, note="test: drops a movie")}
    scraper = ParkTheatre(session, now=session.recorded_at, overrides=overrides)
    showtimes = scraper.scrape()
    assert len(by_title(showtimes, "Inspire Film Fest")) == 1
    assert by_title(showtimes, "Tony") == []
    tony = next(k for k in scraper.skipped if k.title == "Tony")
    assert (tony.reason, tony.needs_review) == ("manual override: not a film (test: drops a movie)", False)
    assert [k.title for k in scraper.skipped if k.needs_review] == ["Private Event"]
    assert len(showtimes) == 63


def test_repo_overrides_clear_every_review_in_the_fixtures(replay):
    # The decisions in overrides.json were made by reviewing exactly these listings.
    overrides = load_overrides(Path(__file__).resolve().parent.parent / "overrides.json")["park_theatre"]
    session = replay(FIXTURES)
    scraper = ParkTheatre(session, now=session.recorded_at, overrides=overrides)
    showtimes = scraper.scrape()
    assert [k.title for k in scraper.skipped if k.needs_review] == []
    assert len(by_title(showtimes, "Inspire Film Fest")) == 1
    assert len(showtimes) == 64 and [k.title for k in scraper.skipped] == ["Private Event"]


def test_scrape_request_plan(scraped):
    _, _, session = scraped
    # listings, movie posts, event posts, language terms; no rate card (/ticket-info/ 404s);
    # then the optional TMDB-hint requests, which this recording predates (see
    # test_scrape_film_hints for a recording that has them).
    assert len(session.requested) == 5
    assert session.requested[4].endswith("/barker/v1/movies-feed")
    assert "/wp/v2/barker-movie-language?include=" in session.requested[3]
    assert session.requested[0].startswith(
        "https://www.theparktheatre.ca/wp-json/barker/v1/listings?start_date=2026-10-18&end_date=2027-04-16"
    )
    assert not any("ticket-info" in u for u in session.requested)


def test_scrape_follows_listing_pages(replay, monkeypatch):
    session = replay(FIXTURES)
    [url] = [u for u in session.responses if "/barker/v1/listings" in u]
    items = json.loads((session.fixture_dir / session.responses[url]).read_text())
    # Serve the same listings as two pages of 50 to exercise pagination.
    page1, page2 = json.dumps(items[:50]), json.dumps(items[50:])
    real_get = session.get_text

    def paged(u):
        if "/barker/v1/listings" in u:
            session.requested.append(u)
            return page1 if u.endswith("page=1") else page2
        return real_get(u)

    monkeypatch.setattr(session, "get_text", paged)
    monkeypatch.setattr(ParkTheatre, "per_page", 50)
    scraper = ParkTheatre(session, now=session.recorded_at)
    monkeypatch.setattr(scraper, "_fetch_posts", lambda listings: _posts_from_fixtures(session))
    monkeypatch.setattr(scraper, "_fetch_language_codes", lambda posts: _codes_from_fixtures(session))
    assert len(scraper.scrape()) == 63
    assert [u.rsplit("&", 1)[1] for u in session.requested if "listings" in u] == ["page=1", "page=2"]


def _posts_from_fixtures(session):
    posts = {}
    for url, name in session.responses.items():
        if "/wp/v2/barker_" in url:
            posts.update(parse_posts((session.fixture_dir / name).read_text()))
    return posts


def _codes_from_fixtures(session):
    [name] = [n for u, n in session.responses.items() if "/wp/v2/barker-movie-language" in u]
    return {i: language_code(n) for i, n in parse_language_terms((session.fixture_dir / name).read_text()).items()}


def test_scrape_film_hints(replay):
    # Barker fills release year and directors from TMDB; they become TMDB matching hints.
    session = replay("park_theatre_2026-10-19")
    scraper = ParkTheatre(session, now=session.recorded_at)
    scraper.scrape()
    assert scraper.hints["Ken Russell's 'The Devils'"] == FilmHints(year=1971, directors=("Ken Russell",))
    assert scraper.hints["The Fly (40th Anniversary Screening)"] == FilmHints(year=1986, directors=("David Cronenberg",))
    # Two directors, and no release year in the feed yet.
    assert scraper.hints["Jim Queen"] == FilmHints(directors=("Marco Nguyen", "Nicolas Athané"))
    assert "The Motorcycle Film Fest" not in scraper.hints  # an event post: no movie data


def test_hint_requests_are_optional(replay, caplog):
    session = replay("park_theatre_2026-10-19")
    [url] = [u for u in session.responses if "/barker-movie-director?" in u]
    del session.responses[url]
    scraper = ParkTheatre(session, now=session.recorded_at)
    assert len(scraper.scrape()) == 59
    assert scraper.hints == {}
    assert "film details for TMDB matching unavailable" in caplog.text
