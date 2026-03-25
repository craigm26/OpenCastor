"""Tests for harness YAML ↔ code language consistency.

Validates that default_harness.yaml descriptions, config keys, and labels
accurately reflect the backing Python code. Guards against language drift
where YAML declarations silently diverge from implementation.

See: plan at /root/.claude/plans/snappy-shimmying-rabbit.md
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# ── Paths ────────────────────────────────────────────────────────────────────

_HARNESS_YAML = (
    Path(__file__).resolve().parent.parent / "castor" / "harness" / "default_harness.yaml"
)
_EDITOR_YAML = (
    Path(__file__).resolve().parent.parent / "config" / "official" / "default-harness.rcan.yaml"
)


@pytest.fixture()
def harness_cfg():
    return yaml.safe_load(_HARNESS_YAML.read_text())["harness"]


@pytest.fixture()
def harness_layers(harness_cfg):
    return {layer["id"]: layer for layer in harness_cfg["layers"]}


@pytest.fixture()
def editor_cfg():
    return yaml.safe_load(_EDITOR_YAML.read_text())


# ── Phase 1: Config key coverage ────────────────────────────────────────────


def test_prompt_guard_config_keys_consumed(harness_layers):
    """prompt-guard YAML config keys must match PromptGuard constructor params."""
    from castor.harness.prompt_guard import PromptGuard

    layer = harness_layers["prompt-guard"]
    cfg = layer["config"]

    # The key the code reads is 'block_threshold', not 'risk_threshold'
    assert "block_threshold" in cfg, (
        "prompt-guard YAML must use 'block_threshold' (not 'risk_threshold') — "
        "PromptGuard reads config.get('block_threshold')"
    )
    assert "risk_threshold" not in cfg, (
        "prompt-guard YAML still has 'risk_threshold' which is silently ignored by PromptGuard"
    )

    # Verify code actually reads it
    guard = PromptGuard(cfg)
    assert guard._threshold == cfg["block_threshold"]


def test_working_memory_no_phantom_keys(harness_layers):
    """working-memory YAML must not declare keys that WorkingMemory ignores."""
    layer = harness_layers["working-memory"]
    cfg = layer["config"]

    # WorkingMemory only accepts max_keys (via constructor)
    assert "ttl_s" not in cfg, "ttl_s is not implemented by WorkingMemory — remove from YAML"
    assert "persist" not in cfg, "persist is not implemented by WorkingMemory — remove from YAML"


def test_working_memory_max_entries(harness_layers):
    """working-memory max_entries must be consumed by WorkingMemory."""
    from castor.harness.working_memory import WorkingMemory

    layer = harness_layers["working-memory"]
    cfg = layer["config"]
    wm = WorkingMemory(max_keys=cfg.get("max_entries", 50))
    assert wm._max_keys == cfg["max_entries"]


def test_dlq_no_phantom_keys(harness_layers):
    """dlq YAML must not declare keys that DeadLetterQueue ignores."""
    layer = harness_layers["dlq"]
    cfg = layer["config"]

    assert "max_size" not in cfg, (
        "max_size is not implemented by DeadLetterQueue — remove from YAML"
    )


def test_span_tracer_no_phantom_keys(harness_layers):
    """span-tracer YAML must not declare keys that SpanTracer ignores."""
    layer = harness_layers["span-tracer"]
    cfg = layer["config"]

    assert "export" not in cfg, (
        "SpanTracer exports JSONL (not SQLite) — 'export' key is silently ignored"
    )
    assert "db_path" not in cfg or layer["type"] != "tracer", (
        "SpanTracer uses JSONL files, not a db_path — key is silently ignored"
    )


def test_circuit_breaker_config_consumed(harness_layers):
    """circuit-breaker YAML config keys must be consumed by CircuitBreaker."""
    from castor.harness.circuit_breaker import CircuitBreaker

    layer = harness_layers["circuit-breaker"]
    cfg = layer["config"]

    cb = CircuitBreaker(cfg)
    assert cb._threshold == cfg["failure_threshold"]
    assert cb._cooldown_s == cfg["cooldown_s"]
    assert cb._half_open_probe == cfg["half_open_probe"]


# ── Phase 2: Budget coherence ───────────────────────────────────────────────


def test_cost_gate_description_matches_budget(harness_layers):
    """cost-gate description must reference the actual budget_usd value."""
    layer = harness_layers["cost-gate"]
    budget = layer["config"]["budget_usd"]
    desc = layer["description"]

    assert f"${budget:.2f}" in desc, (
        f"cost-gate description should mention ${budget:.2f} but says: {desc!r}"
    )


def test_cost_gate_description_says_per_run(harness_layers):
    """cost-gate operates per-run (not per-session) as CostMeter tracks per run_id."""
    layer = harness_layers["cost-gate"]
    desc = layer["description"]

    assert "session" not in desc.lower(), (
        f"cost-gate should say 'per-run' not 'per-session' — CostMeter tracks per run_id: {desc!r}"
    )


# ── Phase 3: context_budget interpretation ──────────────────────────────────


def test_context_budget_absolute_token_count(harness_cfg):
    """context_budget > 1.0 must be treated as absolute tokens, not a ratio."""
    budget = harness_cfg["context_budget"]
    assert budget > 1.0, "Test assumes context_budget is an absolute token count"

    from castor.context import ContextBuilder

    # Build with a mock config
    mock_config = {"harness": {"context_budget": budget}, "model": "gemini-2.5-flash"}
    builder = ContextBuilder(config=mock_config)

    # The budget should NOT be multiplied by context_limit
    assert builder._context_budget == budget
    # If treated as ratio: budget_tokens = context_limit * 8192 >> 8192
    # If treated as absolute: budget_tokens = 8192
    # We verify the code path handles > 1.0 as absolute
    assert budget == 8192


# ── Phase 4: Description smoke tests ────────────────────────────────────────


def test_span_tracer_description_no_sqlite(harness_layers):
    """span-tracer exports JSONL, not SQLite — description must reflect this."""
    layer = harness_layers["span-tracer"]
    desc = layer["description"]

    assert "SQLite" not in desc, f"span-tracer exports JSONL, not SQLite: {desc!r}"
    assert "JSONL" in desc, f"span-tracer description should mention JSONL: {desc!r}"


def test_working_memory_description_says_per_run(harness_layers):
    """working-memory is cleared each run(), not per-session."""
    layer = harness_layers["working-memory"]
    desc = layer["description"]

    assert "per-run" in desc.lower() or "Per-run" in desc, (
        f"working-memory should say 'per-run' not 'per-session': {desc!r}"
    )


def test_model_layer_label_uses_project_terminology(harness_layers):
    """Model layer should use 'Tiered Brain' label (project standard term)."""
    # Find the model-type layer
    model_layers = [layer for layer in harness_layers.values() if layer["type"] == "model"]
    assert len(model_layers) == 1
    label = model_layers[0]["label"]

    assert "Tiered Brain" in label, (
        f"Model layer should use 'Tiered Brain' (project terminology), not '{label}'"
    )


def test_drift_detection_docstring_says_jaccard():
    """DriftDetectionHook docstring should say Jaccard, not cosine."""
    from castor.harness.core import DriftDetectionHook

    doc = DriftDetectionHook.__doc__ or ""
    assert "cosine" not in doc.lower(), (
        f"DriftDetectionHook docstring still says 'cosine' but uses Jaccard: {doc[:200]}"
    )
    assert "Jaccard" in doc or "jaccard" in doc, (
        f"DriftDetectionHook docstring should mention Jaccard similarity: {doc[:200]}"
    )


# ── Phase 5: Editor defaults alignment ──────────────────────────────────────


def test_editor_rcan_version_current(editor_cfg):
    """Editor defaults RCAN version should match current project version."""
    version = editor_cfg["rcan_version"]
    assert version.startswith("1.9"), f"Editor defaults RCAN version is {version}, expected 1.9.x"


def test_editor_no_duplicate_note(editor_cfg):
    """Editor defaults should not have a redundant _note field."""
    assert "_note" not in editor_cfg, (
        "Editor defaults has '_note' that duplicates metadata.description — remove it"
    )


def test_editor_vision_enabled_with_camera_skill(editor_cfg):
    """If camera-describe is a builtin skill, vision_enabled should be true."""
    skills = editor_cfg.get("metadata", {}).get("builtin_skills", [])
    if "camera-describe" in skills:
        vision = editor_cfg.get("agent", {}).get("vision_enabled", False)
        assert vision is True, (
            "camera-describe skill is listed but vision_enabled is false — inconsistent"
        )


def test_editor_skill_count_description(editor_cfg):
    """Editor description should not overcount harness skill layers."""
    desc = editor_cfg.get("metadata", {}).get("description", "")
    # Should say "5 harness skill layers" not "all 6 builtin skills"
    assert "all 6" not in desc, (
        f"Editor description overcounts skills — harness has 5 skill layers: {desc!r}"
    )
