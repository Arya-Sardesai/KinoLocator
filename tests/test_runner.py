"""Scraper registry and the run command's fail-loudly behaviour."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from kinolocator import cli
from kinolocator.config import MissingConfigError, require_env
from kinolocator.scrapers import Scraper, all_scrapers, enabled_scrapers, get_scraper
from kinolocator.scrapers.cinematheque import Cinematheque
from kinolocator.scrapers.rio_theatre import RioTheatre

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
        return [make(venue_id="working", venue_name="Working")]


def test_registry_discovers_venue_modules():
    registry = all_scrapers()
    assert registry["cinematheque"] is Cinematheque
    assert registry["rio_theatre"] is RioTheatre
    assert "rio_theatre" not in enabled_scrapers()  # stub stays out of scheduled runs
    with pytest.raises(KeyError, match="known: cinematheque"):
        get_scraper("nope")


def test_registered_scrapers_declare_identity():
    for venue_id, cls in all_scrapers().items():
        assert cls.venue_id == venue_id
        assert cls.venue_name and cls.municipality and cls.homepage.startswith("https://")


def test_stub_is_not_implemented():
    with pytest.raises(NotImplementedError):
        RioTheatre(fetcher=None, now=NOW).scrape()


def test_run_scrapers_reports_zero_and_errors_as_failures():
    results = {r.venue_id: r for r in cli.run_scrapers({"working": Working, "empty": Empty, "broken": Broken}, None, NOW)}
    assert results["working"].ok and len(results["working"].showtimes) == 1
    assert results["empty"].failure == "no_showtimes" and results["empty"].error is None
    assert results["broken"].failure == "error"
    assert results["broken"].error == "RuntimeError: layout changed"


def test_run_scrapers_against_fixtures(replay):
    session = replay("cinematheque")
    [result] = cli.run_scrapers({"cinematheque": Cinematheque}, session, session.recorded_at)
    assert result.ok and len(result.showtimes) == 40


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
    assert capsys.readouterr().out == "1 showtimes\n"


def test_cmd_run_writes_good_venues_then_fails(monkeypatch, tmp_path, capsys):
    _patch_run(monkeypatch, {"working": Working, "empty": Empty, "broken": Broken})
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    out_dir = tmp_path / "out"

    assert cli.main(["run", "--out-dir", str(out_dir)]) == cli.EXIT_SCRAPERS_FAILED
    out = capsys.readouterr().out
    assert "ok    working: 1 showtimes" in out
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

    assert cli.main(["summary", "--out-dir", str(out_dir)]) == 0
    assert capsys.readouterr().out == "1 showtimes; FAILED: broken (error), empty (no_showtimes)\n"


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
