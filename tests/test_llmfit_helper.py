"""Tests for castor.llmfit_helper."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from castor.llmfit_helper import (
    get_recommendations,
    get_system_info,
    is_installed,
    map_to_provider_config,
    print_recommendations,
    _parse_system_output,
)


# ---------------------------------------------------------------------------
# is_installed
# ---------------------------------------------------------------------------


def test_is_installed_true():
    with patch("shutil.which", return_value="/usr/local/bin/llmfit"):
        assert is_installed() is True


def test_is_installed_false():
    with patch("shutil.which", return_value=None):
        assert is_installed() is False


# ---------------------------------------------------------------------------
# map_to_provider_config
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider_in, expected_out",
    [
        ("ollama", "ollama"),
        ("huggingface", "huggingface"),
        ("hugging_face", "huggingface"),
        ("hf", "huggingface"),
        ("anthropic", "anthropic"),
        ("openai", "openai"),
        ("gemini", "gemini"),
        ("google", "gemini"),
        ("llamacpp", "llamacpp"),
        ("llama.cpp", "llamacpp"),
        ("mlx", "mlx"),
    ],
)
def test_map_to_provider_config_supported(provider_in, expected_out):
    rec = {"name": "Qwen2.5-7B-Q4", "provider": provider_in, "fit": "perfect"}
    result = map_to_provider_config(rec)
    assert result is not None
    assert result["provider"] == expected_out
    assert result["model"] == "Qwen2.5-7B-Q4"
    assert result["fit"] == "perfect"


@pytest.mark.parametrize("provider_in", ["vllm", "tgi", "unknown_provider", "", "lmstudio"])
def test_map_to_provider_config_unsupported(provider_in):
    rec = {"name": "SomeModel", "provider": provider_in, "fit": "good"}
    assert map_to_provider_config(rec) is None


def test_map_to_provider_config_missing_provider():
    rec = {"name": "SomeModel", "fit": "good"}
    assert map_to_provider_config(rec) is None


def test_map_to_provider_config_preserves_tps_and_mem():
    rec = {
        "name": "Llama-3.1-8B",
        "provider": "ollama",
        "fit": "good",
        "estimated_tps": 14,
        "mem_usage_mb": 5200,
    }
    result = map_to_provider_config(rec)
    assert result["estimated_tps"] == 14
    assert result["mem_usage_mb"] == 5200


# ---------------------------------------------------------------------------
# get_recommendations — graceful failure
# ---------------------------------------------------------------------------


def test_get_recommendations_binary_missing():
    with patch("castor.llmfit_helper.is_installed", return_value=False):
        result = get_recommendations()
    assert result == []


def test_get_recommendations_subprocess_error():
    with patch("castor.llmfit_helper.is_installed", return_value=True), patch(
        "subprocess.run", side_effect=Exception("timeout")
    ):
        result = get_recommendations()
    assert result == []


def test_get_recommendations_invalid_json():
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "not json at all"

    with patch("castor.llmfit_helper.is_installed", return_value=True), patch(
        "subprocess.run", return_value=mock_result
    ):
        result = get_recommendations()
    assert result == []


def test_get_recommendations_success():
    recs = [
        {"name": "Qwen2.5-7B", "provider": "ollama", "fit": "perfect", "score": 92},
        {"name": "Llama-3.1-8B", "provider": "ollama", "fit": "good", "score": 78},
    ]
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(recs)

    with patch("castor.llmfit_helper.is_installed", return_value=True), patch(
        "subprocess.run", return_value=mock_result
    ):
        result = get_recommendations(use_case="chat", limit=2)
    assert len(result) == 2
    assert result[0]["name"] == "Qwen2.5-7B"


def test_get_recommendations_nonzero_exit():
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""

    with patch("castor.llmfit_helper.is_installed", return_value=True), patch(
        "subprocess.run", return_value=mock_result
    ):
        result = get_recommendations()
    assert result == []


# ---------------------------------------------------------------------------
# get_system_info — graceful failure
# ---------------------------------------------------------------------------


def test_get_system_info_binary_missing():
    with patch("castor.llmfit_helper.is_installed", return_value=False):
        result = get_system_info()
    assert result is None


def test_get_system_info_subprocess_error():
    with patch("castor.llmfit_helper.is_installed", return_value=True), patch(
        "subprocess.run", side_effect=Exception("timeout")
    ):
        result = get_system_info()
    assert result is None


def test_get_system_info_parse():
    output = "CPU: ARM Cortex-A76\nRAM: 16 GB\nGPU: None\n"
    result = _parse_system_output(output)
    assert result["CPU"] == "ARM Cortex-A76"
    assert result["RAM"] == "16 GB"
    assert result["GPU"] == "None"


# ---------------------------------------------------------------------------
# print_recommendations — smoke test (no crash)
# ---------------------------------------------------------------------------


def test_print_recommendations_empty(capsys):
    print_recommendations([])
    out = capsys.readouterr().out
    assert "No recommendations" in out


def test_print_recommendations_plain(capsys):
    recs = [{"name": "Qwen2.5-7B", "provider": "ollama", "fit": "perfect", "estimated_tps": 18, "mem_usage_mb": 4800}]
    print_recommendations(recs, console=None)
    out = capsys.readouterr().out
    assert "Qwen2.5-7B" in out
    assert "ollama" in out
