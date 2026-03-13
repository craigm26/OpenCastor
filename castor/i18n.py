"""Multi-language robot command translation for OpenCastor (issue #141).

Detects the language of incoming channel messages and translates them to
English before passing to the LLM brain.  Translates responses back.
No external API needed — uses built-in phrase tables for common robot
commands and patterns.

Supported target languages: es, fr, de, zh, ja, pt, ar
Source language: auto-detected or set via CASTOR_LANGUAGE env var.

Usage::

    from castor.i18n import get_translator

    t = get_translator()
    en = t.to_english("avance tout droit", source_lang="fr")
    response = t.from_english("Moving forward at speed 0.5", target_lang="fr")

REST API (integrated via channels -- no dedicated endpoint needed):
    Headers or config: CASTOR_LANGUAGE=auto|en|es|fr|de|zh|ja|pt|ar
"""

import logging
import os
import re
from typing import Optional

logger = logging.getLogger("OpenCastor.I18n")

CASTOR_LANGUAGE = os.getenv("CASTOR_LANGUAGE", "auto")

# ---------------------------------------------------------------------------
# Phrase tables: {lang_code: {native_phrase: english_phrase}}
# Each entry uses lowercase normalised keys.
# ---------------------------------------------------------------------------

_TO_ENGLISH: dict[str, dict[str, str]] = {
    "es": {
        "adelante": "go forward",
        "avanzar": "go forward",
        "avanza": "go forward",
        "atrás": "go backward",
        "retroceder": "go backward",
        "retrocede": "go backward",
        "gira a la izquierda": "turn left",
        "izquierda": "turn left",
        "gira a la derecha": "turn right",
        "derecha": "turn right",
        "para": "stop",
        "parar": "stop",
        "detente": "stop",
        "detener": "stop",
        "fotografía": "take a photo",
        "foto": "take a photo",
        "captura": "take a snapshot",
        "estado": "report status",
        "ayuda": "help",
        "más rápido": "increase speed",
        "más despacio": "decrease speed",
        "patrulla": "start patrol",
        "explorar": "explore",
    },
    "fr": {
        "avance": "go forward",
        "avancer": "go forward",
        "avance tout droit": "go straight",
        "reculer": "go backward",
        "recule": "go backward",
        "tourne à gauche": "turn left",
        "gauche": "turn left",
        "tourne à droite": "turn right",
        "droite": "turn right",
        "arrête": "stop",
        "arrêter": "stop",
        "stop": "stop",
        "prends une photo": "take a photo",
        "photo": "take a photo",
        "capture": "take a snapshot",
        "état": "report status",
        "aide": "help",
        "plus vite": "increase speed",
        "moins vite": "decrease speed",
        "patrouille": "start patrol",
        "explorer": "explore",
    },
    "de": {
        "vorwärts": "go forward",
        "vorwärts fahren": "go forward",
        "rückwärts": "go backward",
        "links abbiegen": "turn left",
        "links": "turn left",
        "rechts abbiegen": "turn right",
        "rechts": "turn right",
        "stopp": "stop",
        "anhalten": "stop",
        "foto machen": "take a photo",
        "foto": "take a photo",
        "schnappschuss": "take a snapshot",
        "status": "report status",
        "hilfe": "help",
        "schneller": "increase speed",
        "langsamer": "decrease speed",
        "patrouillieren": "start patrol",
        "erkunden": "explore",
    },
    "zh": {
        "前进": "go forward",
        "向前": "go forward",
        "后退": "go backward",
        "向后": "go backward",
        "左转": "turn left",
        "向左": "turn left",
        "右转": "turn right",
        "向右": "turn right",
        "停止": "stop",
        "停": "stop",
        "拍照": "take a photo",
        "截图": "take a snapshot",
        "状态": "report status",
        "帮助": "help",
        "加速": "increase speed",
        "减速": "decrease speed",
        "巡逻": "start patrol",
        "探索": "explore",
    },
    "ja": {
        "前進": "go forward",
        "まえに": "go forward",
        "後退": "go backward",
        "うしろに": "go backward",
        "左折": "turn left",
        "ひだりに": "turn left",
        "右折": "turn right",
        "みぎに": "turn right",
        "停止": "stop",
        "とまれ": "stop",
        "写真を撮る": "take a photo",
        "写真": "take a photo",
        "状態": "report status",
        "ヘルプ": "help",
        "スピードアップ": "increase speed",
        "スローダウン": "decrease speed",
        "パトロール": "start patrol",
        "探索": "explore",
    },
    "pt": {
        "avançar": "go forward",
        "frente": "go forward",
        "recuar": "go backward",
        "trás": "go backward",
        "virar à esquerda": "turn left",
        "esquerda": "turn left",
        "virar à direita": "turn right",
        "direita": "turn right",
        "parar": "stop",
        "pare": "stop",
        "tirar foto": "take a photo",
        "foto": "take a photo",
        "captura de tela": "take a snapshot",
        "status": "report status",
        "ajuda": "help",
        "mais rápido": "increase speed",
        "mais devagar": "decrease speed",
        "patrulha": "start patrol",
        "explorar": "explore",
    },
    "ar": {
        "تقدم": "go forward",
        "إلى الأمام": "go forward",
        "تراجع": "go backward",
        "إلى الخلف": "go backward",
        "انعطف يساراً": "turn left",
        "يساراً": "turn left",
        "انعطف يميناً": "turn right",
        "يميناً": "turn right",
        "توقف": "stop",
        "قف": "stop",
        "التقط صورة": "take a photo",
        "صورة": "take a photo",
        "الحالة": "report status",
        "مساعدة": "help",
        "أسرع": "increase speed",
        "أبطأ": "decrease speed",
        "دورية": "start patrol",
        "استكشاف": "explore",
    },
}

# Reverse tables: english -> native (for response translation)
_FROM_ENGLISH: dict[str, dict[str, str]] = {}
for _lang, _table in _TO_ENGLISH.items():
    _FROM_ENGLISH[_lang] = {v: k for k, v in _table.items()}

# Language detection heuristics: unique character ranges.
# Japanese is checked before Chinese because Japanese text often contains
# CJK characters (U+4E00–U+9FFF) that would also match the Chinese range.
_LANG_PATTERNS = [
    ("ja", re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")),  # hiragana/katakana (ja-only)
    ("zh", re.compile(r"[\u4e00-\u9fff]")),
    ("ar", re.compile(r"[\u0600-\u06ff]")),
]

# Common words per language for Latin-script detection
_LATIN_MARKERS: dict[str, tuple[str, ...]] = {
    "es": ("adelante", "atrás", "izquierda", "derecha", "para", "avanza", "detente"),
    "fr": ("avance", "reculer", "gauche", "droite", "arrête", "tourne"),
    "de": ("vorwärts", "rückwärts", "links", "rechts", "stopp", "anhalten"),
    "pt": ("avançar", "recuar", "esquerda", "direita", "parar", "virar"),
}


class Translator:
    """Phrase-table based robot command translator.

    Args:
        default_target: Language code to translate responses to when
                        the source language is detected.  "auto" means
                        reply in the same language as the input.
    """

    def __init__(self, default_target: str = CASTOR_LANGUAGE):
        self._default_target = default_target

    def detect(self, text: str) -> str:
        """Detect the language of *text*.

        Returns:
            BCP-47 language code (e.g. "es", "fr") or "en" as fallback.
        """
        # Unicode range detection (non-Latin scripts)
        for lang, pattern in _LANG_PATTERNS:
            if pattern.search(text):
                return lang

        lowered = text.lower()
        # Latin script: check for known marker words
        for lang, markers in _LATIN_MARKERS.items():
            for marker in markers:
                if marker in lowered:
                    return lang

        return "en"

    def to_english(self, text: str, source_lang: Optional[str] = None) -> tuple[str, str]:
        """Translate *text* to English.

        Args:
            text: Input text (any supported language).
            source_lang: Language code (auto-detects if None).

        Returns:
            Tuple of (translated_text, detected_lang).
            If no phrase match, returns the original text unchanged.
        """
        lang = source_lang or self.detect(text)
        if lang == "en":
            return text, "en"

        table = _TO_ENGLISH.get(lang, {})
        lowered = text.strip().lower()

        # Try longest-match first
        for phrase in sorted(table.keys(), key=len, reverse=True):
            if phrase in lowered:
                translated = lowered.replace(phrase, table[phrase], 1)
                logger.debug("i18n [%s→en]: %r → %r", lang, text, translated)
                return translated, lang

        # No match — return as-is (LLM may handle it)
        logger.debug("i18n [%s→en]: no match for %r", lang, text)
        return text, lang

    def from_english(self, text: str, target_lang: str) -> str:
        """Translate an English response to *target_lang*.

        Args:
            text: English text to translate.
            target_lang: BCP-47 language code.

        Returns:
            Translated text, or original if no match.
        """
        if target_lang == "en" or target_lang not in _FROM_ENGLISH:
            return text

        table = _FROM_ENGLISH[target_lang]
        lowered = text.lower()

        for phrase in sorted(table.keys(), key=len, reverse=True):
            if phrase in lowered:
                translated = text.replace(phrase, table[phrase], 1)
                logger.debug("i18n [en→%s]: %r → %r", target_lang, text, translated)
                return translated

        return text

    def supported_languages(self) -> dict[str, int]:
        """Return a dict of {lang_code: phrase_count} for all supported languages."""
        return {lang: len(table) for lang, table in _TO_ENGLISH.items()}


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_translator: Optional[Translator] = None


def get_translator() -> Translator:
    """Return the process-wide Translator."""
    global _translator
    if _translator is None:
        _translator = Translator()
    return _translator
