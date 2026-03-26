"""
Tests for castor/commands/recommend.py — harness recommendation engine.

Validates that the synthesis findings + preset matrix produce correct
recommendations for hardware × domain combinations.
"""

from __future__ import annotations

import types

from castor.commands.recommend import (
    PRESET_MATRIX,
    SYNTHESIS_FINDINGS,
    _applicable_findings,
    _matching_presets,
    _normalise_domain,
    _normalise_hardware,
    cmd_recommend,
)

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def test_hw_aliases():
    assert _normalise_hardware("pi5") == "pi5_8gb"
    assert _normalise_hardware("pi4") == "pi5_4gb"
    assert _normalise_hardware("cloud") == "server"
    assert _normalise_hardware("budget") == "waveshare"
    assert _normalise_hardware(None) is None


def test_domain_aliases():
    assert _normalise_domain("factory") == "industrial"
    assert _normalise_domain("house") == "home"
    assert _normalise_domain("all") == "general"
    assert _normalise_domain(None) is None


# ---------------------------------------------------------------------------
# Preset matrix
# ---------------------------------------------------------------------------


def test_champion_exists():
    champions = [p for p in PRESET_MATRIX if p["is_champion"]]
    assert len(champions) == 1
    assert champions[0]["id"] == "lower_cost"


def test_all_presets_have_required_keys():
    for p in PRESET_MATRIX:
        assert "id" in p
        assert "ohb1_score" in p
        assert "hardware" in p
        assert "domains" in p


# ---------------------------------------------------------------------------
# Matching presets
# ---------------------------------------------------------------------------


def test_home_pi4gb_recommends_local():
    results = _matching_presets("pi5_4gb", "home")
    assert len(results) > 0
    # local_only or home_optimized should be first (highest domain-specific score)
    assert results[0]["id"] in {"home_optimized", "local_only"}


def test_industrial_server_recommends_quality():
    results = _matching_presets("server", "industrial")
    assert any(p["id"] == "quality_first" for p in results)
    assert results[0]["id"] == "quality_first"


def test_no_filter_returns_all():
    results = _matching_presets(None, None)
    assert len(results) == len(PRESET_MATRIX)


def test_lower_cost_universal():
    results = _matching_presets("waveshare", "home")
    ids = [p["id"] for p in results]
    assert "lower_cost" in ids


# ---------------------------------------------------------------------------
# Synthesis findings
# ---------------------------------------------------------------------------


def test_findings_have_required_keys():
    for f in SYNTHESIS_FINDINGS:
        assert "id" in f
        assert "signal" in f
        assert "applies_to" in f
        assert "config_dim" in f
        assert "confidence" in f


def test_drift_detection_universal():
    findings = _applicable_findings("waveshare", "home")
    ids = [f["id"] for f in findings]
    assert "drift_universal" in ids


def test_retry_industrial_only():
    ind_findings = _applicable_findings("pi5_8gb", "industrial")
    home_findings = _applicable_findings("pi5_8gb", "home")
    ind_ids = [f["id"] for f in ind_findings]
    home_ids = [f["id"] for f in home_findings]
    assert "retry_industrial" in ind_ids
    assert "retry_industrial" not in home_ids


def test_local_model_finding_for_pi():
    findings = _applicable_findings("pi5_4gb", "home")
    ids = [f["id"] for f in findings]
    assert "local_model_home" in ids


def test_no_hardware_no_domain_returns_universal_findings():
    findings = _applicable_findings(None, None)
    # Only truly universal findings (*/*) should match
    for f in findings:
        assert f["applies_to"]["hardware"] == "*"
        assert f["applies_to"]["domain"] == "*"


# ---------------------------------------------------------------------------
# cmd_recommend smoke tests
# ---------------------------------------------------------------------------


def test_recommend_runs_without_error(capsys):
    args = types.SimpleNamespace(
        hardware="pi5_4gb", domain="home", explain=False, list_findings=False
    )
    cmd_recommend(args)
    out = capsys.readouterr().out
    assert "Recommended preset" in out
    assert "home_optimized" in out or "local_only" in out


def test_recommend_explain(capsys):
    args = types.SimpleNamespace(
        hardware="pi5_4gb", domain="home", explain=True, list_findings=False
    )
    cmd_recommend(args)
    out = capsys.readouterr().out
    assert "Synthesis findings" in out
    assert "drift_detection" in out


def test_list_findings(capsys):
    args = types.SimpleNamespace(hardware=None, domain=None, explain=False, list_findings=True)
    cmd_recommend(args)
    out = capsys.readouterr().out
    assert "Synthesis Signals" in out
    assert "retry_on_error" in out


def test_recommend_no_args_falls_back_to_champion(capsys):
    """No hardware/domain → should still return lower_cost champion."""
    args = types.SimpleNamespace(hardware=None, domain=None, explain=False, list_findings=False)
    cmd_recommend(args)
    out = capsys.readouterr().out
    assert "lower_cost" in out or "Recommended" in out


def test_recommend_industrial_server(capsys):
    args = types.SimpleNamespace(
        hardware="server", domain="industrial", explain=True, list_findings=False
    )
    cmd_recommend(args)
    out = capsys.readouterr().out
    assert "quality_first" in out or "Industrial" in out
