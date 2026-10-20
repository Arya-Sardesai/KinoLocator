"""Scraper registry and the run command's fail-loudly behaviour."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from kinolocator import cli
from kinolocator.config import MissingConfigError, require_env
from kinolocator.scrapers import Scraper, all_scrapers, enabled_scrapers, get_scraper
from kinolocator.scrapers.cinematheque import Cinematheque
from kinolocator.scrapers.dunbar_theatre import DunbarTheatre
from kinolocator.scrapers.park_theatre import ParkTheatre
from kinolocator.scrapers.rio_theatre import RioTheatre
from kinolocator.scrapers.viff_centre import ViffCentre

from .test_schema import make

NOW = datetime(2026, 10, 12, 23, 13, tzinfo=timezone.utc)


class Empty(Scraper):
    venue_id = "empty"
    venue_name = "Empty"
    municipality = "Burnaby"
    homepage = "https://example.com"

    def scrape(self):
        return []


class Broken(Empty):
    venue_id = "broken"

    def scrape(self):
        raise RuntimeError("layout changed")


class Working(Empty):
    venue_id = "working"

    def scrape(self):
        self.skip("Comedy Night", "live event")
        self.skip("Mountain Film Night?", "untagged event", start=NOW, needs_review=True)
        return [make(venue_id="working", venue_name="Working")]


def test_registry_discovers_venue_modules():
    registry = all_scrapers()
    assert registry["cinematheque"] is Cinematheque
    assert registry["rio_theatre"] is RioTheatre
    assert registry["park_theatre"] is ParkTheatre
    assert "_barker" not in registry and "BarkerScraper" not in {c.__name__ for c in registry.values()}
    assert set(enabled_scrapers()) == {"cinematheque", "rio_theatre", "park_theatre", "viff_centre", "dunbar_theatre"}
    with pytest.raises(KeyError, match="known: cinematheque"):
        get_scraper("nope")


def test_registered_scrapers_declare_identity():
    for venue_id, cls in all_scrapers().items():
        assert cls.venue_id == venue_id
        assert cls.venue_name and cls.municipality and cls.homepage.startswith("https://")


def test_run_scrapers_reports_zero_and_errors_as_failures():
    results = {r.venue_id: r for r in cli.run_scrapers({"working": Working, "empty": Empty, "broken": Broken}, None, NOW)}
    assert results["working"].ok and len(results["working"].showtimes) == 1
    assert [k.title for k in results["working"].skipped] == ["Comedy Night", "Mountain Film Night?"]
    assert results["empty"].failure == "no_showtimes" and results["empty"].error is None
    assert results["broken"].failure == "error"
    assert results["broken"].error == "RuntimeError: layout changed"


@pytest.mark.parametrize(
    ("cls", "count"), [(Cinematheque, 40), (RioTheatre, 25), (ParkTheatre, 63), (ViffCentre, 135), (DunbarTheatre, 36)]
)
def test_run_scrapers_against_fixtures(replay, cls, count):
    session = replay(cls.venue_id)
    [result] = cli.run_scrapers({cls.venue_id: cls}, session, session.recorded_at)
    assert result.ok and len(result.showtimes) == count


def _patch_run(monkeypatch, scrapers):
    monkeypatch.setattr(cli, "enabled_scrapers", lambda: scrapers)
    monkeypatch.setattr(cli, "PoliteSession", lambda: None)


def test_cmd_run_writes_archive_on_success(monkeypatch, tmp_path, capsys):
    _patch_run(monkeypatch, {"working": Working})
    assert cli.main(["run", "--out-dir", str(tmp_path)]) == 0
    data = json.loads((tmp_path / "showtimes.json").read_text())
    assert data["venues"]["working"]["status"] == "ok"
    assert len(list((tmp_path / "archive").rglob("*.json"))) == 1
    capsys.readouterr()
    assert cli.main(["summary", "--out-dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "1 showtimes; 1 listing(s) need review\n"


def test_cmd_run_writes_good_venues_then_fails(monkeypatch, tmp_path, capsys):
    _patch_run(monkeypatch, {"working": Working, "empty": Empty, "broken": Broken})
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    out_dir = tmp_path / "out"

    assert cli.main(["run", "--out-dir", str(out_dir)]) == cli.EXIT_SCRAPERS_FAILED
    out = capsys.readouterr().out
    assert "ok    working: 1 showtimes, 2 non-film listings skipped (1 need review)" in out
    assert "::warning title=Needs review: working::Mountain Film Night? (2026-10-12 16:13): untagged event" in out
    assert "FAIL  empty: scraper returned zero showtimes" in out
    assert "FAIL  broken: scraper error: RuntimeError: layout changed" in out
    assert "::error title=Scraper failed: empty::" in out
    assert "::error title=Scraper failed: broken::" in out

    data = json.loads((out_dir / "showtimes.json").read_text())
    assert [s["venue_id"] for s in data["showtimes"]] == ["working"]
    assert {vid: (v["status"], v["failure"]) for vid, v in data["venues"].items()} == {
        "broken": ("failed", "error"),
        "empty": ("failed", "no_showtimes"),
        "working": ("ok", None),
    }
    table = summary.read_text()
    assert "| broken | ❌ error | 0 |" in table and "| working | ✅ ok | 1 |" in table
    assert "- working: Mountain Film Night?" in table

    assert cli.main(["summary", "--out-dir", str(out_dir)]) == 0
    assert capsys.readouterr().out == (
        "1 showtimes; FAILED: broken (error), empty (no_showtimes); 1 listing(s) need review\n"
    )


def test_summary_mentions_stale_fallback(monkeypatch, tmp_path, capsys):
    later = datetime(2026, 10, 20, 19, 0, tzinfo=timezone.utc)

    class Upcoming(Empty):
        venue_id = "upcoming"

        def scrape(self):
            return [make(venue_id="upcoming", venue_name="Upcoming", start=later)]

    _patch_run(monkeypatch, {"upcoming": Upcoming})
    assert cli.main(["run", "--out-dir", str(tmp_path)]) == 0
    first = json.loads((tmp_path / "showtimes.json").read_text())["generated_at"]

    class Upcoming(Empty):  # same venue, now broken
        venue_id = "upcoming"

        def scrape(self):
            raise RuntimeError("layout changed")

    _patch_run(monkeypatch, {"upcoming": Upcoming})
    assert cli.main(["run", "--out-dir", str(tmp_path)]) == cli.EXIT_SCRAPERS_FAILED
    capsys.readouterr()
    assert cli.main(["summary", "--out-dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out == (
        f"1 showtimes; FAILED: upcoming (error, showing stale data from {first})\n"
    )


class Ambiguous(Empty):
    venue_id = "rio_theatre"  # a real venue id, so overrides for it validate

    def scrape(self):
        for key, title in (("1", "Mystery Night"), ("2", "Festival Night")):
            override = self.overrides.get(key)
            if override is None:
                self.skip(title, "can't tell", key=key, needs_review=True)
            elif not override.film:
                self.skip(title, override.describe(), key=key)
        return [make(venue_id="rio_theatre", venue_name="Rio Theatre")]


def test_cmd_run_passes_overrides_and_names_the_key(monkeypatch, tmp_path, capsys):
    _patch_run(monkeypatch, {"rio_theatre": Ambiguous})
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    overrides = tmp_path / "overrides.json"
    overrides.write_text(json.dumps({"rio_theatre": {"2": {"film": False, "title": "Festival Night"}}}))

    assert cli.main(["run", "--out-dir", str(tmp_path), "--overrides", str(overrides)]) == 0
    out = capsys.readouterr().out
    assert "2 non-film listings skipped (1 need review)" in out
    review_lines = [line for line in out.splitlines() if "may be a film" in line]
    assert len(review_lines) == 2  # console line + ::warning:: for Mystery Night only
    assert all("Mystery Night" in line for line in review_lines)
    assert f'To decide, add "1" under "rio_theatre" in {overrides}.' in review_lines[0]
    skipped = json.loads((tmp_path / "showtimes.json").read_text())["venues"]["rio_theatre"]["skipped"]
    assert [(k["key"], k["needs_review"], k["reason"]) for k in skipped] == [
        ("1", True, "can't tell"),
        ("2", False, "manual override: not a film"),
    ]


def test_cmd_run_refuses_a_broken_overrides_file(monkeypatch, tmp_path, caplog):
    _patch_run(monkeypatch, {"rio_theatre": Ambiguous})
    overrides = tmp_path / "overrides.json"
    overrides.write_text(json.dumps({"rio_theater": {}}))
    assert cli.main(["run", "--out-dir", str(tmp_path / "out"), "--overrides", str(overrides)]) == 1
    assert "unknown venue 'rio_theater'" in caplog.text
    assert not (tmp_path / "out").exists()


def test_cmd_run_refuses_a_broken_manual_listings_file(monkeypatch, tmp_path, caplog):
    _patch_run(monkeypatch, {"working": Working})
    listings = tmp_path / "manual_listings.json"
    listings.write_text(json.dumps({"hollywood_theatre": [{
        "title": "Paris, Texas", "start": "2026-11-07T19:30:00-08:00",
        "source_url": "https://www.hollywoodtheatre.ca/", "added": "2026-10-20",
    }]}))
    assert cli.main(["run", "--out-dir", str(tmp_path / "out"), "--manual-listings", str(listings)]) == 1
    assert "but Vancouver is at -07:00 then" in caplog.text
    assert not (tmp_path / "out").exists()


def test_cmd_run_dry_run_writes_nothing(monkeypatch, tmp_path):
    _patch_run(monkeypatch, {"working": Working, "empty": Empty})
    assert cli.main(["run", "--dry-run", "--out-dir", str(tmp_path)]) == cli.EXIT_SCRAPERS_FAILED
    assert list(tmp_path.iterdir()) == []


def test_require_env(monkeypatch):
    monkeypatch.setenv("KINOLOCATOR_TEST_KEY", "abc")
    assert require_env("KINOLOCATOR_TEST_KEY", "testing") == "abc"
    monkeypatch.delenv("KINOLOCATOR_TEST_KEY")
    with pytest.raises(MissingConfigError, match="KINOLOCATOR_TEST_KEY is not set.*required for testing"):
        require_env("KINOLOCATOR_TEST_KEY", "testing")
