"""The interface every venue scraper implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, ClassVar

from ..fetch import Fetcher
from ..schema import Showtime


class Scraper(ABC):
    """One venue (or one venue operator's website) per subclass.

    Subclasses set the class attributes and implement `scrape()`. All network
    access must go through `self.fetcher.get_text(url)` so that the scraper can
    be run against recorded fixtures in tests and is rate-limited in production.

    Any subclass defined in a module under kinolocator/scrapers/ is registered
    automatically; set `enabled = False` to keep it out of scheduled runs.
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

    def __init__(self, fetcher: Fetcher, *, now: datetime | None = None) -> None:
        self.fetcher = fetcher
        self.scraped_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

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
