"""Track 2 (Gateway Authority) parity placeholder.

Plan 6 Phase 2 commits OpenCastor to passing the same Gateway Authority
suite as `robot-md-gateway`. The actual integration arrives in Plan 7
Phase 1 (open-core extraction) — at that point this file fills in with
the full property suite running against OpenCastor's gateway integration
and the same `signed-good.md` fixture used by `robot-md-gateway`.

For Plan 6 the contract is captured by:
1. The dependency on `robot-md-gateway>=0.4.0a1` in pyproject.toml.
2. The signed gateway-authority report attached to every gateway release.
3. This placeholder, which imports the gateway's cert modules to ensure
   the dependency is wired correctly even before the integration lands.

Track 2 NORMATIVE-conditional declaration:
    `~/opencastor-ops/operations/2026-05-04-track-2-normative.md`
"""

from __future__ import annotations


def test_robot_md_gateway_dependency_importable():
    """The Plan 7 integration target package must be importable from this venv."""
    import robot_md_gateway  # noqa: F401 — proves the dep installs cleanly
    assert robot_md_gateway.__version__ >= "0.4.0a1"


def test_gateway_cert_property_modules_present():
    """All Phase 0/1/2/4 cert-property modules ship with the gateway dep."""
    from robot_md_gateway.cert import (  # noqa: F401
        audit,
        envelope,
        gates,
        policy,
        revocation,
        safety,
    )


def test_track_2_parity_full_suite_pending_plan_7():
    """Placeholder — full parity suite arrives with open-core extraction."""
    # Plan 7 Phase 1 fills in:
    # - shared signed-good.md fixture path
    # - make_app(...) instantiation through OpenCastor's runtime
    # - end-to-end exercise of all 14 cert properties via the same code paths
    # - assertion that cert_report has all 14 IDs after the suite runs
    # See ~/opencastor-ops/docs/superpowers/plans/<plan-7-spec>.md when published.
    pass
