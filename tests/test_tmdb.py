"""TMDB matching.

TMDB couldn't be reached when this was written, so the responses here are
synthetic: small payloads in the shape of TMDB v3's documented /search/movie
and /movie/{id}?append_to_response=credits responses, served through the
real client so the request and parsing code runs too. Ids and vote counts
are invented; titles, years and directors are the real films'.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from kinolocator import tmdb
from kinolocator.fetch import FetchError
from kinolocator.schema import FilmHints
from kinolocator.tmdb import Matcher, TmdbClient, directors_match
from kinolocator.titles import fold
from kinolocator.tmdb_overrides import TmdbOverride, TmdbOverrides

NOW = datetime(2026, 10, 20, 13, 17, tzinfo=timezone.utc)

# id -> (title, original_title, original_language, release_date, votes, directors, runtime)
FILMS = {
    1: ("The Thing", "The Thing", "en", "1982-06-25", 7000, ["John Carpenter"], 109),
    2: ("The Thing", "The Thing", "en", "2011-10-12", 3000, ["Matthijs van Heijningen Jr."], 103),
    3: ("Atonement", "Atonement", "en", "2007-09-03", 6000, ["Joe Wright"], 123),
    4: ("The Devils", "The Devils", "en", "1971-07-16", 600, ["Ken Russell"], 111),
    5: ("The Devils", "The Devils", "en", "2002-01-01", 3, ["Someone Else"], 90),
    6: ("Ferris Bueller's Day Off", "Ferris Bueller's Day Off", "en", "1986-06-11", 5000, ["John Hughes"], 103),
    7: ("Kwaidan", "怪談", "ja", "1964-12-29", 500, ["Masaki Kobayashi"], 183),
    8: ("Jailer 2", "ஜெயிலர் 2", "ta", "2026-06-12", 80, ["Nelson Dilipkumar"], 152),
    9: ("Obscure", "Obscure", "en", "2019-01-01", 4, ["Nobody Known"], 80),
    10: ("Pulse", "回路", "ja", "2001-02-03", 900, ["Kiyoshi Kurosawa"], 118),
    11: ("Pulse", "Pulse", "en", "2006-08-11", 800, ["Jim Sonzero"], 90),
    12: ("Three Colors: Blue", "Trois couleurs : Bleu", "fr", "1993-09-08", 2500, ["Krzysztof Kieślowski"], 98),
    13: ("Rose", "Rose", "fr", "2021-01-01", 40, ["Aurélie Saada"], 102),
    14: ("Rose", "Rose", "de", "2005-01-01", 20, ["Alain Gsponer"], 100),
}


def search_body(query, year=None):
    # TMDB's search is fuzzy (spellings, alternative titles); folded containment stands in for it.
    q = fold(query)
    results = [
        {"id": i, "title": t, "original_title": o, "original_language": lang, "release_date": d, "vote_count": v,
         "overview": "...", "poster_path": f"/p{i}.jpg"}
        for i, (t, o, lang, d, v, *_rest) in FILMS.items()
        if (q in fold(t) or q in fold(o)) and (year is None or d.startswith(str(year)))
    ]
    # Also an unrelated result, as real searches have; it must never be a candidate.
    results.append({"id": 999, "title": f"Making {query}", "original_title": f"Making {query}", "release_date": "2010-01-01"})
    return json.dumps({"page": 1, "results": results, "total_results": len(results), "total_pages": 1})


def movie_body(i):
    t, o, lang, d, v, directors, runtime = FILMS[i]
    return json.dumps({
        "id": i, "title": t, "original_title": o, "original_language": lang, "release_date": d,
        "runtime": runtime, "overview": f"Synopsis of {t}.", "poster_path": f"/p{i}.jpg", "vote_count": v,
        "credits": {"cast": [], "crew": [{"job": "Director", "name": n} for n in directors]
                    + [{"job": "Screenplay", "name": "A Writer"}]},
    })


class FakeTmdb:
    """Serves the synthetic responses and records every request."""

    def __init__(self, status=200):
        self.status = status
        self.requests: list[tuple[str, dict]] = []

    def get_text(self, url, *, headers=None):
        self.requests.append((url, dict(headers or {})))
        if self.status != 200:
            raise FetchError(f"GET {url} failed: HTTP {self.status}")
        parts = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if parts.path == "/3/search/movie":
            return search_body(q["query"], int(q["year"]) if "year" in q else None)
        if parts.path.startswith("/3/movie/"):
            movie_id = int(parts.path.rsplit("/", 1)[1])
            if movie_id not in FILMS:
                raise FetchError(f"GET {url} failed: HTTP 404")
            return movie_body(movie_id)
        raise AssertionError(url)


def matcher(fake=None, cache=None, overrides=None, now=NOW, token="test-token"):
    fake = fake or FakeTmdb()
    client = TmdbClient(fake, cache, now, token)
    return Matcher(client, overrides), fake


@pytest.mark.parametrize(
    ("title", "hints", "tmdb_id", "reason"),
    [
        ("The Thing (1982)", FilmHints(), 1, "title + year 1982"),
        ("The Thing", FilmHints(directors=("John Carpenter",)), 1, "title + director John Carpenter"),
        ("Kwaidan", FilmHints(year=1964, directors=("Kobayashi Masaki",), runtime=183, original_title="怪談"),
         7, "title + year 1964, director Kobayashi Masaki, runtime 183 min"),
        ("Ken Russell's 'The Devils'", FilmHints(), 4, "title + director Ken Russell"),
        ("Ferris Bueller's Day Off", FilmHints(), 6, "the only TMDB film titled \"Ferris Bueller's Day Off\" (5000 votes)"),
        ("Three Colours: Blue", FilmHints(year=1993), 12, "title + year 1993"),
        ("Jailer 2 (Telugu)", FilmHints(runtime=150), 8, "title + runtime 150 min"),
        ("Pulse", FilmHints(year=2001, directors=("Kurosawa Kiyoshi",)), 10, "title + year 2001, director Kurosawa Kiyoshi"),
    ],
)
def test_confident_matches(title, hints, tmdb_id, reason):
    m, _ = matcher()
    match = m.match("venue", title, hints)
    assert (match.tmdb_id, match.reason) == (tmdb_id, reason)


@pytest.mark.parametrize(
    ("title", "hints", "languages", "reason"),
    [
        ("The Thing", FilmHints(), (), "ambiguous: 2 TMDB films titled 'The Thing' (1982, 2011)"),
        # VIFF's 2026 Atonement is not Joe Wright's.
        ("Atonement", FilmHints(year=2026, directors=("Reed Van Dyk",)), (), "TMDB has 'Atonement' from 2007, not 2026"),
        ("Atonement", FilmHints(directors=("Reed Van Dyk",)), (),
         "TMDB's 'Atonement' (2007: Joe Wright) isn't by Reed Van Dyk"),
        ("Obscure", FilmHints(), (),
         "one TMDB film titled 'Obscure' (2019, 4 votes), but no year or director from the venue to confirm it"),
        ("Nothing Like It", FilmHints(), (), "no TMDB film titled 'Nothing Like It'"),
        ("Five Films by Morgan Fisher", FilmHints(runtime=82), (), "several films: several films in one programme"),
        ("Jailer 2 (Tamil)", FilmHints(runtime=95), (), "TMDB's 'Jailer 2' runs 152 min, not ~95"),
    ],
)
def test_unmatched_with_reasons(title, hints, languages, reason):
    m, _ = matcher()
    match = m.match("venue", title, hints, languages)
    assert (match.tmdb_id, match.reason) == (None, reason)


def test_possessive_credit_must_direct_the_film():
    # Without the credit, "The Devils" is ambiguous; the credit picks 1971, and
    # a credit nobody on TMDB matches leaves it unmatched rather than guessing.
    m, _ = matcher()
    assert m.match("v", "Ken Russell's 'The Devils'").tmdb_id == 4
    miss = m.match("v", "Joe Bloggs's 'The Devils'")
    assert miss.tmdb_id is None and "isn't by Joe Bloggs" in miss.reason


def test_language_breaks_a_tie():
    # The language alone never confirms a match: two "Rose"s, one French, no other hint.
    m, _ = matcher()
    match = m.match("v", "Rose", FilmHints(), ["fr"])
    assert (match.tmdb_id, match.reason) == (None, "ambiguous: 2 TMDB films titled 'Rose' (2005, 2021)")
    m, _ = matcher()
    assert m.match("v", "Pulse", FilmHints(directors=("Kiyoshi Kurosawa", "Jim Sonzero"))).tmdb_id is None
    m, _ = matcher()
    match = m.match("v", "Pulse", FilmHints(directors=("Kiyoshi Kurosawa", "Jim Sonzero")), ["ja"])
    assert (match.tmdb_id, match.reason) == (10, "title + director Kiyoshi Kurosawa / Jim Sonzero, language ja")


def test_year_search_when_the_first_page_lacks_the_year():
    m, fake = matcher()
    assert m.match("v", "The Thing", FilmHints(year=1982)).tmdb_id == 1
    assert len([u for u, _ in fake.requests if "/search/" in u]) == 1  # 1982 was on the first page
    m, fake = matcher()
    assert m.match("v", "Atonement", FilmHints(year=2026)).tmdb_id is None
    searches = [u for u, _ in fake.requests if "/search/" in u]
    assert len(searches) == 2 and searches[1].endswith("&year=2026")


def test_overrides():
    overrides = TmdbOverrides({
        "viff_centre": {"Atonement": TmdbOverride(12345, "the 2026 short")},
        "*": {"The Thing": TmdbOverride(None, "always ambiguous"), "Atonement": TmdbOverride(3)},
    })
    m, fake = matcher(overrides=overrides)
    assert m.match("viff_centre", "Atonement") == tmdb.Match(12345, "set to TMDB 12345 in tmdb_overrides.json (the 2026 short)")
    assert m.match("rio_theatre", "Atonement").tmdb_id == 3  # "*" applies elsewhere
    assert m.match("viff_centre", "The Thing (1982)").tmdb_id == 1  # "*" is keyed by the exact listed title
    assert m.match("park_theatre", "The Thing") == tmdb.Match(None, "blocked in tmdb_overrides.json (always ambiguous)")
    assert not [u for u, _ in fake.requests if "Atonement" in u]  # decided without searching


def test_venue_tmdb_id_hint_is_trusted():
    m, fake = matcher()
    assert m.match("v", "Anything at all", FilmHints(tmdb_id=4)) == tmdb.Match(4, "the venue's own TMDB id")
    assert fake.requests == []


def test_directors_match():
    assert directors_match(["Kobayashi Masaki"], ["Masaki Kobayashi"])
    assert directors_match(["Carl Theodor Dreyer"], ["Carl Th. Dreyer"])
    assert directors_match(["Krzysztof Kieslowski"], ["Krzysztof Kieślowski"])
    assert directors_match(["Andrew Adamson", "Vicky Jenson"], ["Vicky Jenson", "Andrew Adamson"])
    assert not directors_match(["John Carpenter"], ["John Ford"])
    assert not directors_match(["Ken Russell"], [])


def test_token_goes_in_a_header_never_the_url():
    m, fake = matcher(token="s3cret")
    m.match("v", "Kwaidan", FilmHints(year=1964))
    assert fake.requests
    for url, headers in fake.requests:
        assert "s3cret" not in url
        assert headers["Authorization"] == "Bearer s3cret"


def test_no_token_sends_no_authorization_header():
    # In an environment where a proxy adds the token (a network secret).
    m, fake = matcher(token=None)
    m.match("v", "Kwaidan", FilmHints(year=1964))
    assert all("Authorization" not in h for _, h in fake.requests)


def test_cache_saves_requests_and_expires():
    m, fake = matcher()
    assert m.match("v", "Kwaidan", FilmHints(year=1964, directors=("Kobayashi Masaki",))).tmdb_id == 7
    assert len(fake.requests) == 2  # one search, one details lookup
    cache = m.client.cache()
    assert list(cache["searches"]) == ["kwaidan"] and list(cache["movies"]) == ["7"]
    # Only exact-title candidates are kept from a search.
    assert [c["id"] for c in cache["searches"]["kwaidan"]["results"]] == [7]

    again, fake2 = matcher(cache=json.loads(json.dumps(cache)), now=NOW + timedelta(days=6))
    assert again.match("v", "Kwaidan", FilmHints(year=1964, directors=("Kobayashi Masaki",))).tmdb_id == 7
    assert fake2.requests == []

    later, fake3 = matcher(cache=cache, now=NOW + timedelta(days=8))  # the search is stale, the film isn't
    later.match("v", "Kwaidan", FilmHints(year=1964, directors=("Kobayashi Masaki",)))
    assert [urlsplit(u).path for u, _ in fake3.requests] == ["/3/search/movie"]


def test_unused_stale_entries_are_pruned():
    m, _ = matcher()
    m.match("v", "Kwaidan", FilmHints(year=1964, directors=("Kobayashi Masaki",)))
    m2, _ = matcher(cache=m.client.cache(), now=NOW + timedelta(days=40))
    assert m2.client.cache() == {"version": tmdb.CACHE_VERSION, "searches": {}, "movies": {}}


def test_tmdb_refusal_falls_back_to_the_cache():
    warm, _ = matcher()
    warm.match("v", "Kwaidan", FilmHints(year=1964, directors=("Kobayashi Masaki",)))
    stale_cache = warm.client.cache()
    m, fake = matcher(FakeTmdb(status=401), cache=stale_cache, now=NOW + timedelta(days=60))
    # Cached (if stale) data still matches; an uncached title can't be looked up.
    assert m.match("v", "Kwaidan", FilmHints(year=1964, directors=("Kobayashi Masaki",))).tmdb_id == 7
    miss = m.match("v", "The Thing (1982)")
    assert miss == tmdb.Match(None, "not in the cache, and TMDB was unavailable this run")
    assert len(fake.requests) == 1  # after the 401 it stops asking
    assert "HTTP 401" in m.client.unavailable and tmdb.TOKEN_ENV in m.client.unavailable


def payload(*showtimes):
    return {"showtimes": [
        {"venue_id": v, "title": t, "original_language": None, "screening_language": langs, "original_title": None,
         "tmdb_id": tmdb_id}
        for v, t, langs, tmdb_id in showtimes
    ]}


def test_enrich_payload():
    m, _ = matcher()
    p = payload(
        ("dunbar_theatre", "Jailer 2 (Telugu)", ["te"], None),
        ("dunbar_theatre", "Jailer 2 (Telugu)", ["te"], None),
        ("cinematheque", "Kwaidan", ["ja"], None),
        ("viff_centre", "The Thing", ["en"], None),
        ("rio_theatre", "Pulse", None, 10),  # the Rio failed this run: its earlier match is kept
    )
    hints = {
        ("dunbar_theatre", "Jailer 2 (Telugu)"): FilmHints(runtime=150),
        ("cinematheque", "Kwaidan"): FilmHints(year=1964, original_title="怪談"),
    }
    m.enrich(p, hints, {"dunbar_theatre", "cinematheque", "viff_centre"}, report=True)
    jailer, _, kwaidan, thing, pulse = p["showtimes"]
    # Telugu dub: the film's original language and script come from TMDB; the screened language stays.
    assert (jailer["tmdb_id"], jailer["original_title"], jailer["original_language"]) == (8, "ஜெயிலர் 2", "ta")
    assert jailer["screening_language"] == ["te"]
    assert (kwaidan["tmdb_id"], kwaidan["original_title"]) == (7, "怪談")
    assert thing["tmdb_id"] is None and thing["original_title"] is None
    assert pulse["tmdb_id"] == 10
    assert p["films"]["7"] == {
        "id": 7, "title": "Kwaidan", "original_title": "怪談", "original_language": "ja", "year": 1964,
        "directors": ["Masaki Kobayashi"], "runtime": 183, "overview": "Synopsis of Kwaidan.", "poster_path": "/p7.jpg",
    }
    assert list(p["films"]) == ["7", "8", "10"]
    assert p["tmdb"] == {
        "showtimes": 5, "matched_showtimes": 4, "titles": 4, "matched_titles": 3, "unavailable": None,
        "unmatched": [{"venue_id": "viff_centre", "title": "The Thing", "showtimes": 1,
                       "reason": "ambiguous: 2 TMDB films titled 'The Thing' (1982, 2011)"}],
    }


def test_latin_original_titles_are_not_copied():
    m, _ = matcher()
    p = payload(("cinematheque", "Three Colours: Blue", ["fr"], None))
    m.enrich(p, {("cinematheque", "Three Colours: Blue"): FilmHints(year=1993)}, {"cinematheque"}, report=False)
    [blue] = p["showtimes"]
    assert blue["tmdb_id"] == 12 and blue["original_title"] is None  # "Trois couleurs : Bleu" is Latin script
    assert p["films"]["12"]["original_title"] == "Trois couleurs : Bleu"
    assert "tmdb" not in p


def test_a_missing_film_is_not_an_outage():
    # An override pointing at a TMDB id that no longer exists: that showtime
    # gets no details, but TMDB stays available for everything else.
    overrides = TmdbOverrides({"*": {"Gone": TmdbOverride(424242)}})
    m, _ = matcher(overrides=overrides)
    p = payload(("v", "Gone", None, None), ("v", "The Thing (1982)", None, None))
    m.enrich(p, {}, {"v"}, report=True)
    assert m.client.unavailable is None
    assert [d["tmdb_id"] for d in p["showtimes"]] == [424242, 1]
    assert list(p["films"]) == ["1"]
