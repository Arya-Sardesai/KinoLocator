"""Shared scraper for venues whose WordPress site runs the "Barker" cinema
plugin (the Rio Theatre; very likely the Park Theatre, same operator).

Barker exposes everything through the WordPress REST API, so no HTML parsing
is needed except for the venue's rate card:

* GET /wp-json/barker/v1/listings?start_date=YYYY-MM-DD&end_date=...&per_page=&page=
  -> one item per session, films and live events alike:
     {"event": {"id", "title", "link"}, "start_time": ISO-8601 with offset,
      "tickets_link", ...}. The post type shows in the permalink:
     /movie/<slug>/ (barker_movie) or /event/<slug>/ (barker_event).
* GET /wp-json/wp/v2/barker_movie?include=<ids>  (and barker_event)
  -> each post's `class_list` carries its tags ("barker-tag-film-event",
     "barker-tag-live-event") and, for movies, language terms
     ("barker-movie-language-english"); `content` is the description, which
     states prices for special events ("Adult Entry $62 + s/c").

Telling films from non-films (venues mix in comedy, music, burlesque, ...):

  movie post                               -> film
  event post tagged Film Event             -> film (even if also Live Event)
  event post tagged Live Event only        -> skipped: not a film
  event post with neither tag, or an       -> skipped *for review*: it may be
  unknown listing type                        a film (e.g. festival nights)

A decision in overrides.json, keyed by the post id, wins over all of these,
so a reviewed listing isn't flagged again on the next run.
"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, ClassVar
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..fetch import FetchError
from ..languages import language_code
from ..overrides import Override
from ..schema import VANCOUVER, PriceConfidence, Showtime
from .base import Scraper

log = logging.getLogger(__name__)

FILM_TAG = "film-event"
LIVE_TAG = "live-event"
MOVIE, EVENT = "movie", "event"
POST_ENDPOINTS = {MOVIE: "barker_movie", EVENT: "barker_event"}

_PRICE_LABEL = r"(?:General Admission|Adult Entry|Adult Admission|Regular Adult|Regular|Adult|General)"
PRICE_LINE = re.compile(
    rf"\b{_PRICE_LABEL}\b(?:\s*\([^)]*\))?\s*[:–—-]?\s*\$\s?(\d+(?:\.\d{{1,2}})?)", re.IGNORECASE
)
FREE_LINE = re.compile(r"\bfree (?:admission|entry|screening|event)\b", re.IGNORECASE)
STANDARD_PRICE = re.compile(r"Regular Adult\s*[:–—-]?\s*\$\s?(\d+(?:\.\d{1,2})?)", re.IGNORECASE)


class BarkerParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class Listing:
    post_id: int
    kind: str
    """"movie", "event", or whatever the permalink's first path segment is."""
    title: str
    url: str
    start: datetime
    ticket_url: str | None


@dataclass(frozen=True)
class Post:
    post_id: int
    tags: frozenset[str] = frozenset()
    languages: list[str] = field(default_factory=list)
    """ISO 639-1 codes of the film's spoken languages (movies only)."""
    price: float | None = None
    """Price stated in the description, if any."""


def parse_listings(text: str) -> list[Listing]:
    try:
        items = json.loads(text)
    except json.JSONDecodeError as e:
        raise BarkerParseError(f"listings response is not JSON: {e}") from e
    if not isinstance(items, list):
        raise BarkerParseError(f"listings response is a {type(items).__name__}, expected a list")
    listings = []
    for item in items:
        try:
            event = item["event"]
            url = event["link"]
            listings.append(
                Listing(
                    post_id=int(event["id"]),
                    kind=urlsplit(url).path.strip("/").split("/")[0],
                    title=html.unescape(event["title"]).strip(),
                    url=url,
                    start=datetime.fromisoformat(item["start_time"]),
                    ticket_url=item.get("tickets_link") or None,
                )
            )
        except (KeyError, TypeError, ValueError) as e:
            raise BarkerParseError(f"unexpected listing shape ({e!r}): {str(item)[:200]}") from e
    return listings


def parse_posts(text: str) -> dict[int, Post]:
    try:
        items = json.loads(text)
    except json.JSONDecodeError as e:
        raise BarkerParseError(f"posts response is not JSON: {e}") from e
    if not isinstance(items, list):
        raise BarkerParseError(f"posts response is a {type(items).__name__}, expected a list")
    posts = {}
    for item in items:
        classes = item.get("class_list") or []
        tags = frozenset(c.removeprefix("barker-tag-") for c in classes if c.startswith("barker-tag-"))
        languages = [
            code
            for c in classes
            if c.startswith("barker-movie-language-")
            and (code := language_code(c.removeprefix("barker-movie-language-").replace("-", " ")))
        ]
        content = (item.get("content") or {}).get("rendered", "")
        posts[int(item["id"])] = Post(int(item["id"]), tags, languages, parse_description_price(content))
    return posts


def parse_description_price(content_html: str) -> float | None:
    text = " ".join(BeautifulSoup(content_html, "html.parser").get_text(" ").split())
    if FREE_LINE.search(text):
        return 0.0
    m = PRICE_LINE.search(text)
    return float(m[1]) if m else None


def parse_standard_price(page_html: str) -> float | None:
    soup = BeautifulSoup(page_html, "html.parser")
    m = STANDARD_PRICE.search(" ".join((soup.find("main") or soup).get_text(" ").split()))
    return float(m[1]) if m else None


def classify(listing: Listing, post: Post | None, override: Override | None = None) -> tuple[bool | None, str]:
    """(True, why) = film; (False, why) = not a film; (None, why) = can't tell."""
    if override is not None:
        return override.film, override.describe()
    tags = post.tags if post else frozenset()
    if listing.kind == MOVIE:
        return True, "movie listing"
    if listing.kind == EVENT:
        if FILM_TAG in tags:
            return True, "event tagged Film Event"
        if LIVE_TAG in tags:
            return False, "event tagged Live Event (not a film)"
        return None, "event tagged neither Film Event nor Live Event"
    return None, f"unknown listing type {listing.kind!r}"


class BarkerScraper(Scraper):
    """Base for Barker venues: subclasses only set identity and `homepage`."""

    rates_path: ClassVar[str | None] = "/ticket-info/"
    lookahead_days: ClassVar[int] = 180
    per_page: ClassVar[int] = 100
    max_pages: ClassVar[int] = 20

    @property
    def api(self) -> str:
        return self.homepage.rstrip("/") + "/wp-json"

    def scrape(self) -> list[Showtime]:
        today = self.scraped_at.astimezone(VANCOUVER).date()
        listings = self._fetch_listings(today.isoformat(), (today + timedelta(days=self.lookahead_days)).isoformat())
        if not listings:
            return []
        posts = self._fetch_posts(listings)
        standard_price = self._fetch_standard_price()

        showtimes = []
        for listing in listings:
            post = posts.get(listing.post_id)
            key = str(listing.post_id)
            is_film, why = classify(listing, post, self.overrides.get(key))
            if not is_film:
                self.skip(
                    listing.title, why, start=listing.start, url=listing.url,
                    needs_review=is_film is None, key=key,
                )
                continue
            languages = post.languages if post else []
            if post and post.price is not None:
                price, confidence = post.price, PriceConfidence.SCRAPED
            elif standard_price is not None:
                price, confidence = standard_price, PriceConfidence.ESTIMATED
            else:
                price, confidence = None, None
            showtimes.append(
                self.showtime(
                    title=listing.title,
                    start=listing.start,
                    # Barker's language terms are the film's spoken languages,
                    # not necessarily one original; only claim it when unambiguous.
                    original_language=languages[0] if len(languages) == 1 else None,
                    screening_language=languages or None,
                    ticket_url=listing.ticket_url,
                    regular_price=price,
                    price_confidence=confidence,
                )
            )
        return showtimes

    def _fetch_listings(self, start: str, end: str) -> list[Listing]:
        listings: list[Listing] = []
        for page in range(1, self.max_pages + 1):
            url = (
                f"{self.api}/barker/v1/listings?start_date={start}&end_date={end}"
                f"&per_page={self.per_page}&page={page}"
            )
            batch = parse_listings(self.fetcher.get_text(url))
            listings.extend(batch)
            if len(batch) < self.per_page:
                return listings
        raise BarkerParseError(f"listings did not end after {self.max_pages} pages")

    def _fetch_posts(self, listings: list[Listing]) -> dict[int, Post]:
        posts: dict[int, Post] = {}
        for kind, endpoint in POST_ENDPOINTS.items():
            ids = sorted({x.post_id for x in listings if x.kind == kind})
            for i in range(0, len(ids), self.per_page):
                chunk = ",".join(map(str, ids[i : i + self.per_page]))
                url = (
                    f"{self.api}/wp/v2/{endpoint}?include={chunk}&per_page={self.per_page}"
                    f"&_fields=id,class_list,content"
                )
                posts.update(parse_posts(self.fetcher.get_text(url)))
        missing = {x.post_id for x in listings if x.kind in POST_ENDPOINTS} - posts.keys()
        if missing:
            log.warning("%s: no post data for listing ids %s", self.venue_id, sorted(missing))
        return posts

    def _fetch_standard_price(self) -> float | None:
        if not self.rates_path:
            return None
        url = self.homepage.rstrip("/") + self.rates_path
        try:
            price = parse_standard_price(self.fetcher.get_text(url))
        except FetchError as e:
            log.warning("%s: rate card %s unavailable (%s); prices left empty", self.venue_id, url, e)
            return None
        if price is None:
            log.warning("%s: no 'Regular Adult $' rate on %s; prices left empty", self.venue_id, url)
        return price
