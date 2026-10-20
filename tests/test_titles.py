from __future__ import annotations

import pytest

from kinolocator.titles import clean_title, fold, is_latin


@pytest.mark.parametrize(
    ("raw", "queries"),
    [
        # Real listings, 2026-10.
        ("Kwaidan", ["Kwaidan"]),
        ("Ken Russell's 'The Devils'", ["Ken Russell's 'The Devils'", "The Devils"]),
        ("Ken Russell’s ‘The Devils’", ["Ken Russell's 'The Devils'", "The Devils"]),
        ("Brian de Palma's 'Carrie' (50th Anniversary Screening)", ["Brian de Palma's 'Carrie'", "Carrie"]),
        ("Dune: Part Three (Presented in 70mm)", ["Dune: Part Three", "Part Three"]),
        ("Beetlejuice (FREE Halloween Screening!)", ["Beetlejuice"]),
        ("The Rocky Horror Picture Show (Rockytober!)", ["The Rocky Horror Picture Show"]),
        ("Shot in Vancouver: The Changeling, introduced by Sam Wiebe", ["Shot in Vancouver: The Changeling", "The Changeling"]),
        ("Gremlins: In Conversation with Zach Galligan", ["Gremlins"]),
        ("Traces of Home (With Filmmakers Dr. Gabor Maté & Colette Ghunim Q&A)", ["Traces of Home"]),
        ("Menopause: Coming in Hot (Filmmakers in Attendance for Q&A)", ["Menopause: Coming in Hot", "Coming in Hot"]),
        ("Jailer 2 (Tamil)", ["Jailer 2"]),
        ("Vampyr × Applied Silence", ["Vampyr × Applied Silence", "Vampyr"]),
        ("Rex: A Dinosaur’s Story", ["Rex: A Dinosaur's Story", "A Dinosaur's Story"]),
        ("VIMFF 2026: Extreme Antarctica", ["VIMFF 2026: Extreme Antarctica", "Extreme Antarctica"]),
        # Real titles that only look decorated keep their full form first.
        ("Mission: Impossible", ["Mission: Impossible", "Impossible"]),
        ("Ferris Bueller's Day Off", ["Ferris Bueller's Day Off", "Day Off"]),
        ("Schindler's List", ["Schindler's List"]),
        ("A Short Film About Killing", ["A Short Film About Killing"]),
        ("Grease Sing-Along", ["Grease"]),
        ("The Thing (1982), introduced by Jane Doe", ["The Thing"]),
    ],
)
def test_queries(raw, queries):
    assert list(clean_title(raw).queries) == queries


def test_year_and_credit():
    assert clean_title("The Thing (1982)").year == 1982
    c = clean_title("Ken Russell's 'The Devils'")
    assert c.credit == "Ken Russell" and c.credited_queries == ("The Devils",)
    assert clean_title("Rex: A Dinosaur’s Story").credit is None  # "A Dinosaur" is not a person


@pytest.mark.parametrize(
    "raw",
    [
        "Five Films by Morgan Fisher",
        "Kieślowski Documentaries × 6",
        "Program One: Figure and Ground",
        "Popeye Meets Sindbad the Sailor and More Fleischer Cartoons",
        "Halloween Live! The Crypt Keepers + The Day of the Beast",
        "Coast Mountain Film Fest: Ski Night",
        "Late Night Shorts",
        "Double Bill: Alien / Aliens",
        "The Saturday Morning All-You-Can-Eat Cereal Party: Halloween Edition!",
    ],
)
def test_multiple_films(raw):
    c = clean_title(raw)
    assert c.multiple_films and c.queries == ()


def test_fold():
    assert fold("Three Colours: Blue") == fold("Three Colors: Blue") == "three colors blue"
    assert fold("The Double Life of Véronique") == "the double life of veronique"
    assert fold("Meet Me In St. Louis") == fold("Meet Me in St. Louis")
    assert fold("Thelma & Louise") == "thelma and louise"


def test_is_latin():
    assert is_latin("Amélie") and is_latin("Dražen") and is_latin("3.0 Kilograms")
    assert not is_latin("怪談") and not is_latin("ஜெயிலர் 2") and not is_latin("Русский")
