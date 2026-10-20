"""Builds stats.json, the numbers behind web/stats.html, from every archive snapshot.

Each daily snapshot repeats the showtimes still to come, so a screening is
counted once however many snapshots list it: screenings are unique by
(venue id, start time, listed title).

Older snapshots predate TMDB matching (no tmdb_id on their showtimes), so a
screening without one takes the match its venue and title have in the current
showtimes.json, when there is one. Film details (release year) come from
showtimes.json's "films" table and tmdb_cache.json.

A film is a TMDB film, or for an unmatched listing, that venue's title (the
same grouping the page's detail view uses).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .archive import ARCHIVE_DIRNAME, CURRENT_FILENAME
from .schema import VANCOUVER

STATS_FILENAME = "stats.json"
STATS_VERSION = 1
TOP_FILMS = 10
TOP_LANGUAGES = 10


def _load(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def snapshot_paths(root: Path) -> list[Path]:
    """Every archive/YYYY/MM/DD.json, oldest first."""
    return sorted((root / ARCHIVE_DIRNAME).glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9].json"))


def main_language(s: Mapping[str, Any]) -> str | None:
    """The one language a listing is tagged with (as web/app.js mainLanguage):
    its screening language when that is a single one, else the film's original
    language, else the first screening language."""
    screened = s.get("screening_language") or []
    if len(screened) == 1:
        return screened[0]
    return s.get("original_language") or (screened[0] if screened else None)


def build_stats(
    snapshots: Iterable[Mapping[str, Any]],
    current: Mapping[str, Any] | None,
    cached_movies: Mapping[str, Any] | None,
    venue_names: Mapping[str, str],
    now: datetime,
) -> dict[str, Any]:
    films_info: dict[str, Any] = dict(cached_movies or {})
    films_info.update((current or {}).get("films") or {})
    matched: dict[tuple[str, str], int] = {}
    for s in (current or {}).get("showtimes") or []:
        if s.get("tmdb_id"):
            matched[(s["venue_id"], s["title"])] = s["tmdb_id"]

    screenings: dict[tuple[str, str, str], dict[str, Any]] = {}
    days: list[str] = []
    for snap in snapshots:
        days.append(snap["date"])
        films_info.update(snap.get("films") or {})
        for s in snap.get("showtimes") or []:
            key = (s["venue_id"], s["start"], s["title"])
            # A later snapshot's copy wins: it carries any later matching.
            screenings[key] = s

    this_year = now.astimezone(VANCOUVER).year
    by_film: Counter[str] = Counter()
    film_meta: dict[str, dict[str, Any]] = {}
    by_venue: Counter[str] = Counter()
    by_language: Counter[str] = Counter()
    by_format: Counter[str] = Counter()
    starts: list[str] = []
    for (venue_id, start, title), s in screenings.items():
        tmdb_id = s.get("tmdb_id") or matched.get((venue_id, title))
        film = films_info.get(str(tmdb_id)) if tmdb_id else None
        key = f"tmdb:{tmdb_id}" if tmdb_id else f"listing:{venue_id}|{title}"
        by_film[key] += 1
        if key not in film_meta:
            film_meta[key] = {
                "title": film["title"] if film else title,
                "year": film.get("year") if film else None,
                "tmdb_id": tmdb_id or None,
            }
        by_venue[venue_id] += 1
        by_language[main_language(s) or (film and film.get("original_language")) or "unknown"] += 1
        by_format[s.get("format") or "unlisted"] += 1
        starts.append(start[:10])

    decades: Counter[int] = Counter()
    repertory = {"films": 0, "screenings": 0}
    new = {"films": 0, "screenings": 0}
    unknown_year = {"films": 0, "screenings": 0}
    for key, n in by_film.items():
        year = film_meta[key]["year"]
        if not year:
            unknown_year["films"] += 1
            unknown_year["screenings"] += n
            continue
        decades[year // 10 * 10] += 1
        bucket = repertory if year < this_year - 1 else new
        bucket["films"] += 1
        bucket["screenings"] += n

    languages = by_language.most_common()
    top_languages = [{"code": c, "screenings": n} for c, n in languages[:TOP_LANGUAGES]]
    other = sum(n for _, n in languages[TOP_LANGUAGES:])
    top_films = sorted(by_film.items(), key=lambda kv: (-kv[1], film_meta[kv[0]]["title"].lower()))[:TOP_FILMS]

    return {
        "stats_version": STATS_VERSION,
        "generated_at": now.isoformat(),
        "days": len(days),
        "first_day": days[0] if days else None,
        "last_day": days[-1] if days else None,
        "screening_dates": {"first": min(starts), "last": max(starts)} if starts else None,
        "totals": {"screenings": len(screenings), "films": len(by_film), "cinemas": len(by_venue)},
        # Films (not screenings) per decade of release, by TMDB year.
        "decades": [{"decade": d, "films": n} for d, n in sorted(decades.items())],
        # Repertory: released before the previous calendar year; new: since.
        "repertory": {"cutoff_year": this_year - 1, "repertory": repertory, "new": new, "unknown_year": unknown_year},
        "languages": top_languages + ([{"code": "other", "screenings": other}] if other else []),
        "formats": [{"format": f, "screenings": n} for f, n in by_format.most_common()],
        "top_films": [{**film_meta[k], "screenings": n} for k, n in top_films],
        "cinemas": [{"venue_id": v, "name": venue_names.get(v, v), "screenings": n} for v, n in by_venue.most_common()],
    }


def write_stats(root: Path, out: Path, venues_path: Path, now: datetime) -> dict[str, Any]:
    """Build stats from `root`'s archive, showtimes.json and tmdb_cache.json,
    and write them to `out`."""
    current_path = root / CURRENT_FILENAME
    cache_path = root / "tmdb_cache.json"
    current = _load(current_path) if current_path.exists() else None
    cache = _load(cache_path) if cache_path.exists() else {}
    venues = _load(venues_path).get("venues", []) if venues_path.exists() else []
    names = {v["id"]: v.get("name") or v["id"] for v in venues}
    stats = build_stats((_load(p) for p in snapshot_paths(root)), current, cache.get("movies"), names, now)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(stats, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(out)
    return stats
