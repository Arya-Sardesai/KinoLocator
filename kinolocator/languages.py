"""Mapping from English language names (as venues write them) to ISO 639-1 codes."""

from __future__ import annotations

import logging

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
    "kazakh": "kk", "khmer": "km", "korean": "ko", "kurdish": "ku", "latvian": "lv",
    "lithuanian": "lt", "macedonian": "mk", "malay": "ms", "malayalam": "ml",
    "mandarin": "zh", "marathi": "mr", "mongolian": "mn", "nepali": "ne", "norwegian": "no",
    "polish": "pl", "portuguese": "pt", "punjabi": "pa", "romanian": "ro", "russian": "ru",
    "serbian": "sr", "sinhala": "si", "slovak": "sk", "slovenian": "sl", "spanish": "es",
    "swahili": "sw", "swedish": "sv", "tamil": "ta", "telugu": "te", "thai": "th",
    "tibetan": "bo", "turkish": "tr", "ukrainian": "uk", "urdu": "ur", "vietnamese": "vi",
    "welsh": "cy", "wolof": "wo", "yiddish": "yi", "yoruba": "yo", "zulu": "zu",
}


def language_code(name: str) -> str | None:
    """ISO 639-1 code for an English language name, or None (logged) if unknown."""
    code = _CODES.get(name.strip().lower())
    if code is None:
        log.warning("unknown language name %r; add it to kinolocator/languages.py", name)
    return code
