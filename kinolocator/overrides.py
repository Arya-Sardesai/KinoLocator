"""Manual film / not-a-film decisions for listings a scraper can't classify.

overrides.json (repo root) maps venue id -> listing key -> decision:

    {
      "rio_theatre": {
        "350890": {"film": true, "title": "VIMFF 2026: Reel Rock 20 Vancouver",
                   "note": "mountain film festival programme"}
      }
    }

The listing key is whatever the venue's scraper uses to identify a listing
across runs (for Barker venues, the WordPress post id, shared by every session
of that post). It is shown with each skipped listing that needs review.
"film" is required; "title" (for humans reading the file) and "note" are
optional. An override wins over the scraper's own classification. Top-level
keys starting with "_" are ignored, for comments.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_FILENAME = "overrides.json"
_FIELDS = {"film", "title", "note"}


class OverridesError(ValueError):
    pass


@dataclass(frozen=True)
class Override:
    film: bool
    title: str | None = None
    note: str | None = None

    def describe(self) -> str:
        what = "film" if self.film else "not a film"
        return f"manual override: {what}" + (f" ({self.note})" if self.note else "")


Overrides = dict[str, dict[str, Override]]


def parse_overrides(data: Any, known_venues: Iterable[str] | None = None) -> Overrides:
    if not isinstance(data, dict):
        raise OverridesError("overrides must be a JSON object of venue id -> {listing key: decision}")
    known = set(known_venues) if known_venues is not None else None
    result: Overrides = {}
    for venue_id, entries in data.items():
        if venue_id.startswith("_"):
            continue
        if known is not None and venue_id not in known:
            raise OverridesError(f"unknown venue {venue_id!r}; known: {', '.join(sorted(known))}")
        if not isinstance(entries, dict):
            raise OverridesError(f"{venue_id}: expected an object of listing key -> decision")
        venue: dict[str, Override] = {}
        for key, entry in entries.items():
            where = f"{venue_id}/{key}"
            if not isinstance(entry, dict):
                raise OverridesError(f"{where}: expected an object like {{\"film\": true}}")
            if extra := set(entry) - _FIELDS:
                raise OverridesError(f"{where}: unknown field(s) {sorted(extra)}; allowed: {sorted(_FIELDS)}")
            if not isinstance(entry.get("film"), bool):
                raise OverridesError(f"{where}: \"film\" must be true or false")
            for field in ("title", "note"):
                if entry.get(field) is not None and not isinstance(entry[field], str):
                    raise OverridesError(f"{where}: \"{field}\" must be a string")
            venue[str(key)] = Override(entry["film"], entry.get("title"), entry.get("note"))
        result[venue_id] = venue
    return result


def load_overrides(path: Path, known_venues: Iterable[str] | None = None) -> Overrides:
    """Read and validate an overrides file; a missing file means no overrides."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as e:
        raise OverridesError(f"{path}: invalid JSON: {e}") from e
    try:
        return parse_overrides(data, known_venues)
    except OverridesError as e:
        raise OverridesError(f"{path}: {e}") from e
