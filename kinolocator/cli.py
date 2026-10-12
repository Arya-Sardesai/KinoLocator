"""Command line: `python -m kinolocator {run,list,record}`."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import archive
from .fetch import Fetcher, PoliteSession
from .schema import Showtime
from .scrapers import Scraper, all_scrapers, enabled_scrapers, get_scraper

log = logging.getLogger("kinolocator")

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class RunResult:
    showtimes: list[Showtime] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failures


def run_scrapers(
    scrapers: dict[str, type[Scraper]], fetcher: Fetcher, now: datetime
) -> RunResult:
    """Run each scraper; a scraper that raises or returns nothing is a failure."""
    result = RunResult()
    for venue_id, cls in scrapers.items():
        log.info("scraping %s", venue_id)
        try:
            showtimes = cls(fetcher, now=now).scrape()
        except Exception as e:
            log.exception("%s failed", venue_id)
            result.failures[venue_id] = f"{type(e).__name__}: {e}"
            continue
        result.counts[venue_id] = len(showtimes)
        if not showtimes:
            result.failures[venue_id] = "returned zero showtimes"
            continue
        result.showtimes.extend(showtimes)
    return result


def cmd_run(args: argparse.Namespace) -> int:
    scrapers = {v: get_scraper(v) for v in args.venue} if args.venue else enabled_scrapers()
    if not scrapers:
        log.error("no scrapers to run")
        return 1
    now = datetime.now(timezone.utc)
    result = run_scrapers(scrapers, PoliteSession(), now)

    for venue_id in scrapers:
        if venue_id in result.failures:
            print(f"FAIL  {venue_id}: {result.failures[venue_id]}")
        else:
            print(f"ok    {venue_id}: {result.counts[venue_id]} showtimes")

    if not result.ok:
        # Write nothing: a partial run must not replace today's snapshot or
        # showtimes.json with data that's silently missing a venue.
        if os.environ.get("GITHUB_ACTIONS"):
            for venue_id, reason in result.failures.items():
                print(f"::error title=Scraper failed: {venue_id}::{reason}")
        print(f"\n{len(result.failures)} scraper(s) failed; archive not written.", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"dry run: {len(result.showtimes)} showtimes, nothing written")
        return 0
    snap, current = archive.write_run(Path(args.out_dir), result.showtimes, now)
    print(f"wrote {snap} and {current} ({len(result.showtimes)} showtimes)")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for venue_id, cls in all_scrapers().items():
        state = "enabled " if cls.enabled else "disabled"
        print(f"{venue_id:20} {state}  {cls.venue_name} ({cls.municipality})  {cls.homepage}")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    """Re-record a venue's fixtures from the live site."""
    cls = get_scraper(args.venue)
    out = Path(args.dir) if args.dir else REPO_ROOT / "tests" / "fixtures" / cls.venue_id
    if out.exists():
        shutil.rmtree(out)
    session = PoliteSession(record_dir=out)
    try:
        showtimes = cls(session, now=session.started_at).scrape()
        print(f"recorded {cls.venue_id}: scrape() returned {len(showtimes)} showtimes")
    except NotImplementedError:
        if not cls.fixture_urls:
            log.error("%s.scrape() is not implemented and it has no fixture_urls", cls.__name__)
            return 1
        for url in cls.fixture_urls:
            session.get_text(url)
        print(f"recorded {len(cls.fixture_urls)} fixture URL(s) for {cls.venue_id}")
    print(f"fixtures in {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kinolocator", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="scrape venues and write the archive")
    p_run.add_argument("--venue", action="append", help="venue id (repeatable); default: all enabled")
    p_run.add_argument("--out-dir", default=".", help="where showtimes.json and archive/ live")
    p_run.add_argument("--dry-run", action="store_true", help="scrape but don't write files")
    p_run.set_defaults(func=cmd_run)

    p_list = sub.add_parser("list", help="list registered venues")
    p_list.set_defaults(func=cmd_list)

    p_rec = sub.add_parser("record", help="save live responses as test fixtures")
    p_rec.add_argument("venue")
    p_rec.add_argument("--dir", help="default: tests/fixtures/<venue>")
    p_rec.set_defaults(func=cmd_record)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)
