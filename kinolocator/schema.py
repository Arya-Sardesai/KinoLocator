"""The Showtime record every scraper produces, and its JSON form."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

VANCOUVER = ZoneInfo("America/Vancouver")

# Canonical spellings for projection formats. Scrapers should normalise to one
# of these via normalize_format(); anything unrecognised is kept verbatim.
KNOWN_FORMATS = (
    "35mm",
    "70mm",
    "16mm",
    "8mm",
    "Super 8",
    "DCP",
    "IMAX",
    "IMAX 70mm",
    "Digital",
    "Video",
)

_FORMAT_LOOKUP = {f.lower().replace(" ", ""): f for f in KNOWN_FORMATS}


# Formats as venues write them in titles and labels ("Presented in 70mm",
# "35mm | Pantheon"). IMAX 70mm before 70mm, Super 8 before 8mm.
FORMAT_PATTERN = re.compile(
    r"\b(IMAX[\s-]*70\s*mm|70\s*mm|35\s*mm|16\s*mm|Super[\s-]*8|8\s*mm|IMAX|DCP)\b", re.IGNORECASE
)


def find_format(text: str | None) -> str | None:
    """The first projection format named in `text`, normalised, or None."""
    m = FORMAT_PATTERN.search(text or "")
    return normalize_format(m[1].replace("-", " ")) if m else None


def normalize_format(raw: str | None) -> str | None:
    if raw is None:
        return None
    raw = " ".join(raw.split())
    if not raw:
        return None
    return _FORMAT_LOOKUP.get(raw.lower().replace(" ", ""), raw)


class PriceConfidence(str, Enum):
    """How much to trust Showtime.regular_price.

    SCRAPED: the venue stated this price for this specific screening.
    ESTIMATED: derived from the venue's general rate card or a default.
    """

    SCRAPED = "scraped"
    ESTIMATED = "estimated"


@dataclass(frozen=True)
class Showtime:
    venue_id: str
    venue_name: str
    municipality: str
    title: str
    """Film title exactly as the venue lists it."""
    start: datetime
    """Timezone-aware; stored in America/Vancouver."""
    scraped_at: datetime
    """Timezone-aware UTC timestamp of when the data was fetched."""
    tmdb_id: int | None = None
    original_title: str | None = None
    """Title in the film's original script (e.g. "கல்கி 2898 AD") when it differs
    from `title`. None = unknown; reserved for TMDB matching, no scraper sets it yet."""
    original_language: str | None = None
    """ISO 639-1 code of the film's original language (TMDB convention, "xx" = no language)."""
    screening_language: list[str] | None = None
    """ISO 639-1 codes of the audio as screened. None = unknown."""
    subtitles: list[str] | None = None
    """ISO 639-1 codes of subtitle tracks. [] = known to have none, None = unknown."""
    format: str | None = None
    """Projection format, e.g. "35mm", "70mm", "DCP", "IMAX". See KNOWN_FORMATS."""
    screen: str | None = None
    """Auditorium within the venue as the venue names it, e.g. "Lochmaddy Studio
    Theatre". The venue is still one venue; None = single screen or not stated."""
    ticket_url: str | None = None
    regular_price: float | None = None
    """Regular adult admission in CAD."""
    price_confidence: PriceConfidence | None = None

    def __post_init__(self) -> None:
        for name in ("start", "scraped_at"):
            value = getattr(self, name)
            if not isinstance(value, datetime):
                raise TypeError(f"{name} must be a datetime, got {type(value).__name__}")
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware, got naive {value!r}")
        # Normalise so every serialized start carries Vancouver's offset.
        object.__setattr__(self, "start", self.start.astimezone(VANCOUVER))
        if (self.regular_price is None) != (self.price_confidence is None):
            raise ValueError("regular_price and price_confidence must both be set or both be None")
        if self.price_confidence is not None:
            object.__setattr__(self, "price_confidence", PriceConfidence(self.price_confidence))

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["start"] = self.start.isoformat()
        d["scraped_at"] = self.scraped_at.isoformat()
        d["price_confidence"] = self.price_confidence.value if self.price_confidence else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Showtime:
        d = dict(d)
        d["start"] = datetime.fromisoformat(d["start"])
        d["scraped_at"] = datetime.fromisoformat(d["scraped_at"])
        if d.get("price_confidence") is not None:
            d["price_confidence"] = PriceConfidence(d["price_confidence"])
        return cls(**d)

    @property
    def sort_key(self) -> tuple[datetime, str, str]:
        return (self.start, self.venue_id, self.title)


@dataclass(frozen=True)
class Skipped:
    """A listing a scraper deliberately did not turn into a Showtime.

    Recorded in the snapshot so non-film events, rentals etc. are dropped
    visibly. `needs_review` marks ones the scraper couldn't classify with
    confidence (they might be films); the run surfaces those as warnings.
    """

    title: str
    reason: str
    start: datetime | None = None
    url: str | None = None
    needs_review: bool = False
    key: str | None = None
    """The venue's stable id for this listing, used in overrides.json."""

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["start"] = self.start.astimezone(VANCOUVER).isoformat() if self.start else None
        return d
