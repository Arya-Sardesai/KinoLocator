"""Turning a title as a venue lists it into what to search TMDB for.

Venues decorate titles: "Ken Russell's 'The Devils'", "Dune: Part Three
(Presented in 70mm)", "Shot in Vancouver: The Changeling, introduced by Sam
Wiebe", "Jailer 2 (Tamil)". `clean_title` peels that off and returns search
queries in order of preference (the least-altered first), so a real title
that happens to look decorated ("Mission: Impossible", "Ferris Bueller's Day
Off") is tried as it stands before any shortened form.

Listings that are several films (shorts programmes, double bills, "Five
Films by ...") get a `multiple_films` reason instead: they are never matched.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"', "–": "-", "—": "-"})

# Several films in one listing. Matched against the whole title, case-insensitively.
_MULTI = [
    (r"\bshorts\b|\bshort films\b", "a programme of short films"),
    (r"\b(double|triple) (bill|feature)\b", "a double bill"),
    (r"\bmarathon\b", "a marathon of several films"),
    (r"^program(me)?\s+(\d+|one|two|three|four|five|six)\b", "a numbered programme"),
    (r"\b(two|three|four|five|six|seven|eight|nine|ten|\d+) (films|features|documentaries)\b", "several films in one programme"),
    (r"\bdocumentaries\b", "several films in one programme"),
    (r"×\s*\d+", "several films in one programme"),
    (r"\s\+\s", "several films (or a film and a live act) joined with '+'"),
    (r"\bcartoons\b|\bcartoon party\b|\bcereal party\b", "a programme of cartoons"),
    (r"\bfilm fest(ival)?\b", "a film festival programme"),
]
_MULTI = [(re.compile(p, re.IGNORECASE), why) for p, why in _MULTI]

# Framing after the title: ", introduced by Sam Wiebe", ": In Conversation with Zach Galligan".
_TRAILING_FRAMING = re.compile(
    r"\s*(?:[,:\-]\s*|\s)(?:introduced by|presented by|hosted by|followed by|with special guests?|"
    r"in conversation with|with (?:the )?filmmakers?|with director|plus q&a|with q&a|\+ q&a|q&a with)\b.*$",
    re.IGNORECASE,
)
_IN_CONVERSATION = re.compile(r"\s+with\s+.+\s+in conversation\s*$", re.IGNORECASE)
_TRAILING_ASIDE = re.compile(r"\s*[(\[]([^()\[\]]*)[)\]]\s*$")
_YEAR = re.compile(r"^(18[89]\d|19\d\d|20\d\d)$")
_FORMAT_NOTE = re.compile(
    r"\s*[-,:]?\s*\b(?:presented |screening |shown )?in (?:glorious )?(?:70\s?mm|35\s?mm|16\s?mm|4k|imax|3-?d)\b.*$",
    re.IGNORECASE,
)
_ALONG = re.compile(r"\s*[-:]?\s*\b(?:sing|quote|shout)[- ]?a?[- ]?long\b.*$", re.IGNORECASE)
# "Ken Russell's ..." / "Brian de Palma's ...": a possessive credit of two to four words.
_POSSESSIVE = re.compile(
    r"^(?!(?:A|An|The)\s)(?P<name>[A-Z][\w.\-]*(?:\s+(?:de|del|della|di|da|van|von|der|le|la|[A-Z][\w.\-]*)){1,3})'s\s+(?P<rest>.+)$"
)
_WRAPPING_QUOTES = re.compile(r"""^(['"])(.+)\1$""")


@dataclass(frozen=True)
class CleanTitle:
    queries: tuple[str, ...]
    """Titles to search for, most faithful first. Empty if nothing is left."""
    year: int | None = None
    """A year the venue put in the title: "The Thing (1982)"."""
    credit: str | None = None
    """A possessive credit that was removed: "Ken Russell" from "Ken Russell's 'The Devils'"."""
    credited_queries: tuple[str, ...] = field(default=())
    """The queries that exist only because the credit was removed; a match on
    one of them must be confirmed by the credited name directing the film."""
    multiple_films: str | None = None
    """Why the listing is several films, if it is."""


def _strip_asides(title: str) -> tuple[str, int | None]:
    """Remove trailing (...) groups, keeping a year if one is there."""
    year = None
    while m := _TRAILING_ASIDE.search(title):
        inner = m[1].strip()
        if _YEAR.match(inner):
            year = int(inner)
        title = title[: m.start()]
    return title.strip(), year


def _strip_framing(title: str) -> str:
    title = _TRAILING_FRAMING.sub("", title)
    title = _IN_CONVERSATION.sub("", title)
    title = _FORMAT_NOTE.sub("", title)
    title = _ALONG.sub("", title)
    return title.strip(" -,:")


def _unquote(title: str) -> str:
    m = _WRAPPING_QUOTES.match(title.strip())
    return m[2].strip() if m else title.strip()


def clean_title(raw: str) -> CleanTitle:
    title = " ".join(raw.translate(_QUOTES).split())
    for pattern, why in _MULTI:
        if pattern.search(title):
            return CleanTitle((), multiple_films=why)

    title, year = _strip_asides(title)
    title = _unquote(_strip_framing(title))
    title, year2 = _strip_asides(title)  # an aside before the framing: "X (1982), introduced by Y"
    year = year or year2

    queries: list[str] = [title] if title else []
    # A series or event prefix: "Shot in Vancouver: The Changeling", "VIMFF 2026: Extreme Antarctica".
    if ": " in title:
        prefix, rest = title.split(": ", 1)
        if len(prefix.split()) <= 8 and rest:
            queries.append(_unquote(rest))
    # A film with a live act: "Vampyr × Applied Silence" (a live score).
    if " × " in title:
        queries.append(title.split(" × ", 1)[0].strip())
    credit = None
    credited: list[str] = []
    for q in list(queries):
        if m := _POSSESSIVE.match(q):
            credit = m["name"]
            credited.append(_unquote(m["rest"]))
    queries += credited
    unique = tuple(dict.fromkeys(q for q in queries if q))
    return CleanTitle(unique, year, credit, tuple(dict.fromkeys(credited)))


_SPELLINGS = {
    "colour": "color", "colours": "colors", "centre": "center", "theatre": "theater", "honour": "honor",
    "favourite": "favorite", "grey": "gray", "labour": "labor", "neighbour": "neighbor",
    "neighbours": "neighbors", "harbour": "harbor", "programme": "program", "travelling": "traveling",
}


def fold(text: str) -> str:
    """Comparison form of a title: no accents, case, punctuation or British spellings."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_QUOTES).lower())
    text = "".join(c for c in decomposed if not unicodedata.combining(c))
    text = text.replace("&", " and ")
    words = re.findall(r"\w+", text)
    return " ".join(_SPELLINGS.get(w, w) for w in words)


def is_latin(text: str) -> bool:
    """True if every letter in `text` is Latin script ("Amélie" yes, "怪談" no)."""
    for c in text:
        if c.isalpha() and not unicodedata.name(c, "").startswith("LATIN"):
            return False
    return True
