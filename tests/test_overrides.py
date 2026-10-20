from __future__ import annotations

import json
from pathlib import Path

import pytest

from kinolocator.overrides import Override, OverridesError, load_overrides, parse_overrides
from kinolocator.scrapers import all_scrapers

REPO_OVERRIDES = Path(__file__).resolve().parent.parent / "overrides.json"


def test_parse():
    data = {
        "_about": "comments are ignored",
        "rio_theatre": {
            "350890": {"film": True, "title": "VIMFF", "note": "film festival"},
            "350909": {"film": False},
        },
        "park_theatre": {},
    }
    assert parse_overrides(data, {"rio_theatre", "park_theatre"}) == {
        "rio_theatre": {
            "350890": Override(True, "VIMFF", "film festival"),
            "350909": Override(False),
        },
        "park_theatre": {},
    }


def test_describe():
    assert Override(True, note="film festival").describe() == "manual override: film (film festival)"
    assert Override(False).describe() == "manual override: not a film"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "must be a JSON object"),
        ({"rio_theater": {}}, "unknown venue 'rio_theater'"),
        ({"rio_theatre": []}, "expected an object of listing key"),
        ({"rio_theatre": {"1": True}}, "rio_theatre/1: expected an object"),
        ({"rio_theatre": {"1": {}}}, '"film" must be true or false'),
        ({"rio_theatre": {"1": {"film": "yes"}}}, '"film" must be true or false'),
        ({"rio_theatre": {"1": {"film": True, "is_film": True}}}, "unknown field(s) ['is_film']"),
        ({"rio_theatre": {"1": {"film": True, "note": 3}}}, '"note" must be a string'),
    ],
)
def test_parse_rejects_mistakes(data, message):
    with pytest.raises(OverridesError) as e:
        parse_overrides(data, {"rio_theatre"})
    assert message in str(e.value)


def test_load_missing_file_means_no_overrides(tmp_path):
    assert load_overrides(tmp_path / "overrides.json") == {}


def test_load_invalid_json_names_the_file(tmp_path):
    path = tmp_path / "overrides.json"
    path.write_text('{"rio_theatre": {"1": {"film": true},}}')
    with pytest.raises(OverridesError, match="overrides.json: invalid JSON"):
        load_overrides(path)


def test_repo_overrides_file_is_valid():
    # The scheduled run refuses to start on a broken overrides.json; catch it here first.
    overrides = load_overrides(REPO_OVERRIDES, all_scrapers())
    assert overrides["rio_theatre"]["350890"].film is True
    for venue in overrides.values():
        for key, decision in venue.items():
            assert key and decision.title, f"{key}: add a title so the file stays readable"


def test_repo_overrides_file_is_tidy():
    data = json.loads(REPO_OVERRIDES.read_text())
    for venue_id, entries in data.items():
        if not venue_id.startswith("_"):
            assert list(entries) == list(dict.fromkeys(entries)), "duplicate keys"
