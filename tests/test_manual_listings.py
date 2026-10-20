from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from kinolocator.manual_listings import (
    ManualListing,
    ManualListingsError,
    load_manual_listings,
    parse_manual_listings,
)
from kinolocator.schema import VANCOUVER
from kinolocator.venues import LISTINGS_LINK_OUT, listings_kind, load_venues

ROOT = Path(__file__).resolve().parent.parent
REPO_FILE = ROOT / "manual_listings.json"
VENUES = load_venues(ROOT / "web" / "venues.json")
TAKES_LISTINGS = {v for v, entry in VENUES.items() if listings_kind(entry) != LISTINGS_LINK_OUT}

GOOD = {
    "title": "Paris, Texas",
    "start": "2026-11-07T19:30:00-07:00",
    "source_url": "https://www.hollywoodtheatre.ca/events/paris-texas",
    "added": "2026-10-20",
}


def entry(**changes):
    e = dict(GOOD, **changes)
    return {k: v for k, v in e.items() if v is not ...}


def test_parse():
    data = {
        "_about": "comments are ignored",
        "_example": {"nope": []},
        "hollywood_theatre": [entry(price=15, note="35mm"), entry(title="Free Night", price=0)],
        "norm_theatre": [],
    }
    start = datetime(2026, 11, 7, 19, 30, tzinfo=VANCOUVER)
    url = GOOD["source_url"]
    assert parse_manual_listings(data, TAKES_LISTINGS) == {
        "hollywood_theatre": [
            ManualListing("Paris, Texas", start, url, date(2026, 10, 20), 15.0, "35mm"),
            ManualListing("Free Night", start, url, date(2026, 10, 20), 0.0),
        ],
        "norm_theatre": [],
    }


def test_offsets_before_the_change_are_accepted():
    # BC's last switch back to UTC-8 was 2025-11-02; from 2026-11-01 it stays at -07:00.
    [listing] = parse_manual_listings({"hollywood_theatre": [entry(start="2025-12-01T19:00-08:00")]})["hollywood_theatre"]
    assert listing.start.utcoffset().total_seconds() == -8 * 3600


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "must be a JSON object"),
        ({"hollywood": []}, "unknown venue 'hollywood'"),
        # Cineplex is link-out only: its times are never copied, not even by hand.
        ({"cineplex_scotiabank_vancouver": [entry()]}, "unknown venue 'cineplex_scotiabank_vancouver' (or one that is link-out only)"),
        ({"hollywood_theatre": {}}, "expected a list of listings"),
        ({"hollywood_theatre": ["Paris, Texas"]}, "hollywood_theatre[0]: expected an object"),
        ({"hollywood_theatre": [entry(source_url=...)]}, "missing required field(s) ['source_url']"),
        ({"hollywood_theatre": [entry(added=...)]}, "missing required field(s) ['added']"),
        ({"hollywood_theatre": [entry(ticket_url="https://x")]}, "unknown field(s) ['ticket_url']"),
        ({"hollywood_theatre": [entry(title="  ")]}, '"title" must be a non-empty string'),
        ({"hollywood_theatre": [entry(start="2026-11-07T19:30:00")]}, '"start" must look like'),
        ({"hollywood_theatre": [entry(start="2026-11-07T02:30:00Z")]}, '"start" must look like'),
        ({"hollywood_theatre": [entry(start="2026-11-07 19:30-07:00")]}, '"start" must look like'),
        (
            {"hollywood_theatre": [entry(start="2026-11-07T19:30:00-08:00")]},
            "has offset -08:00, but Vancouver is at -07:00 then (UTC-7 year-round from 2026-11-01); "
            "write it as 2026-11-07T19:30:00-07:00",
        ),
        ({"hollywood_theatre": [entry(source_url="hollywoodtheatre.ca")]}, '"source_url" must be an http(s) URL'),
        ({"hollywood_theatre": [entry(source_url="javascript:alert(1)")]}, '"source_url" must be an http(s) URL'),
        ({"hollywood_theatre": [entry(added="20 Oct 2026")]}, '"added" must be a date'),
        ({"hollywood_theatre": [entry(added="2026-02-30")]}, '"added" must be a date'),
        ({"hollywood_theatre": [entry(price="$15")]}, '"price" must be a number'),
        ({"hollywood_theatre": [entry(price=-1)]}, '"price" must be a number'),
        ({"hollywood_theatre": [entry(price=True)]}, '"price" must be a number'),
        ({"hollywood_theatre": [entry(note=3)]}, '"note" must be a string'),
        ({"hollywood_theatre": [entry(), entry(note="dup")]}, "Paris, Texas at 2026-11-07T19:30:00-07:00 is listed twice"),
    ],
)
def test_parse_rejects_mistakes(data, message):
    with pytest.raises(ManualListingsError) as e:
        parse_manual_listings(data, TAKES_LISTINGS)
    assert message in str(e.value)


def test_errors_name_the_listing():
    with pytest.raises(ManualListingsError, match=r"hollywood_theatre\[1\] \(Second\): \"added\""):
        parse_manual_listings({"hollywood_theatre": [entry(), entry(title="Second", added="soon")]})


def test_load_missing_file_means_no_listings(tmp_path):
    assert load_manual_listings(tmp_path / "manual_listings.json") == {}


def test_load_invalid_json_names_the_file(tmp_path):
    path = tmp_path / "manual_listings.json"
    path.write_text('{"hollywood_theatre": [],}')
    with pytest.raises(ManualListingsError, match="manual_listings.json: invalid JSON"):
        load_manual_listings(path)


def test_repo_file_is_valid():
    # The scheduled run refuses to start on a broken file; catch it here first.
    listings = load_manual_listings(REPO_FILE, TAKES_LISTINGS)
    assert {"hollywood_theatre", "norm_theatre"} <= set(listings)


def test_repo_file_example_is_valid():
    # The "_example" block is there to be copied, so it must pass validation itself.
    example = json.loads(REPO_FILE.read_text())["_example"]
    assert parse_manual_listings(example, TAKES_LISTINGS)

