"""Seasonal collections: web/collections.json, read by the page.

    {
      "collections": [
        {
          "id": "halloween",
          "name": "Halloween",
          "start": "10-01", "end": "11-01",
          "accent": "#F07A2E",
          "rules": {
            "genres": ["Horror"],
            "title_keywords": ["live score"],
            "late_from_hour": 22,
            "tmdb_ids": [948]
          }
        }
      ]
    }

start and end are inclusive Vancouver dates, either "MM-DD" (every year) or
"YYYY-MM-DD" (that year only); start must not come after end. While today
falls in that range the page shows the collection as a chip on the shelf;
ticking it filters the shelf and the map to its listings and swaps the page's
accent colour (accent, a #RRGGBB hex) for the collection's.

A listing belongs to a collection when any rule matches:

* genres: a TMDB genre of its film, by name ("Horror", "Family");
* title_keywords: a word or phrase in its listed or TMDB title, ignoring case
  and accents and matched on word boundaries ("elf" doesn't match "itself");
* late_from_hour: it starts at this hour or later (0-23), or before 4 AM;
* tmdb_ids: its film is one of these TMDB ids.

Validated by the tests, like overrides.json. Top-level keys starting with "_"
are ignored, for comments.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("web") / "collections.json"
_FIELDS = {"id", "name", "start", "end", "accent", "rules", "note"}
_RULES = {"genres", "title_keywords", "late_from_hour", "tmdb_ids"}
_DATE = re.compile(r"^(?:(\d{4})-)?(\d{2})-(\d{2})$")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class CollectionsError(ValueError):
    pass


def _date_key(value: Any, where: str) -> tuple[int | None, int, int]:
    m = _DATE.match(value) if isinstance(value, str) else None
    if not m:
        raise CollectionsError(f"{where}: expected \"MM-DD\" or \"YYYY-MM-DD\", got {value!r}")
    year = int(m[1]) if m[1] else None
    try:
        date(year or 2024, int(m[2]), int(m[3]))  # 2024: a leap year, so 02-29 is allowed
    except ValueError as e:
        raise CollectionsError(f"{where}: not a real date: {value!r}") from e
    return year, int(m[2]), int(m[3])


def _strings(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v.strip() for v in value):
        raise CollectionsError(f"{where}: expected a non-empty list of strings")
    return value


def parse_collections(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or not isinstance(data.get("collections"), list):
        raise CollectionsError("expected an object with a \"collections\" list")
    seen: set[str] = set()
    out = []
    for i, c in enumerate(data["collections"]):
        where = f"collections[{i}]"
        if not isinstance(c, dict):
            raise CollectionsError(f"{where}: expected an object")
        where = f"collection {c.get('id', i)!r}"
        if extra := set(c) - _FIELDS:
            raise CollectionsError(f"{where}: unknown field(s) {sorted(extra)}; allowed: {sorted(_FIELDS)}")
        if not isinstance(c.get("id"), str) or not _ID.match(c["id"]):
            raise CollectionsError(f"{where}: \"id\" must be lower-case letters, digits, - or _")
        if c["id"] in seen:
            raise CollectionsError(f"{where}: duplicate id")
        seen.add(c["id"])
        if not isinstance(c.get("name"), str) or not c["name"].strip():
            raise CollectionsError(f"{where}: \"name\" must be a non-empty string")
        start, end = _date_key(c.get("start"), f"{where} start"), _date_key(c.get("end"), f"{where} end")
        if (start[0] is None) != (end[0] is None):
            raise CollectionsError(f"{where}: start and end must both have a year or both leave it out")
        if (start[0] or 0, start[1], start[2]) > (end[0] or 0, end[1], end[2]):
            raise CollectionsError(f"{where}: start comes after end")
        if not isinstance(c.get("accent"), str) or not _HEX.match(c["accent"]):
            raise CollectionsError(f"{where}: \"accent\" must be a #RRGGBB colour")
        rules = c.get("rules")
        if not isinstance(rules, dict) or not rules:
            raise CollectionsError(f"{where}: \"rules\" must be an object with at least one rule")
        if extra := set(rules) - _RULES:
            raise CollectionsError(f"{where}: unknown rule(s) {sorted(extra)}; allowed: {sorted(_RULES)}")
        for key in ("genres", "title_keywords"):
            if key in rules:
                _strings(rules[key], f"{where} {key}")
        if "late_from_hour" in rules:
            h = rules["late_from_hour"]
            if not isinstance(h, int) or isinstance(h, bool) or not 0 <= h <= 23:
                raise CollectionsError(f"{where} late_from_hour: expected a whole hour, 0 to 23")
        if "tmdb_ids" in rules:
            ids = rules["tmdb_ids"]
            if not isinstance(ids, list) or not ids or not all(isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in ids):
                raise CollectionsError(f"{where} tmdb_ids: expected a non-empty list of TMDB ids")
        out.append(c)
    return out


def load_collections(path: Path = DEFAULT_PATH) -> list[dict[str, Any]]:
    """Read and validate a collections file; a missing file means none."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as e:
        raise CollectionsError(f"{path}: invalid JSON: {e}") from e
    try:
        return parse_collections(data)
    except CollectionsError as e:
        raise CollectionsError(f"{path}: {e}") from e
