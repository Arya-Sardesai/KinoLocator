"""Command line: `python -m kinolocator {run,summary,list,record}`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import archive
from .archive import VenueResult
from .fetch import Fetcher, PoliteSession
from .scrapers import Scraper, all_scrapers, enabled_scrapers, get_scraper

log = logging.getLogger("kinolocator")

REPO_ROOT = Path(__file__).resolve().parent.parent


# Exit code when one or more scrapers failed but the run still wrote its
# results. Distinct from 1 (crash / bad usage) so the workflow can commit the
# good data first and then fail the job.
EXIT_SCRAPERS_FAILED = 3


def run_scrapers(
    scrapers: dict[str, type[Scraper]], fetcher: Fetcher, now: datetime
) -> list[VenueResult]:
    """Run each scraper; one raising never stops the others."""
    results = []
    for venue_id, cls in scrapers.items():
        log.info("scraping %s", venue_id)
        try:
            showtimes = cls(fetcher, now=now).scrape()
        except Exception as e:
            log.exception("%s failed", venue_id)
            message = " ".join(str(e).split())  # one line, for annotations and JSON
            results.append(VenueResult(venue_id, error=f"{type(e).__name__}: {message}"))
            continue
        results.append(VenueResult(venue_id, showtimes))
    return results


def describe(result: VenueResult) -> str:
    if result.failure == archive.FAILURE_ERROR:
        return f"FAIL  {result.venue_id}: scraper error: {result.error}"
    if result.failure == archive.FAILURE_NO_SHOWTIMES:
        return f"FAIL  {result.venue_id}: scraper returned zero showtimes"
    return f"ok    {result.venue_id}: {len(result.showtimes)} showtimes"


def cmd_run(args: argparse.Namespace) -> int:
    scrapers = {v: get_scraper(v) for v in args.venue} if args.venue else enabled_scrapers()
    if not scrapers:
        log.error("no scrapers to run")
        return 1
    now = datetime.now(timezone.utc)
    results = run_scrapers(scrapers, PoliteSession(), now)
    failed = [r for r in results if not r.ok]
    for r in results:
        print(describe(r))

    if args.dry_run:
        print("dry run: nothing written")
    else:
        snap, current, payload = archive.write_run(Path(args.out_dir), results, now)
        print(f"wrote {snap} and {current} ({payload['showtime_count']} showtimes)")
        _github_summary(payload, {r.venue_id for r in failed})

    if failed:
        if os.environ.get("GITHUB_ACTIONS"):
            for r in failed:
                print(f"::error title=Scraper failed: {r.venue_id}::{describe(r)}")
        print(f"\n{len(failed)} of {len(results)} scraper(s) failed.", file=sys.stderr)
        return EXIT_SCRAPERS_FAILED
    return 0


def _github_summary(payload: dict, failed_now: set[str]) -> None:
    """Per-venue status table on the Actions run page."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        f"### Showtimes snapshot {payload['date']}: {payload['showtime_count']} showtimes",
        "",
        "| venue | status | showtimes | data from | detail |",
        "|---|---|---|---|---|",
    ]
    for vid, v in payload["venues"].items():
        status = "✅ ok" if v["status"] == archive.STATUS_OK else f"❌ {v['failure']}"
        if vid not in failed_now and v["status"] != archive.STATUS_OK:
            status += " (earlier run)"
        lines.append(
            f"| {vid} | {status} | {v['showtime_count']} | {v['showtimes_scraped_at'] or '-'} "
            f"| {(v['error'] or '').replace('|', '/')[:200]} |"
        )
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def cmd_summary(args: argparse.Namespace) -> int:
    """One line describing showtimes.json, e.g. for commit messages."""
    payload = json.loads((Path(args.out_dir) / archive.CURRENT_FILENAME).read_text("utf-8"))
    line = f"{payload['showtime_count']} showtimes"
    failed = archive.failed_venues(payload)
    if failed:
        line += "; FAILED: " + ", ".join(f"{vid} ({v['failure']})" for vid, v in failed.items())
    print(line)
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

    p_sum = sub.add_parser("summary", help="one-line summary of showtimes.json")
    p_sum.add_argument("--out-dir", default=".")
    p_sum.set_defaults(func=cmd_summary)

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
