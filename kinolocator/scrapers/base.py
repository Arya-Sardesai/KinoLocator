"""The interface every venue scraper implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, ClassVar, Mapping

from ..fetch import Fetcher
from ..overrides import Override
from ..schema import Showtime, Skipped


class Scraper(ABC):
    """One venue (or one venue operator's website) per subclass.

    Subclasses set the class attributes and implement `scrape()`. All network
    access must go through `self.fetcher.get_text(url)` so that the scraper can
    be run against recorded fixtures in tests and is rate-limited in production.

    Listings that aren't film screenings (live shows, rentals, meetings) must
    not become Showtimes; record each with `self.skip(...)` instead so the
    snapshot shows what was dropped and why. Pass `needs_review=True` when the
    scraper can't tell whether something is a film, plus the listing's stable
    `key` so a human decision can be recorded in overrides.json; scrapers that
    classify listings should consult `self.overrides` (key -> Override) first.

    Any subclass defined in a module under kinolocator/scrapers/ is registered
    automatically (modules starting with "_" are skipped, so shared helpers for
    several venues can live there); set `enabled = False` to keep a scraper out
    of scheduled runs.
    """

    venue_id: ClassVar[str]
    """Stable, unique slug, e.g. "cinematheque". Used in data and on the CLI."""
    venue_name: ClassVar[str]
    municipality: ClassVar[str]
    """Greater Vancouver municipality, e.g. "Vancouver", "Burnaby", "North Vancouver"."""
    homepage: ClassVar[str]
    enabled: ClassVar[bool] = True
    fixture_urls: ClassVar[tuple[str, ...]] = ()
    """Pages `kinolocator record` saves when scrape() isn't implemented yet."""

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        now: datetime | None = None,
        overrides: Mapping[str, Override] | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.overrides: Mapping[str, Override] = overrides or {}
        self.scraped_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.skipped: list[Skipped] = []

    @abstractmethod
    def scrape(self) -> list[Showtime]:
        """Fetch and return every upcoming showtime the venue currently lists.

        Raise an exception if the venue's pages can't be fetched or no longer
        parse; return [] only if the venue genuinely lists nothing (the runner
        treats that as a failure too, so it gets noticed).
        """

    def showtime(self, **fields: Any) -> Showtime:
        """Build a Showtime with this venue's identity and scrape time filled in."""
        return Showtime(
            venue_id=self.venue_id,
            venue_name=self.venue_name,
            municipality=self.municipality,
            scraped_at=self.scraped_at,
            **fields,
        )

    def skip(
        self,
        title: str,
        reason: str,
        *,
        start: datetime | None = None,
        url: str | None = None,
        needs_review: bool = False,
        key: str | None = None,
    ) -> None:
        """Record a listing that deliberately produced no Showtime."""
        self.skipped.append(Skipped(title, reason, start, url, needs_review, key))
