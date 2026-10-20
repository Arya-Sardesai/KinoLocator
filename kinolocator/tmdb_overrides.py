"""Hand-set TMDB matches: force a listing to a film, or keep it unmatched.

tmdb_overrides.json (repo root) maps venue id (or "*" for every venue) ->
title exactly as the venue lists it -> decision:

    {
      "viff_centre": {
        "Atonement": {"tmdb_id": 1234567, "note": "the 2026 short, not Joe Wright's"}
      },
      "*": {
        "Shrek Cabaret": {"tmdb_id": null, "note": "a live show around the film"}
      }
    }

"tmdb_id" is required: a positive integer sets the match, null blocks any
match. "note" is optional. A venue's own entry wins over "*". The titles are
the ones the run's TMDB report lists as unmatched (or wrongly matched).
Top-level keys starting with "_" are ignored, for comments.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_FILENAME = "tmdb_overrides.json"
ALL_VENUES = "*"
_FIELDS = {"tmdb_id", "note"}


class TmdbOverridesError(ValueError):
    pass


@dataclass(frozen=True)
class TmdbOverride:
    tmdb_id: int | None
    """None blocks a match."""
    note: str | None = None

    def describe(self) -> str:
        what = f"set to TMDB {self.tmdb_id}" if self.tmdb_id else "blocked"
        return f"{what} in {DEFAULT_FILENAME}" + (f" ({self.note})" if self.note else "")


class TmdbOverrides:
    def __init__(self, entries: dict[str, dict[str, TmdbOverride]] | None = None) -> None:
        self.entries = entries or {}

    def get(self, venue_id: str, title: str) -> TmdbOverride | None:
        for key in (venue_id, ALL_VENUES):
            if (found := self.entries.get(key, {}).get(title)) is not None:
                return found
        return None

    def __eq__(self, other: object) -> bool:
        return isinstance(other, TmdbOverrides) and self.entries == other.entries


def parse_tmdb_overrides(data: Any, known_venues: Iterable[str] | None = None) -> TmdbOverrides:
    if not isinstance(data, dict):
        raise TmdbOverridesError("TMDB overrides must be a JSON object of venue id -> {title: decision}")
    known = set(known_venues) | {ALL_VENUES} if known_venues is not None else None
    result: dict[str, dict[str, TmdbOverride]] = {}
    for venue_id, entries in data.items():
        if venue_id.startswith("_"):
            continue
        if known is not None and venue_id not in known:
            raise TmdbOverridesError(f"unknown venue {venue_id!r}; known: {', '.join(sorted(known))}")
        if not isinstance(entries, dict):
            raise TmdbOverridesError(f"{venue_id}: expected an object of listed title -> decision")
        venue: dict[str, TmdbOverride] = {}
        for title, entry in entries.items():
            where = f"{venue_id}/{title}"
            if not title.strip():
                raise TmdbOverridesError(f"{venue_id}: empty title")
            if not isinstance(entry, dict):
                raise TmdbOverridesError(f"{where}: expected an object like {{\"tmdb_id\": 123}}")
            if extra := set(entry) - _FIELDS:
                raise TmdbOverridesError(f"{where}: unknown field(s) {sorted(extra)}; allowed: {sorted(_FIELDS)}")
            if "tmdb_id" not in entry:
                raise TmdbOverridesError(f"{where}: \"tmdb_id\" is required (a TMDB movie id, or null to block a match)")
            tmdb_id = entry["tmdb_id"]
            if tmdb_id is not None and (isinstance(tmdb_id, bool) or not isinstance(tmdb_id, int) or tmdb_id <= 0):
                raise TmdbOverridesError(f"{where}: \"tmdb_id\" must be a positive integer or null, got {tmdb_id!r}")
            if entry.get("note") is not None and not isinstance(entry["note"], str):
                raise TmdbOverridesError(f"{where}: \"note\" must be a string")
            venue[title] = TmdbOverride(tmdb_id, entry.get("note"))
        result[venue_id] = venue
    return TmdbOverrides(result)


def load_tmdb_overrides(path: Path, known_venues: Iterable[str] | None = None) -> TmdbOverrides:
    """Read and validate a TMDB overrides file; a missing file means none."""
    if not path.exists():
        return TmdbOverrides()
    try:
        data = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as e:
        raise TmdbOverridesError(f"{path}: invalid JSON: {e}") from e
    try:
        return parse_tmdb_overrides(data, known_venues)
    except TmdbOverridesError as e:
        raise TmdbOverridesError(f"{path}: {e}") from e
