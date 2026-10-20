# Kinolocator

Showtime aggregator for Greater Vancouver cinemas. Twice a day a GitHub Actions
job scrapes each venue's website, matches the films on TMDB, and commits:

- `showtimes.json`: every upcoming showtime from the latest run, with a
  failed venue's last good showtimes carried over and flagged stale, plus
  TMDB details for the matched films and a report of the misses
- `archive/YYYY/MM/DD.json`: a dated snapshot of exactly what was observed, one per day,
  never rewritten after that day
- `tmdb_cache.json`: cached TMDB lookups, so a run only searches new titles

| Venue | Module | Source | Status |
|---|---|---|---|
| The Cinematheque (Vancouver) | `scrapers/cinematheque.py` | HTML (no API) | live |
| Rio Theatre (Vancouver) | `scrapers/rio_theatre.py` | Barker WordPress JSON API | live |
| Park Theatre (Vancouver) | `scrapers/park_theatre.py` | Barker WordPress JSON API | live |
| VIFF Centre (Vancouver) | `scrapers/viff_centre.py` | WordPress calendar JSON + event pages | live |
| Dunbar Theatre (Vancouver) | `scrapers/dunbar_theatre.py` | Veezi sessions pages (schema.org JSON-LD + HTML) | live |

The map also shows venues no scraper covers:

| Venue | Listings | Why |
|---|---|---|
| Hollywood Theatre (Kitsilano) | hand-entered in `manual_listings.json` | occasional film nights |
| The Norm Theatre (UBC) | hand-entered in `manual_listings.json` | neither events.ubc.ca nor Showpass carries FilmSoc's or Cinema Politica's screenings (see Venue notes) |
| Scotiabank Theatre Vancouver, Fifth Avenue Cinemas, International Village, Marine Gateway (Cineplex) | link-out only: a hollow dot linking to cineplex.com | cineplex.com's robots.txt disallows all but search engines; none of its data is copied |

## Quick start

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'

pytest                                       # offline, runs against recorded fixtures
python -m kinolocator list                   # registered venues
python -m kinolocator run --dry-run          # scrape live, write nothing
python -m kinolocator run                    # scrape live, write showtimes.json + archive/
python -m kinolocator run --venue cinematheque
python -m kinolocator summary                # one-line status of showtimes.json
```

## Architecture

```
kinolocator/
  schema.py        Showtime dataclass + JSON (de)serialisation
  fetch.py         PoliteSession (live HTTP), ReplaySession (fixtures), record mode
  archive.py       append-only archive/YYYY/MM/DD.json + showtimes.json (with stale fallback)
  cli.py           run / summary / list / record commands
  config.py        require_env(): API keys come from environment variables only
  languages.py     language name -> ISO 639-1
  overrides.py     loads/validates overrides.json (manual film / not-a-film decisions)
  manual_listings.py  loads/validates manual_listings.json (hand-entered showtimes)
  venues.py        reads web/venues.json and its "listings" kinds
  titles.py        listed title -> TMDB search queries; spots multi-film programmes
  tmdb.py          TMDB client + cache, the matching rules, payload enrichment + report
  tmdb_overrides.py  loads/validates tmdb_overrides.json (hand-set matches)
  scrapers/
    __init__.py    auto-discovers every Scraper subclass in this package
    base.py        Scraper base class
    _barker.py     shared implementation for sites on the Barker cinema plugin
                   ("_" modules hold shared code and aren't registered as venues)
    cinematheque.py
    rio_theatre.py   (Barker)
    park_theatre.py  (Barker)
    viff_centre.py   (calendar JSON + event pages)
    dunbar_theatre.py  (Veezi)
overrides.json     reviewed decisions for listings scrapers can't classify
manual_listings.json  hand-entered showtimes for venues without a scraper
tmdb_overrides.json   hand-set or blocked TMDB matches
tmdb_cache.json    TMDB responses (written by each run)
tests/
  fixtures/<venue_id>/   real responses recorded from each site + manifest.json
.github/workflows/
  scrape.yml       twice-daily scrape -> commit
  tests.yml        tests on every push / PR
```

How a run works: `cli.run_scrapers` instantiates each enabled scraper with a
shared `PoliteSession` and calls `scrape()`. One scraper failing never stops
the others. A venue **fails** if its scraper raises (`failure: "error"`) or
returns zero showtimes (`failure: "no_showtimes"`). The run then:

1. matches every showtime on TMDB (see [TMDB matching](#tmdb-matching)) and
   writes today's snapshot and `showtimes.json` with every venue that
   succeeded, plus a status entry for each failed venue (see below);
2. prints `FAIL` lines and, on Actions, `::error::` annotations and a per-venue
   table in the run summary. Skipped listings that might be films get a
   `::warning::` each and a list in the run summary (they don't fail the run);
3. exits **3** if any venue failed (0 if none). Exit 1 or any other code means
   the run itself crashed, and nothing should be committed.

The workflow treats 0 and 3 alike up to the commit, so good data always lands.
After pushing, it fails the job if the exit code was 3, which turns the run red
and sends GitHub's failure email. The commit message also names the failed
venues and any stale fallback, e.g.
`(134 showtimes; FAILED: rio_theatre (error, showing stale data from 2026-10-18T01:17:40+00:00); 4 listing(s) need review; TMDB matched 112/134)`.
`python -m kinolocator summary` prints that line for any `showtimes.json`
(`--tmdb` adds every unmatched title with why).

### The Showtime schema

| field | type | notes |
|---|---|---|
| `venue_id` | str | stable slug, e.g. `cinematheque` |
| `venue_name` | str | |
| `municipality` | str | `Vancouver`, `Burnaby`, `North Vancouver`, ... |
| `title` | str | film title exactly as the venue lists it |
| `tmdb_id` | int \| null | the TMDB film, set only on a confident match; its details are in the payload's `films` table |
| `original_title` | str \| null | title in the film's original script when it differs from `title` (e.g. `கல்கி 2898 AD`): TMDB's original title, set only when it's in a non-Latin script. Added in schema_version 3 |
| `original_language` | str \| null | ISO 639-1, TMDB convention (`xx` = no language); from the venue, or from TMDB when the venue doesn't say |
| `screening_language` | list[str] \| null | audio as screened; null = unknown |
| `subtitles` | list[str] \| null | `[]` = known to have none, null = unknown |
| `start` | ISO 8601 string | timezone-aware, always rendered in America/Vancouver |
| `screen` | str \| null | auditorium within the venue as it names it (e.g. `Lochmaddy Studio Theatre`); the venue is still one venue. Added in schema_version 4 |
| `format` | str \| null | normalised to `35mm`, `70mm`, `16mm`, `DCP`, `IMAX`, ... (`schema.KNOWN_FORMATS`); unknown values kept verbatim |
| `ticket_url` | str \| null | |
| `regular_price` | float \| null | regular adult admission, CAD |
| `price_confidence` | `"scraped"` \| `"estimated"` \| null | `scraped` = stated for this screening; `estimated` = from a general rate card |
| `scraped_at` | ISO 8601 string | UTC |

Snapshot files wrap the list, with a status entry for every venue attempted that day:

```json
{
  "schema_version": 2,
  "date": "2026-10-12",
  "generated_at": "2026-10-13T01:17:40+00:00",
  "showtime_count": 40,
  "venues": {
    "cinematheque": {
      "status": "failed",
      "failure": "error",
      "error": "CinemathequeParseError: no #eventCalendar days found; page layout changed?",
      "attempted_at": "2026-10-13T01:17:40+00:00",
      "showtimes_scraped_at": "2026-10-12T13:17:52+00:00",
      "showtime_count": 40,
      "skipped": []
    },
    "rio_theatre": {
      "status": "ok", "failure": null, "error": null,
      "attempted_at": "2026-10-13T01:17:40+00:00",
      "showtimes_scraped_at": "2026-10-13T01:17:40+00:00",
      "showtime_count": 25,
      "skipped": [
        {"title": "Crazy Diamonds 2026", "reason": "event tagged Live Event (not a film)",
         "start": "2026-11-14T21:00:00-07:00", "url": "https://riotheatre.ca/event/crazy-diamonds-2026/",
         "needs_review": false},
        {"title": "VIMFF 2026: Reel Rock 20 Vancouver", "reason": "event tagged neither Film Event nor Live Event",
         "start": "2026-11-15T19:00:00-07:00", "url": "https://riotheatre.ca/event/reel-rock-20-vancouver-vimff-2026/",
         "needs_review": true}
      ]
    }
  },
  "showtimes": [ ... ]
}
```

| venue field | meaning |
|---|---|
| `status` | `ok` or `failed`: the outcome of the **latest** attempt today |
| `failure` | `null`, `"error"` (the scraper raised, so the code or site broke), or `"no_showtimes"` (it ran cleanly but found nothing; the venue may be dark, or the parser may be silently missing everything) |
| `error` | exception type and message when `failure` is `"error"` |
| `attempted_at` | when the latest attempt ran |
| `showtimes_scraped_at` | when this venue's showtimes in this file were scraped; `null` if there are none. Earlier than `attempted_at` means the latest attempt failed and these are kept from an earlier run today |
| `showtime_count` | how many of `showtimes` belong to this venue |
| `skipped` | listings the scraper deliberately didn't turn into showtimes (live shows, rentals, meetings), each with a `reason`. `needs_review: true` means the scraper couldn't tell whether it's a film. Every listing a venue publishes ends up either in `showtimes` or here |

A venue with no entry wasn't attempted that day (e.g. it was disabled).

`showtimes.json` has the same shape plus two fields per venue:

| current-file field | meaning |
|---|---|
| `stale` | `true` when this venue's showtimes don't come from its latest attempt: the venue failed, and the data is either from an earlier run today or carried over from the previous `showtimes.json` |
| `last_confirmed_at` | when the showtimes shown were last scraped successfully (`null` if never) |

When a venue fails and today's snapshot has no showtimes for it,
`showtimes.json` carries over that venue's showtimes from the previous
`showtimes.json`, keeping only those starting today or later. The original
`last_confirmed_at` is kept, so data that stays stale for days still shows when it
was last confirmed. The first successful run clears it. Only venues in
today's snapshot are considered, so a venue that's disabled drops out.

Since schema_version 5 every payload also has a `films` table, keyed by
`tmdb_id` (as a string), for the films its showtimes match:

```json
"films": {
  "30959": {"id": 30959, "title": "Kwaidan", "original_title": "怪談", "original_language": "ja",
            "year": 1964, "directors": ["Masaki Kobayashi"], "runtime": 183,
            "overview": "...", "poster_path": "/....jpg"}
}
```

and `showtimes.json` has the match report, `tmdb`: `showtimes`,
`matched_showtimes`, `titles`, `matched_titles`, `unavailable` (why TMDB
couldn't be used this run, or null) and `unmatched`, one entry per title with
`venue_id`, `title`, `showtimes` and `reason`.

### Archive rules

- The date is the **Vancouver** calendar date of the run (the 18:17 run is 01:17 UTC the next day).
- Later runs on the same day update that day's file **venue by venue**:
  - a venue that succeeds replaces its showtimes;
  - a venue that fails is marked failed but keeps any showtimes an earlier successful run captured that day,
    so a broken evening run can't erase the morning's data;
  - a venue the run didn't attempt (`run --venue x`) is left untouched.
- A failure on the first run of a new day is recorded with zero showtimes.
  Yesterday's data is never copied forward into a new date.
- The archive never contains carried-over data: it records what each run observed.
  The stale fallback exists only in `showtimes.json`.
- Past dates are immutable. `archive.write_snapshot` refuses to touch an existing past date,
  and the workflow also checks `git diff` and fails if anything other than today's file was modified or deleted.

### Time zones

British Columbia stopped changing its clocks on 2026‑11‑01 and is now at
UTC−7 year-round. Older tz databases get that wrong, so `tzdata>=2026.5` is a
dependency and the workflows set `PYTHONTZPATH=""` so Python reads that package
instead of the runner's OS copy. If `test_scrape_uses_vancouver_offset_after_2026_time_change`
fails locally, run `pip install -U tzdata` and set `PYTHONTZPATH=""`.

### Being polite

`PoliteSession` sends an identifying User-Agent with a link to this repo, waits
at least 4 s between requests to the same host, honours `robots.txt`, and backs
off on 429/5xx (respecting `Retry-After`). Scrapers fetch only through
`self.fetcher.get_text(url)`, so they never bypass these rules.

### Reviewing ambiguous listings (`overrides.json`)

When a scraper can't tell whether a listing is a film, it skips it with
`needs_review: true` and the run warns:

```
review rio_theatre: VIMFF 2026: Reel Rock 20 Vancouver (2026-11-15 19:00): event tagged neither
Film Event nor Live Event; skipped, but it may be a film. To decide, add "350890" under
"rio_theatre" in overrides.json.
```

Record the decision in `overrides.json` at the repo root and commit it:

```json
{
  "rio_theatre": {
    "350890": {"film": true, "title": "VIMFF 2026: Reel Rock 20 Vancouver", "note": "mountain film festival"},
    "350909": {"film": false, "title": "Shawn Chidiac", "note": "stand-up comedy"}
  }
}
```

- The key is the listing's stable id at that venue (the WordPress post id for the
  Rio and Park). It's printed in the warning and stored as `key` on every skipped
  listing in the snapshot. One post covers all its sessions, so one decision
  covers every date it plays.
- `film` is required; `title` is for people reading the file (the tests require it);
  `note` is optional and is copied into the skip reason (`manual override: not a film (stand-up comedy)`).
- A decision wins over the scraper's own classification, including tags.
- The file is validated before every run. Unknown venue ids, missing or non-boolean
  `film`, and unknown fields stop the run with exit 1 before anything is scraped,
  and the unit tests (which the scheduled workflow runs first) check the committed file.
- Overrides are passed to each scraper as `self.overrides` (key -> `Override`).
  Only scrapers that classify listings use them (currently the Barker venues).

### Hand-entered listings (`manual_listings.json`)

For venues without a scraper (the Hollywood Theatre's film nights, FilmSoc
at the Norm), type the times into `manual_listings.json` at the repo root and
commit it:

```json
{
  "hollywood_theatre": [
    {"title": "Paris, Texas", "start": "2026-11-07T19:30:00-07:00",
     "source_url": "https://www.hollywoodtheatre.ca/events/paris-texas",
     "added": "2026-10-20", "price": 15, "note": "35mm print"}
  ]
}
```

- Keyed by venue id; the venue must be in `web/venues.json` and must not be a
  link-out venue (Cineplex times are never copied, even by hand).
- Required: `title`, `start`, `source_url` (where you read it) and `added`
  (the date you typed it in, `YYYY-MM-DD`). Optional: `price` (regular adult
  admission in CAD as the source states it; `0` = free) and `note` (shown
  under the title, e.g. "35mm print", "Q&A after").
- `start` is Vancouver wall-clock time with its offset, and the offset must be
  the one Vancouver uses on that date: `-07:00` from 2026-11-01 on. A stale
  `-08:00` is rejected with the corrected value in the message.
- Validated like `overrides.json`: before every run (exit 1, nothing scraped)
  and by the unit tests. Unknown fields, missing fields, duplicates, bad URLs
  and bad dates are all errors. Top-level `_` keys are comments; the
  `_example` block is tested too, so it's safe to copy.
- The page reads the file directly (Netlify copies it into `web/`), so a
  commit shows up on the next deploy without waiting for a scrape. Each
  listing is tagged **Hand-entered** with a "Details at …" link to
  `source_url`, and drops off the page once it has started: past entries can
  stay in the file. Nothing from it goes into `showtimes.json` or `archive/`,
  which record only what scrapers observed.

### TMDB matching

Each run matches every showtime to a TMDB film (`kinolocator/tmdb.py`), once
per venue and listed title. A wrong film is worse than none, so `tmdb_id` is
set only on a confident match:

1. `tmdb_overrides.json` decides first (below).
2. Listings that are several films stay unmatched: shorts programmes, double
   bills, "Five Films by …", "Program One: …", "Kieślowski Documentaries × 6",
   "The Crypt Keepers + The Day of the Beast", film-festival nights.
3. The title is cleaned (`titles.py`): possessive credits ("Ken Russell's"),
   quotes, format notes ("Presented in 70mm"), anniversary, language and
   other asides, sing-alongs, and framing ("Shot in Vancouver: …", ",
   introduced by …", ": In Conversation with …"). Queries go least-altered
   first, so "Mission: Impossible" is searched as it stands before "Impossible".
4. Only TMDB results whose title or original title equals the query (ignoring
   case, accents, punctuation and British spellings) are candidates. Then the
   venue's hints narrow them: **year** within one, **director** in common (any
   word order: the Cinematheque prints "Kobayashi Masaki"), **runtime** within
   12 minutes. Scrapers record hints with `self.hint(title, FilmHints(...))`:
   the Cinematheque's masthead (year, director, runtime, original title),
   VIFF's Director and Year fields, Barker's release year and director terms
   (Barker fills these from TMDB), Veezi's durations, and a year in the title
   ("The Thing (1982)").
5. One survivor with a hint checked is a match. The listing's language breaks
   a tie only after a real hint. With no hints at all, only a lone
   exact-title film with 50+ votes is accepted. A query that exists only
   because a credit was removed must be directed by that person.

So VIFF's "Atonement" (2026, Reed Van Dyk) doesn't become Joe Wright's 2007
film, and "The Thing" with no year stays unmatched as ambiguous.

For each matched film the run stores title, original title, original language,
year, director(s), runtime, overview and poster path in `films`, fills
`original_language` where the venue didn't, and sets `original_title` when
TMDB's is in another script (so `கல்கி 2898 AD` and `怪談` show as written).

**Checking the misses.** The run prints how many showtimes matched and every
unmatched title with its reason; on Actions they're in the run summary, and
`python -m kinolocator summary --tmdb` prints them from `showtimes.json`:

```
TMDB: 312 of 390 showtimes matched (128 of 165 titles)
      unmatched viff_centre: The Thing (1): ambiguous: 2 TMDB films titled 'The Thing' (1982, 2011)
      unmatched cinematheque: Five Films by Morgan Fisher (1): several films: several films in one programme
```

**`tmdb_overrides.json`** sets or blocks a match by hand, keyed by venue id (or
`"*"` for every venue) and the title exactly as listed:

```json
{
  "viff_centre": {"Atonement": {"tmdb_id": 1234567, "note": "the 2026 short"}},
  "*": {"Shrek Cabaret": {"tmdb_id": null, "note": "a live show around the film"}}
}
```

`tmdb_id` is required (a TMDB movie id, or `null` to keep it unmatched);
`note` is optional. It's validated like `overrides.json`: before every run
(exit 1, nothing scraped) and by the tests.

**Cache and rate limits.** TMDB responses go in `tmdb_cache.json`, which the
workflow commits: searches for 7 days, film details for 30, and only
exact-title candidates are kept from a search. A run only asks TMDB about
titles it hasn't seen recently, with requests 0.25 s apart (TMDB allows far
more). A failed venue's carried-over showtimes keep the match they already
have. If TMDB refuses (no token: HTTP 401) or fails, the run carries on from
the cache, the report's `unavailable` says why, and Actions shows a warning.

### Secrets

TMDB matching reads a TMDB **read access token** from `TMDB_READ_TOKEN` and
sends it as a Bearer header, never in a URL, so it isn't in logs, fixtures or
the cache. On Actions it's the repository secret `TMDB_READ_TOKEN`, mapped in
the `env:` block of `scrape.yml`. Whitespace around it (a newline from
pasting) is stripped. A value that still isn't a read access token (spaces or
other characters inside it, the wrong number of dot-separated parts, a v3 API
key) stops the run with exit 1 before anything is scraped, with a message that
says what's wrong but never the value. `PoliteSession` also blanks per-request
header values out of its error messages, because `requests` quotes a bad header
in its exception and that text ends up in the committed report. Without the variable, requests go out with no
token, which works where a proxy adds it (a network secret) and otherwise gets
a 401 and falls back to the cache. Other keys can use
`config.require_env(name, purpose)`, which fails with a clear message if the
variable is unset. Locally, put keys in `.env` (git-ignored) and export them.

## Web frontend (`web/`)

The tonight view: a Leaflet map of venues, a listings shelf, and a 5 PM–midnight
scrubber. Plain HTML, CSS and JS; no framework, no build tooling. Leaflet 1.9.4
is vendored in `web/vendor/leaflet/` (BSD-2, licence included).

- `web/venues.json`: id, name, neighbourhood, address, coordinates and site for
  every `venue_id`. A venue in the data but missing here is still listed, just
  without a map dot (and a console warning). `listings` says where a venue's
  times come from: absent for a scraper, `"hand_entered"` for
  `manual_listings.json` only, `"link_out"` for a venue that isn't scraped at
  all (`tests/test_venues.py` checks these against the scrapers).
- Matched films get a small poster in their shelf listing: TMDB's `w92`, or
  `w154` on high-density screens, lazy-loaded and fixed in size so nothing
  shifts. Unmatched listings show no poster and no placeholder.
- Clicking a listing (or its title, a button) opens the film's detail view, a
  `<dialog>`: poster, original-script title, year, director(s), runtime,
  synopsis, and every upcoming screening of the film across all venues by
  day, with prices and book links. An unmatched listing opens the same view
  for its own screenings. It fills the screen on a phone.
- The footer carries TMDB's required notice ("This product uses the TMDB API
  but is not endorsed or certified by TMDB") with its logo, `web/tmdb-logo.svg`.
  That file is a stand-in: a CC0 trace of TMDB's mark from Simple Icons, since
  the official files at themoviedb.org/about/logos-attribution couldn't be
  downloaded when this was built. Replace it with one of those.
- Link-out venues (the four City of Vancouver Cineplex locations) get a
  hollow, never-lit dot. Hovering shows the name; clicking opens a card with
  the address and "Showtimes on cineplex.com ↗" linking to that location's
  `showtimes_url`. They never merge with other dots, never count towards
  "N venues" or the "Last confirmed" list, and don't widen the opening view.
- `web/memberships.json`: membership discounts, shown in the page's
  Memberships menu. Adding one needs no code change. `applies_to` picks which
  prices a discount reduces: `rate_card` (`price_confidence: estimated`),
  `venue_stated` (`scraped`) or `all`. Event prices are left as listed.

**How the page gets `showtimes.json` on Netlify.** The snapshot lives at the repo
root, but Netlify publishes only `web/`. `netlify.toml` sets `publish = "web"`
and the deploy command `cp showtimes.json manual_listings.json web/`, so the page
fetches both from its own origin. Each snapshot commit from the
scrape workflow triggers a redeploy, so page and data always come from the same
commit. The copy is git-ignored. Netlify setup: connect the repo; build
settings come from `netlify.toml`.

**Preview locally**, the same way Netlify serves it:

```bash
cp showtimes.json manual_listings.json web/
python -m http.server 8000 -d web
# http://localhost:8000/?at=2026-10-19T18:10  (?at= pretends it's that Vancouver time)
```

Times are read from each showtime's own ISO string (wall clock plus offset), not
the browser's time-zone database, which may predate BC's permanent UTC-7 from
2026-11-01.

The basemap is Stadia Maps' Alidade Smooth Dark (OpenMapTiles, OpenStreetMap
data). Stadia authorises it by domain: the Stadia property is set up for
`kinolocator.netlify.app`, so there is no API key in the code. Stadia serves
`localhost` without one, so a local preview works too; any other host (a
Netlify deploy preview, say) needs adding to the property. A refused tile comes
back as HTTP 401 with a "401 Error" picture, which an `<img>` would show as if
it were the map, so the page fetches each tile and checks its status first. If
tiles fail or are refused, the dots and listings still work and the map says so.

Map dots that would overlap at the current zoom (VIFF Centre and the
Cinematheque, a block apart) merge into one ringed dot labelled with their
shared neighbourhood and venue count; clicking it, or zooming in, separates them.

Listings get one language tag: the screened language when there's exactly one
(so a Telugu dub of a Tamil film says Telugu), else the film's
`original_language`, else the first `screening_language`; none when it's English. "By your
price" compares prices with GST added for venues marked `prices_exclude_gst`
(5%; BC charges no PST on admissions), but each price is shown as listed.

## Adding a venue

One self-contained module, its fixtures and its tests. Nothing else needs editing,
because the registry discovers scrapers automatically.

1. **Investigate the site first.** Open the listings page with your browser's
   devtools Network tab. If showtimes arrive as JSON (XHR/fetch), scrape that
   endpoint instead of the HTML. Only reach for Playwright if the data is
   genuinely unavailable without running JavaScript. Check `robots.txt`.
   WordPress sites list their API at `/wp-json/`; that's how the Rio's Barker
   endpoints were found. Write what you found in the module docstring (see
   `cinematheque.py`).

   If the site runs software another venue already uses (e.g. Barker), subclass
   that shared scraper instead: `rio_theatre.py` is just identity fields on top
   of `_barker.BarkerScraper`.

2. **Create `kinolocator/scrapers/<venue_id>.py`:**

   ```python
   from ..schema import PriceConfidence, VANCOUVER
   from .base import Scraper

   class Hollywood(Scraper):
       venue_id = "hollywood_theatre"
       venue_name = "Hollywood Theatre"
       municipality = "Vancouver"
       homepage = "https://hollywoodtheatre.ca"

       def scrape(self):
           html = self.fetcher.get_text(f"{self.homepage}/showtimes")
           showtimes = []
           for e in parse_listings(html):      # pure function: text in, data out
               if not e.is_film:
                   self.skip(e.title, "live music night", start=e.start, url=e.url)
                   continue
               showtimes.append(self.showtime(title=e.title, start=e.start, ticket_url=e.url))
           return showtimes
   ```

   `self.showtime(...)` fills in venue id/name/municipality and `scraped_at`.
   If the venue's pages give a film's year, director, runtime or original
   title, pass them on with `self.hint(title, FilmHints(...))`; they make
   TMDB matching much more reliable.
   Keep parsing in pure `parse_*` functions that take response text, so they
   can be tested directly. Raise on layout changes (missing containers) rather
   than returning `[]`.

   **Never drop a listing silently.** Anything that isn't a film screening
   (comedy, concerts, rentals, meetings) goes through `self.skip(title, reason, ...)`
   so it's recorded in the snapshot. If you can't tell whether it's a film, pass
   `needs_review=True`. The run then raises a warning instead of guessing.
   A good test is that every listing ends up as a showtime or a skip
   (see `test_every_listing_is_a_showtime_or_a_recorded_skip`).

3. **Record fixtures** from the live site:

   ```sh
   python -m kinolocator record <venue_id>
   ```

   This runs your `scrape()` through a recording session and saves every
   response plus a `manifest.json` (URL -> file, with the recording time) to
   `tests/fixtures/<venue_id>/`. Before `scrape()` works, list starting pages
   in the class's `fixture_urls` and `record` saves those.

4. **Write `tests/test_<venue_id>.py`** against the fixtures. The `replay`
   fixture serves the recorded responses, and the scraper should use the
   recording time as `now` so date filtering matches:

   ```python
   def test_scrape(replay):
       session = replay("hollywood_theatre")
       showtimes = Hollywood(session, now=session.recorded_at).scrape()
       assert len(showtimes) == ...
   ```

   Assert concrete values (a specific film's start time, format, ticket URL),
   not just counts. Tests never touch the network (`conftest.py` blocks it), so
   a failing test means the code broke, not that the site changed. When the
   site *does* change, re-record, update the assertions, and commit both together.

5. Run `python -m kinolocator run --venue <venue_id> --dry-run` to check it live.
   Make sure `enabled` is `True` (the default) and open a PR.

## Venue notes

### Rio Theatre

- WordPress with the **Barker** cinema plugin, which serves JSON from `/wp-json/`. Each run
  makes 5 requests: `barker/v1/listings` (every session, films and live events, ISO
  times with offsets, ticket links), `wp/v2/barker_movie` and `wp/v2/barker_event` with
  `include=<ids>` (tags, language term ids, descriptions), `wp/v2/barker-movie-language`
  with `include=<term ids>` (language names), and the `/ticket-info/` rate card.
  Then 3 optional requests for TMDB hints: `barker/v1/movies-feed` (each
  movie's `release_year`), `wp/v2/barker_movie?_fields=id,barker-movie-director`
  and `wp/v2/barker-movie-director` (director names). If any fails, the venue
  still scrapes, without hints. (The Rio's recorded fixtures predate these;
  the Park's `park_theatre_2026-10-19` recording has them.)
- **Films vs. everything else** uses the Rio's own data: the post type (`/movie/` vs
  `/event/` permalink) and its *Film Event* / *Live Event* tags.

  | listing | result |
  |---|---|
  | movie post | film (includes live-score screenings and film + Q&A nights) |
  | event tagged Film Event (even if also Live Event) | film (e.g. Mountains on Stage, the Saturday-morning cartoon party) |
  | event tagged only Live Event | skipped: not a film |
  | event with neither tag | skipped, **needs review** |

  On 2026-10-18 the "neither tag" bucket held 4 VIMFF film nights *and* 4
  comedy shows, so no default would have been right. These listings are flagged
  instead of guessed, and once reviewed the decision goes in `overrides.json`
  keyed by post id (all 8 are recorded there). A decision there wins over the
  tags, so it can also correct a mis-tagged listing.
- Prices: a price stated in the description ("Adult Entry $62 + s/c",
  "General Admission – $16", "Tickets $35+ fees", a FREE screening) is *scraped*.
  Otherwise "Regular Adult $16.00" from `/ticket-info/` is used as *estimated*.
- Languages are the film's spoken-language list (TMDB-style; Rocky Horror lists
  German and English), so `original_language` is set only when a single language is listed.
  Each movie's language term ids are resolved to the terms' *names*, which are
  TMDB's native names ("తెలుగు", "日本語", "Pусский" with a Latin P, and "?????",
  TMDB's literal name for Kannada) and map to ISO codes in `languages.py`. Term
  ids are per site and the slugs in `class_list` lose every non-ASCII byte
  (Game Changer's four languages show there as bare ids), so neither is used.
  If the name lookup fails, the venue's scrape fails rather than dropping
  languages. `tests/test_barker_languages.py` replays a recorded Jan 2025
  window (Telugu, Tamil, Hindi, Kannada releases) and checks all 127 of the
  Rio's language terms. Punjabi can't be checked with Rio data: Tel Visa, a
  Punjabi film, has no language terms there at all.
- Format: a format in a bracketed aside of the title ("(Presented in 70mm)",
  "[IMAX]") is extracted for every Barker venue; the title is kept as listed.
  None of the Rio's titles recorded on 2026-10-18 name a format.

### Park Theatre

- Run by the Rio's operator on the same Barker plugin, so it is the Rio's
  scraper with different identity fields. Everything above applies; the
  differences, from fixtures recorded 2026-10-18 (65 listings, 63 films):
- **No rate card.** `/ticket-info/` 404s, so the scraper doesn't request it
  (`rates_path = None`). Regular screenings link to
  `tickets.theparktheatre.ca/events/<id>`, which is where their prices
  presumably are; that site hasn't been examined, so those prices are left empty.
  Only descriptions that state a price ("Tickets $35+ fees", "Adults $23", a
  FREE screening) give one, as *scraped*. The Rio's rate card says the joint
  membership saves $3 at both theatres but doesn't give the Park's price, so
  the Rio's $16 is not reused.
- Puts the format in titles: "Dune: Part Three (Presented in 70mm)" → `70mm`.
  The Insider and Advance Dune screenings say "Screening in 70mm" only in the
  description, which isn't parsed for format, so they have no `format`.
- Both untagged events on 2026-10-18 (Inspire Film Fest, Private Event) are
  decided in `overrides.json`.

### VIFF Centre

- WordPress with an events plugin whose REST routes are under `v1/attendable/`;
  ticketing is an Elevent widget
  (`content.elevent.app`) that isn't used. Each run makes about 45 requests:
  `/whats-on-calendar/` (for the public REST token and the screen list), the
  calendar JSON `/wp-json/v1/attendable/calendar/instances` with header
  `X-WP-Nonce: <token>` (every upcoming screening, ~2 months), `/ticket-info/`,
  and each event page once (language, format, per-screening tags, JSON-LD).
  robots.txt only disallows `/admin/`. If the token can't be read, or the
  calendar answers 403, the venue fails and the runner keeps the last good data.
- Two screens, **VIFF Cinema** and **Lochmaddy Studio Theatre**: one venue,
  with the screen kept in `Showtime.screen`. Only screenings listed under
  "VIFF Centre - <screen>", and placed at 1181 Seymour by the event page's
  JSON-LD where given, become showtimes. Festival or partner screenings
  elsewhere are skipped with the place in the reason.
- **Films vs. everything else**: the calendar's `eventType`. `Film` is a film;
  anything else ("VIFF Live", talks, workshops) is flagged for review and
  decided in `overrides.json` by VIFF's event id. On 2026-10-19 both VIFF Live
  events included a full film and are marked as films there.
- Prices: the `/ticket-info/` rate card has one column per screen (Adult $17
  in the VIFF Cinema, $15 in the Lochmaddy), used as *estimated* by screen.
  A screening tagged with an offer the event page prices ("First Look Fridays:
  $12 Tickets") gets that price as *scraped*. VIFF's prices exclude GST and are
  stored as listed; `web/venues.json` marks the venue `prices_exclude_gst`.
  Member tiers (VIFF+ $3 off, VIFF+ Premium / U25 $5 off, the same on both
  screens) are in `web/memberships.json`; a test checks them against the
  recorded rate card.
- Languages: a labelled field, "In Japanese with English subtitles" or just
  "English", parsed with the Cinematheque's rules (`languages.parse_language_line`).
  Format: the masthead line ("35mm", "35mm | Pantheon").
- Sold-out screenings have no booking link; `ticket_url` is the film's page.

### The Cinematheque

- No JSON API. The site is server-rendered HTML, so requests + BeautifulSoup is enough.
- `/films/calendar` gives date, time, title, film URL and ticket URL for about 10 weeks. Each
  film page adds format (`.filmFormat`) and a language line ("In Japanese with English
  subtitles", "Silent", ...). Rentals (e.g. VIFF) and non-screening events are recorded as
  skipped. An unfamiliar calendar entry type is skipped with `needs_review`.
- Prices aren't listed per screening. The general rate on `/about/visit` ($15) is
  used as an *estimated* price. A "Free Admission" callout gives a *scraped* $0.
- `original_language` is taken from the first listed language: the Cinematheque
  screens original versions, but TMDB matching should eventually be the source of truth.
- Tickets are sold through Agile Ticketing (`tickets.thecinematheque.ca`), which
  hasn't been checked for a structured feed with real per-screening prices.
- The site returns HTTP 429 to bursts of requests; the 4 s delay avoids it.
- Its robots.txt misspells `Disallow` in the `*` group. Python's `urllib.robotparser`
  then merges that group with the `GPTBot: Disallow /` group and blocks
  everyone. `fetch.parse_robots` accepts the same misspellings Google's parser
  does, which gives the intended result: everyone allowed except `/wp-admin/`,
  and the named AI crawlers blocked.

### The Norm Theatre (UBC)

Hand-entered, not scraped. Checked on 2026-10-20:

- **events.ubc.ca** is WordPress with The Events Calendar, so it has a clean
  JSON API (`/wp-json/tribe/events/v1/events?categories=161`, "Type – Film"),
  and robots.txt allows it. But the film category had **no upcoming events**,
  only 60 ever, and none from FilmSoc or Cinema Politica (searching all
  categories for "film society", "filmsoc" and "cinema politica" found none
  either). Departments post one-off screenings there; the Norm's regular
  programmes don't. A scraper for it would mostly return nothing.
- **ubcfilmsociety.ca** is a Wix site (robots.txt allows everything but
  lightbox URLs). It has no listings of its own: its "Screenings" button goes
  to a Showpass short link (`show.ps`).
- **Showpass** (checked 2026-10-20) has a public JSON API that robots.txt
  explicitly allows (`/api/`), and the short link resolves to FilmSoc's
  organiser (venue id 19906). But the only FilmSoc event there, past or
  upcoming, is the 2026/27 membership; its description says screenings
  (double features Mondays and Thursdays, monthly beer gardens) are posted
  on Instagram and the website. Searches for "filmsoc", "film society", "norm
  theatre" and "cinema politica" find nothing else. Screenings aren't sold
  individually, so there's nothing to scrape.

### Dunbar Theatre

- No site of its own: `dunbartheatre.ca` doesn't resolve in DNS (checked
  2026-10-20). Its Facebook page links to Veezi, which has two sites for it
  on `ticketing.uswest.veezi.com`: the main one (first-run films) and "Dunbar
  Theatre Special Events" (Tamil and Telugu releases, fundraisers).
- Veezi's API needs a token issued to the cinema, so the scraper reads the
  public `/sessions/?siteToken=…` pages (allowed by robots.txt; `/purchase/`
  isn't, and is only linked to). Each has schema.org JSON-LD, one event per
  session with an ISO start, duration and purchase link, and a "by film" tab
  with Veezi's film ids. The two are joined on the purchase link and must
  agree, time included.
- Veezi's time-zone data predates BC's permanent UTC-7: sessions from
  2026-11-01 come as `-08:00` while the page shows the same wall-clock time.
  The wall clock is kept, with Vancouver's real offset.
- **Language.** Nothing in Veezi's data says it. The Dunbar marks versions in
  the title, "Jailer 2 (Tamil)" and "Jailer 2 (Telugu)" (two Veezi films with
  one poster: the Telugu is a dub). A trailing parenthesis naming a language
  sets `screening_language`; `original_language` is left to TMDB, since a
  dub's screened language isn't the film's.
- Every main-site listing is a film. On the special-events site, a title that
  reads like an event ("Fundraiser - Wild Cats Soft Ball") is flagged for
  review: such fundraisers are usually a screening, but the title doesn't say
  of what. Decide it in `overrides.json` under `dunbar_theatre`, keyed
  `"special:<Veezi film id>"` (e.g. `"special:ST00000106"`).
- No prices: they appear only on the disallowed purchase pages. Durations
  become runtime hints for TMDB.
