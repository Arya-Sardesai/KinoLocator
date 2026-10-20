"""Matching showtimes to TMDB films, for posters, synopses and original titles.

A showtime gets a `tmdb_id` only when the match is confident; a wrong film is
worse than none. For each listed title (once per venue and title per run):

1. tmdb_overrides.json decides, if it has an entry (set or block).
2. A listing that is several films (shorts, double bills, "Five Films by ...",
   see titles.py) stays unmatched.
3. Otherwise TMDB is searched for the cleaned title, then for less faithful
   forms of it (without a series prefix, without a possessive credit), and
   for the original title the venue prints. Only results whose title or
   original title equals the query (ignoring case, accents and punctuation)
   are candidates. Then the venue's hints narrow them:
     - year: within one year of TMDB's release year;
     - director(s): at least one name in common (in any word order, so
       "Kobayashi Masaki" is "Masaki Kobayashi");
     - runtime: within 12 minutes (Veezi's durations include trailers).
   One survivor, with at least one hint checked, is a match. Several
   survivors are narrowed by the listing's language, else left unmatched as
   ambiguous (the language alone never confirms a match). With no hints at all, the only exact-title candidate is
   accepted if TMDB has nothing else of that title and it has at least 50
   votes (an established film, not a namesake). A query that exists only
   because a possessive credit was removed ("Ken Russell's ...") must also
   have that person among the directors.

Every unmatched title keeps its reason, and the run reports them.

API use (TMDB v3, read access token as a Bearer header):
  GET /search/movie?query=&include_adult=false&language=en-US&page=1[&year=]
  GET /movie/{id}?language=en-US&append_to_response=credits
Responses are cached in tmdb_cache.json (committed by the scrape workflow):
searches for 7 days, film details for 30. Only exact-title candidates and
the top few other results are kept from a search, so the file stays small. Requests go through a
PoliteSession with a 0.25 s gap, well inside TMDB's limits. If TMDB refuses
(no token, HTTP 401) or fails, the run carries on with the cache only and
the report says so.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode

from .fetch import FetchError, Fetcher
from .schema import FilmHints
from .titles import clean_title, fold, is_latin
from .tmdb_overrides import TmdbOverrides

log = logging.getLogger(__name__)

API = "https://api.themoviedb.org/3"
TOKEN_ENV = "TMDB_READ_TOKEN"
CACHE_FILENAME = "tmdb_cache.json"
CACHE_VERSION = 2  # 2: searches also keep the top non-exact results ("loose")
SEARCH_TTL = timedelta(days=7)
MOVIE_TTL = timedelta(days=30)
REQUEST_GAP = 0.25  # seconds between TMDB requests

YEAR_SLACK = 1
WIDE_YEAR_SLACK = 3  # only when the director confirms it (festival vs release year)
LOOSE_RESULTS = 5  # non-exact search results kept, for alternative titles
LOOSE_LOOKUPS = 3
RUNTIME_SLACK = 12
MIN_VOTES_WITHOUT_HINTS = 50
MAX_DETAIL_LOOKUPS = 6


class TmdbUnavailable(RuntimeError):
    """TMDB couldn't be reached or refused us; the run continues from the cache."""


@dataclass(frozen=True)
class Candidate:
    """A search result whose title or original title equals the query."""

    id: int
    title: str
    original_title: str
    original_language: str | None
    year: int | None
    vote_count: int


@dataclass(frozen=True)
class Film:
    id: int
    title: str
    original_title: str
    original_language: str | None
    year: int | None
    directors: tuple[str, ...]
    runtime: int | None
    overview: str | None
    poster_path: str | None
    genres: tuple[str, ...] = ()
    """TMDB genre names ("Horror", "Family"), for the page's seasonal collections."""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["directors"] = list(self.directors)
        d["genres"] = list(self.genres)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Film:
        return cls(
            id=d["id"], title=d["title"], original_title=d["original_title"],
            original_language=d.get("original_language"), year=d.get("year"),
            directors=tuple(d.get("directors") or ()), runtime=d.get("runtime"),
            overview=d.get("overview"), poster_path=d.get("poster_path"), genres=tuple(d.get("genres") or ()),
        )


@dataclass(frozen=True)
class Match:
    tmdb_id: int | None
    reason: str


def _year(release_date: Any) -> int | None:
    return int(release_date[:4]) if isinstance(release_date, str) and release_date[:4].isdigit() else None


def parse_search(text: str, query: str) -> tuple[int, list[Candidate], list[Candidate]]:
    """(total results, the results whose title or original title equals `query`,
    the first few others). TMDB's search also matches alternative titles
    ("Sunless" finds Sans Soleil), and those are only ever accepted with a
    year and a director to confirm them."""
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("TMDB search response has no results list")
    want = fold(query)
    found, loose = [], []
    for r in data["results"]:
        c = Candidate(
            int(r["id"]), r.get("title") or "", r.get("original_title") or "",
            r.get("original_language") or None, _year(r.get("release_date")), int(r.get("vote_count") or 0),
        )
        if want in (fold(c.title), fold(c.original_title)):
            found.append(c)
        elif len(loose) < LOOSE_RESULTS:
            loose.append(c)
    return int(data.get("total_results") or 0), found, loose


def parse_movie(text: str) -> Film:
    d = json.loads(text)
    crew = ((d.get("credits") or {}).get("crew")) or []
    directors = tuple(dict.fromkeys(c["name"] for c in crew if c.get("job") == "Director" and c.get("name")))
    return Film(
        id=int(d["id"]), title=d.get("title") or "", original_title=d.get("original_title") or "",
        original_language=d.get("original_language") or None, year=_year(d.get("release_date")),
        directors=directors, runtime=d.get("runtime") or None, overview=(d.get("overview") or "").strip() or None,
        poster_path=d.get("poster_path") or None,
        genres=tuple(g["name"] for g in d.get("genres") or () if isinstance(g, dict) and g.get("name")),
    )


class TmdbClient:
    """TMDB lookups through a cache. Network failures switch it to cache-only."""

    def __init__(self, fetcher: Fetcher | None, cache: dict[str, Any] | None, now: datetime, token: str | None = None):
        self.fetcher = fetcher
        self.now = now
        self.token = token
        cache = cache if cache and cache.get("version") == CACHE_VERSION else {}
        self.searches: dict[str, Any] = dict(cache.get("searches", {}))
        self.movies: dict[str, Any] = dict(cache.get("movies", {}))
        self.used: set[str] = set()
        self.unavailable: str | None = None if fetcher is not None else "no TMDB access configured"
        self.requests = 0

    # -- network ---------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any]) -> str:
        if self.unavailable:
            raise TmdbUnavailable(self.unavailable)
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            self.requests += 1
            return self.fetcher.get_text(f"{API}{path}?{urlencode(params)}", headers=headers)
        except FetchError as e:
            if "HTTP 404" in str(e):
                raise  # one missing film (a stale override id), not an outage
            # The token is in a header, never in the URL, and PoliteSession redacts header
            # values from its errors; redact here too, since this text is committed.
            msg = str(e).replace(self.token, "[token]") if self.token else str(e)
            self.unavailable = f"TMDB request failed ({msg}); using cached matches only"
            if "HTTP 401" in str(e):
                self.unavailable = f"TMDB refused the request (HTTP 401): is {TOKEN_ENV} set? Using cached matches only"
            log.warning("%s", self.unavailable)
            raise TmdbUnavailable(self.unavailable) from e

    def _fresh(self, entry: Mapping[str, Any] | None, ttl: timedelta) -> bool:
        if not entry:
            return False
        if self.unavailable:
            return True  # stale beats nothing when TMDB is down
        return self.now - datetime.fromisoformat(entry["fetched_at"]) < ttl

    # -- lookups ---------------------------------------------------------------

    def search(self, query: str, year: int | None = None) -> list[Candidate]:
        return self._search(query, year)["results"]

    def loose(self, query: str, year: int | None = None) -> list[Candidate]:
        """The top search results that aren't titled `query` (see parse_search)."""
        return self._search(query, year)["loose"]

    def _search(self, query: str, year: int | None) -> dict[str, list[Candidate]]:
        key = fold(query) + (f"|{year}" if year else "")
        self.used.add("s:" + key)
        entry = self.searches.get(key)
        if not self._fresh(entry, SEARCH_TTL):
            params: dict[str, Any] = {"query": query, "include_adult": "false", "language": "en-US", "page": 1}
            if year:
                params["year"] = year
            try:
                total, found, loose = parse_search(self._get("/search/movie", params), query)
            except TmdbUnavailable:
                if entry is None:
                    raise
            else:
                entry = {"fetched_at": self.now.isoformat(), "total": total, "results": [asdict(c) for c in found],
                         "loose": [asdict(c) for c in loose]}
                self.searches[key] = entry
        return {k: [Candidate(**c) for c in entry.get(k, [])] for k in ("results", "loose")}

    def movie(self, tmdb_id: int) -> Film | None:
        key = str(tmdb_id)
        self.used.add("m:" + key)
        entry = self.movies.get(key)
        # Entries cached before genres were stored are refetched once.
        if not self._fresh(entry, MOVIE_TTL) or "genres" not in entry:
            params = {"language": "en-US", "append_to_response": "credits"}
            try:
                film = parse_movie(self._get(f"/movie/{tmdb_id}", params))
            except TmdbUnavailable:
                if entry is None:
                    return None
            except FetchError:
                return None
            else:
                entry = {"fetched_at": self.now.isoformat(), **film.to_dict()}
                self.movies[key] = entry
        return Film.from_dict(entry)

    def cache(self) -> dict[str, Any]:
        """The cache to save: everything used this run, plus unused entries still fresh."""
        def keep(prefix: str, key: str, entry: Mapping[str, Any], ttl: timedelta) -> bool:
            return prefix + key in self.used or self.now - datetime.fromisoformat(entry["fetched_at"]) < ttl

        return {
            "version": CACHE_VERSION,
            "searches": {k: v for k, v in sorted(self.searches.items()) if keep("s:", k, v, SEARCH_TTL)},
            "movies": {k: v for k, v in sorted(self.movies.items(), key=lambda kv: int(kv[0])) if keep("m:", k, v, MOVIE_TTL)},
        }


def _name_tokens(name: str) -> frozenset[str]:
    return frozenset(fold(name).split())


def directors_match(hinted: Iterable[str], credited: Iterable[str]) -> bool:
    """True if any hinted name is one of TMDB's directors, in any word order."""
    credited_sets = [_name_tokens(n) for n in credited]
    for name in hinted:
        a = _name_tokens(name)
        for b in credited_sets:
            if a and (a == b or len(a & b) >= 2):
                return True
    return False


@dataclass
class _Outcome:
    match: Match | None = None
    reason: str | None = None
    final: bool = False


def _years(cands: Iterable[Candidate | Film]) -> str:
    years = sorted({c.year for c in cands if c.year})
    return ", ".join(map(str, years)) if years else "no year"


@dataclass
class Report:
    showtimes: int = 0
    matched_showtimes: int = 0
    titles: int = 0
    matched_titles: int = 0
    unmatched: list[dict[str, Any]] = field(default_factory=list)
    unavailable: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Matcher:
    def __init__(self, client: TmdbClient, overrides: TmdbOverrides | None = None) -> None:
        self.client = client
        self.overrides = overrides or TmdbOverrides()
        self._memo: dict[tuple[str, str], Match] = {}

    def match(self, venue_id: str, title: str, hints: FilmHints = FilmHints(), languages: Iterable[str] = ()) -> Match:
        key = (venue_id, title)
        if key not in self._memo:
            try:
                self._memo[key] = self._match(venue_id, title, hints, tuple(dict.fromkeys(languages)))
            except TmdbUnavailable:
                # Why TMDB is unavailable is said once, in the report's `unavailable`.
                self._memo[key] = Match(None, "not in the cache, and TMDB was unavailable this run")
        return self._memo[key]

    def _match(self, venue_id: str, title: str, hints: FilmHints, languages: tuple[str, ...]) -> Match:
        if (override := self.overrides.get(venue_id, title)) is not None:
            return Match(override.tmdb_id, override.describe())
        if hints.tmdb_id:
            return Match(hints.tmdb_id, "the venue's own TMDB id")
        clean = clean_title(title)
        if clean.multiple_films:
            return Match(None, f"several films: {clean.multiple_films}")
        if hints.multiple_films:
            return Match(None, "several films, per the venue's page")
        queries = list(clean.queries)
        if hints.original_title and fold(hints.original_title) not in map(fold, queries):
            queries.insert(1, hints.original_title)
        if not queries:
            return Match(None, "nothing left of the title to search for")

        year = hints.year or clean.year
        reasons = []
        for query in queries:
            credited = query in clean.credited_queries
            outcome = self._decide(query, year, hints, languages, clean.credit if credited else None)
            if outcome.match:
                return outcome.match
            reasons.append(outcome.reason)
            if outcome.final:
                return Match(None, outcome.reason)
        if year and hints.directors and (m := self._alternative_title(queries[0], year, hints.directors)):
            return m
        return Match(None, reasons[0] if len(set(reasons)) == 1 else "; ".join(dict.fromkeys(reasons)))

    def _alternative_title(self, query: str, year: int, directors: tuple[str, ...]) -> Match | None:
        """A film TMDB's search found for `query` under another title (VIFF's
        "Sunless" is Sans Soleil): accepted only when it's from the venue's
        year and the only one of the top results by the venue's director."""
        cands = {c.id: c for c in self.client.loose(query) + self.client.loose(query, year)}
        near = [c for c in cands.values() if c.year and abs(c.year - year) <= YEAR_SLACK][:LOOSE_LOOKUPS]
        films = [f for c in near if (f := self.client.movie(c.id)) is not None and directors_match(directors, f.directors)]
        if len(films) != 1:
            return None
        return Match(films[0].id, f"TMDB's {films[0].title!r} (searched as {query!r}) + year {year}, "
                                  f"director {' / '.join(directors)}")

    def _decide(
        self, query: str, year: int | None, hints: FilmHints, languages: tuple[str, ...], credit: str | None
    ) -> _Outcome:
        cands = self.client.search(query)
        if year and not any(c.year and abs(c.year - year) <= YEAR_SLACK for c in cands):
            by_id = {c.id: c for c in cands}
            by_id.update({c.id: c for c in self.client.search(query, year)})
            cands = list(by_id.values())
        if not cands:
            return _Outcome(reason=f"no TMDB film titled {query!r}")

        checked: list[str] = []
        survivors: list[Candidate | Film] = list(cands)
        directors = tuple(hints.directors) + ((credit,) if credit else ())
        if year:
            survivors = [c for c in cands if c.year and abs(c.year - year) <= YEAR_SLACK]
            checked.append(f"year {year}")
            if not survivors and directors:
                # A festival year can be two or three years off the release
                # date (Begotten: 1989 at the Cinematheque, 1991 on TMDB); the
                # director filter below must then confirm it.
                survivors = [c for c in cands if c.year and abs(c.year - year) <= WIDE_YEAR_SLACK]
                checked[-1] = f"year {year} (TMDB: {_years(survivors)})"
            if not survivors:
                return _Outcome(reason=f"TMDB has {query!r} from {_years(cands)}, not {year}")

        if directors or hints.runtime:
            if len(survivors) > MAX_DETAIL_LOOKUPS:
                return _Outcome(reason=f"ambiguous: {len(survivors)} TMDB films titled {query!r} ({_years(survivors)})", final=True)
            films = [f for c in survivors if (f := self.client.movie(c.id)) is not None]
            if len(films) < len(survivors):
                return _Outcome(reason=f"couldn't fetch TMDB details to check {query!r}")
            survivors = films
            if directors:
                survivors = [f for f in films if directors_match(directors, f.directors)]
                checked.append("director " + " / ".join(directors))
                if not survivors:
                    who = "; ".join(f"{f.year}: {', '.join(f.directors) or 'no director listed'}" for f in films)
                    return _Outcome(reason=f"TMDB's {query!r} ({who}) isn't by {' / '.join(directors)}")
            if hints.runtime:
                fits = [f for f in survivors if f.runtime is not None and abs(f.runtime - hints.runtime) <= RUNTIME_SLACK]
                # A film whose runtime is known and fits beats one TMDB has no runtime for.
                fits = fits or [f for f in survivors if f.runtime is None]
                if fits:
                    survivors = fits
                    checked.append(f"runtime {hints.runtime} min")
                elif not directors:
                    return _Outcome(reason=f"TMDB's {query!r} runs {', '.join(str(f.runtime) for f in films)} min, not ~{hints.runtime}")
                # Otherwise the director already confirmed it, and a venue's
                # running time (with intros, shorts or trailers) doesn't veto that.

        if credit:
            survivors = [f for f in survivors if directors_match((credit,), getattr(f, "directors", ()))]
            if not survivors:
                return _Outcome(reason=f"{query!r} (the title without {credit}'s credit) isn't by {credit} on TMDB")

        # The listing's language only breaks a tie between films a real hint already fits.
        if len(survivors) > 1 and languages and checked:
            narrowed = [c for c in survivors if c.original_language in languages]
            if len(narrowed) == 1:
                survivors = narrowed
                checked.append(f"language {narrowed[0].original_language}")

        if len(survivors) > 1:
            return _Outcome(reason=f"ambiguous: {len(survivors)} TMDB films titled {query!r} ({_years(survivors)})", final=True)
        only = survivors[0]
        if checked:
            return _Outcome(Match(only.id, f"title + {', '.join(checked)}"))
        votes = only.vote_count if isinstance(only, Candidate) else next(c.vote_count for c in cands if c.id == only.id)
        if len(cands) == 1 and votes >= MIN_VOTES_WITHOUT_HINTS:
            return _Outcome(Match(only.id, f"the only TMDB film titled {query!r} ({votes} votes)"))
        return _Outcome(
            reason=f"one TMDB film titled {query!r} ({_years([only])}, {votes} votes), but no year or director "
            "from the venue to confirm it",
            final=True,
        )

    # -- payloads ------------------------------------------------------------------

    def enrich(
        self,
        payload: dict[str, Any],
        hints: Mapping[tuple[str, str], FilmHints],
        fresh_venues: Iterable[str],
        *,
        report: bool,
    ) -> None:
        """Set tmdb_id (and original_title / original_language where TMDB knows
        them) on every showtime in a snapshot payload, add the `films` table,
        and with `report`, the `tmdb` match report.

        Showtimes of venues not scraped successfully this run (kept from an
        earlier run, or carried over as stale) have no hints now, so a match
        they already carry is kept rather than redone without them."""
        fresh = set(fresh_venues)
        films: dict[str, Any] = {}
        titles: dict[tuple[str, str], list[Any]] = {}  # key -> [showtime count, Match]
        for d in payload["showtimes"]:
            key = (d["venue_id"], d["title"])
            if d["venue_id"] not in fresh and d.get("tmdb_id") and self.overrides.get(*key) is None:
                m = Match(d["tmdb_id"], "kept from an earlier run")
            else:
                languages = [d["original_language"]] if d.get("original_language") else []
                languages += d.get("screening_language") or []
                m = self.match(*key, hints.get(key, FilmHints()), languages)
            titles.setdefault(key, [0, m])[0] += 1
            d["tmdb_id"] = m.tmdb_id
            film = self.client.movie(m.tmdb_id) if m.tmdb_id else None
            if film is None:
                continue
            films[str(film.id)] = film.to_dict()
            if not d.get("original_language") and film.original_language:
                d["original_language"] = film.original_language
            # Shown above the listed title, so only when it's in another script.
            if film.original_title and not is_latin(film.original_title) and fold(film.original_title) != fold(d["title"]):
                d["original_title"] = film.original_title
        payload["films"] = dict(sorted(films.items(), key=lambda kv: int(kv[0])))
        if not report:
            return
        r = Report(unavailable=self.client.unavailable)
        for (venue_id, title), (n, m) in sorted(titles.items()):
            r.showtimes += n
            r.titles += 1
            if m.tmdb_id:
                r.matched_showtimes += n
                r.matched_titles += 1
            else:
                r.unmatched.append({"venue_id": venue_id, "title": title, "showtimes": n, "reason": m.reason})
        payload["tmdb"] = r.to_dict()


def load_cache(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text("utf-8")) if path.exists() else None
    except json.JSONDecodeError:
        log.warning("%s is not valid JSON; starting an empty TMDB cache", path)
        return None


def save_cache(path: Path, client: TmdbClient) -> None:
    path.write_text(json.dumps(client.cache(), indent=1, ensure_ascii=False, sort_keys=True) + "\n", "utf-8")


class TmdbTokenError(ValueError):
    """TMDB_READ_TOKEN is set but can't be a read access token. The message never includes it."""


_TOKEN_CHARS = re.compile(r"[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)*")


def token_from_env() -> str | None:
    """The read access token, if this environment sets one, without the
    surrounding whitespace a pasted secret often has. When it isn't set,
    requests go out without one: a proxy may add it (a network secret), and
    otherwise TMDB answers 401 and the run uses the cache.

    A value that still isn't a token (whitespace or other characters inside
    it, or a v3 API key instead of a read access token) raises TmdbTokenError,
    which says what's wrong without repeating the value."""
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        return None
    where = f"{TOKEN_ENV} (set in your shell, or the repository secret mapped in .github/workflows/scrape.yml)"
    if not _TOKEN_CHARS.fullmatch(token):
        bad = sorted({"whitespace" if c.isspace() else "control characters" if not c.isprintable() else repr(c)
                      for c in token if not (c.isascii() and (c.isalnum() or c in "._-"))})
        raise TmdbTokenError(
            f"{where} is malformed: {len(token)} characters including {', '.join(bad) or 'empty segments between dots'}. "
            "A TMDB read access token is one line of letters, digits, '-', '_' and '.'. Paste it again from "
            "themoviedb.org/settings/api (the value isn't shown here)."
        )
    if token.count(".") != 2:
        hint = " That looks like a v3 API key; use the API Read Access Token instead." if re.fullmatch(
            r"[0-9a-f]{32}", token) else ""
        raise TmdbTokenError(
            f"{where} is malformed: a read access token has three dot-separated parts, this has "
            f"{token.count('.') + 1}.{hint} Copy it from themoviedb.org/settings/api (the value isn't shown here)."
        )
    return token

