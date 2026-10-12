"""HTTP access for scrapers: polite live fetching, plus fixture record/replay.

Scrapers only ever call `fetcher.get_text(url)`, so the same scraper code runs
against the live site (PoliteSession) or against saved fixtures (ReplaySession).
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests

log = logging.getLogger(__name__)

USER_AGENT = (
    "Kinolocator/0.1 (Greater Vancouver showtime aggregator; "
    "+https://github.com/arya-sardesai/kinolocator)"
)
MANIFEST = "manifest.json"

# Misspellings of "disallow" that Google's reference robots.txt parser accepts.
# urllib.robotparser ignores them, and an ignored rule can make it merge two
# user-agent groups (thecinematheque.ca's robots.txt hits exactly this).
_DISALLOW_TYPOS = re.compile(
    r"^\s*(dissallow|dissalow|disalow|diasllow|disallaw)\s*:", re.IGNORECASE | re.MULTILINE
)


def parse_robots(text: str) -> RobotFileParser:
    parser = RobotFileParser()
    parser.parse(_DISALLOW_TYPOS.sub("Disallow:", text).splitlines())
    return parser


class FetchError(RuntimeError):
    pass


class Fetcher(Protocol):
    def get_text(self, url: str) -> str: ...


class PoliteSession:
    """requests wrapper that identifies itself, rate-limits per host, honours
    robots.txt, and backs off on 429/5xx (respecting Retry-After).

    Pass `record_dir` to save every response body plus a URL manifest, which
    ReplaySession can serve back in tests.
    """

    def __init__(
        self,
        *,
        min_delay: float = 4.0,
        max_retries: int = 3,
        timeout: float = 30.0,
        user_agent: str = USER_AGENT,
        respect_robots: bool = True,
        record_dir: Path | None = None,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.min_delay = min_delay
        self.max_retries = max_retries
        self.timeout = timeout
        self.respect_robots = respect_robots
        self.record_dir = record_dir
        self.started_at = datetime.now(timezone.utc)
        self._session = session or requests.Session()
        self._session.headers.update(
            {"User-Agent": user_agent, "Accept-Language": "en-CA,en;q=0.8"}
        )
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    def get_text(self, url: str) -> str:
        if self.respect_robots and not self._allowed(url):
            raise FetchError(f"robots.txt disallows {url}")
        resp = self._get(url)
        if self.record_dir is not None:
            _record(self.record_dir, url, resp, self.started_at)
        return resp.text

    def _get(self, url: str) -> requests.Response:
        host = urlsplit(url).netloc
        last_error: str = ""
        for attempt in range(self.max_retries + 1):
            self._throttle(host)
            try:
                resp = self._session.get(url, timeout=self.timeout)
            except requests.RequestException as e:
                last_error = f"{type(e).__name__}: {e}"
                self._backoff(attempt, None, url, last_error)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}"
                self._backoff(attempt, resp.headers.get("Retry-After"), url, last_error)
                continue
            if resp.status_code >= 400:
                raise FetchError(f"GET {url} failed: HTTP {resp.status_code}")
            if "charset" not in resp.headers.get("Content-Type", "").lower():
                resp.encoding = "utf-8"
            return resp
        raise FetchError(f"GET {url} failed after {self.max_retries + 1} attempts: {last_error}")

    def _throttle(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self.min_delay - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()

    def _backoff(self, attempt: int, retry_after: str | None, url: str, reason: str) -> None:
        if attempt >= self.max_retries:
            return
        wait = self.min_delay * 2 ** (attempt + 1)
        if retry_after and retry_after.strip().isdigit():
            wait = max(wait, float(retry_after))
        wait = min(wait, 120.0)
        log.warning("GET %s: %s; retrying in %.0fs", url, reason, wait)
        self._sleep(wait)

    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser: RobotFileParser | None
            try:
                parser = parse_robots(self._get(f"{origin}/robots.txt").text)
            except FetchError as e:
                # No readable robots.txt (e.g. 404) means no restrictions.
                log.info("no robots.txt for %s (%s); assuming allowed", origin, e)
                parser = None
            self._robots[origin] = parser
        parser = self._robots[origin]
        ua = self._session.headers["User-Agent"]
        return parser is None or parser.can_fetch(ua, url)


class ReplaySession:
    """Serves responses previously saved by PoliteSession(record_dir=...).

    `recorded_at` is when the recording was made; pass it as the scraper's `now`
    so date-relative logic (e.g. "drop past screenings") sees the same day.
    """

    def __init__(self, fixture_dir: Path) -> None:
        self.fixture_dir = fixture_dir
        manifest = json.loads((fixture_dir / MANIFEST).read_text("utf-8"))
        self.recorded_at = datetime.fromisoformat(manifest["recorded_at"])
        self.responses: dict[str, str] = manifest["responses"]
        self.requested: list[str] = []

    def get_text(self, url: str) -> str:
        self.requested.append(url)
        try:
            name = self.responses[url]
        except KeyError:
            raise FetchError(f"no fixture recorded for {url} in {self.fixture_dir}") from None
        return (self.fixture_dir / name).read_text("utf-8")


def fixture_filename(url: str, content_type: str = "") -> str:
    parts = urlsplit(url)
    stem = parts.path.strip("/") or "index"
    if parts.query:
        stem += "_" + parts.query
    stem = re.sub(r"[^A-Za-z0-9.-]+", "_", stem).strip("_")[:150]
    ext = ".json" if "json" in content_type.lower() else ".html"
    return stem + ext


def _record(record_dir: Path, url: str, resp: requests.Response, started_at: datetime) -> None:
    record_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = record_dir / MANIFEST
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text("utf-8"))
    else:
        manifest = {"recorded_at": started_at.isoformat(), "responses": {}}
    name = fixture_filename(url, resp.headers.get("Content-Type", ""))
    (record_dir / name).write_text(resp.text, encoding="utf-8")
    manifest["responses"][url] = name
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", "utf-8")
