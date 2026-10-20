"""Barker language resolution against real Rio data, recorded 2026-10-18.

* rio_theatre_2025-01/: a full scrape of the two weeks from 2025-01-08 (the
  listings API serves past dates), chosen for its South Asian releases. Replay
  it with lookahead_days = 14 and now = 2025-01-08. The /ticket-info/ page in
  it is the current one, not January 2025's.
* rio_theatre_languages/: every term in the Rio's barker-movie-language
  taxonomy (127 of them), which is where TMDB's native names show up.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from kinolocator.fetch import FetchError
from kinolocator.languages import language_code
from kinolocator.schema import VANCOUVER
from kinolocator.scrapers._barker import parse_language_terms, parse_posts
from kinolocator.scrapers.rio_theatre import RioTheatre

FIXTURES = Path(__file__).parent / "fixtures"
WINDOW = "rio_theatre_2025-01"
WINDOW_START = datetime(2025, 1, 8, 12, 0, tzinfo=VANCOUVER)


@pytest.fixture
def window(replay, monkeypatch):
    monkeypatch.setattr(RioTheatre, "lookahead_days", 14)
    session = replay(WINDOW)
    return session, RioTheatre(session, now=WINDOW_START)


def one(showtimes, title):
    found = [s for s in showtimes if s.title == title]
    assert found, title
    assert len({(tuple(s.screening_language or ()), s.original_language) for s in found}) == 1
    return found[0]


def test_window_resolves_non_latin_languages(window, caplog):
    _, scraper = window
    showtimes = scraper.scrape()
    assert len(showtimes) == 31
    assert "unknown language" not in caplog.text and "no name for language term" not in caplog.text

    # Telugu, with Kannada ("?????"), Hindi and Tamil versions: all four kept.
    game_changer = one(showtimes, "Game Changer")
    assert game_changer.screening_language == ["kn", "hi", "ta", "te"]
    assert game_changer.original_language is None  # several languages: ambiguous

    daaku = one(showtimes, "Daaku Maharaj")  # తెలుగు only
    assert (daaku.screening_language, daaku.original_language) == (["te"], "te")
    light = one(showtimes, "All We Imagine As Light")  # हिन्दी only
    assert (light.screening_language, light.original_language) == (["hi"], "hi")

    # Non-Latin names alongside Latin ones are no longer dropped.
    assert one(showtimes, "Maria").screening_language == ["en", "es", "it", "el"]  # ελληνικά
    assert one(showtimes, "2001: A Space Odyssey").screening_language == ["en", "ru"]  # Pусский
    assert one(showtimes, "Flow (VOKRA Fundraiser)").original_language == "xx"  # No Language

    # Tel Visa (a Punjabi film) has no language terms at the Rio: unknown, not guessed.
    tel_visa = one(showtimes, "Tel Visa")
    assert (tel_visa.screening_language, tel_visa.original_language) == (None, None)


def test_class_list_cannot_carry_these_languages(fixture_dir):
    # Why the term ids are used: WordPress builds class_list from slugs with
    # non-ASCII bytes deleted, falling back to the term id when nothing is left.
    [path] = (fixture_dir / WINDOW).glob("wp-json_wp_v2_barker_movie_*")
    [game_changer] = [p for p in json.loads(path.read_text()) if p["id"] == 222196]
    assert [c for c in game_changer["class_list"] if c.startswith("barker-movie-language-")] == [
        "barker-movie-language-11602",
        "barker-movie-language-11352",
        "barker-movie-language-11784",
        "barker-movie-language-11293",
    ]
    assert parse_posts(path.read_text(), with_languages=True)[222196].language_ids == (11602, 11352, 11784, 11293)


def test_window_request_plan(window):
    session, scraper = window
    scraper.scrape()
    # 5 requests, then the optional TMDB-hint requests: this recording has none,
    # so the first (movies-feed) fails and the rest are skipped.
    assert len(session.requested) == 6
    assert session.requested[5].endswith("/barker/v1/movies-feed")
    assert session.requested[1].endswith("&_fields=id,class_list,content,barker-movie-language")  # movies
    assert session.requested[2].endswith("&_fields=id,class_list,content")  # events: no languages
    assert "/wp/v2/barker-movie-language?include=" in session.requested[3]


def test_language_term_failure_fails_the_scrape(window):
    # Languages are core data: a missing term lookup must not silently drop them.
    session, scraper = window
    [url] = [u for u in session.responses if "/wp/v2/barker-movie-language" in u]
    del session.responses[url]
    with pytest.raises(FetchError):
        scraper.scrape()


def test_missing_term_name_is_logged_and_dropped(window, caplog):
    session, scraper = window
    [url] = [u for u in session.responses if "/wp/v2/barker-movie-language" in u]
    names = parse_language_terms((session.fixture_dir / session.responses[url]).read_text())
    del names[11293]  # తెలుగు
    real_get = session.get_text

    def get_text(u):
        if u == url:
            return json.dumps([{"id": i, "name": n} for i, n in names.items()])
        return real_get(u)

    session.get_text = get_text
    showtimes = scraper.scrape()
    assert "no name for language term ids [11293]" in caplog.text
    assert one(showtimes, "Daaku Maharaj").screening_language is None
    assert one(showtimes, "Game Changer").screening_language == ["kn", "hi", "ta"]


# --- every term the Rio has ever used --------------------------------------


@pytest.fixture(scope="module")
def taxonomy():
    names = {}
    for path in sorted((FIXTURES / "rio_theatre_languages").glob("wp-json*")):
        names.update(parse_language_terms(path.read_text("utf-8")))
    return names


def test_taxonomy_fixture_is_complete(taxonomy):
    assert len(taxonomy) == 127


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("हिन्दी", "hi"), ("தமிழ்", "ta"), ("తెలుగు", "te"), ("?????", "kn"), ("සිංහල", "si"),
        ("اردو", "ur"), ("فارسی", "fa"), ("العربية", "ar"), ("עִבְרִית", "he"), ("日本語", "ja"),
        ("普通话", "zh"), ("广州话 / 廣州話", "cn"), ("한국어/조선말", "ko"), ("ภาษาไทย", "th"),
        ("ελληνικά", "el"), ("Pусский", "ru"), ("Український", "uk"), ("беларуская мова", "be"),
        ("български език", "bg"), ("Azərbaycan", "az"), ("Tiếng Việt", "vi"),
        ("Japanese (ja)", "ja"), ("Spanish; Castilian", "es"), ("Hebrew (modern)", "he"),
        ("Punjabi", "pa"), ("Panjabi", "pa"),
    ],
)
def test_taxonomy_names(taxonomy, name, code):
    assert name in taxonomy.values()
    assert language_code(name) == code


def test_every_taxonomy_term_resolves(taxonomy, caplog):
    unresolved = sorted(n for n in taxonomy.values() if language_code(n) is None)
    assert unresolved == [
        "Central",  # half of "Khmer, Central" / "Tibetan, Central"
        "English Subtitles",  # not a spoken language: venue data error
        "In Spanish with English subtitles",  # ditto
        "Konkani",  # no ISO 639-1 code
        "Modern",  # half of "Greek, Modern"
        "Taiwanese",  # no ISO 639-1 code
        "Tulu",  # no ISO 639-1 code
    ]
    warned = sorted({r.args[0] for r in caplog.records if "unknown language" in r.getMessage()})
    assert warned == ["English Subtitles", "In Spanish with English subtitles"]
