from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from kinolocator.schema import VANCOUVER, PriceConfidence, Showtime, normalize_format

SCRAPED_AT = datetime(2026, 10, 12, 23, 13, tzinfo=timezone.utc)


def make(**overrides):
    fields = dict(
        venue_id="cinematheque",
        venue_name="The Cinematheque",
        municipality="Vancouver",
        title="Harakiri",
        start=datetime(2026, 10, 12, 18, 0, tzinfo=VANCOUVER),
        scraped_at=SCRAPED_AT,
        original_language="ja",
        screening_language=["ja"],
        subtitles=["en"],
        format="35mm",
        ticket_url="https://example.com/t",
        regular_price=15.0,
        price_confidence=PriceConfidence.ESTIMATED,
    )
    fields.update(overrides)
    return Showtime(**fields)


def test_json_round_trip():
    s = make()
    d = json.loads(json.dumps(s.to_dict()))
    assert d["start"] == "2026-10-12T18:00:00-07:00"
    assert d["scraped_at"] == "2026-10-12T23:13:00+00:00"
    assert d["price_confidence"] == "estimated"
    assert d["tmdb_id"] is None
    assert Showtime.from_dict(d) == s


def test_start_is_converted_to_vancouver():
    s = make(start=datetime(2026, 10, 13, 1, 0, tzinfo=timezone.utc))
    assert s.start.isoformat() == "2026-10-12T18:00:00-07:00"


@pytest.mark.parametrize("field", ["start", "scraped_at"])
def test_naive_datetimes_rejected(field):
    with pytest.raises(ValueError, match="timezone-aware"):
        make(**{field: datetime(2026, 10, 12, 18, 0)})


def test_price_and_confidence_go_together():
    with pytest.raises(ValueError):
        make(regular_price=None)
    with pytest.raises(ValueError):
        make(price_confidence=None)
    s = make(regular_price=None, price_confidence=None)
    assert s.to_dict()["price_confidence"] is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("35mm", "35mm"), ("35MM", "35mm"), (" dcp ", "DCP"), ("Imax 70mm", "IMAX 70mm"),
     ("Super8", "Super 8"), ("", None), (None, None), ("Nitrate", "Nitrate")],
)
def test_normalize_format(raw, expected):
    assert normalize_format(raw) == expected
