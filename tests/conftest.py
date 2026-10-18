from __future__ import annotations

from pathlib import Path

import pytest

from kinolocator.fetch import ReplaySession

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_dir():
    return FIXTURES


@pytest.fixture
def replay():
    """replay("cinematheque") -> ReplaySession over tests/fixtures/cinematheque/."""
    return lambda venue_id: ReplaySession(FIXTURES / venue_id)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Tests must never touch the network: they run against recorded fixtures."""

    def refuse(*args, **kwargs):
        raise AssertionError("tests must not make real HTTP requests")

    monkeypatch.setattr("requests.Session.request", refuse)
