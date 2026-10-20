from __future__ import annotations

import json
from pathlib import Path

import pytest

from kinolocator.scrapers import all_scrapers
from kinolocator.tmdb_overrides import (
    TmdbOverride,
    TmdbOverridesError,
    load_tmdb_overrides,
    parse_tmdb_overrides,
)

REPO_FILE = Path(__file__).resolve().parent.parent / "tmdb_overrides.json"


def test_parse_and_lookup():
    overrides = parse_tmdb_overrides(
        {
            "_about": "ignored",
            "viff_centre": {"Atonement": {"tmdb_id": 42, "note": "the 2026 short"}},
            "*": {"Atonement": {"tmdb_id": None}, "Shrek Cabaret": {"tmdb_id": None}},
        },
        {"viff_centre"},
    )
    assert overrides.get("viff_centre", "Atonement") == TmdbOverride(42, "the 2026 short")
    assert overrides.get("rio_theatre", "Atonement") == TmdbOverride(None)
    assert overrides.get("rio_theatre", "Shrek") is None
    assert TmdbOverride(None, "live show").describe() == "blocked in tmdb_overrides.json (live show)"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "must be a JSON object"),
        ({"viff": {}}, "unknown venue 'viff'"),
        ({"viff_centre": []}, "expected an object of listed title"),
        ({"viff_centre": {"Cure": 42}}, "viff_centre/Cure: expected an object"),
        ({"viff_centre": {"Cure": {}}}, '"tmdb_id" is required'),
        ({"viff_centre": {"Cure": {"tmdb_id": "42"}}}, "must be a positive integer or null"),
        ({"viff_centre": {"Cure": {"tmdb_id": 0}}}, "must be a positive integer or null"),
        ({"viff_centre": {"Cure": {"tmdb_id": True}}}, "must be a positive integer or null"),
        ({"viff_centre": {"Cure": {"tmdb_id": 42, "film": True}}}, "unknown field(s) ['film']"),
        ({"viff_centre": {"Cure": {"tmdb_id": 42, "note": 1}}}, '"note" must be a string'),
        ({"viff_centre": {" ": {"tmdb_id": 42}}}, "empty title"),
    ],
)
def test_parse_rejects_mistakes(data, message):
    with pytest.raises(TmdbOverridesError) as e:
        parse_tmdb_overrides(data, {"viff_centre"})
    assert message in str(e.value)


def test_load_missing_file_means_none(tmp_path):
    assert load_tmdb_overrides(tmp_path / "tmdb_overrides.json").get("x", "y") is None


def test_repo_file_is_valid():
    load_tmdb_overrides(REPO_FILE, all_scrapers())
    example = json.loads(REPO_FILE.read_text())["_example"]
    assert parse_tmdb_overrides(example, all_scrapers()).get("viff_centre", "Atonement").tmdb_id == 1234567
