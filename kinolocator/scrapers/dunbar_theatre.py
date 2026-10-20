"""Dunbar Theatre, 4555 Dunbar St, Vancouver — Veezi ticketing only

Site structure (checked 2026-10-20):

* The theatre has no site of its own: dunbartheatre.ca doesn't resolve in
  DNS. Its Facebook page links to Veezi, which has two sites for it on
  ticketing.uswest.veezi.com: the main one (first-run films) and "Dunbar
  Theatre Special Events" (Tamil and Telugu releases, fundraisers).
* Veezi's real API (api.us.veezi.com) needs a token issued to the cinema, so
  this reads the public sessions page, GET /sessions/?siteToken=<token>.
  robots.txt allows it; it disallows /purchase/, which is only linked to.
* The page carries schema.org JSON-LD: one VisualArtsEvent per session with
  `name`, `startDate` (ISO 8601 with offset), `duration` (e.g. "PT2H30M") and
  `url` (the session's /purchase/ link). The HTML "by film" tab
  (#sessionsByFilmConent div.film) groups the same sessions under each film:
  Veezi's film id (id="ST00000100"), the title (h3.title), a synopsis
  (.film-desc) and the sessions' /purchase/ links with their times.
* Veezi's offsets come from pre-2026 time-zone data: a session after BC's
  move to permanent UTC-7 on 2026-11-01 arrives as "...T10:00:00-08:00" while
  the page shows "10:00 AM". The wall-clock time is right, so it is kept and
  given Vancouver's actual offset.
* No language field anywhere. The Dunbar marks language versions in the
  title: "Jailer 2 (Tamil)", "Jailer 2 (Telugu)" (two films in Veezi sharing
  one poster: the Telugu one is a dub). A trailing parenthesis naming a
  language sets `screening_language`; `original_language` is left unset,
  since a dub's original language isn't the one screened. Titles are kept
  as listed.
* No prices: they appear only on the disallowed purchase pages.

Films vs other events: every listing on the main site is a film. On the
special-events site, a title that reads like an event ("Fundraiser - Wild Cats
Soft Ball", a rental, a meeting) is flagged for review: fundraisers here are
usually a film screening but the title doesn't say which. Decide them in
overrides.json, keyed "<site>:<Veezi film id>", e.g. "special:ST00000106".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..languages import language_code
from ..overrides import Override
from ..schema import VANCOUVER, FilmHints, Showtime
from .base import Scraper

HOST = "https://ticketing.uswest.veezi.com"
SITES = {
    "main": "sap55z6c6aj6b96yshhtj7vyw4",
    "special": "b2fkxrvna0ydd3hyn7wv85791c",
}
EVENT_WORDS = re.compile(r"\b(fundraiser|benefit|private|rental|meeting|concert|live|gala|party)\b", re.IGNORECASE)
TRAILING_ASIDE = re.compile(r"\(([^()]+)\)\s*$")
DURATION = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?$")
TIME = re.compile(r"^(\d{1,2}):(\d{2})\s*([AP]M)$", re.IGNORECASE)


class VeeziParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class Session:
    film_id: str
    title: str
    start: datetime
    url: str
    """Absolute /purchase/ link."""
    runtime: int | None = None
    """Minutes, from the JSON-LD duration."""


def sessions_url(token: str) -> str:
    return f"{HOST}/sessions/?siteToken={token}"


def _minutes(duration: str | None) -> int | None:
    m = DURATION.match(duration or "")
    if not m or not (m[1] or m[2]):
        return None
    return int(m[1] or 0) * 60 + int(m[2] or 0)


def _vancouver_wall_clock(iso: str) -> datetime:
    """Veezi's local time with Vancouver's real offset (see module docstring)."""
    return datetime.fromisoformat(iso).replace(tzinfo=None).replace(tzinfo=VANCOUVER)


def _clock(text: str) -> tuple[int, int] | None:
    m = TIME.match(" ".join(text.split()))
    if not m:
        return None
    hour = int(m[1]) % 12 + (12 if m[3].upper() == "PM" else 0)
    return hour, int(m[2])


def _json_ld_events(soup: BeautifulSoup) -> list[dict[str, Any]]:
    events = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except json.JSONDecodeError as e:
            raise VeeziParseError(f"unreadable JSON-LD: {e}") from e
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "VisualArtsEvent":
                events.append(item)
    return events


def parse_sessions(page: str) -> list[Session]:
    """Every session on a Veezi sessions page, from its JSON-LD joined to the
    "by film" tab on the purchase link. Raises if the two disagree."""
    soup = BeautifulSoup(page, "html.parser")
    by_film = soup.select_one("#sessionsByFilmConent")
    if by_film is None:
        raise VeeziParseError("no #sessionsByFilmConent on the sessions page; layout changed?")

    films: dict[str, tuple[str, str, tuple[int, int] | None]] = {}  # purchase path -> (film id, title, clock)
    for film in by_film.select("div.film"):
        title_el = film.select_one("h3.title")
        if not film.get("id") or title_el is None:
            raise VeeziParseError("a film in the by-film tab has no id or title")
        title = " ".join(title_el.get_text(" ", strip=True).split())
        for a in film.select("a[href*='/purchase/']"):
            films[urlsplit(a["href"]).path.lower()] = (film["id"], title, _clock(a.get_text(" ", strip=True)))

    sessions = []
    events = _json_ld_events(soup)
    for ev in events:
        path = urlsplit(ev.get("url", "")).path.lower()
        if path not in films:
            raise VeeziParseError(f"JSON-LD session {ev.get('url')!r} isn't in the by-film tab")
        film_id, title, clock = films.pop(path)
        start = _vancouver_wall_clock(ev["startDate"])
        if clock is not None and clock != (start.hour, start.minute):
            raise VeeziParseError(f"{title}: JSON-LD says {start:%H:%M} but the page shows {clock[0]:02d}:{clock[1]:02d}")
        url = ev["url"] if ev["url"].startswith("https://") else HOST + path
        sessions.append(Session(film_id, title, start, url, _minutes(ev.get("duration"))))
    if films:
        raise VeeziParseError(f"{len(films)} session link(s) in the by-film tab have no JSON-LD, e.g. {next(iter(films))}")
    return sessions


def title_language(title: str) -> str | None:
    """ISO code of a language named in a trailing parenthesis: "Jailer 2 (Tamil)" -> "ta"."""
    m = TRAILING_ASIDE.search(title)
    return language_code(m[1], quiet=True) if m else None


def classify(site: str, title: str, override: Override | None) -> tuple[bool | None, str]:
    if override is not None:
        return override.film, override.describe()
    if site == "main":
        return True, "listed on the main Veezi site"
    if m := EVENT_WORDS.search(title):
        return None, f"special-events listing titled like an event ({m[1].lower()!r}); the film isn't named"
    return True, "special-events screening"


class DunbarTheatre(Scraper):
    venue_id = "dunbar_theatre"
    venue_name = "Dunbar Theatre"
    municipality = "Vancouver"
    homepage = sessions_url(SITES["main"])
    sites: ClassVar[dict[str, str]] = SITES

    def scrape(self) -> list[Showtime]:
        today = self.scraped_at.astimezone(VANCOUVER).date()
        showtimes = []
        for site, token in self.sites.items():
            for s in parse_sessions(self.fetcher.get_text(sessions_url(token))):
                if s.start.date() < today:
                    continue
                key = f"{site}:{s.film_id}"
                is_film, why = classify(site, s.title, self.overrides.get(key))
                if not is_film:
                    self.skip(s.title, why, start=s.start, url=s.url, needs_review=is_film is None, key=key)
                    continue
                language = title_language(s.title)
                self.hint(s.title, FilmHints(runtime=s.runtime))
                showtimes.append(
                    self.showtime(
                        title=s.title,
                        start=s.start,
                        screening_language=[language] if language else None,
                        ticket_url=s.url,
                    )
                )
        return showtimes
