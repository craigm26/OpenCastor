"""
castor/commands/recommend.py — Harness recommendation engine.

Encodes findings from the autoresearch fleet as actionable recommendations.
This is the feedback loop: autoresearcher learns → runtime applies.

Subcommand: ``castor research recommend [--hardware X] [--domain Y] [--explain]``

Examples::

    castor research recommend
    castor research recommend --hardware pi5_4gb --domain home
    castor research recommend --hardware jetson --domain industrial --explain
    castor research recommend --list-findings
"""

from __future__ import annotations

import os
import pathlib
from typing import Optional

# ---------------------------------------------------------------------------
# Synthesis findings encoded from autoresearch runs
# ---------------------------------------------------------------------------
#
# These are the learnable signals the autoresearcher has produced so far.
# Format: each finding has a description, which hardware/domain it applies to,
# and the config dimension it maps to.
#
# Updated as new champion data arrives; see research/index.json for raw scores.

SYNTHESIS_FINDINGS: list[dict] = [
    {
        "id": "drift_universal",
        "signal": "drift_detection=true is a free win universally",
        "evidence": "All 5 preset winners use it. 0 cost on Pi-class hardware.",
        "applies_to": {"hardware": "*", "domain": "*"},
        "config_dim": "drift_detection",
        "recommended_value": True,
        "confidence": "high",
    },
    {
        "id": "retry_industrial",
        "signal": "retry_on_error is the #1 lever for industrial tasks",
        "evidence": "industrial_optimized wins by +12% median vs configs without retry.",
        "applies_to": {"hardware": "*", "domain": "industrial"},
        "config_dim": "retry_on_error",
        "recommended_value": True,
        "confidence": "high",
    },
    {
        "id": "local_model_home",
        "signal": "Local models (gemma3:1b) match cloud for home tasks",
        "evidence": "local_only scores 0.8103 on home; quality_first scores 0.87 on home. "
        "Latency wins on grip/navigate tasks outweigh reasoning gap.",
        "applies_to": {"hardware": ["pi5_4gb", "pi5_8gb", "waveshare", "jetson"], "domain": "home"},
        "config_dim": "force_local",
        "recommended_value": True,
        "confidence": "high",
    },
    {
        "id": "cloud_reasoning",
        "signal": "Cloud models win on multi-step reasoning tasks",
        "evidence": "quality_first (cloud) scores 0.9801 on server hardware. "
        "Local models fall 15-20% behind on complex instruction-following.",
        "applies_to": {"hardware": ["server", "pi5_hailo"], "domain": ["industrial", "general"]},
        "config_dim": "slow_provider",
        "recommended_value": "google",
        "confidence": "high",
    },
    {
        "id": "context_cap_pi",
        "signal": "context_budget beyond 8192 doesn't help on Pi-class hardware",
        "evidence": "Pi5 8GB configs with context_budget=16384 score same as 8192. "
        "Memory pressure hurts latency without quality gain.",
        "applies_to": {"hardware": ["pi5_4gb", "pi5_8gb", "waveshare"], "domain": "*"},
        "config_dim": "context_budget",
        "recommended_value": 8192,
        "confidence": "medium",
    },
    {
        "id": "cost_gate_budget_hw",
        "signal": "cost_gate_usd prevents runaway API spend on budget hardware",
        "evidence": "lower_cost (cost_gate=0.01) is the overall champion. "
        "Unconstrained configs cost 10x more with <5% quality gain on Pi-class.",
        "applies_to": {"hardware": ["pi5_4gb", "waveshare"], "domain": "*"},
        "config_dim": "cost_gate_usd",
        "recommended_value": 0.01,
        "confidence": "high",
    },
    {
        "id": "skill_order_industrial",
        "signal": "alert-hook + retry-hook after model-router improves industrial reliability",
        "evidence": "industrial_optimized skill order: p66-consent → context-builder → "
        "model-router → alert-hook → skill-executor → error-handler → retry-hook",
        "applies_to": {"hardware": "*", "domain": "industrial"},
        "config_dim": "skill_order",
        "recommended_value": [
            "p66-consent",
            "context-builder",
            "model-router",
            "alert-hook",
            "skill-executor",
            "error-handler",
            "retry-hook",
        ],
        "confidence": "medium",
    },
    {
        "id": "thinking_budget_pi",
        "signal": "thinking_budget=512 is optimal for Pi 4GB; 1024 for Pi 8GB",
        "evidence": "Higher thinking_budget adds latency without score gain on memory-constrained boards.",
        "applies_to": {"hardware": ["pi5_4gb", "waveshare"], "domain": "*"},
        "config_dim": "thinking_budget",
        "recommended_value": 512,
        "confidence": "medium",
    },
]

# ---------------------------------------------------------------------------
# Preset → (hardware tiers, domains, score)
# Directly from research/index.json champion data
# ---------------------------------------------------------------------------

PRESET_MATRIX: list[dict] = [
    {
        "id": "lower_cost",
        "name": "Lower Cost",
        "ohb1_score": 0.6541,
        "is_champion": True,
        "hardware": ["pi5_hailo", "pi5_8gb", "pi5_4gb", "jetson", "server", "waveshare"],
        "domains": ["general", "home", "industrial"],
        "tagline": "Best overall balance — the fleet champion",
    },
    {
        "id": "local_only",
        "name": "Local Only",
        "ohb1_score": 0.8103,
        "is_champion": False,
        "hardware": ["pi5_4gb", "pi5_8gb", "jetson", "waveshare"],
        "domains": ["home"],
        "tagline": "Fully offline — best for home tasks on Pi-class hardware",
    },
    {
        "id": "home_optimized",
        "name": "Home Optimized",
        "ohb1_score": 0.8644,
        "is_champion": False,
        "hardware": ["pi5_4gb", "pi5_8gb", "jetson"],
        "domains": ["home"],
        "tagline": "Low-latency local model, strict P66, grip-hook priority",
    },
    {
        "id": "industrial_optimized",
        "name": "Industrial Optimized",
        "ohb1_score": 0.8812,
        "is_champion": False,
        "hardware": ["server", "pi5_hailo", "pi5_8gb"],
        "domains": ["industrial"],
        "tagline": "Retry-heavy, alert-aware — +12% industrial median",
    },
    {
        "id": "quality_first",
        "name": "Quality First",
        "ohb1_score": 0.9801,
        "is_champion": False,
        "hardware": ["server", "pi5_hailo"],
        "domains": ["industrial", "general"],
        "tagline": "Max score — server/cloud hardware only",
    },
]

# Hardware aliases (normalise user input)
_HW_ALIASES: dict[str, str] = {
    "pi5": "pi5_8gb",
    "pi5_8gb": "pi5_8gb",
    "pi5_4gb": "pi5_4gb",
    "pi": "pi5_4gb",
    "pi4": "pi5_4gb",
    "hailo": "pi5_hailo",
    "pi5_hailo": "pi5_hailo",
    "hailo8l": "pi5_hailo",
    "jetson": "jetson",
    "jetson_nano": "jetson",
    "server": "server",
    "cloud": "server",
    "gpu": "server",
    "waveshare": "waveshare",
    "budget": "waveshare",
}

_DOMAIN_ALIASES: dict[str, str] = {
    "home": "home",
    "house": "home",
    "domestic": "home",
    "industrial": "industrial",
    "factory": "industrial",
    "warehouse": "industrial",
    "manufacturing": "industrial",
    "general": "general",
    "all": "general",
    "mixed": "general",
}


def _normalise_hardware(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    return _HW_ALIASES.get(raw.lower().replace("-", "_"), raw.lower())


def _normalise_domain(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    return _DOMAIN_ALIASES.get(raw.lower(), raw.lower())


def _matching_presets(hardware: Optional[str], domain: Optional[str]) -> list[dict]:
    """Return presets that match hardware and/or domain, sorted by OHB-1 score desc."""
    results = []
    for p in PRESET_MATRIX:
        hw_match = hardware is None or hardware in p["hardware"]
        domain_match = domain is None or domain in p["domains"]
        if hw_match and domain_match:
            results.append(p)

    # Sort: domain-specific matches before general; then by score
    def _rank(p: dict) -> tuple:
        is_general = "general" in p["domains"] and len(p["domains"]) == 1
        return (not is_general, -p["ohb1_score"])

    return sorted(results, key=_rank)


def _applicable_findings(hardware: Optional[str], domain: Optional[str]) -> list[dict]:
    """Return synthesis findings that apply to this hardware+domain."""
    out = []
    for f in SYNTHESIS_FINDINGS:
        hw_ok = f["applies_to"]["hardware"] == "*" or (
            hardware is not None
            and (
                isinstance(f["applies_to"]["hardware"], list)
                and hardware in f["applies_to"]["hardware"]
                or f["applies_to"]["hardware"] == hardware
            )
        )
        dm_ok = f["applies_to"]["domain"] == "*" or (
            domain is not None
            and (
                isinstance(f["applies_to"]["domain"], list)
                and domain in f["applies_to"]["domain"]
                or f["applies_to"]["domain"] == domain
            )
        )
        if hw_ok and dm_ok:
            out.append(f)
    return out


def _detect_local_hardware() -> Optional[str]:
    """Best-effort hardware detection from /proc/cpuinfo or env."""
    hw = os.getenv("OPENCASTOR_HARDWARE")
    if hw:
        return _normalise_hardware(hw)
    try:
        cpu = pathlib.Path("/proc/cpuinfo").read_text()
        if "BCM2712" in cpu or "Raspberry Pi 5" in cpu:
            # Pi 5 — check total memory to distinguish 4GB vs 8GB
            mem_kb = 0
            for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    mem_kb = int(line.split()[1])
                    break
            return "pi5_8gb" if mem_kb > 6_000_000 else "pi5_4gb"
        if "BCM2711" in cpu or "Raspberry Pi 4" in cpu:
            return "pi5_4gb"  # treat Pi 4 same tier
        if "Jetson" in cpu or "tegra" in cpu.lower():
            return "jetson"
    except Exception:
        pass
    return None


def _print_recommendation(
    hardware: Optional[str],
    domain: Optional[str],
    explain: bool = False,
) -> None:
    hw_label = hardware or "(auto-detect)"
    dm_label = domain or "general"

    print()
    print("  ╔══════════════════════════════════════════════════╗")
    print("  ║    Harness Recommendation  (autoresearch data)   ║")
    print("  ╚══════════════════════════════════════════════════╝")
    print(f"  Hardware:  {hw_label}")
    print(f"  Domain:    {dm_label}")
    print()

    presets = _matching_presets(hardware, domain)
    findings = _applicable_findings(hardware, domain)

    if not presets:
        print("  No matching preset found. Falling back to fleet champion: lower_cost")
        presets = [next(p for p in PRESET_MATRIX if p["id"] == "lower_cost")]

    best = presets[0]
    champion_flag = " ★ FLEET CHAMPION" if best["is_champion"] else ""
    print(f"  Recommended preset:  {best['name']}{champion_flag}")
    print(f"  OHB-1 score:         {best['ohb1_score']}")
    print(f"  {best['tagline']}")
    print()
    print("  Apply:")
    print(f"    castor harness apply --config {best['id']}")
    print(
        f"    # or download: https://raw.githubusercontent.com/craigm26/OpenCastor/main/research/presets/{best['id']}.yaml"
    )
    print()

    if len(presets) > 1:
        print("  Other matches:")
        for p in presets[1:3]:
            print(f"    {p['id']:<25}  OHB-1: {p['ohb1_score']}  — {p['tagline']}")
        print()

    if explain and findings:
        print("  ── Synthesis findings that apply to your config ──────────────")
        for f in findings:
            conf_icon = "●" if f["confidence"] == "high" else "○"
            print(f"  {conf_icon} [{f['confidence'].upper()}] {f['signal']}")
            print(f"       Evidence: {f['evidence']}")
            if f["config_dim"] != "skill_order":
                print(f"       Dim: {f['config_dim']} = {f['recommended_value']}")
            print()

    print("  Source: https://craigm26.github.io/OpenCastor/ · research/index.json")
    print()


def _print_all_findings() -> None:
    print()
    print("  ── Synthesis Signals from OpenCastor Autoresearch Fleet ──────────")
    print("  These are the learnable findings from distributed harness evaluation.")
    print()
    for f in SYNTHESIS_FINDINGS:
        conf_icon = "●" if f["confidence"] == "high" else "○"
        hw = f["applies_to"]["hardware"]
        dm = f["applies_to"]["domain"]
        hw_str = "*" if hw == "*" else (", ".join(hw) if isinstance(hw, list) else hw)
        dm_str = "*" if dm == "*" else (", ".join(dm) if isinstance(dm, list) else dm)
        print(f"  {conf_icon} {f['signal']}")
        print(f"       Hardware: {hw_str}  Domain: {dm_str}  Dim: {f['config_dim']}")
        print(f"       {f['evidence']}")
        print()
    print("  ● = high confidence  ○ = medium confidence")
    print("  Findings update as new research runs complete.")
    print()


def cmd_recommend(args) -> None:
    """Recommend a harness preset based on hardware and domain."""
    hardware = _normalise_hardware(getattr(args, "hardware", None))
    domain = _normalise_domain(getattr(args, "domain", None))
    explain = getattr(args, "explain", False)
    list_findings = getattr(args, "list_findings", False)

    if list_findings:
        _print_all_findings()
        return

    # Auto-detect hardware if not specified
    if hardware is None:
        hardware = _detect_local_hardware()
        if hardware:
            print(f"\n  Auto-detected hardware: {hardware}")

    _print_recommendation(hardware, domain, explain=explain)
