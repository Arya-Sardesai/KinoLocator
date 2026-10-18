"""Mapping from language names (as venues write them) to ISO 639-1 codes."""

from __future__ import annotations

import logging
import re
import unicodedata

log = logging.getLogger(__name__)

# TMDB's code for films with no spoken language.
NO_LANGUAGE = "xx"

_CODES = {
    "afrikaans": "af", "amharic": "am", "arabic": "ar", "armenian": "hy", "bengali": "bn",
    "bosnian": "bs", "bulgarian": "bg", "cantonese": "cn", "catalan": "ca", "chinese": "zh",
    "croatian": "hr", "czech": "cs", "danish": "da", "dutch": "nl", "english": "en",
    "estonian": "et", "farsi": "fa", "persian": "fa", "filipino": "tl", "tagalog": "tl",
    "finnish": "fi", "flemish": "nl", "french": "fr", "georgian": "ka", "german": "de",
    "greek": "el", "hebrew": "he", "hindi": "hi", "hungarian": "hu", "icelandic": "is",
    "indonesian": "id", "inuktitut": "iu", "irish": "ga", "italian": "it", "japanese": "ja",
    "kazakh": "kk", "khmer": "km", "korean": "ko", "kurdish": "ku", "latin": "la", "latvian": "lv",
    "lithuanian": "lt", "macedonian": "mk", "malay": "ms", "malayalam": "ml",
    "mandarin": "zh", "marathi": "mr", "mongolian": "mn", "nepali": "ne", "norwegian": "no",
    "polish": "pl", "portuguese": "pt", "punjabi": "pa", "romanian": "ro", "russian": "ru",
    "serbian": "sr", "sinhala": "si", "slovak": "sk", "slovenian": "sl", "spanish": "es",
    "swahili": "sw", "swedish": "sv", "tamil": "ta", "telugu": "te", "thai": "th",
    "tibetan": "bo", "turkish": "tr", "ukrainian": "uk", "urdu": "ur", "vietnamese": "vi",
    "welsh": "cy", "wolof": "wo", "yiddish": "yi", "yoruba": "yo", "zulu": "zu",
}


# Endonyms, as some venues (e.g. the Rio) label languages. Keys are compared
# with diacritics stripped, so "Français" matches "francais".
_CODES.update({
    "francais": "fr", "espanol": "es", "deutsch": "de", "italiano": "it",
    "portugues": "pt", "nederlands": "nl", "svenska": "sv", "norsk": "no",
    "dansk": "da", "suomi": "fi", "polski": "pl", "cestina": "cs", "magyar": "hu",
    "turkce": "tr", "ellinika": "el", "russkiy": "ru", "nihongo": "ja",
    "putonghua": "zh", "guoyu": "zh", "hangugeo": "ko", "farsi": "fa",
    "no language": NO_LANGUAGE, "silent": NO_LANGUAGE,
    "maltese": "mt", "slovene": "sl", "galician": "gl", "basque": "eu",
    "estonian": "et", "luxembourgish": "lb", "albanian": "sq", "azerbaijani": "az",
    "uzbek": "uz", "kyrgyz": "ky", "tajik": "tg", "pashto": "ps", "dari": "fa",
    "lao": "lo", "burmese": "my", "gujarati": "gu", "kannada": "kn", "cree": "cr", "ojibwe": "oj",
})

# More names from the Barker taxonomies (TMDB's native names, or ISO 639's
# English ones such as "Spanish; Castilian", which language_code splits).
_CODES.update({
    "belarusian": "be", "bislama": "bi", "esperanto": "eo", "fulfulde": "ff", "gaeilge": "ga",
    "haitian": "ht", "haitian creole": "ht", "kinyarwanda": "rw", "kirundi": "rn",
    "kiswahili": "sw", "maori": "mi", "moldavian": "ro", "moldovan": "ro", "navajo": "nv",
    "navaho": "nv", "panjabi": "pa", "romansh": "rm", "sinhalese": "si", "somali": "so",
    "xhosa": "xh", "ede yoruba": "yo", "ede-yoruba": "yo", "bahasa indonesia": "id", "bahasa melayu": "ms",
    "bosanski": "bs", "hrvatski": "hr", "islenska": "is", "romana": "ro", "slovencina": "sk",
    "slovenscina": "sl", "srpski": "sr", "tieng viet": "vi", "cesky": "cs", "azerbaycan": "az", "azərbaycan": "az",
    "tibetan standard": "bo",
})

# Endonyms in non-Latin scripts. TMDB (and so Barker) names many languages
# this way, e.g. "தமிழ்", "日本語", "广州话 / 廣州話"; keys are folded like lookups.
_CODES.update({
    # South Asia
    "हिन्दी": "hi", "हिंदी": "hi", "தமிழ்": "ta", "తెలుగు": "te", "ಕನ್ನಡ": "kn",
    "മലയാളം": "ml", "ਪੰਜਾਬੀ": "pa", "پنجابی": "pa", "বাংলা": "bn", "मराठी": "mr",
    "ગુજરાતી": "gu", "ଓଡ଼ିଆ": "or", "অসমীয়া": "as", "नेपाली": "ne", "සිංහල": "si",
    "اردو": "ur", "پښتو": "ps", "ދިވެހި": "dv",
    # TMDB's native name for Kannada is literally "?????". On the Rio's site it
    # is attached only to Kannada films (Kantara: Chapter One, Toby, Kabzaa, ...).
    "?????": "kn",
    # East and Southeast Asia
    "日本語": "ja", "普通话": "zh", "中文": "zh", "汉语": "zh", "漢語": "zh", "國語": "zh",
    "广州话": "cn", "廣州話": "cn", "粵語": "cn", "粤语": "cn", "한국어": "ko", "조선말": "ko",
    "ภาษาไทย": "th", "ไทย": "th", "ພາສາລາວ": "lo", "ລາວ": "lo", "ភាសាខ្មែរ": "km", "ខ្មែរ": "km",
    "မြန်မာ": "my", "монгол": "mn", "བོད་སྐད": "bo",
    # West Asia, Caucasus, Africa
    "العربية": "ar", "فارسی": "fa", "עברית": "he", "ייִדיש": "yi", "ქართული": "ka",
    "հայերեն": "hy", "አማርኛ": "am",
    # Cyrillic and Greek. TMDB spells Russian "Pусский" with a Latin P.
    "русский": "ru", "pусский": "ru", "український": "uk", "українська": "uk",
    "беларуская мова": "be", "беларуская": "be", "български език": "bg", "български": "bg",
    "српски": "sr", "македонски јазик": "mk", "македонски": "mk", "қазақ": "kk",
    "қазақ тілі": "kk", "кыргызча": "ky", "тоҷикӣ": "tg", "ελληνικά": "el",
})

# Real languages with no ISO 639-1 code: dropped without a warning. Also the
# stray halves of ISO names like "Khmer, Central" that Barker stores as terms.
_NO_ISO_639_1 = {
    "hokkien", "shanghainese", "teochew", "hakka", "taiwanese", "michif", "konkani", "tulu",
    "central", "modern",
}


def _fold(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name.strip().lower())
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


_CODES = {_fold(k): v for k, v in _CODES.items()}


def _candidates(folded: str) -> list[str]:
    """The name, then without "(...)" asides, then each part of "A; B" / "A / B" / "A, B"."""
    base = re.sub(r"\s*\([^)]*\)", "", folded).strip()
    return [folded, base, *(p for p in re.split(r"\s*[;/,]\s*", base) if p)]


def language_code(name: str) -> str | None:
    """ISO 639-1 code for a language name (English or endonym), or None (logged) if unknown."""
    candidates = _candidates(_fold(name))
    for candidate in candidates:
        if code := _CODES.get(candidate):
            return code
    if not any(c in _NO_ISO_639_1 for c in candidates):
        log.warning("unknown language name %r; add it to kinolocator/languages.py", name)
    return None
