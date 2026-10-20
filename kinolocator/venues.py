"""web/venues.json: every venue the page knows, and how its listings arrive.

Each venue's "listings" field says where its times come from:

- absent or "scraped": a scraper in kinolocator/scrapers/ (venue id = scraper id)
- "hand_entered": no scraper; times are typed into manual_listings.json
- "link_out": not scraped and never copied. The page shows a hollow dot that
  links to the venue's own showtimes page ("showtimes_url"). Used for the
  Cineplex locations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("web") / "venues.json"

LISTINGS_SCRAPED = "scraped"
LISTINGS_HAND_ENTERED = "hand_entered"
LISTINGS_LINK_OUT = "link_out"
LISTINGS_KINDS = (LISTINGS_SCRAPED, LISTINGS_HAND_ENTERED, LISTINGS_LINK_OUT)


def listings_kind(venue: dict[str, Any]) -> str:
    return venue.get("listings", LISTINGS_SCRAPED)


def load_venues(path: Path) -> dict[str, dict[str, Any]]:
    """venue id -> entry, in file order."""
    data = json.loads(path.read_text("utf-8"))
    return {v["id"]: v for v in data["venues"]}
