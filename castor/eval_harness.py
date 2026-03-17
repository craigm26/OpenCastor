"""
castor/eval_harness.py — Skill evaluation harness.

Runs a skill's tests/eval.json against the live model and produces a
pass/fail scorecard. Every run is saved to the trajectory log.

CLI usage (wired via castor.cli)::

    castor eval --skill web-lookup
    castor eval --skill navigate-to --verbose
    castor eval --skill arm-manipulate --json
    castor eval --all

Programmatic::

    from castor.eval_harness import run_skill_eval, EvalResult
    results = await run_skill_eval("web-lookup", harness, loader)
    print(results.summary())
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.Eval")

__all__ = ["run_skill_eval", "EvalResult", "CheckResult"]

# ── Check registry ────────────────────────────────────────────────────────────

def _build_check_registry() -> dict:
    """Build the registry of check functions.

    Each check takes a HarnessResult and returns bool.
    """
    from castor.harness import PHYSICAL_TOOLS

    return {
        # Tool call checks
        "calls_camera":   lambda r: any(t.tool_name == "get_camera_frame" for t in r.tools_called),
        "calls_distance": lambda r: any(t.tool_name == "get_distance" for t in r.tools_called),
        "calls_grip":     lambda r: any(t.tool_name == "grip" for t in r.tools_called),
        "calls_move":     lambda r: any(t.tool_name == "move" for t in r.tools_called),
        "calls_web":      lambda r: any(t.tool_name == "web_search" for t in r.tools_called),
        "calls_rcan":     lambda r: any(t.tool_name == "send_rcan_message" for t in r.tools_called),
        "calls_telemetry":lambda r: any(t.tool_name == "get_telemetry" for t in r.tools_called),

        # P66 checks
        "requests_consent": lambda r: r.p66_consent_required,
        "no_movement_before_consent": lambda r: not any(
            t.tool_name in PHYSICAL_TOOLS and not t.p66_blocked
            for t in r.tools_called
            if not r.p66_consent_granted
        ),

        # Output checks
        "has_response":   lambda r: len(r.thought.raw_text.strip()) > 10,
        "not_error":      lambda r: r.error is None,
        "no_blocked":     lambda r: not r.p66_blocked,
    }


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    check_id: str
    passed: bool
    detail: str = ""


@dataclass
class CaseResult:
    case_id: str
    prompt: str
    should_trigger: bool
    triggered: bool
    checks: list[CheckResult] = field(default_factory=list)
    latency_ms: float = 0.0
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        if self.should_trigger != self.triggered:
            return False
        return all(c.passed for c in self.checks)

    @property
    def check_score(self) -> str:
        total = len(self.checks)
        passed = sum(1 for c in self.checks if c.passed)
        return f"{passed}/{total}"


@dataclass
class EvalResult:
    skill_name: str
    cases: list[CaseResult] = field(default_factory=list)
    model_used: str = ""
    total_latency_ms: float = 0.0

    @property
    def pass_count(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def total_count(self) -> int:
        return len(self.cases)

    @property
    def pass_rate(self) -> float:
        if not self.cases:
            return 0.0
        return self.pass_count / self.total_count

    def summary(self, verbose: bool = False) -> str:
        """Human-readable scorecard."""
        lines = [
            f"╔{'═' * 52}╗",
            f"║  castor eval — {self.skill_name:<36}║",
            f"╠{'═' * 52}╣",
            f"║  {self.pass_count}/{self.total_count} tests passed  ({self.pass_rate * 100:.1f}%){' ' * (24 - len(str(self.pass_count)) - len(str(self.total_count)))}║",
            f"╠{'═' * 52}╣",
        ]
        for case in self.cases:
            icon = "✅" if case.passed else "❌"
            score = case.check_score
            row = f"║  {icon} {case.case_id[:32]:<32} {score:>5}  ║"
            lines.append(row)
            if verbose and not case.passed:
                for chk in case.checks:
                    if not chk.passed:
                        lines.append(f"║     └── FAIL: {chk.check_id:<37}║")
                if case.error:
                    lines.append(f"║     └── ERROR: {case.error[:36]}║")
        avg_lat = (
            sum(c.latency_ms for c in self.cases) / len(self.cases)
            if self.cases else 0
        )
        lines += [
            f"╠{'═' * 52}╣",
            f"║  Avg latency: {avg_lat:.0f}ms  Model: {self.model_used[:20]:<20}  ║",
            f"╚{'═' * 52}╝",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "skill_name": self.skill_name,
            "pass_count": self.pass_count,
            "total_count": self.total_count,
            "pass_rate": round(self.pass_rate, 3),
            "model_used": self.model_used,
            "cases": [
                {
                    "id": c.case_id,
                    "passed": c.passed,
                    "triggered": c.triggered,
                    "should_trigger": c.should_trigger,
                    "checks": [{"id": ch.check_id, "passed": ch.passed} for ch in c.checks],
                    "latency_ms": c.latency_ms,
                    "error": c.error,
                }
                for c in self.cases
            ],
        }


# ── Main eval runner ──────────────────────────────────────────────────────────

async def run_skill_eval(
    skill_name: str,
    harness: Any,                    # AgentHarness
    skill_loader: Any,               # SkillLoader
    dry_run: bool = True,
    verbose: bool = False,
) -> EvalResult:
    """Run a skill's eval.json test suite.

    Args:
        skill_name:   Name of the skill to evaluate.
        harness:      AgentHarness instance to use for inference.
        skill_loader: SkillLoader with skills loaded.
        dry_run:      If True, physical tool calls are blocked (default True).
        verbose:      If True, print progress to stdout.

    Returns:
        EvalResult with per-case results and summary statistics.
    """
    from castor.harness import HarnessContext

    result = EvalResult(skill_name=skill_name)
    check_registry = _build_check_registry()

    # Locate eval.json
    eval_path = _find_eval_json(skill_name, skill_loader)
    if eval_path is None:
        logger.warning("No eval.json found for skill: %s", skill_name)
        return result

    cases = json.loads(eval_path.read_text())
    if verbose:
        print(f"\nRunning eval for: {skill_name} ({len(cases)} cases)")

    t_total = time.perf_counter()

    for case in cases:
        case_id = case.get("id", "unknown")
        prompt = case.get("prompt", "")
        should_trigger = bool(case.get("should_trigger", True))
        expected_checks = case.get("expected_checks", [])
        scope = case.get("scope", "chat")

        ctx = HarnessContext(
            instruction=prompt,
            scope=scope,
            surface="eval_harness",
            # In dry-run mode, consent not granted → physical tools blocked
            consent_granted=not dry_run,
        )

        t0 = time.perf_counter()
        try:
            hresult = await harness.run(ctx)
        except Exception as exc:
            result.cases.append(CaseResult(
                case_id=case_id, prompt=prompt,
                should_trigger=should_trigger, triggered=False,
                error=str(exc),
                latency_ms=(time.perf_counter() - t0) * 1000,
            ))
            continue
        latency = (time.perf_counter() - t0) * 1000

        # Did the skill trigger?
        triggered = hresult.skill_triggered == skill_name

        # Evaluate checks
        check_results = []
        for check_id in expected_checks:
            fn = check_registry.get(check_id)
            if fn is None:
                check_results.append(CheckResult(check_id, False, "Unknown check"))
                continue
            try:
                passed = fn(hresult)
            except Exception:
                passed = False
            check_results.append(CheckResult(check_id, passed))

        case_result = CaseResult(
            case_id=case_id,
            prompt=prompt,
            should_trigger=should_trigger,
            triggered=triggered,
            checks=check_results,
            latency_ms=latency,
        )
        result.cases.append(case_result)

        if verbose:
            icon = "✅" if case_result.passed else "❌"
            print(f"  {icon} {case_id}: {case_result.check_score} checks")

    result.total_latency_ms = (time.perf_counter() - t_total) * 1000
    result.model_used = getattr(harness._provider, "model_name", "unknown")

    return result


def _find_eval_json(skill_name: str, skill_loader: Any) -> Optional[Path]:
    """Find the eval.json for a named skill."""
    from castor.skills.loader import _BUILTIN_DIR, _USER_DIR

    for base in [_BUILTIN_DIR, _USER_DIR]:
        path = base / skill_name / "tests" / "eval.json"
        if path.exists():
            return path

    # Check skill_loader paths
    if skill_loader and hasattr(skill_loader, "_extra_paths"):
        for extra in skill_loader._extra_paths:
            path = Path(extra) / skill_name / "tests" / "eval.json"
            if path.exists():
                return path

    return None


# ── CLI integration helper ────────────────────────────────────────────────────

def run_eval_cli(
    skill_names: list[str],
    output_json: bool = False,
    verbose: bool = False,
) -> int:
    """Entry point for ``castor eval`` CLI command.

    Returns exit code (0 = all passed, 1 = failures).
    """
    from castor.harness import AgentHarness
    from castor.skills.loader import SkillLoader
    from castor.tools import ToolRegistry

    try:
        from castor.main import get_shared_brain
        brain = get_shared_brain()
    except Exception:
        brain = None

    if brain is None:
        print("ERROR: No brain initialised. Start the gateway first: castor gateway --config <file>")
        return 1

    loader = SkillLoader()
    reg = ToolRegistry()
    harness = AgentHarness(provider=brain, tool_registry=reg)

    all_results = []
    exit_code = 0

    async def _run():
        nonlocal exit_code
        names = skill_names if skill_names else list(loader.load_all().keys())
        for name in names:
            result = await run_skill_eval(name, harness, loader, verbose=verbose)
            all_results.append(result)
            if result.pass_rate < 1.0:
                exit_code = 1
            if not output_json:
                print(result.summary(verbose=verbose))

    asyncio.run(_run())

    if output_json:
        print(json.dumps([r.to_dict() for r in all_results], indent=2))

    return exit_code
