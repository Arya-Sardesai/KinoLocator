"""The Park Theatre, 3440 Cambie St, Vancouver — https://www.theparktheatre.ca

UNVERIFIED, so disabled. The Park is run by the Rio's operator (the Rio sells
joint "Rio & Park Theatre" memberships, and Rio listings link to
www.theparktheatre.ca/event/<slug>/, Barker's permalink style), so it very
likely exposes the same Barker JSON API. That hasn't been checked against the
live site: the environment this was written in couldn't reach it.

To enable:
  1. python -m kinolocator run --venue park_theatre --dry-run
     (if it fails, the site isn't Barker after all: write a scraper from
     scratch, checking for a JSON endpoint first)
  2. Check rates_path points at the Park's price page.
  3. python -m kinolocator record park_theatre
  4. Add tests/test_park_theatre.py against the fixtures (see test_rio_theatre.py)
  5. Set enabled = True.
"""

from __future__ import annotations

from ._barker import BarkerScraper


class ParkTheatre(BarkerScraper):
    venue_id = "park_theatre"
    venue_name = "Park Theatre"
    municipality = "Vancouver"
    homepage = "https://www.theparktheatre.ca"
    enabled = False
