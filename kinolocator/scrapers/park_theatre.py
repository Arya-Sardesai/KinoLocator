"""The Park Theatre, 3440 Cambie St, Vancouver — https://www.theparktheatre.ca

Run by the Rio's operator on the same Barker WordPress plugin; see _barker.py
for the JSON endpoints and how films are told apart from other events.

Venue specifics (fixtures recorded 2026-10-18):
* No rate card: /ticket-info/ 404s and no other page on www. lists a regular
  price. Regular screenings link to tickets.theparktheatre.ca/events/<id>
  (the ticketing site, not yet examined), so their price is left empty. Special
  events that state a price in the description ("Tickets $35+ fees",
  "Adults $23", a FREE screening) get it as scraped.
* The projection format is sometimes in the title ("Dune: Part Three
  (Presented in 70mm)"), which _barker.title_format picks up.
* Language terms are the film's spoken languages, as at the Rio; Russian's
  term comes through as the mangled slug "p" (see _barker.MANGLED_LANGUAGE_SLUGS).
* Ticket links mostly go to tickets.theparktheatre.ca; a few festival
  screenings link to the festival's own ticketing.
"""

from __future__ import annotations

from ._barker import BarkerScraper


class ParkTheatre(BarkerScraper):
    venue_id = "park_theatre"
    venue_name = "Park Theatre"
    municipality = "Vancouver"
    homepage = "https://www.theparktheatre.ca"
    rates_path = None
