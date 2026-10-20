"""The Cinematheque, 1131 Howe St, Vancouver — https://thecinematheque.ca

Site structure (checked 2026-10-12):

* No JSON/API endpoint: pages are server-rendered HTML with a little jQuery,
  so plain requests + BeautifulSoup is enough.
* /films/calendar lists every scheduled programme for the next ~10 weeks.
  Each day is `#eventCalendar > li` (with .dom/.mon/.year spans) holding
  `ol.programs > li.programScreening` entries with the time (`span.time.am|pm`),
  title link (`a.programTitle` -> /films/YYYY/slug) and ticket link (`a.cta`,
  Agile Ticketing at tickets.thecinematheque.ca). `programRental` (third-party
  rentals such as VIFF) and `programEvent` (meetings, talks) are skipped.
* Each film page has structured details (`.filmSpecs .filmFormat`, e.g. 35mm/DCP;
  `.filmAltTitle` in the original script, `.filmYear`, `.filmDirector` with
  East Asian names surname first, `.filmRuntime` in minutes; programmes of
  several films leave year and director out) and a free-text language line in the description, e.g.
  "In Japanese with English subtitles", "In English", "Silent", "No dialogue".
* Prices are not listed per screening. /about/visit carries the general rate
  card ("$15 General (18+)"), used as an *estimated* price; a "Free Admission"
  callout on a film page gives a *scraped* price of 0.
* The site answers bursts of requests with HTTP 429, so keep the fetcher's
  per-host delay at a few seconds.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from ..languages import parse_language_line
from ..schema import VANCOUVER, FilmHints, PriceConfidence, Showtime, Skipped, normalize_format
from .base import Scraper

log = logging.getLogger(__name__)

BASE_URL = "https://thecinematheque.ca"
CALENDAR_URL = f"{BASE_URL}/films/calendar"
RATES_URL = f"{BASE_URL}/about/visit"

MONTHS = {
    m: i
    for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"],
        start=1,
    )
}

SKIP_REASONS = {
    "programRental": "third-party rental (not programmed by the Cinematheque; no showtime listed)",
    "programEvent": "non-screening event",
}
GENERAL_PRICE = re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)\s+General\b")
NAME_LIST = re.compile(r"\s*(?:,|&|\band\b)\s*")


class CinemathequeParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class CalendarEntry:
    title: str
    start: datetime
    film_url: str | None
    ticket_url: str | None


@dataclass(frozen=True)
class FilmDetails:
    format: str | None = None
    languages: list[str] | None = None
    subtitles: list[str] | None = None
    free_admission: bool = False
    hints: FilmHints = FilmHints()


def _text(el: Tag | None) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def parse_time(text: str, meridiem: str) -> time:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text.strip())
    if not m or meridiem not in ("am", "pm"):
        raise CinemathequeParseError(f"unrecognised time {text!r} ({meridiem!r})")
    hour, minute = int(m[1]), int(m[2])
    if not 1 <= hour <= 12:
        raise CinemathequeParseError(f"hour out of range in {text!r}")
    return time(hour % 12 + (12 if meridiem == "pm" else 0), minute)


def _parse_day(day_li: Tag) -> date:
    try:
        return date(
            int(_text(day_li.select_one(".day .year"))),
            MONTHS[_text(day_li.select_one(".day .mon")).lower()],
            int(_text(day_li.select_one(".day .dom"))),
        )
    except (KeyError, ValueError) as e:
        raise CinemathequeParseError(f"unparseable calendar day: {_text(day_li)[:80]!r}") from e


def parse_calendar(html: str) -> tuple[list[CalendarEntry], list[tuple[date, Skipped]]]:
    """Screenings, plus (day, Skipped) for every non-screening programme
    (rentals, events). Rentals list no time, hence the separate day."""
    soup = BeautifulSoup(html, "html.parser")
    days = soup.select("#eventCalendar > li")
    if not days:
        raise CinemathequeParseError("no #eventCalendar days found; page layout changed?")
    entries: list[CalendarEntry] = []
    skipped: list[tuple[date, Skipped]] = []
    for day_li in days:
        day = _parse_day(day_li)
        for prog in day_li.select("ol.programs > li[class]"):
            title_link = prog.select_one("a.programTitle")
            title = _text(title_link)
            url = urljoin(BASE_URL, title_link["href"]) if title_link and title_link.get("href") else None
            time_span = prog.select_one(".details .time")
            start = None
            if time_span is not None:
                meridiem = next((c for c in time_span.get("class", []) if c in ("am", "pm")), "")
                start = datetime.combine(day, parse_time(_text(time_span), meridiem), VANCOUVER)
            kind = prog["class"][0]
            if kind != "programScreening":
                # An unfamiliar entry type might be a new way of listing films.
                reason = SKIP_REASONS.get(kind, f"calendar entry of type {kind!r}")
                skip = Skipped(title or _text(prog), reason, start, url, needs_review=kind not in SKIP_REASONS)
                skipped.append((day, skip))
                continue
            if not title or start is None:
                skip = Skipped(title or _text(prog), "screening without title or time", None, url, needs_review=True)
                skipped.append((day, skip))
                continue
            cta = prog.select_one("a.cta[href]")
            entries.append(
                CalendarEntry(title=title, start=start, film_url=url, ticket_url=cta["href"] if cta else None)
            )
    return entries, skipped


def original_title(alt: str) -> str | None:
    """The original title from the masthead's alternative-title line, which can
    also carry English "aka" titles: "aka Midnight Matinee", or
    "aka Walter Lapin) (Allez Hëpp Hopp Hopp!" (the outer brackets are the
    page's). An "aka" title is used only when there's nothing else, since the
    hint is just one more TMDB search."""
    parts = [x.strip(" ()") for x in re.split(r"\)\s*\(", alt)]
    parts = [x for x in parts if x]
    original = [x for x in parts if not re.match(r"aka\b", x, re.IGNORECASE)]
    if original:
        return original[0]
    return re.sub(r"^aka\s+", "", parts[0], flags=re.IGNORECASE) if parts else None


def parse_film_page(html: str) -> FilmDetails:
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one("#details .filmTitle") is None:
        raise CinemathequeParseError("film page has no #details .filmTitle; page layout changed?")
    languages = subtitles = None
    for p in soup.select("#description p"):
        if parsed := parse_language_line(_text(p)):
            languages, subtitles = parsed
            break
    year = _text(soup.select_one("#details .filmYear"))
    runtime = _text(soup.select_one("#details .filmRuntime"))
    directors = _text(soup.select_one("#details .filmDirector"))
    return FilmDetails(
        format=normalize_format(_text(soup.select_one("#details .filmFormat")) or None),
        languages=languages,
        subtitles=subtitles,
        free_admission="free admission" in _text(soup.select_one("#description .callout")).lower(),
        hints=FilmHints(
            year=int(year) if year.isdigit() else None,
            directors=tuple(n for n in NAME_LIST.split(directors) if n),
            runtime=int(runtime) if runtime.isdigit() else None,
            original_title=original_title(_text(soup.select_one("#details .filmAltTitle"))),
        ),
    )


def parse_general_price(html: str) -> float | None:
    soup = BeautifulSoup(html, "html.parser")
    m = GENERAL_PRICE.search(_text(soup.find("main") or soup))
    return float(m[1]) if m else None


class Cinematheque(Scraper):
    venue_id = "cinematheque"
    venue_name = "The Cinematheque"
    municipality = "Vancouver"
    homepage = BASE_URL

    def scrape(self) -> list[Showtime]:
        today = self.scraped_at.astimezone(VANCOUVER).date()
        entries, skipped = parse_calendar(self.fetcher.get_text(CALENDAR_URL))
        entries = [e for e in entries if e.start.date() >= today]
        self.skipped.extend(s for day, s in skipped if day >= today)
        if not entries:
            return []

        general_price = parse_general_price(self.fetcher.get_text(RATES_URL))
        if general_price is None:
            log.warning("could not find the general admission rate on %s; prices left empty", RATES_URL)

        details = self._fetch_film_details({e.film_url for e in entries if e.film_url})

        showtimes = []
        for e in entries:
            d = details.get(e.film_url or "", FilmDetails())
            self.hint(e.title, d.hints)
            if d.free_admission:
                price, confidence = 0.0, PriceConfidence.SCRAPED
            elif general_price is not None:
                price, confidence = general_price, PriceConfidence.ESTIMATED
            else:
                price, confidence = None, None
            showtimes.append(
                self.showtime(
                    title=e.title,
                    start=e.start,
                    # The Cinematheque screens original-language versions, so the
                    # first listed language is taken as the original language.
                    original_language=d.languages[0] if d.languages else None,
                    screening_language=d.languages,
                    subtitles=d.subtitles,
                    format=d.format,
                    ticket_url=e.ticket_url,
                    regular_price=price,
                    price_confidence=confidence,
                )
            )
        return showtimes

    def _fetch_film_details(self, urls: set[str]) -> dict[str, FilmDetails]:
        details: dict[str, FilmDetails] = {}
        failures: list[str] = []
        for url in sorted(urls):
            if urlsplit(url).netloc.removeprefix("www.") != urlsplit(BASE_URL).netloc:
                continue  # off-site link; nothing of ours to enrich from
            try:
                details[url] = parse_film_page(self.fetcher.get_text(url))
            except Exception as e:  # one bad page shouldn't sink the venue
                log.warning("film page %s failed: %s", url, e)
                failures.append(url)
        if failures and len(failures) * 2 > len(urls):
            raise CinemathequeParseError(
                f"{len(failures)} of {len(urls)} film pages failed; first: {failures[0]}"
            )
        return details
