"""Hand-entered showtimes for venues no scraper covers.

manual_listings.json (repo root) maps venue id -> list of listings:

    {
      "hollywood_theatre": [
        {"title": "Paris, Texas", "start": "2026-10-24T19:30:00-07:00",
         "source_url": "https://www.hollywoodtheatre.ca/events/paris-texas",
         "added": "2026-10-20", "price": 15, "note": "35mm print"}
      ]
    }

"title", "start", "source_url" and "added" are required; "price" (regular
adult admission in CAD, as the source states it) and "note" are optional.
"start" is the Vancouver wall-clock time with its UTC offset, and the offset
must be the one Vancouver actually uses then (UTC-7 year-round from
2026-11-01), so a stale "-08:00" is caught. "added" is the date the entry
was typed in. Top-level keys starting with "_" are ignored, for comments.

The page reads this file directly (Netlify copies it next to showtimes.json),
shows each listing as "hand-entered", and hides it once it has started, so
past entries drop off without editing the file. Nothing here goes into
showtimes.json or the archive, which record only what scrapers observed.

Venue ids must be in web/venues.json and must not be link-out venues
(Cineplex): their times are never copied.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from .schema import VANCOUVER

DEFAULT_FILENAME = "manual_listings.json"
_REQUIRED = ("title", "start", "source_url", "added")
_FIELDS = set(_REQUIRED) | {"price", "note"}
# The form the page's parser reads: date, "T", hours:minutes[:seconds], offset.
_START = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?[+-]\d{2}:\d{2}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class ManualListingsError(ValueError):
    pass


@dataclass(frozen=True)
class ManualListing:
    title: str
    start: datetime
    source_url: str
    added: date
    price: float | None = None
    note: str | None = None


ManualListings = dict[str, list[ManualListing]]


def _offset(dt: datetime) -> str:
    minutes = int(dt.utcoffset().total_seconds() // 60)
    sign = "-" if minutes < 0 else "+"
    return f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def _parse_entry(where: str, entry: Any) -> ManualListing:
    if not isinstance(entry, dict):
        raise ManualListingsError(f"{where}: expected an object like {{\"title\": ..., \"start\": ...}}")
    if extra := set(entry) - _FIELDS:
        raise ManualListingsError(f"{where}: unknown field(s) {sorted(extra)}; allowed: {sorted(_FIELDS)}")
    if missing := [f for f in _REQUIRED if f not in entry]:
        raise ManualListingsError(f"{where}: missing required field(s) {missing}")

    title = entry["title"]
    if not isinstance(title, str) or not title.strip():
        raise ManualListingsError(f"{where}: \"title\" must be a non-empty string")
    where = f"{where} ({title})"

    raw = entry["start"]
    if not isinstance(raw, str) or not _START.fullmatch(raw):
        raise ManualListingsError(
            f"{where}: \"start\" must look like \"2026-10-24T19:30:00-07:00\" (Vancouver time with its offset), got {raw!r}"
        )
    start = datetime.fromisoformat(raw)
    expected = start.astimezone(VANCOUVER)
    if start.utcoffset() != expected.utcoffset():
        raise ManualListingsError(
            f"{where}: \"start\" {raw} has offset {_offset(start)}, but Vancouver is at {_offset(expected)} then "
            f"(UTC-7 year-round from 2026-11-01); write it as {start.replace(tzinfo=None).isoformat()}{_offset(expected)}"
        )

    url = entry["source_url"]
    parts = urlsplit(url) if isinstance(url, str) else None
    if not parts or parts.scheme not in ("http", "https") or not parts.netloc:
        raise ManualListingsError(f"{where}: \"source_url\" must be an http(s) URL, got {url!r}")

    added = entry["added"]
    try:
        if not isinstance(added, str) or not _DATE.fullmatch(added):
            raise ValueError
        added_date = date.fromisoformat(added)
    except ValueError:
        raise ManualListingsError(f"{where}: \"added\" must be a date like \"2026-10-20\", got {added!r}") from None

    price = entry.get("price")
    if price is not None and (isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0):
        raise ManualListingsError(f"{where}: \"price\" must be a number of dollars (0 for free), got {price!r}")

    note = entry.get("note")
    if note is not None and not isinstance(note, str):
        raise ManualListingsError(f"{where}: \"note\" must be a string")

    return ManualListing(title, start, url, added_date, None if price is None else float(price), note)


def parse_manual_listings(data: Any, known_venues: Iterable[str] | None = None) -> ManualListings:
    """Validate manual_listings.json's content. `known_venues`: ids that may take listings."""
    if not isinstance(data, dict):
        raise ManualListingsError("manual listings must be a JSON object of venue id -> [listing, ...]")
    known = set(known_venues) if known_venues is not None else None
    result: ManualListings = {}
    for venue_id, entries in data.items():
        if venue_id.startswith("_"):
            continue
        if known is not None and venue_id not in known:
            raise ManualListingsError(
                f"unknown venue {venue_id!r} (or one that is link-out only); "
                f"venues that take hand-entered listings: {', '.join(sorted(known))}"
            )
        if not isinstance(entries, list):
            raise ManualListingsError(f"{venue_id}: expected a list of listings")
        listings = [_parse_entry(f"{venue_id}[{i}]", entry) for i, entry in enumerate(entries)]
        seen: set[tuple[str, datetime]] = set()
        for listing in listings:
            key = (listing.title, listing.start)
            if key in seen:
                raise ManualListingsError(f"{venue_id}: {listing.title} at {listing.start.isoformat()} is listed twice")
            seen.add(key)
        result[venue_id] = listings
    return result


def load_manual_listings(path: Path, known_venues: Iterable[str] | None = None) -> ManualListings:
    """Read and validate a manual listings file; a missing file means none."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as e:
        raise ManualListingsError(f"{path}: invalid JSON: {e}") from e
    try:
        return parse_manual_listings(data, known_venues)
    except ManualListingsError as e:
        raise ManualListingsError(f"{path}: {e}") from e
