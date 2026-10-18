# Kinolocator

Showtime aggregator for Greater Vancouver cinemas. Twice a day a GitHub Actions
job scrapes each venue's website and commits two things to this repo:

- `showtimes.json`: every upcoming showtime from the latest run, with a
  failed venue's last good showtimes carried over and flagged stale
- `archive/YYYY/MM/DD.json`: a dated snapshot of exactly what was observed, one per day,
  never rewritten after that day

| Venue | Module | Source | Status |
|---|---|---|---|
| The Cinematheque (Vancouver) | `scrapers/cinematheque.py` | HTML (no API) | live |
| Rio Theatre (Vancouver) | `scrapers/rio_theatre.py` | Barker WordPress JSON API | live |
| Park Theatre (Vancouver) | `scrapers/park_theatre.py` | Barker (assumed, unverified) | disabled until verified |

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
  scrapers/
    __init__.py    auto-discovers every Scraper subclass in this package
    base.py        Scraper base class
    _barker.py     shared implementation for sites on the Barker cinema plugin
                   ("_" modules hold shared code and aren't registered as venues)
    cinematheque.py
    rio_theatre.py   (Barker)
    park_theatre.py  (Barker, disabled: unverified)
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

1. writes today's snapshot and `showtimes.json` with every venue that
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
`(134 showtimes; FAILED: rio_theatre (error, showing stale data from 2026-10-18T01:17:40+00:00); 4 listing(s) need review)`.
`python -m kinolocator summary` prints that line for any `showtimes.json`.

### The Showtime schema

| field | type | notes |
|---|---|---|
| `venue_id` | str | stable slug, e.g. `cinematheque` |
| `venue_name` | str | |
| `municipality` | str | `Vancouver`, `Burnaby`, `North Vancouver`, ... |
| `title` | str | film title exactly as the venue lists it |
| `tmdb_id` | int \| null | not populated yet (reserved for TMDB matching) |
| `original_language` | str \| null | ISO 639-1, TMDB convention (`xx` = no language) |
| `screening_language` | list[str] \| null | audio as screened; null = unknown |
| `subtitles` | list[str] \| null | `[]` = known to have none, null = unknown |
| `start` | ISO 8601 string | timezone-aware, always rendered in America/Vancouver |
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

### Secrets

No current scraper needs a key. When one does (e.g. TMDB), read it with
`config.require_env("TMDB_API_KEY", "TMDB matching")`, which fails with a clear
message if the variable is unset. Locally, put keys in `.env` (git-ignored) and
export them. On Actions, add a repository secret and map it in the `env:`
block of `scrape.yml`.

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
  makes 4 requests: `barker/v1/listings` (every session, films and live events, ISO
  times with offsets, ticket links), `wp/v2/barker_movie` and `wp/v2/barker_event` with
  `include=<ids>` (tags, languages, descriptions), and the `/ticket-info/` rate card.
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
  instead of guessed. If they become frequent, possible fixes are a per-venue
  allowlist of title patterns (e.g. `VIMFF`), or asking the Rio to tag them.
- Prices: a price stated in the description ("Adult Entry $62 + s/c",
  "General Admission – $16") is *scraped*. Otherwise "Regular Adult $16.00" from
  `/ticket-info/` is used as *estimated*.
- Languages are the film's spoken-language list (TMDB-style; Rocky Horror lists
  German and English), so `original_language` is set only when a single language is listed.
  No projection format is published.

### Park Theatre

- Run by the Rio's operator ("Rio & Park Theatre" memberships; Rio listings link
  to `www.theparktheatre.ca/event/<slug>/`, Barker's permalink style), so it is
  very likely the same Barker API. **Not yet verified**: `www.theparktheatre.ca`
  couldn't be reached from the environment this was written in. The module is
  a disabled `BarkerScraper` subclass. Its docstring lists the steps to verify,
  record fixtures, test and enable it.

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
