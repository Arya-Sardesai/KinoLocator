"""VIFF Centre, 1181 Seymour St, Vancouver — https://viff.org

Site structure (checked 2026-10-19). WordPress; ticketing is an Elevent widget
(content.elevent.app) that this scraper does not use.

* /whats-on-calendar/ embeds `wpApiSettings` (the public REST token every
  visitor's browser gets) and `calendar_venues` (resource id -> name, e.g.
  "viff-centre-lochmaddy-studio-theatre" -> "VIFF Centre - Lochmaddy Studio
  Theatre"). robots.txt only disallows /admin/.
* GET /wp-json/v1/attendable/calendar/instances with header X-WP-Nonce: <token>
  -> every upcoming screening (no `dates` parameter: with one it returns a
  single day). Each item has `resourceId` (the screen), `eventType` ("Film",
  "VIFF Live", ...) and `title`, an HTML card holding <time datetime> with the
  Vancouver offset, the title, the event page link and, unless sold out, a
  /book/ link. Without a valid token it answers 403; that fails the venue, so
  the runner falls back to the last good data.
* Each event page (/whats-on/<slug>/) has a labelled "Language" field ("In
  Japanese with English subtitles", or just "English"), the format in the
  masthead ("35mm", "35mm | Pantheon"), one schema.org Event per screening in
  JSON-LD (startDate + location with street address), per-screening tags
  (`.c-event-instance__access`, e.g. "First Look Fridays") and price cards for
  tagged screenings ("First Look Fridays: $12 Tickets").
* /ticket-info/ has the rate card as a table, screens as columns: Adult $17 in
  the VIFF Cinema, $15 in the Lochmaddy Studio Theatre. Prices exclude GST and
  are stored as listed.

Only screenings at VIFF Centre become showtimes: the calendar venue must be
"VIFF Centre - <screen>" and, where the event page gives the screening's
address, it must be 1181 Seymour. Festival and partner screenings elsewhere
are skipped with the place in the reason. Calendar type "Film" is a film;
any other type ("VIFF Live", talks, ...) is flagged for review unless decided
in overrides.json, keyed by VIFF's event id.
"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from ..fetch import FetchError
from ..languages import parse_language_line
from ..overrides import Override
from ..schema import VANCOUVER, PriceConfidence, Showtime, find_format
from .base import Scraper

log = logging.getLogger(__name__)

BASE_URL = "https://viff.org"
CALENDAR_PAGE_URL = f"{BASE_URL}/whats-on-calendar/"
INSTANCES_URL = f"{BASE_URL}/wp-json/v1/attendable/calendar/instances"
RATES_URL = f"{BASE_URL}/ticket-info/"

VENUE = "VIFF Centre"
STREET = "1181 Seymour"
FILM_TYPE = "Film"
EVENT_PAGE = re.compile(r"^https://viff\.org/whats-on/[^/]+/$")
TAG_PRICE = re.compile(r"^(?P<tag>.+?):\s*\$\s?(?P<price>\d+(?:\.\d{1,2})?)\s+Tickets?$", re.IGNORECASE)
PRICE = re.compile(r"^\$\s?(\d+(?:\.\d{1,2})?)$")


class ViffParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class CalendarPage:
    token: str
    screens: dict[str, str]
    """Calendar resource id -> name as VIFF lists it ("VIFF Centre - VIFF Cinema")."""


@dataclass(frozen=True)
class Instance:
    instance_id: str
    event_id: str
    title: str
    start: datetime
    resource_id: str
    event_type: str
    event_url: str | None
    book_url: str | None


@dataclass(frozen=True)
class EventPage:
    languages: list[str] | None = None
    subtitles: list[str] | None = None
    format: str | None = None
    locations: dict[str, tuple[str, str]] = field(default_factory=dict)
    """Screening start (ISO, with offset) -> (place name, street address) from JSON-LD."""
    instance_tags: dict[str, frozenset[str]] = field(default_factory=dict)
    """Instance id -> tags shown on that screening ("First Look Fridays", ...)."""
    tag_prices: dict[str, float] = field(default_factory=dict)
    """Tag -> price stated for screenings with it ("First Look Fridays: $12 Tickets")."""


def _json_assignment(page: str, name: str) -> Any:
    m = re.search(rf"\b{re.escape(name)}\s*=\s*(\{{.*?\}})\s*;", page)
    if not m:
        return None
    try:
        return json.loads(m[1])
    except json.JSONDecodeError:
        return None


def parse_calendar_page(page: str) -> CalendarPage:
    settings = _json_assignment(page, "wpApiSettings")
    token = settings.get("nonce") if isinstance(settings, dict) else None
    if not token:
        raise ViffParseError("calendar page has no wpApiSettings nonce; can't request the calendar")
    venues = _json_assignment(page, "calendar_venues")
    try:
        screens = {v["id"]: html.unescape(v["title"]) for v in venues["venues"]}
    except (KeyError, TypeError) as e:
        raise ViffParseError(f"calendar page has no usable `calendar_venues` list ({e!r})") from e
    if not screens:
        raise ViffParseError("calendar page lists no venues")
    return CalendarPage(token, screens)


def parse_instances(text: str) -> list[Instance]:
    try:
        items = json.loads(text)
    except json.JSONDecodeError as e:
        raise ViffParseError(f"instances response is not JSON: {e}") from e
    if not isinstance(items, list):
        raise ViffParseError(f"instances response is a {type(items).__name__}, expected a list")
    instances = []
    for item in items:
        try:
            card = BeautifulSoup(item["title"], "html.parser")
            ids = card.select_one("[data-instanceid]")
            title = card.select_one(".c-calendar-instance__title")
            when = card.select_one("time[datetime]")
            if ids is None or title is None:
                raise ValueError("card has no instance id or title")
            if when is not None:
                start = datetime.fromisoformat(when["datetime"])
            else:  # the card normally carries the offset; the bare field doesn't
                start = datetime.fromisoformat(item["start"]).replace(tzinfo=VANCOUVER)
            links = [a["href"] for a in card.find_all("a", href=True)]
            instances.append(
                Instance(
                    instance_id=ids["data-instanceid"],
                    event_id=ids["data-eventid"],
                    title=" ".join(title.get_text(" ").split()),
                    start=start,
                    resource_id=item["resourceId"],
                    event_type=item.get("eventType") or "",
                    event_url=next((u for u in links if EVENT_PAGE.match(u)), None),
                    book_url=next((u for u in links if "/book/" in u), None),
                )
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ViffParseError(f"unexpected calendar instance ({e!r}): {str(item)[:200]}") from e
    return instances


def parse_event_page(page: str) -> EventPage:
    soup = BeautifulSoup(page, "html.parser")
    if soup.select_one("h1.c-masthead__title") is None:
        raise ViffParseError("event page has no masthead title; page layout changed?")

    languages = subtitles = None
    for label in soup.select(".c-event__details-title"):
        if label.get_text(strip=True).lower() != "language":
            continue
        value = label.find_next_sibling(class_="c-event__details-details")
        line = value.get_text(" ", strip=True) if value else ""
        parsed = parse_language_line(line, require_in=False)
        if parsed:
            languages, subtitles = parsed
        elif line:
            log.warning("viff_centre: unrecognised language line %r", line)
        break

    masthead = soup.select_one(".c-masthead__content")
    fmt = find_format(masthead.get_text(" ") if masthead else None)

    locations: dict[str, tuple[str, str]] = {}
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        for ev in data if isinstance(data, list) else [data]:
            if not isinstance(ev, dict) or ev.get("@type") != "Event" or not ev.get("startDate"):
                continue
            place = ev.get("location") or {}
            street = (place.get("address") or {}).get("streetAddress", "")
            start = datetime.fromisoformat(ev["startDate"]).isoformat()
            locations[start] = (place.get("name", ""), street)

    instance_tags = {}
    for row in soup.select(".c-event-instance"):
        ids = row.select_one("[data-instanceid]")
        if ids is not None:
            tags = {t.get_text(" ", strip=True) for t in row.select(".c-event-instance__access .access")}
            instance_tags[ids["data-instanceid"]] = frozenset(t for t in tags if t)

    tag_prices = {}
    for card in soup.select(".c-col-card__title"):
        m = TAG_PRICE.match(" ".join(card.get_text(" ").split()))
        if m:
            tag_prices[m["tag"]] = float(m["price"])

    return EventPage(languages, subtitles, fmt, locations, instance_tags, tag_prices)


def parse_rate_card(page: str) -> dict[str, float]:
    """Screen name -> adult price, from the /ticket-info/ table whose rows are
    ticket types and whose columns are screens."""
    soup = BeautifulSoup(page, "html.parser")
    for table in soup.find_all("table"):
        rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])] for tr in table.find_all("tr")]
        if not rows or not any(r and r[0].lower() == "adult" for r in rows):
            continue
        screens = rows[0][1:]
        adult = next(r for r in rows if r and r[0].lower() == "adult")[1:]
        prices = {}
        for screen, cell in zip(screens, adult):
            m = PRICE.match(cell)
            if screen and m:
                prices[screen] = float(m[1])
        return prices
    return {}


def classify(instance: Instance, override: Override | None = None) -> tuple[bool | None, str]:
    """(True, why) = film; (None, why) = can't tell, needs review."""
    if override is not None:
        return override.film, override.describe()
    if instance.event_type == FILM_TYPE:
        return True, "calendar type Film"
    return None, f"calendar type {instance.event_type or '(none)'!r}, not Film"


class ViffCentre(Scraper):
    venue_id = "viff_centre"
    venue_name = "VIFF Centre"
    municipality = "Vancouver"
    homepage = BASE_URL

    def scrape(self) -> list[Showtime]:
        calendar = parse_calendar_page(self.fetcher.get_text(CALENDAR_PAGE_URL))
        instances = parse_instances(self.fetcher.get_text(INSTANCES_URL, headers={"X-WP-Nonce": calendar.token}))
        if not instances:
            return []
        rates = self._fetch_rates()

        at_venue: list[tuple[Instance, str]] = []
        for inst in instances:
            listed = calendar.screens.get(inst.resource_id)
            venue, _, screen = (listed or "").partition(" - ")
            if listed is None:
                self.skip(inst.title, f"calendar venue {inst.resource_id!r} isn't in the calendar's venue list",
                          start=inst.start, url=inst.event_url, key=inst.event_id)
            elif venue.strip() != VENUE:
                self.skip(inst.title, f"at {listed}, not VIFF Centre", start=inst.start, url=inst.event_url,
                          key=inst.event_id)
            else:
                at_venue.append((inst, screen.strip()))

        pages = {url: parse_event_page(self.fetcher.get_text(url))
                 for url in sorted({i.event_url for i, _ in at_venue if i.event_url})}

        showtimes = []
        for inst, screen in at_venue:
            page = pages.get(inst.event_url, EventPage())
            place = page.locations.get(inst.start.isoformat())
            if place and STREET.lower() not in place[1].lower():
                self.skip(inst.title, f"event page places it at {place[0]}, {place[1]}, not VIFF Centre",
                          start=inst.start, url=inst.event_url, key=inst.event_id)
                continue
            is_film, why = classify(inst, self.overrides.get(inst.event_id))
            if not is_film:
                self.skip(inst.title, why, start=inst.start, url=inst.event_url,
                          needs_review=is_film is None, key=inst.event_id)
                continue

            stated = [page.tag_prices[t] for t in page.instance_tags.get(inst.instance_id, ()) if t in page.tag_prices]
            if stated:
                price, confidence = min(stated), PriceConfidence.SCRAPED
            elif screen in rates:
                price, confidence = rates[screen], PriceConfidence.ESTIMATED
            else:
                price, confidence = None, None
            languages = page.languages
            showtimes.append(
                self.showtime(
                    title=inst.title,
                    start=inst.start,
                    original_language=languages[0] if languages and len(languages) == 1 else None,
                    screening_language=languages,
                    subtitles=page.subtitles,
                    format=page.format,
                    screen=screen or None,
                    ticket_url=inst.book_url or inst.event_url,
                    regular_price=price,
                    price_confidence=confidence,
                )
            )
        return showtimes

    def _fetch_rates(self) -> dict[str, float]:
        try:
            rates = parse_rate_card(self.fetcher.get_text(RATES_URL))
        except FetchError as e:
            log.warning("%s: rate card %s unavailable (%s); prices left empty", self.venue_id, RATES_URL, e)
            return {}
        if not rates:
            log.warning("%s: no Adult row in the %s table; prices left empty", self.venue_id, RATES_URL)
        return rates
