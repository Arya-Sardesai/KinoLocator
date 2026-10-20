"""web/venues.json: the page's venue list."""

from __future__ import annotations

import json
from pathlib import Path

from kinolocator.scrapers import all_scrapers
from kinolocator.venues import (
    LISTINGS_HAND_ENTERED,
    LISTINGS_KINDS,
    LISTINGS_LINK_OUT,
    LISTINGS_SCRAPED,
    listings_kind,
    load_venues,
)

PATH = Path(__file__).resolve().parent.parent / "web" / "venues.json"
VENUES = load_venues(PATH)
# Link-out venues, never scraped or copied: the Cineplex and Landmark
# locations (their sites don't allow it) and the Caprice, each linking to its
# own showtimes page.
LINK_OUT = {
    "cineplex_scotiabank_vancouver": "https://www.cineplex.com/theatre/",
    "cineplex_fifth_avenue": "https://www.cineplex.com/theatre/",
    "cineplex_international_village": "https://www.cineplex.com/theatre/",
    "cineplex_marine_gateway": "https://www.cineplex.com/theatre/",
    "cineplex_metropolis": "https://www.cineplex.com/theatre/",
    "cineplex_brentwood": "https://www.cineplex.com/theatre/",
    "cineplex_riverport": "https://www.cineplex.com/theatre/",
    "cineplex_strawberry_hill": "https://www.cineplex.com/theatre/",
    "cineplex_coquitlam": "https://www.cineplex.com/theatre/",
    "cineplex_langley": "https://www.cineplex.com/theatre/",
    "cineplex_park_royal": "https://www.cineplex.com/theatre/",
    "cineplex_meadowtown": "https://www.cineplex.com/theatre/",
    "landmark_new_westminster": "https://www.landmarkcinemas.com/showtimes/",
    "landmark_guildford": "https://www.landmarkcinemas.com/showtimes/",
    "caprice_white_rock": "https://hollywoodcinemas.ca/",
}


def test_ids_are_unique():
    ids = [v["id"] for v in json.loads(PATH.read_text())["venues"]]
    assert len(ids) == len(set(ids)) == len(VENUES)


def test_every_venue_is_placed_in_greater_vancouver():
    for venue_id, v in VENUES.items():
        assert v["name"] and v["short_name"] and v["neighbourhood"] and v["address"], venue_id
        assert 49.0 < v["lat"] < 49.45 and -123.35 < v["lng"] < -122.5, venue_id
        assert v["url"].startswith("https://"), venue_id


def test_listings_kinds():
    for venue_id, v in VENUES.items():
        assert listings_kind(v) in LISTINGS_KINDS, venue_id
        assert v.get("listings") != LISTINGS_SCRAPED, f"{venue_id}: leave 'listings' out for scraped venues"


def test_every_scraper_has_a_scraped_venue_and_back():
    scraped = {v for v, entry in VENUES.items() if listings_kind(entry) == LISTINGS_SCRAPED}
    assert scraped == set(all_scrapers())


def test_hand_entered_venues_have_no_scraper():
    hand = {v for v, entry in VENUES.items() if listings_kind(entry) == LISTINGS_HAND_ENTERED}
    assert hand == {"hollywood_theatre", "norm_theatre"}
    assert not hand & set(all_scrapers())


def test_link_out_venues_only_link_to_their_own_page():
    link_out = {v: entry for v, entry in VENUES.items() if listings_kind(entry) == LISTINGS_LINK_OUT}
    assert set(link_out) == set(LINK_OUT)
    for venue_id, v in link_out.items():
        assert v["showtimes_url"].startswith(LINK_OUT[venue_id]), venue_id
        assert not set(v) & {"prices_exclude_gst"}, venue_id
    assert all("showtimes_url" not in v for vid, v in VENUES.items() if vid not in link_out)
