"""PoliteSession behaviour, using a fake requests.Session (no network)."""

from __future__ import annotations

import json

import pytest
import requests

from kinolocator.fetch import USER_AGENT, FetchError, PoliteSession, ReplaySession, fixture_filename, parse_robots

# The live robots.txt as served on 2026-10-12, including its "Disalow" typo.
CINEMATHEQUE_ROBOTS = (
    "User-agent:*\nDisalow:/wp-admin/\nSitemap:https://www.thecinematheque.ca/?sitemap.xml\n\n"
    "User-agent: GPTBot\nDisallow: /\n\nUser-agent: ChatGPT-User\nDisallow: /\n"
)


class FakeResponse:
    def __init__(self, status=200, text="ok", headers=None):
        self.status_code = status
        self.text = text
        self.headers = {"Content-Type": "text/html; charset=UTF-8", **(headers or {})}
        self.encoding = None


class FakeSession:
    def __init__(self, routes):
        self.routes = {url: list(responses) for url, responses in routes.items()}
        self.headers = {}
        self.calls = []

    def get(self, url, timeout):
        self.calls.append(url)
        queue = self.routes.get(url)
        if not queue:
            return FakeResponse(404, "not found")
        return queue.pop(0) if len(queue) > 1 else queue[0]


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_session(routes, **kwargs):
    fake, clock = FakeSession(routes), FakeClock()
    s = PoliteSession(session=fake, sleep=clock.sleep, clock=clock, **kwargs)
    return s, fake, clock


def test_sets_identifying_user_agent():
    s, fake, _ = make_session({})
    assert fake.headers["User-Agent"] == USER_AGENT
    assert "Kinolocator" in USER_AGENT and "github.com" in USER_AGENT


def test_throttles_requests_to_same_host():
    s, _, clock = make_session(
        {"https://a.test/1": [FakeResponse()], "https://a.test/2": [FakeResponse()]},
        min_delay=4.0, respect_robots=False,
    )
    s.get_text("https://a.test/1")
    clock.now += 1.0
    s.get_text("https://a.test/2")
    assert clock.sleeps == [3.0]


def test_retries_429_honouring_retry_after():
    url = "https://a.test/x"
    s, fake, clock = make_session(
        {url: [FakeResponse(429, headers={"Retry-After": "30"}), FakeResponse(text="done")]},
        min_delay=1.0, respect_robots=False,
    )
    assert s.get_text(url) == "done"
    assert fake.calls == [url, url]
    assert 30 in clock.sleeps


def test_gives_up_after_max_retries():
    url = "https://a.test/x"
    s, fake, _ = make_session({url: [FakeResponse(503)]}, max_retries=2, respect_robots=False)
    with pytest.raises(FetchError, match="after 3 attempts: HTTP 503"):
        s.get_text(url)
    assert len(fake.calls) == 3


def test_client_errors_are_not_retried():
    s, fake, _ = make_session({}, respect_robots=False)
    with pytest.raises(FetchError, match="HTTP 404"):
        s.get_text("https://a.test/missing")
    assert len(fake.calls) == 1


def test_network_errors_are_retried():
    url = "https://a.test/x"
    s, fake, _ = make_session({url: [FakeResponse(text="ok")]}, respect_robots=False)
    real_get = fake.get
    attempts = []

    def flaky(u, timeout):
        attempts.append(u)
        if len(attempts) == 1:
            raise requests.ConnectionError("reset")
        return real_get(u, timeout)

    fake.get = flaky
    assert s.get_text(url) == "ok"


def test_robots_typo_does_not_block_everyone():
    parser = parse_robots(CINEMATHEQUE_ROBOTS)
    assert parser.can_fetch(USER_AGENT, "https://thecinematheque.ca/films/calendar")
    assert not parser.can_fetch(USER_AGENT, "https://thecinematheque.ca/wp-admin/")
    assert not parser.can_fetch("GPTBot", "https://thecinematheque.ca/films/calendar")


def test_robots_disallow_is_enforced():
    s, fake, _ = make_session(
        {"https://a.test/robots.txt": [FakeResponse(text="User-agent: *\nDisallow: /private/\n")],
         "https://a.test/public": [FakeResponse(text="hi")]}
    )
    assert s.get_text("https://a.test/public") == "hi"
    with pytest.raises(FetchError, match="robots.txt disallows"):
        s.get_text("https://a.test/private/x")
    assert fake.calls.count("https://a.test/robots.txt") == 1  # cached per origin


def test_missing_robots_means_allowed():
    s, _, _ = make_session({"https://a.test/page": [FakeResponse(text="hi")]})
    assert s.get_text("https://a.test/page") == "hi"


def test_record_then_replay(tmp_path):
    urls = {"https://a.test/films/calendar": "<cal>", "https://a.test/api/x?d=1": '{"a": 1}'}
    routes = {u: [FakeResponse(text=t)] for u, t in urls.items()}
    routes["https://a.test/api/x?d=1"] = [FakeResponse(text='{"a": 1}', headers={"Content-Type": "application/json"})]
    s, _, _ = make_session(routes, respect_robots=False, record_dir=tmp_path)
    for u in urls:
        s.get_text(u)

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["responses"] == {
        "https://a.test/films/calendar": "films_calendar.html",
        "https://a.test/api/x?d=1": "api_x_d_1.json",
    }
    replay = ReplaySession(tmp_path)
    assert replay.recorded_at == s.started_at
    assert {u: replay.get_text(u) for u in urls} == urls
    with pytest.raises(FetchError, match="no fixture"):
        replay.get_text("https://a.test/other")


def test_long_fixture_names_are_unique():
    base = "https://a.test/wp-json/wp/v2/barker_event?include=" + ",".join(str(i) for i in range(100, 160))
    a, b = fixture_filename(base + ",1"), fixture_filename(base + ",2")
    assert a != b and len(a) <= 130 and a.endswith(".html")
