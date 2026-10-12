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
        return [make()]


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
    result = cli.run_scrapers({"working": Working, "empty": Empty, "broken": Broken}, None, NOW)
    assert not result.ok
    assert result.failures == {"empty": "returned zero showtimes", "broken": "RuntimeError: layout changed"}
    assert result.counts == {"working": 1, "empty": 0}
    assert len(result.showtimes) == 1


def test_run_scrapers_against_fixtures(replay):
    session = replay("cinematheque")
    result = cli.run_scrapers({"cinematheque": Cinematheque}, session, session.recorded_at)
    assert result.ok and result.counts == {"cinematheque": 40}


def _patch_run(monkeypatch, scrapers):
    monkeypatch.setattr(cli, "enabled_scrapers", lambda: scrapers)
    monkeypatch.setattr(cli, "PoliteSession", lambda: None)


def test_cmd_run_writes_archive_on_success(monkeypatch, tmp_path):
    _patch_run(monkeypatch, {"working": Working})
    assert cli.main(["run", "--out-dir", str(tmp_path)]) == 0
    data = json.loads((tmp_path / "showtimes.json").read_text())
    assert data["venues"] == {"cinematheque": 1}
    assert len(list((tmp_path / "archive").rglob("*.json"))) == 1


def test_cmd_run_fails_loudly_and_writes_nothing(monkeypatch, tmp_path, capsys):
    _patch_run(monkeypatch, {"working": Working, "empty": Empty})
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert cli.main(["run", "--out-dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "FAIL  empty: returned zero showtimes" in out
    assert "::error title=Scraper failed: empty::" in out
    assert list(tmp_path.iterdir()) == []


def test_require_env(monkeypatch):
    monkeypatch.setenv("KINOLOCATOR_TEST_KEY", "abc")
    assert require_env("KINOLOCATOR_TEST_KEY", "testing") == "abc"
    monkeypatch.delenv("KINOLOCATOR_TEST_KEY")
    with pytest.raises(MissingConfigError, match="KINOLOCATOR_TEST_KEY is not set.*required for testing"):
        require_env("KINOLOCATOR_TEST_KEY", "testing")
