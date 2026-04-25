"""Second slice of RCAN 3.x conformance checks.

Builds on tests/test_conformance_v3.py (slice 1: rcan_version, signing_alg,
agent_runtimes, rrn_format). This slice adds:

- estop_response_ms — Art. 9 reactive-safety budget. When
  safety.estop.software is declared, response_ms must be declared AND
  bounded (≤ 100ms is the EU AI Act risk-management ceiling for
  human-collaborating manipulators).
- capability_namespacing — 3.x mandates dotted `verb.noun` shape for
  capabilities[] (e.g., `manipulate.pick`, `perceive.rgb`). Catches
  legacy bare names like `pick` or `wave`.
- record_url — when metadata.rrn is declared, metadata.record_url must
  reference the canonical RRF v2 path
  (https://robotregistryfoundation.org/v2/robots/<rrn>). Catches stale
  rcan.dev refs that survived the cascade migration.
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
            "record_url": "https://robotregistryfoundation.org/v2/robots/RRN-000000000099",
        },
        "network": {"signing_alg": "pqc-hybrid-v1"},
        "agent": {
            "runtimes": [
                {
                    "id": "opencastor",
                    "harness": "castor-default",
                    "models": [
                        {"provider": "anthropic", "model": "claude-sonnet-4-6", "role": "primary"}
                    ],
                }
            ],
        },
        "safety": {"estop": {"software": True, "response_ms": 50}},
        "capabilities": ["manipulate.pick", "manipulate.place", "perceive.rgb"],
    }


# ---- _v3_estop_response_ms ------------------------------------------------


def test_v3_estop_response_ms_declared_passes():
    cfg = _bob_like_3x_config()
    checker = ConformanceChecker(cfg)
    r = next(
        r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.estop_response_ms"
    )
    assert r.status == "pass"


def test_v3_estop_response_ms_skips_when_no_software_estop():
    """If software estop isn't declared, the budget check is N/A."""
    cfg = _bob_like_3x_config()
    cfg["safety"]["estop"]["software"] = False
    checker = ConformanceChecker(cfg)
    r = next(
        r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.estop_response_ms"
    )
    assert r.status == "skip"


def test_v3_estop_response_ms_missing_when_software_declared_fails():
    """software: true without a response_ms = silent reactive-safety hole."""
    cfg = _bob_like_3x_config()
    cfg["safety"]["estop"] = {"software": True}
    checker = ConformanceChecker(cfg)
    r = next(
        r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.estop_response_ms"
    )
    assert r.status == "fail"
    assert "response_ms" in (r.detail or "")


def test_v3_estop_response_ms_over_budget_fails():
    """200ms > 100ms ceiling for collaborative manipulators."""
    cfg = _bob_like_3x_config()
    cfg["safety"]["estop"]["response_ms"] = 200
    checker = ConformanceChecker(cfg)
    r = next(
        r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.estop_response_ms"
    )
    assert r.status == "fail"
    assert "100" in (r.detail or "")


def test_v3_estop_response_ms_at_ceiling_passes():
    """Exactly 100ms is acceptable — strict ≤ ceiling."""
    cfg = _bob_like_3x_config()
    cfg["safety"]["estop"]["response_ms"] = 100
    checker = ConformanceChecker(cfg)
    r = next(
        r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.estop_response_ms"
    )
    assert r.status == "pass"


def test_v3_estop_response_ms_skips_for_2x():
    cfg = _bob_like_3x_config()
    cfg["rcan_version"] = "2.2"
    checker = ConformanceChecker(cfg)
    r = next(
        r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.estop_response_ms"
    )
    assert r.status == "skip"


# ---- _v3_capability_namespacing -------------------------------------------


def test_v3_capability_namespacing_dotted_passes():
    cfg = _bob_like_3x_config()
    checker = ConformanceChecker(cfg)
    r = next(
        r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.capability_namespacing"
    )
    assert r.status == "pass"


def test_v3_capability_namespacing_legacy_bare_fails():
    """Bare names like `pick`, `wave` are pre-3.x — must be `manipulate.pick`."""
    cfg = _bob_like_3x_config()
    cfg["capabilities"] = ["pick", "manipulate.place"]
    checker = ConformanceChecker(cfg)
    r = next(
        r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.capability_namespacing"
    )
    assert r.status == "fail"
    assert "pick" in (r.detail or "")


def test_v3_capability_namespacing_empty_list_passes():
    """No capabilities declared = no naming violations possible."""
    cfg = _bob_like_3x_config()
    cfg["capabilities"] = []
    checker = ConformanceChecker(cfg)
    r = next(
        r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.capability_namespacing"
    )
    assert r.status == "pass"


def test_v3_capability_namespacing_double_dot_passes():
    """Multi-segment names like `manipulate.pick.precise` are valid."""
    cfg = _bob_like_3x_config()
    cfg["capabilities"] = ["manipulate.pick.precise", "perceive.rgb.stream"]
    checker = ConformanceChecker(cfg)
    r = next(
        r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.capability_namespacing"
    )
    assert r.status == "pass"


def test_v3_capability_namespacing_skips_for_2x():
    cfg = _bob_like_3x_config()
    cfg["rcan_version"] = "2.2"
    checker = ConformanceChecker(cfg)
    r = next(
        r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.capability_namespacing"
    )
    assert r.status == "skip"


# ---- _v3_record_url -------------------------------------------------------


def test_v3_record_url_canonical_passes():
    cfg = _bob_like_3x_config()
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.record_url")
    assert r.status == "pass"


def test_v3_record_url_missing_when_rrn_present_warns():
    """Having an rrn but no record_url is a documentation gap, not a fail."""
    cfg = _bob_like_3x_config()
    cfg["metadata"].pop("record_url", None)
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.record_url")
    assert r.status == "warn"


def test_v3_record_url_stale_rcan_dev_fails():
    """rcan.dev was the old registry; cascade moved everything to RRF v2.
    A stale rcan.dev URL is an explicit drift signal, not a warning."""
    cfg = _bob_like_3x_config()
    cfg["metadata"]["record_url"] = "https://rcan.dev/api/v1/robots/RRN-000000000099"
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.record_url")
    assert r.status == "fail"
    assert "rcan.dev" in (r.detail or "") or "robotregistryfoundation" in (r.detail or "")


def test_v3_record_url_wrong_rrn_fails():
    """record_url must reference the same RRN as metadata.rrn."""
    cfg = _bob_like_3x_config()
    cfg["metadata"]["record_url"] = "https://robotregistryfoundation.org/v2/robots/RRN-999999999999"
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.record_url")
    assert r.status == "fail"


def test_v3_record_url_skips_when_rrn_missing():
    """Without an rrn, there's no canonical URL to validate against."""
    cfg = _bob_like_3x_config()
    cfg["metadata"].pop("rrn", None)
    cfg["metadata"].pop("record_url", None)
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.record_url")
    assert r.status == "skip"


def test_v3_record_url_skips_for_2x():
    cfg = _bob_like_3x_config()
    cfg["rcan_version"] = "2.2"
    checker = ConformanceChecker(cfg)
    r = next(r for r in checker.run_category("rcan_v3") if r.check_id == "rcan_v3.record_url")
    assert r.status == "skip"


# ---- category integration -------------------------------------------------


def test_run_all_includes_seven_v3_checks():
    """Slice 1 (4) + slice 2 (3) = 7 v3 checks total."""
    cfg = _bob_like_3x_config()
    checker = ConformanceChecker(cfg)
    v3_results = [r for r in checker.run_all() if r.category == "rcan_v3"]
    assert len(v3_results) == 7, (
        f"expected 7 v3 checks; got {len(v3_results)}: {[r.check_id for r in v3_results]}"
    )
