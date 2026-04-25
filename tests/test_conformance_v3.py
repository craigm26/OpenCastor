"""Tests for RCAN 3.x-specific conformance checks.

Pins the structural contract for RCAN 3.0+ ROBOT.md manifests:

- Version gate: checks return ``skip`` if rcan_version isn't 3.x.
- Signing algorithm: must declare a post-quantum option
  (``pqc-hybrid-v1`` or ``ml-dsa-65``).
- agent.runtimes[]: 3.x replaces the single ``brain`` block with a list
  of runtimes, each declaring ``id``, ``harness``, and ``models[]``.
- RRN format: ``metadata.rrn`` must match ``RRN-\\d{12}``.

Bob's manifest (RRN-000000000002, rcan_version=3.2) is the canonical
shape these tests pin against.
"""

from __future__ import annotations

from castor.conformance import ConformanceChecker


def _bob_like_3x_config() -> dict:
    """Minimal RCAN 3.x config that should pass all _v3_* checks."""
    return {
        "rcan_version": "3.2",
        "metadata": {
            "robot_name": "bob",
            "rrn": "RRN-000000000099",
        },
        "network": {
            "signing_alg": "pqc-hybrid-v1",
        },
        "agent": {
            "runtimes": [
                {
                    "id": "opencastor",
                    "harness": "castor-default",
                    "default": True,
                    "models": [
                        {
                            "provider": "anthropic",
                            "model": "claude-sonnet-4-6",
                            "role": "primary",
                        }
                    ],
                }
            ],
        },
    }


# ---- _v3_rcan_version (gate) ---------------------------------------------


def test_v3_rcan_version_gate_passes_for_3x():
    cfg = _bob_like_3x_config()
    checker = ConformanceChecker(cfg)
    results = [r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.rcan_version"]
    assert len(results) == 1
    assert results[0].status == "pass"


def test_v3_rcan_version_gate_skips_for_2x():
    """2.x manifests skip 3.x-only checks. Skip ≠ fail (don't bother
    a 2.x fleet manifest with 3.x-specific structural assertions)."""
    cfg = _bob_like_3x_config()
    cfg["rcan_version"] = "2.2"
    checker = ConformanceChecker(cfg)
    results = checker.run_category("rcan_v3")
    # All v3 checks should skip — none fail
    assert results, "rcan_v3 category must always emit at least the version gate"
    assert all(r.status == "skip" for r in results), (
        f"non-3.x manifest should skip all v3 checks; got: "
        f"{[(r.check_id, r.status) for r in results]}"
    )


def test_v3_rcan_version_gate_passes_for_3_3():
    cfg = _bob_like_3x_config()
    cfg["rcan_version"] = "3.3"
    checker = ConformanceChecker(cfg)
    results = [r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.rcan_version"]
    assert results[0].status == "pass"


# ---- _v3_signing_alg ------------------------------------------------------


def test_v3_signing_alg_pqc_hybrid_passes():
    cfg = _bob_like_3x_config()
    cfg["network"]["signing_alg"] = "pqc-hybrid-v1"
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.signing_alg")
    assert r.status == "pass"


def test_v3_signing_alg_ml_dsa_65_passes():
    """ml-dsa-65 alone (without ed25519 hybrid) is also acceptable in 3.x."""
    cfg = _bob_like_3x_config()
    cfg["network"]["signing_alg"] = "ml-dsa-65"
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.signing_alg")
    assert r.status == "pass"


def test_v3_signing_alg_ed25519_only_fails():
    """ed25519 alone is deprecated in 3.x — must opt into PQ."""
    cfg = _bob_like_3x_config()
    cfg["network"]["signing_alg"] = "ed25519"
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.signing_alg")
    assert r.status == "fail"
    assert r.fix is not None and "pqc-hybrid-v1" in r.fix


def test_v3_signing_alg_missing_warns():
    """No declaration = warn (operator should declare explicitly)."""
    cfg = _bob_like_3x_config()
    cfg["network"].pop("signing_alg", None)
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.signing_alg")
    assert r.status == "warn"


# ---- _v3_agent_runtimes ---------------------------------------------------


def test_v3_agent_runtimes_valid_passes():
    cfg = _bob_like_3x_config()
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.agent_runtimes")
    assert r.status == "pass"


def test_v3_agent_runtimes_missing_fails():
    """3.x mandates agent.runtimes[] (replaces single brain block)."""
    cfg = _bob_like_3x_config()
    cfg.pop("agent", None)
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.agent_runtimes")
    assert r.status == "fail"


def test_v3_agent_runtimes_empty_list_fails():
    cfg = _bob_like_3x_config()
    cfg["agent"]["runtimes"] = []
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.agent_runtimes")
    assert r.status == "fail"


def test_v3_agent_runtimes_legacy_brain_block_fails():
    """3.x forbids the single-brain shape; must migrate to agent.runtimes[]."""
    cfg = _bob_like_3x_config()
    cfg.pop("agent", None)
    cfg["brain"] = {"planning_provider": "anthropic", "planning_model": "claude-3"}
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.agent_runtimes")
    assert r.status == "fail"
    assert "brain" in (r.detail or "").lower() or "runtimes" in (r.detail or "").lower()


def test_v3_agent_runtimes_runtime_missing_id_fails():
    cfg = _bob_like_3x_config()
    del cfg["agent"]["runtimes"][0]["id"]
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.agent_runtimes")
    assert r.status == "fail"


def test_v3_agent_runtimes_runtime_no_models_fails():
    cfg = _bob_like_3x_config()
    cfg["agent"]["runtimes"][0]["models"] = []
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.agent_runtimes")
    assert r.status == "fail"


# ---- _v3_rrn_format -------------------------------------------------------


def test_v3_rrn_format_valid_passes():
    cfg = _bob_like_3x_config()
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.rrn_format")
    assert r.status == "pass"


def test_v3_rrn_format_missing_fails():
    cfg = _bob_like_3x_config()
    cfg["metadata"].pop("rrn", None)
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.rrn_format")
    assert r.status == "fail"


def test_v3_rrn_format_wrong_shape_fails():
    """RRN must be RRN- + 12 digits. Anything else fails."""
    cfg = _bob_like_3x_config()
    cfg["metadata"]["rrn"] = "RRN-12345"  # too short
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.rrn_format")
    assert r.status == "fail"


def test_v3_rrn_format_lowercase_fails():
    cfg = _bob_like_3x_config()
    cfg["metadata"]["rrn"] = "rrn-000000000099"
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.rrn_format")
    assert r.status == "fail"


# ---- category integration -------------------------------------------------


def test_run_all_includes_rcan_v3_for_3x_manifest():
    """run_all() must execute the v3 category for 3.x manifests."""
    cfg = _bob_like_3x_config()
    checker = ConformanceChecker(cfg)
    all_results = checker.run_all()
    v3_results = [r for r in all_results if r.category == "rcan_v3"]
    assert len(v3_results) >= 4, f"expected ≥4 v3 checks; got {len(v3_results)}"


def test_run_all_includes_rcan_v3_for_2x_manifest_but_skipped():
    """run_all() always runs the v3 category — 2.x manifests skip the contents."""
    cfg = _bob_like_3x_config()
    cfg["rcan_version"] = "2.2"
    checker = ConformanceChecker(cfg)
    all_results = checker.run_all()
    v3_results = [r for r in all_results if r.category == "rcan_v3"]
    assert v3_results, "rcan_v3 category must run unconditionally"
    assert all(r.status == "skip" for r in v3_results)
