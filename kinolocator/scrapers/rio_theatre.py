"""The Rio Theatre, 1660 E Broadway, Vancouver — https://riotheatre.ca

STUB: shows the shape of a venue module; it is disabled until implemented.

To finish it, follow the README's "Adding a venue" checklist:
  1. Look for a JSON feed first (browser devtools > Network while the listings
     page loads); fall back to HTML parsing only if there isn't one.
  2. Record fixtures:  python -m kinolocator record rio_theatre
  3. Write pure parse_* functions that take response text and return plain data,
     and test them against tests/fixtures/rio_theatre/.
  4. Implement scrape() below in terms of those functions, then set enabled = True.
"""

from __future__ import annotations

from ..schema import Showtime
from .base import Scraper

BASE_URL = "https://riotheatre.ca"


class RioTheatre(Scraper):
    venue_id = "rio_theatre"
    venue_name = "Rio Theatre"
    municipality = "Vancouver"
    homepage = BASE_URL
    enabled = False

    # URLs `python -m kinolocator record` should fetch when this venue has no
    # working scrape() yet. Once scrape() works, recording just runs it.
    fixture_urls = (f"{BASE_URL}/",)

    def scrape(self) -> list[Showtime]:
        # Sketch of a finished implementation:
        #   html = self.fetcher.get_text(f"{BASE_URL}/calendar/")
        #   return [
        #       self.showtime(title=..., start=..., ticket_url=..., format=...)
        #       for entry in parse_listings(html)
        #   ]
        raise NotImplementedError("Rio Theatre scraper is a stub; see module docstring")
