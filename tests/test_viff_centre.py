"""VIFF Centre scraper against responses recorded from viff.org on 2026-10-19
(tests/fixtures/viff_centre/manifest.json). Re-record with
`python -m kinolocator record viff_centre` only when the site itself changes."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from kinolocator.fetch import FetchError, ReplaySession
from kinolocator.overrides import Override, load_overrides
from kinolocator.schema import VANCOUVER, PriceConfidence
from kinolocator.scrapers.viff_centre import (
    CALENDAR_PAGE_URL,
    INSTANCES_URL,
    RATES_URL,
    Instance,
    ViffCentre,
    ViffParseError,
    classify,
    parse_calendar_page,
    parse_event_page,
    parse_instances,
    parse_rate_card,
)

FIXTURES = "viff_centre"
REPO = Path(__file__).resolve().parent.parent
LIVE = {"51352": "Halloween Live! The Crypt Keepers + The Day of the Beast", "56287": "Shrek Cabaret"}


def fixture(session_or_dir, url):
    d = session_or_dir if isinstance(session_or_dir, Path) else session_or_dir.fixture_dir
    manifest = json.loads((d / "manifest.json").read_text())
    return (d / manifest["responses"][url]).read_text("utf-8")


@pytest.fixture
def fdir(fixture_dir):
    return fixture_dir / FIXTURES


@pytest.fixture(scope="module")
def scraped():
    # One replay for the read-only tests: it parses 42 event pages (~2 s).
    # Tests that alter responses build their own session.
    session = ReplaySession(Path(__file__).parent / "fixtures" / FIXTURES)
    scraper = ViffCentre(session, now=session.recorded_at)
    return scraper.scrape(), scraper.skipped, session


def find(showtimes, title, start):
    [match] = [s for s in showtimes if s.title == title and s.start == start]
    return match


def at(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=VANCOUVER)


def serve(session, monkeypatch, replace):
    """Serve `replace[url]` (text, or an exception to raise) instead of the fixture."""
    real = session.get_text

    def get_text(url, *, headers=None):
        if url in replace:
            session.requested.append(url)
            if isinstance(replace[url], Exception):
                raise replace[url]
            return replace[url]
        return real(url, headers=headers)

    monkeypatch.setattr(session, "get_text", get_text)


# --- parsing -----------------------------------------------------------------


def test_parse_calendar_page(fdir):
    page = parse_calendar_page(fixture(fdir, CALENDAR_PAGE_URL))
    assert page.token and len(page.token) == 10
    assert page.screens == {
        "viff-centre-lochmaddy-studio-theatre": "VIFF Centre - Lochmaddy Studio Theatre",
        "viff-centre-viff-cinema": "VIFF Centre - VIFF Cinema",
    }


@pytest.mark.parametrize(
    "page",
    [
        "<html><script>var calendar_venues = {\"venues\": []};</script></html>",  # no token
        "<script>var wpApiSettings = {\"root\": \"x\", \"nonce\": \"\"};</script>",  # empty token
        "<script>var wpApiSettings = {\"nonce\": \"abc\"};</script>",  # token, no venues
    ],
)
def test_parse_calendar_page_fails_loudly(page):
    with pytest.raises(ViffParseError):
        parse_calendar_page(page)


def test_parse_instances(fdir):
    instances = parse_instances(fixture(fdir, INSTANCES_URL))
    assert len(instances) == 137
    assert instances[0] == Instance(
        instance_id="55819",
        event_id="55789",
        title="You Had to Be There",
        start=at(2026, 10, 19, 18, 0),
        resource_id="viff-centre-lochmaddy-studio-theatre",
        event_type="Film",
        event_url="https://viff.org/whats-on/you-had-to-be-there/",
        book_url=None,  # sold out: no /book/ link
    )
    assert all(i.start.utcoffset() is not None for i in instances)
    assert {i.event_type for i in instances} == {"Film", "VIFF Live"}
    assert sum(i.book_url is None for i in instances) == 20
    assert all(i.event_url for i in instances)


@pytest.mark.parametrize("body", ['{"code": "forbidden"}', "<html>maintenance</html>", '[{"title": "<p>x</p>"}]'])
def test_parse_instances_rejects_unexpected_responses(body):
    with pytest.raises(ViffParseError):
        parse_instances(body)


def test_parse_event_page_language_format_and_jsonld(fdir):
    bola = parse_event_page(fixture(fdir, "https://viff.org/whats-on/la-bola-negra/"))
    assert (bola.languages, bola.subtitles, bola.format) == (["es"], ["en"], "35mm")
    assert bola.locations["2026-10-30T14:30:00-07:00"] == ("VIFF Centre - VIFF Cinema", "1181 Seymour Street")
    assert bola.tag_prices == {"First Look Fridays": 12.0}

    wrath = parse_event_page(fixture(fdir, "https://viff.org/whats-on/day-of-wrath/"))
    assert (wrath.languages, wrath.subtitles, wrath.format) == (["da"], ["en"], "35mm")  # "35mm | Pantheon"

    fly = parse_event_page(fixture(fdir, "https://viff.org/whats-on/the-fly/"))
    assert (fly.languages, fly.subtitles, fly.format) == (["en"], [], None)  # bare "English"; series label isn't a format

    naza = parse_event_page(fixture(fdir, "https://viff.org/whats-on/naza/"))
    assert (naza.languages, naza.subtitles) == (["he", "en"], ["en"])


def test_parse_event_page_tolerates_vIFFs_language_typo(fdir, caplog):
    # "In English, Bemba, Chewa, and and Tonga with English subtitles": Bemba and
    # (Zambian) Tonga have no ISO 639-1 code and are dropped without a warning.
    witch = parse_event_page(fixture(fdir, "https://viff.org/whats-on/i-am-not-a-witch/"))
    assert (witch.languages, witch.subtitles) == (["en", "ny"], ["en"])
    assert "unknown language" not in caplog.text and "unrecognised language line" not in caplog.text


def test_parse_event_page_rejects_other_pages():
    with pytest.raises(ViffParseError):
        parse_event_page("<html><body><h1>Page not found</h1></body></html>")


def test_parse_rate_card(fdir):
    assert parse_rate_card(fixture(fdir, RATES_URL)) == {"VIFF Cinema": 17.0, "Lochmaddy Studio Theatre": 15.0}
    assert parse_rate_card("<table><tr><td></td><td>X</td></tr><tr><td>Senior</td><td>$1</td></tr></table>") == {}


def test_memberships_json_matches_viffs_published_tiers(fdir):
    # web/memberships.json must say what VIFF's own rate card says, screen by screen.
    rows = {}
    table = BeautifulSoup(fixture(fdir, RATES_URL), "html.parser").find("table")
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        rows[cells[0]] = cells[1:]
    adult = [float(p.strip("$")) for p in rows["Adult"]]
    expected = {
        name: {a - float(p.strip("$")) for a, p in zip(adult, rows[tier])}
        for name, tier in [("VIFF+", "VIFF+"), ("VIFF+ Premium / U25", "VIFF+ Premium/U25")]
    }
    assert expected == {"VIFF+": {3.0}, "VIFF+ Premium / U25": {5.0}}  # same saving on both screens

    memberships = json.loads((REPO / "web" / "memberships.json").read_text())["memberships"]
    viff = {
        m["name"]: d
        for m in memberships
        for d in m["discounts"]
        if "viff_centre" in d["venues"]
    }
    assert set(viff) == set(expected)
    for name, savings in expected.items():
        assert {viff[name]["amount_off"]} == savings
        assert viff[name]["venues"] == ["viff_centre"] and viff[name]["applies_to"] == "rate_card"


# --- film / non-film classification ---------------------------------------


def instance(event_type):
    return Instance("1", "2", "X", at(2026, 11, 1, 19, 0), "viff-centre-viff-cinema", event_type, None, None)


@pytest.mark.parametrize(
    ("event_type", "expected"),
    [("Film", True), ("VIFF Live", None), ("Talk", None), ("Workshop", None), ("", None)],
)
def test_classify(event_type, expected):
    assert classify(instance(event_type))[0] is expected


@pytest.mark.parametrize(("event_type", "film"), [("VIFF Live", True), ("Talk", False), ("Film", False)])
def test_override_wins(event_type, film):
    is_film, why = classify(instance(event_type), Override(film, note="checked"))
    assert is_film is film
    assert why == f"manual override: {'film' if film else 'not a film'} (checked)"


# --- full scrape -----------------------------------------------------------


def test_scrape_counts(scraped):
    showtimes, skipped, _ = scraped
    assert len(showtimes) == 135
    assert len(skipped) == 2


def test_every_listing_is_a_showtime_or_a_recorded_skip(scraped):
    showtimes, skipped, session = scraped
    instances = parse_instances(fixture(session, INSTANCES_URL))
    produced = sorted([(s.title, s.start) for s in showtimes] + [(k.title, k.start) for k in skipped])
    assert produced == sorted((i.title, i.start) for i in instances)
    assert {s.venue_id for s in showtimes} == {"viff_centre"}
    assert all(s.start.tzinfo is not None for s in showtimes)


def test_scrape_film_fields(scraped):
    showtimes, _, _ = scraped
    there = find(showtimes, "You Had to Be There", at(2026, 10, 19, 18, 0))
    assert there.start.isoformat() == "2026-10-19T18:00:00-07:00"
    assert there.screen == "Lochmaddy Studio Theatre"
    assert there.original_language == "en" and there.screening_language == ["en"] and there.subtitles == []
    assert there.format is None
    assert (there.regular_price, there.price_confidence) == (15.0, PriceConfidence.ESTIMATED)
    # Sold out, so no booking link: the film's page instead.
    assert there.ticket_url == "https://viff.org/whats-on/you-had-to-be-there/"

    bola = find(showtimes, "La Bola Negra", at(2026, 10, 30, 17, 50))
    assert (bola.screen, bola.format, bola.screening_language, bola.subtitles) == ("VIFF Cinema", "35mm", ["es"], ["en"])
    assert bola.ticket_url == "https://viff.org/whats-on/la-bola-negra/book/i5TsvY0qPS"


def test_scrape_screens_are_one_venue(scraped):
    showtimes, _, _ = scraped
    assert {s.screen for s in showtimes} == {"VIFF Cinema", "Lochmaddy Studio Theatre"}
    assert {(s.venue_id, s.venue_name) for s in showtimes} == {("viff_centre", "VIFF Centre")}


def test_scrape_rate_card_price_follows_the_screen(scraped):
    showtimes, _, _ = scraped
    estimated = {(s.screen, s.regular_price) for s in showtimes if s.price_confidence is PriceConfidence.ESTIMATED}
    assert estimated == {("VIFF Cinema", 17.0), ("Lochmaddy Studio Theatre", 15.0)}


def test_scrape_stated_price_for_tagged_screenings(scraped):
    showtimes, _, _ = scraped
    scraped_prices = sorted((s.title, s.start, s.regular_price) for s in showtimes if s.price_confidence is PriceConfidence.SCRAPED)
    assert scraped_prices == [  # "First Look Fridays: $12 Tickets", only on the tagged screenings
        ("La Bola Negra", at(2026, 10, 30, 14, 30), 12.0),
        ("Once Upon a Time in Harlem", at(2026, 10, 23, 13, 30), 12.0),
    ]
    later = find(showtimes, "La Bola Negra", at(2026, 10, 30, 20, 50))
    assert (later.regular_price, later.price_confidence) == (17.0, PriceConfidence.ESTIMATED)


def test_scrape_languages(scraped):
    showtimes, _, _ = scraped
    naza = find(showtimes, "NAZA", at(2026, 10, 20, 20, 0))
    assert (naza.screening_language, naza.subtitles, naza.original_language) == (["he", "en"], ["en"], None)
    assert all(s.screening_language for s in showtimes)


def test_scrape_flags_non_film_types_for_review(scraped):
    _, skipped, _ = scraped
    assert {k.key: k.title for k in skipped} == LIVE
    assert all(k.needs_review and k.reason == "calendar type 'VIFF Live', not Film" for k in skipped)
    shrek = next(k for k in skipped if k.key == "56287")
    assert shrek.url == "https://viff.org/whats-on/shrek-cabaret-live/"


def test_overrides_resolve_review_listings(replay):
    session = replay(FIXTURES)
    overrides = {"51352": Override(True, note="film after live set"), "56287": Override(False, note="test")}
    scraper = ViffCentre(session, now=session.recorded_at, overrides=overrides)
    showtimes = scraper.scrape()
    live = find(showtimes, LIVE["51352"], at(2026, 10, 24, 20, 0))
    assert live.screen == "VIFF Cinema" and live.ticket_url.endswith("/book/DhGOOnwG0E")
    [shrek] = scraper.skipped
    assert (shrek.reason, shrek.needs_review) == ("manual override: not a film (test)", False)


def test_repo_overrides_clear_every_review_in_the_fixtures(replay, caplog):
    # The decisions in overrides.json were made by reviewing exactly these listings.
    overrides = load_overrides(REPO / "overrides.json")["viff_centre"]
    session = replay(FIXTURES)
    scraper = ViffCentre(session, now=session.recorded_at, overrides=overrides)
    showtimes = scraper.scrape()
    assert scraper.skipped == []
    assert len(showtimes) == 137
    # Every language line in the recording parses with the current table.
    assert "unknown language" not in caplog.text and "unrecognised language line" not in caplog.text


def test_scrape_request_plan(scraped):
    _, _, session = scraped
    assert session.requested[:3] == [CALENDAR_PAGE_URL, INSTANCES_URL, RATES_URL]
    events = session.requested[3:]
    assert len(events) == 42 and len(set(events)) == 42  # each event page once
    token = parse_calendar_page(fixture(session, CALENDAR_PAGE_URL)).token
    assert session.request_headers[1] == {"X-WP-Nonce": token}  # only the calendar call carries it
    assert not any(h for i, h in enumerate(session.request_headers) if i != 1)


# --- failure modes ---------------------------------------------------------


def test_missing_token_fails_the_venue(replay, monkeypatch):
    session = replay(FIXTURES)
    serve(session, monkeypatch, {CALENDAR_PAGE_URL: "<html><body>Calendar</body></html>"})
    with pytest.raises(ViffParseError, match="nonce"):
        ViffCentre(session, now=session.recorded_at).scrape()
    assert session.requested == [CALENDAR_PAGE_URL]  # no attempt without the token


def test_rejected_token_fails_the_venue(replay, monkeypatch):
    session = replay(FIXTURES)
    serve(session, monkeypatch, {INSTANCES_URL: FetchError(f"GET {INSTANCES_URL} failed: HTTP 403")})
    with pytest.raises(FetchError, match="403"):
        ViffCentre(session, now=session.recorded_at).scrape()


def test_missing_rate_card_leaves_estimated_prices_empty(replay, monkeypatch, caplog):
    session = replay(FIXTURES)
    serve(session, monkeypatch, {RATES_URL: FetchError("HTTP 404")})
    showtimes = ViffCentre(session, now=session.recorded_at).scrape()
    assert "rate card" in caplog.text
    assert {s.price_confidence for s in showtimes} == {None, PriceConfidence.SCRAPED}
    assert len(showtimes) == 135


def test_screenings_elsewhere_are_skipped_never_attached(replay, monkeypatch):
    # No festival or partner screenings are listed right now, so this edits the
    # recorded calendar: one screening moved to another venue's resource.
    session = replay(FIXTURES)
    page = fixture(session, CALENDAR_PAGE_URL).replace(
        '{"id":"viff-centre-viff-cinema","title":"VIFF Centre - VIFF Cinema"}',
        '{"id":"viff-centre-viff-cinema","title":"VIFF Centre - VIFF Cinema"},'
        '{"id":"rio-theatre","title":"The Rio Theatre"}',
    )
    items = json.loads(fixture(session, INSTANCES_URL))
    moved = next(i for i in items if 'data-instanceid="56375"' in i["title"])  # The Fly, Sun 25 Oct 5:10 pm
    moved["resourceId"] = "rio-theatre"
    unknown = next(i for i in items if 'data-instanceid="56376"' in i["title"])  # The Fly, Tue 27 Oct 7:00 pm
    unknown["resourceId"] = "somewhere-new"
    serve(session, monkeypatch, {CALENDAR_PAGE_URL: page, INSTANCES_URL: json.dumps(items)})
    scraper = ViffCentre(session, now=session.recorded_at)
    showtimes = scraper.scrape()

    assert not [s for s in showtimes if s.title == "The Fly"]
    reasons = {k.start: (k.reason, k.needs_review, k.key) for k in scraper.skipped if k.title == "The Fly"}
    assert reasons == {
        at(2026, 10, 25, 17, 10): ("at The Rio Theatre, not VIFF Centre", False, "56278"),
        at(2026, 10, 27, 19, 0): ("calendar venue 'somewhere-new' isn't in the calendar's venue list", False, "56278"),
    }
    assert len(showtimes) == 133


def test_event_page_address_elsewhere_is_skipped(replay, monkeypatch):
    # A screening listed under a VIFF Centre screen whose event page places it at
    # another address is not attached to VIFF Centre either.
    session = replay(FIXTURES)
    url = "https://viff.org/whats-on/the-fly/"
    page = fixture(session, url).replace(
        '"startDate":"2026-10-27T19:00:00-07:00","endDate":"2026-10-27T20:46:00-07:00","location":{"@type":"Place","name":"VIFF Centre - Lochmaddy Studio Theatre","address":{"@type":"PostalAddress","streetAddress":"1181 Seymour Street"',
        '"startDate":"2026-10-27T19:00:00-07:00","endDate":"2026-10-27T20:46:00-07:00","location":{"@type":"Place","name":"Partner Hall","address":{"@type":"PostalAddress","streetAddress":"1 Elsewhere Ave"',
    )
    assert page != fixture(session, url)
    serve(session, monkeypatch, {url: page})
    scraper = ViffCentre(session, now=session.recorded_at)
    showtimes = scraper.scrape()
    assert [s.start for s in showtimes if s.title == "The Fly"] == [at(2026, 10, 25, 17, 10)]
    [k] = [k for k in scraper.skipped if k.title == "The Fly"]
    assert k.reason == "event page places it at Partner Hall, 1 Elsewhere Ave, not VIFF Centre"
