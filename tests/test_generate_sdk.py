"""Tests for castor.generate_sdk — OpenAPI Python client SDK generator (issue #264)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from castor.generate_sdk import (
    SDKGenerator,
    _generate_method,
    _load_cached_schema,
    _method_name,
    _python_type,
    _save_cached_schema,
)

# ---------------------------------------------------------------------------
# Minimal OpenAPI schema fixture
# ---------------------------------------------------------------------------

MINIMAL_SCHEMA = {
    "openapi": "3.1.0",
    "info": {"title": "OpenCastor", "version": "2026.3.1.16"},
    "paths": {
        "/api/health": {
            "get": {
                "summary": "Health check",
                "operationId": "health",
                "parameters": [],
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/api/command": {
            "post": {
                "summary": "Send a command",
                "parameters": [],
                "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/api/memory/episodes": {
            "get": {
                "summary": "List episodes",
                "parameters": [
                    {
                        "name": "limit",
                        "in": "query",
                        "schema": {"type": "integer"},
                        "required": False,
                    }
                ],
                "responses": {"200": {"description": "OK"}},
            }
        },
    },
}


# ---------------------------------------------------------------------------
# _method_name
# ---------------------------------------------------------------------------


class TestMethodName:
    def test_get_health(self):
        assert _method_name("get", "/api/health") == "get_api_health"

    def test_post_command(self):
        assert _method_name("post", "/api/command") == "post_api_command"

    def test_path_param_stripped(self):
        name = _method_name("get", "/api/episodes/{id}")
        assert "{" not in name
        assert "}" not in name

    def test_hyphen_replaced(self):
        name = _method_name("get", "/api/some-endpoint")
        assert "-" not in name


# ---------------------------------------------------------------------------
# _python_type
# ---------------------------------------------------------------------------


class TestPythonType:
    def test_string(self):
        assert _python_type("string") == "str"

    def test_integer(self):
        assert _python_type("integer") == "int"

    def test_number(self):
        assert _python_type("number") == "float"

    def test_boolean(self):
        assert _python_type("boolean") == "bool"

    def test_unknown_returns_any(self):
        assert _python_type("unknown-type") == "Any"


# ---------------------------------------------------------------------------
# _generate_method
# ---------------------------------------------------------------------------


class TestGenerateMethod:
    def test_generates_def_line(self):
        code = _generate_method("get", "/api/health", {"summary": "Health"})
        assert "def get_api_health" in code

    def test_generates_docstring(self):
        code = _generate_method("get", "/api/health", {"summary": "Health check"})
        assert "Health check" in code

    def test_post_includes_body_param(self):
        code = _generate_method("post", "/api/command", {"summary": "Cmd"})
        assert "body" in code

    def test_query_param_included(self):
        op = {
            "summary": "List",
            "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
        }
        code = _generate_method("get", "/api/list", op)
        assert "limit" in code

    def test_path_param_in_url_template(self):
        op = {
            "summary": "Get by id",
            "parameters": [{"name": "ep_id", "in": "path", "schema": {"type": "string"}}],
        }
        code = _generate_method("get", "/api/episodes/{ep_id}", op)
        assert "ep_id" in code


# ---------------------------------------------------------------------------
# _load_cached_schema / _save_cached_schema
# ---------------------------------------------------------------------------


class TestCacheHelpers:
    def test_save_and_load(self, tmp_path):
        cache = tmp_path / "schema.json"
        schema = {"openapi": "3.0.0", "paths": {}}
        _save_cached_schema(schema, cache)
        loaded = _load_cached_schema(cache)
        assert loaded == schema

    def test_load_returns_none_when_missing(self, tmp_path):
        assert _load_cached_schema(tmp_path / "missing.json") is None

    def test_load_returns_none_on_invalid_json(self, tmp_path):
        cache = tmp_path / "bad.json"
        cache.write_text("not-json")
        assert _load_cached_schema(cache) is None


# ---------------------------------------------------------------------------
# SDKGenerator.fetch_schema
# ---------------------------------------------------------------------------


class TestSDKGeneratorFetchSchema:
    def test_fetch_from_live_url(self, tmp_path):
        cache = tmp_path / "cache.json"
        gen = SDKGenerator(base_url="http://localhost:8000", cache_path=cache)

        mock_resp = MagicMock()
        mock_resp.json.return_value = MINIMAL_SCHEMA
        mock_resp.raise_for_status = MagicMock()

        with (
            patch("castor.generate_sdk.HAS_HTTPX", False),
            patch("castor.generate_sdk._httpx", create=True),
        ):
            urllib_resp = MagicMock()
            urllib_resp.read.return_value = json.dumps(MINIMAL_SCHEMA).encode()
            urllib_resp.__enter__ = lambda s: s
            urllib_resp.__exit__ = MagicMock(return_value=False)
            with patch("urllib.request.urlopen", return_value=urllib_resp):
                schema = gen.fetch_schema()

        assert schema["info"]["title"] == "OpenCastor"

    def test_falls_back_to_cache_on_error(self, tmp_path):
        cache = tmp_path / "cache.json"
        _save_cached_schema(MINIMAL_SCHEMA, cache)

        gen = SDKGenerator(base_url="http://bad-host/", cache_path=cache)
        with (
            patch("castor.generate_sdk.HAS_HTTPX", False),
            patch("urllib.request.urlopen", side_effect=Exception("conn refused")),
        ):
            schema = gen.fetch_schema()

        assert schema["info"]["title"] == "OpenCastor"

    def test_raises_when_no_live_and_no_cache(self, tmp_path):
        cache = tmp_path / "missing_cache.json"
        gen = SDKGenerator(base_url="http://bad-host/", cache_path=cache)
        with (
            patch("castor.generate_sdk.HAS_HTTPX", False),
            patch("urllib.request.urlopen", side_effect=Exception("conn refused")),
            pytest.raises(RuntimeError, match="Cannot fetch"),
        ):
            gen.fetch_schema()


# ---------------------------------------------------------------------------
# SDKGenerator.generate
# ---------------------------------------------------------------------------


class TestSDKGeneratorGenerate:
    def test_generates_class(self):
        gen = SDKGenerator()
        code = gen.generate(schema=MINIMAL_SCHEMA)
        assert "class CastorClient" in code

    def test_generates_get_method(self):
        gen = SDKGenerator()
        code = gen.generate(schema=MINIMAL_SCHEMA)
        assert "def get_api_health" in code

    def test_generates_post_method(self):
        gen = SDKGenerator()
        code = gen.generate(schema=MINIMAL_SCHEMA)
        assert "def post_api_command" in code

    def test_includes_base_url(self):
        gen = SDKGenerator(base_url="http://mybot:8000")
        code = gen.generate(schema=MINIMAL_SCHEMA)
        assert "mybot:8000" in code


# ---------------------------------------------------------------------------
# SDKGenerator.write
# ---------------------------------------------------------------------------


class TestSDKGeneratorWrite:
    def test_writes_client_py(self, tmp_path):
        gen = SDKGenerator()
        code = gen.generate(schema=MINIMAL_SCHEMA)
        out = gen.write(code, output_dir=str(tmp_path / "sdk"))
        assert out.exists()
        assert out.name == "client.py"

    def test_written_file_contains_class(self, tmp_path):
        gen = SDKGenerator()
        code = gen.generate(schema=MINIMAL_SCHEMA)
        out = gen.write(code, output_dir=str(tmp_path / "sdk"))
        text = out.read_text()
        assert "CastorClient" in text
