"""
tests/test_i18n.py — Unit + API tests for castor/i18n.py.

Covers:
  - Language detection (Unicode ranges + Latin markers)
  - to_english() phrase lookup
  - from_english() reverse translation
  - supported_languages()
  - API: GET /api/i18n/languages, POST /api/i18n/detect, /translate
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def t():
    from castor.i18n import Translator

    return Translator()


def test_detect_english(t):
    assert t.detect("go forward and turn left") == "en"


def test_detect_spanish(t):
    assert t.detect("adelante") == "es"


def test_detect_french(t):
    assert t.detect("avance tout droit") == "fr"


def test_detect_german(t):
    assert t.detect("vorwärts") == "de"


def test_detect_chinese(t):
    assert t.detect("前进") == "zh"


def test_detect_japanese(t):
    assert t.detect("前進してください") == "ja"


def test_detect_arabic(t):
    assert t.detect("تقدم") == "ar"


def test_to_english_spanish(t):
    translated, lang = t.to_english("adelante")
    assert translated == "go forward"
    assert lang == "es"


def test_to_english_french(t):
    translated, lang = t.to_english("avance tout droit")
    assert "forward" in translated or "straight" in translated
    assert lang == "fr"


def test_to_english_chinese(t):
    translated, lang = t.to_english("前进")
    assert translated == "go forward"
    assert lang == "zh"


def test_to_english_already_english(t):
    translated, lang = t.to_english("go forward")
    assert translated == "go forward"
    assert lang == "en"


def test_to_english_unknown_phrase(t):
    """Unknown phrase should be returned as-is."""
    translated, lang = t.to_english("zorp zorp", source_lang="es")
    assert translated == "zorp zorp"


def test_from_english_french(t):
    result = t.from_english("go forward", "fr")
    assert result != "go forward"
    # Should be some French phrase
    assert len(result) > 0


def test_from_english_to_same_language(t):
    result = t.from_english("go forward", "en")
    assert result == "go forward"


def test_from_english_unsupported_lang(t):
    result = t.from_english("go forward", "xx")
    assert result == "go forward"


def test_supported_languages(t):
    langs = t.supported_languages()
    for code in ("es", "fr", "de", "zh", "ja", "pt", "ar"):
        assert code in langs
        assert langs[code] > 0


def test_stop_command_multiple_langs(t):
    stops = {
        "es": "para",
        "fr": "arrête",
        "de": "stopp",
        "zh": "停止",
        "pt": "parar",
    }
    for lang, word in stops.items():
        translated, detected = t.to_english(word, source_lang=lang)
        assert "stop" in translated.lower(), f"Expected stop for {lang}: got {translated}"


def test_singleton():
    import castor.i18n as m

    m._translator = None
    t1 = m.get_translator()
    t2 = m.get_translator()
    assert t1 is t2
    m._translator = None


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client():
    from fastapi.testclient import TestClient

    from castor.api import app

    return TestClient(app)


def test_api_i18n_languages(api_client):
    resp = api_client.get("/api/i18n/languages")
    assert resp.status_code == 200
    data = resp.json()
    assert "languages" in data
    assert "es" in data["languages"]


def test_api_i18n_detect(api_client):
    resp = api_client.post("/api/i18n/detect", json={"text": "adelante"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["lang"] == "es"


def test_api_i18n_translate_to_english(api_client):
    resp = api_client.post("/api/i18n/translate", json={"text": "adelante", "source_lang": "es"})
    assert resp.status_code == 200
    data = resp.json()
    assert "forward" in data["translated"].lower()
    assert data["target_lang"] == "en"


def test_api_i18n_translate_from_english(api_client):
    resp = api_client.post(
        "/api/i18n/translate",
        json={"text": "go forward", "target_lang": "fr"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["target_lang"] == "fr"
    assert data["translated"] != "go forward"


def test_api_i18n_translate_english_passthrough(api_client):
    resp = api_client.post("/api/i18n/translate", json={"text": "go forward", "source_lang": "en"})
    assert resp.status_code == 200
    assert resp.json()["translated"] == "go forward"
