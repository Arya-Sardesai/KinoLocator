"""Mapping from language names (as venues write them) to ISO 639-1 codes."""

from __future__ import annotations

import logging
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

# Real languages with no ISO 639-1 code: dropped without a warning.
_NO_ISO_639_1 = {"hokkien", "shanghainese", "teochew", "hakka", "taiwanese", "michif"}


def _fold(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name.strip().lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def language_code(name: str) -> str | None:
    """ISO 639-1 code for a language name (English or endonym), or None (logged) if unknown."""
    folded = _fold(name)
    code = _CODES.get(folded)
    if code is None and folded not in _NO_ISO_639_1:
        log.warning("unknown language name %r; add it to kinolocator/languages.py", name)
    return code
