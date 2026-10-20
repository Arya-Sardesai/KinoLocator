import json

import pytest

from kinolocator.seasonal import DEFAULT_PATH, CollectionsError, load_collections, parse_collections
from pathlib import Path

REPO_COLLECTIONS = Path(__file__).resolve().parent.parent / DEFAULT_PATH


def collection(**kw):
    c = {"id": "x", "name": "X", "start": "10-01", "end": "11-01", "accent": "#F07A2E", "rules": {"genres": ["Horror"]}}
    c.update(kw)
    return {"collections": [c]}


def test_repo_collections_file_is_valid():
    ids = [c["id"] for c in load_collections(REPO_COLLECTIONS)]
    assert "halloween" in ids and "holidays" in ids


@pytest.mark.parametrize("change, message", [
    ({"start": "11-02"}, "start comes after end"),
    ({"start": "2026-10-01"}, "both have a year"),
    ({"end": "02-30"}, "not a real date"),
    ({"accent": "orange"}, "#RRGGBB"),
    ({"rules": {"genre": ["Horror"]}}, "unknown rule"),
    ({"rules": {"late_from_hour": 24}}, "0 to 23"),
    ({"rules": {"tmdb_ids": ["948"]}}, "TMDB ids"),
    ({"colour": "#000000"}, "unknown field"),
])
def test_rejects_bad_entries(change, message):
    with pytest.raises(CollectionsError, match=message):
        parse_collections(collection(**change))


def test_rejects_duplicate_ids():
    data = collection()
    data["collections"].append(dict(data["collections"][0]))
    with pytest.raises(CollectionsError, match="duplicate"):
        parse_collections(data)


def test_load_invalid_json_names_the_file(tmp_path):
    path = tmp_path / "collections.json"
    path.write_text("{", "utf-8")
    with pytest.raises(CollectionsError, match="collections.json: invalid JSON"):
        load_collections(path)
