"""Rio Theatre scraper against Barker API responses recorded from riotheatre.ca
on 2026-10-18 (tests/fixtures/rio_theatre/manifest.json). Re-record with
`python -m kinolocator record rio_theatre` only when the site itself changes."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from kinolocator.overrides import Override, load_overrides
from kinolocator.schema import VANCOUVER, PriceConfidence
from kinolocator.scrapers._barker import (
    BarkerParseError,
    Listing,
    Post,
    classify,
    language_codes,
    parse_description_price,
    parse_language_terms,
    parse_listings,
    parse_posts,
    parse_standard_price,
    title_format,
)
from kinolocator.languages import language_code
from kinolocator.scrapers.rio_theatre import RioTheatre

FIXTURES = "rio_theatre"


def fixture_text(fixture_dir, prefix):
    [path] = (fixture_dir / FIXTURES).glob(prefix + "*")
    return path.read_text("utf-8")


@pytest.fixture
def scraped(replay):
    session = replay(FIXTURES)
    scraper = RioTheatre(session, now=session.recorded_at)
    return scraper.scrape(), scraper.skipped, session


def find(showtimes, title, start):
    [match] = [s for s in showtimes if s.title == title and s.start == start]
    return match


# --- parsing ---------------------------------------------------------------


def test_parse_listings(fixture_dir):
    listings = parse_listings(fixture_text(fixture_dir, "wp-json_barker_v1_listings"))
    assert len(listings) == 64
    assert {x.kind for x in listings} == {"movie", "event"}
    musk = listings[0]
    assert musk == Listing(
        post_id=350954,
        kind="movie",
        title="Musk",
        url="https://riotheatre.ca/movie/musk/",
        start=datetime(2026, 10, 18, 12, 30, tzinfo=VANCOUVER),
        ticket_url="https://riotheatretickets.ca/events/45537-musk",
    )


def test_parse_listings_unescapes_titles(fixture_dir):
    titles = {x.title for x in parse_listings(fixture_text(fixture_dir, "wp-json_barker_v1_listings"))}
    assert "Claudio Simonetti's Goblin Performs TENEBRE" in titles or (
        "Claudio Simonetti’s Goblin Performs TENEBRE" in titles
    )
    assert not any("&#" in t for t in titles)


@pytest.mark.parametrize(
    "body", ['{"code": "rest_missing_callback_param"}', "<html>maintenance</html>", '[{"event": {}}]']
)
def test_parse_listings_rejects_unexpected_responses(body):
    with pytest.raises(BarkerParseError):
        parse_listings(body)


def test_parse_movie_posts(fixture_dir):
    posts = parse_posts(fixture_text(fixture_dir, "wp-json_wp_v2_barker_movie"), with_languages=True)
    assert len(posts) == 13
    tenebre = posts[350263]
    assert tenebre.tags == {"coming-soon", "film-event", "live-event"}
    assert tenebre.language_ids == (11305,)
    assert tenebre.price == 62.0  # "Adult Entry $62+ s/c"
    assert posts[218940].language_ids == (11357, 3125)  # Rocky Horror: spoken languages
    assert posts[350954].price is None  # Musk: no price in description


def test_parse_language_terms_and_codes(fixture_dir):
    posts = parse_posts(fixture_text(fixture_dir, "wp-json_wp_v2_barker_movie"), with_languages=True)
    names = parse_language_terms(fixture_text(fixture_dir, "wp-json_wp_v2_barker-movie-language"))
    assert names == {3125: "English", 11250: "Español", 11259: "Français", 11305: "Italiano", 11357: "Deutsch"}
    codes = {i: language_code(n) for i, n in names.items()}
    assert language_codes(posts[350263], codes) == ["it"]
    assert language_codes(posts[218940], codes) == ["de", "en"]
    assert language_codes(None, codes) == []


def test_parse_event_posts(fixture_dir):
    posts = parse_posts(fixture_text(fixture_dir, "wp-json_wp_v2_barker_event"))
    assert posts[350788].tags == {"film-event"}  # Mountains on Stage
    assert posts[350301].tags == {"coming-soon", "live-event"}  # Dandy's drag cabaret
    assert posts[350890].tags == frozenset()  # VIMFF: untagged


@pytest.mark.parametrize(
    ("text", "price"),
    [
        ("<p>General Admission &#8211; $16 + fees</p>", 16.0),
        ("<p>Tickets Regular (includes 1 photo) : $25 + s/c VIP: $80</p>", 25.0),
        ("<p>Artists Circle $85+ s/c Adult Entry $62+ s/c</p>", 62.0),
        ("<p>This is a FREE screening, all welcome</p>", 0.0),
        ("<p>a FREE (!) Halloween screening of BEETLEJUICE</p>", 0.0),
        ("<p>Tickets $35+ fees HERE</p>", 35.0),
        ("<p>Adults $23 Kids Pricing (13 and under) $15</p>", 23.0),
        ("<p>VIP Tickets $80 General $25</p>", 25.0),
        ("<p>VIP Tickets $80</p>", None),
        ("<p>a tax receipt for your donation of $10 or more</p>", None),
        ("<p>Tickets HERE</p>", None),
    ],
)
def test_description_price(text, price):
    assert parse_description_price(text) == price


def test_standard_price(fixture_dir):
    assert parse_standard_price(fixture_text(fixture_dir, "ticket-info")) == 16.0


@pytest.mark.parametrize(
    ("title", "fmt"),
    [
        ("Dune: Part Three (Presented in 70mm)", "70mm"),  # the Park's style
        ("Vertigo (New 35mm Print)", "35mm"),
        ("Oppenheimer [IMAX 70MM]", "IMAX 70mm"),
        ("Home Movies (Super-8 Program)", "Super 8"),
        ("Interstellar (IMAX)", "IMAX"),
        ("Scream (30th Anniversary Screening)", None),
        ("Ken Russell's 'The Devils' (4K Restoration)", None),
        ("35mm: A Musical Exploration", None),  # a format word outside an aside is the title
        ("Tony", None),
    ],
)
def test_title_format(title, fmt):
    assert title_format(title) == fmt


def test_language_codes_keep_order_and_drop_unknown_and_duplicates():
    post = Post(1, language_ids=(5, 1, 2, 3, 4))
    codes = {1: "en", 2: None, 3: "ru", 4: "en"}  # 2: unknown name; 5: term with no name
    assert language_codes(post, codes) == ["en", "ru"]


def test_movie_posts_without_language_ids_are_rejected():
    body = json.dumps([{"id": 1, "class_list": ["barker-movie-language-english"], "content": {"rendered": ""}}])
    assert parse_posts(body) == {1: Post(1)}  # event posts have no language field
    with pytest.raises(BarkerParseError, match="barker-movie-language"):
        parse_posts(body, with_languages=True)


@pytest.mark.parametrize("body", ['{"code": "rest_no_route"}', '[{"name": "English"}]', "<html></html>"])
def test_parse_language_terms_rejects_unexpected_responses(body):
    with pytest.raises(BarkerParseError):
        parse_language_terms(body)


# --- film / non-film classification ---------------------------------------


def listing(kind):
    return Listing(1, kind, "X", f"https://riotheatre.ca/{kind}/x/", datetime(2026, 11, 1, tzinfo=VANCOUVER), None)


@pytest.mark.parametrize(
    ("kind", "tags", "expected"),
    [
        ("movie", set(), True),
        ("movie", {"live-event"}, True),  # e.g. live score + film
        ("event", {"film-event"}, True),
        ("event", {"film-event", "live-event"}, True),
        ("event", {"live-event"}, False),
        ("event", {"coming-soon", "live-event"}, False),
        ("event", set(), None),  # can't tell -> review
        ("event", {"coming-soon"}, None),
        ("festival", {"film-event"}, None),  # unknown listing type -> review
    ],
)
def test_classify(kind, tags, expected):
    assert classify(listing(kind), Post(1, frozenset(tags)))[0] is expected


@pytest.mark.parametrize(
    ("kind", "tags", "film"),
    [
        ("event", set(), True),  # the usual case: an untagged event, reviewed
        ("event", set(), False),
        ("event", {"live-event"}, True),  # an override also corrects a wrong tag
        ("movie", set(), False),
        ("festival", set(), True),
    ],
)
def test_override_wins(kind, tags, film):
    is_film, why = classify(listing(kind), Post(1, frozenset(tags)), Override(film, note="checked"))
    assert is_film is film
    assert why == f"manual override: {'film' if film else 'not a film'} (checked)"


def test_classify_event_without_post_data_needs_review():
    assert classify(listing("event"), None)[0] is None
    assert classify(listing("movie"), None)[0] is True


# --- full scrape -----------------------------------------------------------


def test_scrape_counts(scraped):
    showtimes, skipped, _ = scraped
    assert len(showtimes) == 25
    assert len(skipped) == 39


def test_every_listing_is_a_showtime_or_a_recorded_skip(scraped, fixture_dir):
    showtimes, skipped, _ = scraped
    listings = parse_listings(fixture_text(fixture_dir, "wp-json_barker_v1_listings"))
    produced = sorted([(s.title, s.start) for s in showtimes] + [(k.title, k.start) for k in skipped])
    assert produced == sorted((x.title, x.start) for x in listings)
    assert {s.venue_id for s in showtimes} == {"rio_theatre"}
    assert all(s.start.tzinfo is not None for s in showtimes)


def test_scrape_film_fields(scraped):
    showtimes, _, _ = scraped
    musk = find(showtimes, "Musk", datetime(2026, 10, 18, 12, 30, tzinfo=VANCOUVER))
    assert musk.start.isoformat() == "2026-10-18T12:30:00-07:00"
    assert musk.ticket_url == "https://riotheatretickets.ca/events/45537-musk"
    assert musk.original_language == "en" and musk.screening_language == ["en"]
    assert musk.subtitles is None and musk.format is None
    assert (musk.regular_price, musk.price_confidence) == (16.0, PriceConfidence.ESTIMATED)


def test_scrape_no_formats_in_rio_titles(scraped):
    # Title-format extraction is shared with the Park, but none of the Rio's
    # recorded titles name a format ("(50th Anniversary Screening)" etc. don't).
    showtimes, _, _ = scraped
    assert {s.format for s in showtimes} == {None}


def test_scrape_scraped_price_and_ambiguous_language(scraped):
    showtimes, _, _ = scraped
    tenebre = next(s for s in showtimes if "TENEBRE" in s.title)
    assert (tenebre.regular_price, tenebre.price_confidence) == (62.0, PriceConfidence.SCRAPED)
    assert tenebre.original_language == "it"
    rocky = [s for s in showtimes if s.title.startswith("The Rocky Horror Picture Show")]
    assert len(rocky) == 9
    assert all(s.original_language is None and s.screening_language == ["de", "en"] for s in rocky)


def test_scrape_includes_film_events(scraped):
    showtimes, _, _ = scraped
    titles = {s.title for s in showtimes}
    assert "Mountains on Stage: Winter 2026" in titles  # event tagged Film Event
    assert any(t.startswith("The Saturday Morning All-You-Can-Eat Cereal Party") for t in titles)


def test_scrape_skips_live_events(scraped):
    showtimes, skipped, _ = scraped
    titles = {s.title for s in showtimes}
    by_title = {k.title: k for k in skipped}
    for live in ("Dandy's CABIN IN THE WOODS", "Crazy Diamonds 2026", "JFL Vancouver: Ron Funches"):
        title = next(t for t in by_title if t.replace("’", "'") == live)
        assert title not in titles
        assert by_title[title].reason == "event tagged Live Event (not a film)"
        assert not by_title[title].needs_review


def test_scrape_flags_untagged_events_for_review(scraped):
    _, skipped, _ = scraped
    review = sorted({k.title for k in skipped if k.needs_review})
    assert review == [
        "Hassan Phills: JFLVancouver 2027",
        "Matt Puzhitsky",
        "Shawn Chidiac",
        "The Comic Strippers: A Male Stripper Parody & Improv Comedy Show",
        "VIMFF 2026: Bike Night: Riding Free",
        "VIMFF 2026: Extreme Antarctica",
        "VIMFF 2026: Reel Rock 20 Vancouver",
        "VIMFF 2026: Ski & Snow Show",
    ]
    reel_rock = next(k for k in skipped if k.title == "VIMFF 2026: Reel Rock 20 Vancouver")
    assert reel_rock.start == datetime(2026, 11, 15, 19, 0, tzinfo=VANCOUVER)
    assert reel_rock.url == "https://riotheatre.ca/event/reel-rock-20-vancouver-vimff-2026/"


def test_review_skips_carry_the_override_key(scraped):
    _, skipped, _ = scraped
    reel_rock = next(k for k in skipped if k.title == "VIMFF 2026: Reel Rock 20 Vancouver")
    assert reel_rock.key == "350890"
    assert all(k.key and k.key.isdigit() for k in skipped)


def test_overrides_resolve_review_listings(replay):
    session = replay(FIXTURES)
    overrides = {
        "350890": Override(True, note="VIMFF programme"),  # untagged -> film
        "350909": Override(False, note="stand-up"),  # untagged -> not a film
        "350748": Override(True, note="test: flips a Live Event tag"),  # Crazy Diamonds
    }
    scraper = RioTheatre(session, now=session.recorded_at, overrides=overrides)
    showtimes = scraper.scrape()

    reel_rock = find(showtimes, "VIMFF 2026: Reel Rock 20 Vancouver", datetime(2026, 11, 15, 19, 0, tzinfo=VANCOUVER))
    assert reel_rock.ticket_url and reel_rock.regular_price is not None
    assert any(s.title == "Crazy Diamonds 2026" for s in showtimes)
    assert len(showtimes) == 25 + 1 + 1

    chidiac = next(k for k in scraper.skipped if k.title == "Shawn Chidiac")
    assert (chidiac.reason, chidiac.needs_review) == ("manual override: not a film (stand-up)", False)
    assert sorted({k.title for k in scraper.skipped if k.needs_review}) == [
        "Hassan Phills: JFLVancouver 2027",
        "Matt Puzhitsky",
        "The Comic Strippers: A Male Stripper Parody & Improv Comedy Show",
        "VIMFF 2026: Bike Night: Riding Free",
        "VIMFF 2026: Extreme Antarctica",
        "VIMFF 2026: Ski & Snow Show",
    ]


def test_repo_overrides_clear_every_review_in_the_fixtures(replay):
    # The decisions in overrides.json were made by reviewing exactly these listings.
    overrides = load_overrides(Path(__file__).resolve().parent.parent / "overrides.json")["rio_theatre"]
    session = replay(FIXTURES)
    scraper = RioTheatre(session, now=session.recorded_at, overrides=overrides)
    showtimes = scraper.scrape()
    assert [k.title for k in scraper.skipped if k.needs_review] == []
    vimff = [s for s in showtimes if s.title.startswith("VIMFF 2026")]
    assert len(vimff) == 4
    assert len(showtimes) == 29 and len(scraper.skipped) == 35


def test_scrape_request_plan(scraped):
    _, _, session = scraped
    # listings, movie posts, event posts, language terms, rate card, then the
    # optional TMDB-hint requests, which this recording predates.
    assert len(session.requested) == 6
    assert session.requested[5].endswith("/barker/v1/movies-feed")
    assert "/wp/v2/barker-movie-language?include=3125,11250,11259,11305,11357&" in session.requested[3]
    assert session.requested[0].startswith(
        "https://riotheatre.ca/wp-json/barker/v1/listings?start_date=2026-10-18&end_date=2027-04-16"
    )


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
    monkeypatch.setattr(RioTheatre, "per_page", 50)
    scraper = RioTheatre(session, now=session.recorded_at)
    monkeypatch.setattr(scraper, "_fetch_posts", lambda listings: _posts_from_fixtures(session))
    monkeypatch.setattr(scraper, "_fetch_language_codes", lambda posts: _codes_from_fixtures(session))
    assert len(scraper.scrape()) == 25
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
