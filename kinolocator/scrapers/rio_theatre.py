"""The Rio Theatre, 1660 E Broadway, Vancouver — https://riotheatre.ca

Runs on the Barker WordPress plugin; see _barker.py for the JSON endpoints and
how films are told apart from the Rio's comedy, music and burlesque nights.

Venue specifics (checked 2026-10-18):
* /ticket-info/ lists "Regular Adult – $16.00", used as the estimated price;
  special events state their own price in the description (scraped).
* Language terms are the film's spoken languages (TMDB-style), e.g. Rocky
  Horror lists Deutsch + English, so original_language is only set when a
  single language is listed. No projection format is published.
* Some live-event listings link to other sites' ticketing (JFL, Ticketmaster);
  those are skipped as non-films anyway.
"""

from __future__ import annotations

from ._barker import BarkerScraper


class RioTheatre(BarkerScraper):
    venue_id = "rio_theatre"
    venue_name = "Rio Theatre"
    municipality = "Vancouver"
    homepage = "https://riotheatre.ca"
