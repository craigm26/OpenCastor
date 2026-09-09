"""Export a ten-minute record into inspect-robots' EvalLog v1.

WHY THIS EXISTS RATHER THAN A SECOND FORMAT. Microduck Studio already wrote the
hard part: ``StudioKit/Sources/StudioKit/EvalLog.swift`` pins
``schemaVersion = 1`` and is the only file in that package that spells one of
the schema's forty wire keys, and ``scripts/check_evallog_parity.sh`` proves its
output is read by the real ``inspect-robots==0.58.0``, re-dumped byte-identically
and rendered. Fixing the mapping here stops two projects inventing two formats
for the same measurement.

TWO REFUSALS CARRIED OVER FROM THE APP'S OWN LIST.

* **No success scorer without a motion-evidence scorer.** A run that passed C1
  through C6 and skipped C7 has proved that commands were accepted, not that a
  robot moved. So ``success_at_end`` is written **only** when C7 actually
  measured motion, and ``motion_evidence`` is written beside it. A viewer must
  not be able to read the first as the second.
* **Nothing is added to the schema.** Their reader builds ``EvalSpec(**data["eval"])``
  and one extra key there is a ``TypeError``. Everything this benchmark wants to
  record that upstream has no field for rides inside ``policy_config``,
  ``embodiment_info``, ``scene_metadata`` or ``trial_metadata``, which their
  reader passes through untouched.

``eval.inspect_robots_version`` carries the same shape of honest string the app
uses: a sentence naming what wrote the file, because upstream's own report
prints that field verbatim in its footer and a version number there would say a
Python library ran. No inspect-robots ran here either.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from castor.bench.record import BENCHMARK, Record, utc_now_iso

#: EvalLog v1. ``read_eval_log`` refuses anything else, by design.
SCHEMA_VERSION = 1

#: Which route the run took, as ``samples[].scene_id``.
SCENES = {
    "A": "A-castor-duck",
    "B": "B-studio-bridge",
    "C": "C-pollen-console",
}

#: The producer sentence. It names itself and denies them, for the reason the
#: app states beside its own: their report prints this string in its footer.
PRODUCER = "written by OpenCastor castor.bench; no inspect-robots ran"


def _clean(value: Any) -> Any:
    """JSON that survives their ``_sanitize`` and ``allow_nan=False``."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    return value


def to_eval_log(record: Record, *, scene: str = "A") -> dict:
    """Build the EvalLog v1 object for one ten-minute record.

    Args:
        record: A decided record — ``run()`` returns one.
        scene: ``"A"``, ``"B"`` or ``"C"``; the route the run took.

    Returns:
        A dict ready for ``json.dump`` and for ``read_eval_log``.
    """
    data = record.to_dict()
    checkpoints = data["checkpoints"]
    by_id = {cp["id"]: cp for cp in checkpoints}
    passed = sum(1 for cp in checkpoints if cp["ok"] is True and cp["id"] != "C7")

    c7 = by_id.get("C7") or {"ok": None, "evidence": {}}
    stepped = c7.get("evidence", {}).get("stepped")
    measured_motion = c7.get("ok") is True and stepped is True

    # One trial per checkpoint, so `trial_metadata` carries id, t, ok, evidence
    # and the eight parallel arrays stay parallel by construction.
    trials = [cp for cp in checkpoints]
    epochs: list[dict[str, float]] = []
    termination: list[Optional[str]] = []
    trial_metadata: list[dict[str, Any]] = []
    for cp in trials:
        cid = cp["id"]
        if cp["ok"] is None:
            # An unrun checkpoint is recorded and never scored: an empty epoch
            # beside a null termination reason, which is what their reducer and
            # `errored_trials` mean.
            epochs.append({})
            termination.append(None)
        else:
            epochs.append({"checkpoint_reached": 1.0 if cp["ok"] else 0.0})
            termination.append(
                _termination_for(cid, cp, data) if not cp["ok"] else _reached(cid, cp)
            )
        trial_metadata.append(
            _clean({"checkpoint": cid, "t": cp["t"], "ok": cp["ok"], "evidence": cp["evidence"]})
        )

    scored = [e for e in epochs if e]
    reduced: dict[str, Any] = {}
    if scored:
        reduced["checkpoint_reached"] = sum(
            e["checkpoint_reached"] for e in scored
        ) / len(scored)
    if measured_motion:
        # Only now may a success number exist, and it never travels alone.
        reduced["motion_evidence"] = 1.0
        reduced["success_at_end"] = 1.0 if data["verdict"] in ("pass", "ci-pass") else 0.0
        for index, cp in enumerate(trials):
            if cp["id"] == "C7":
                epochs[index]["motion_evidence"] = 1.0
                epochs[index]["success_at_end"] = reduced["success_at_end"]
                termination[index] = "success" if reduced["success_at_end"] == 1.0 else "failure"

    errored = sum(1 for e in epochs if not e)

    sample = {
        "scene_id": SCENES.get(scene, scene),
        "status": "success" if data["verdict"] in ("pass", "ci-pass") else "error",
        "reduced": _clean(reduced),
        "epochs": [_clean(e) for e in epochs],
        "error": None if data["verdict"] in ("pass", "ci-pass") else data["verdict_reason"],
        "instruction": BENCHMARK,
        "scene_metadata": _clean(
            {
                "reducer": "mean",
                "route": SCENES.get(scene, scene),
                "target": data["target"],
                "verdict": data["verdict"],
                "verdict_reason": data["verdict_reason"],
                "budget_s": data["budget_s"],
                "excluded": data["excluded"],
                "transcript": data["transcript"],
                "wire": data["wire"],
                "notes": data["notes"],
            }
        ),
        "operator_judgements": [None] * len(epochs),
        "judgement_sources": [None] * len(epochs),
        "operator_notes": [None] * len(epochs),
        "operator_messages": [[] for _ in epochs],
        "trial_metadata": trial_metadata,
        "termination_reasons": termination,
        "policy_transcripts": [None] * len(epochs),
    }

    duck = data["duck"] or {}
    health = duck.get("health") or {}
    loop = health.get("control_loop") or {}
    embodiment_info = _clean(
        {
            "hello": duck.get("hello"),
            "name": duck.get("name"),
            "serial": duck.get("serial"),
            "name_source": duck.get("name_source"),
            "policies": duck.get("policies"),
            "obs_len": duck.get("obs_len"),
            "action_len": duck.get("action_len"),
            "achieved_hz": loop.get("achieved_hz"),
            "target_hz": loop.get("target_hz"),
            "battery": health.get("battery"),
            "capabilities": ["self_paced"],
        }
    )

    brain = data["brain"] or {}
    policy_config = _clean(
        {
            "criterion": (
                "all of C1..C6 in order, with C6.t - T0 under the budget; wall clock from "
                "the first command, including every retry, prompt and wait"
            ),
            "horizon_note": (
                f"max_seconds is the benchmark's own budget ({data['budget_s']} s); "
                "max_steps is null because nothing here counts control ticks"
            ),
            "seed_note": "nothing in this benchmark is seeded, so eval.seed is null",
            "identity_note": (
                "every identity field was read off the wire and held to the keys in "
                "castor/bench/wire.py; an absent key fails C3 rather than printing '?'"
            ),
            "step_counts": (
                "total_steps is 0 unless C7 measured motion; it is never seconds multiplied "
                "by a tick rate"
            ),
            "trace_note": (
                "scene_metadata carries the full transcript and the wire lines, which is "
                "what makes the number auditable by someone who was not there"
            ),
            "provider": brain.get("provider"),
            "model": brain.get("model"),
            "scripted_brain": brain.get("scripted"),
            "tools": brain.get("tools"),
            "transport": data["environment"].get("transport"),
            "repo_shas": data["environment"].get("repo_shas"),
        }
    )

    started = data["started_at"]
    return {
        "version": SCHEMA_VERSION,
        "status": "success" if data["verdict"] in ("pass", "ci-pass") else "error",
        "eval": {
            "task": BENCHMARK,
            "policy": f"{brain.get('provider') or 'none'}"
            + (f":{brain['model']}" if brain.get("model") else ""),
            "embodiment": data["robot"],
            "created": started,
            "inspect_robots_version": PRODUCER,
            "git_commit": (data["environment"].get("opencastor") or {}).get("git_sha"),
            "policy_config": policy_config,
            "embodiment_info": embodiment_info,
            "seed": None,
            "max_steps": None,
            "max_seconds": float(data["budget_s"]),
        },
        "results": {
            "total_scenes": 1,
            "total_trials": len(epochs),
            "metrics": _clean(reduced),
            "errored_trials": errored,
        },
        "stats": {
            "started_at": started,
            "completed_at": utc_now_iso(),
            "duration_s": float(data["elapsed_s"]),
            "total_steps": 0,
            "mean_inference_latency_s": None,
            "frames_dir": None,
        },
        "samples": [sample],
        "error": None if data["verdict"] in ("pass", "ci-pass") else data["verdict_reason"],
        "_checkpoints_passed": None,  # placeholder removed below
    }


def _reached(cid: str, cp: dict) -> str:
    if cid == "C6":
        return str(cp.get("evidence", {}).get("fired_by") or "stopped")
    return "reached"


def _termination_for(cid: str, cp: dict, data: dict) -> str:
    evidence = cp.get("evidence") or {}
    for key in ("error", "reason"):
        if evidence.get(key):
            return str(evidence[key])[:200]
    if evidence.get("problems"):
        return "; ".join(str(p) for p in evidence["problems"])[:200]
    return f"{cid} failed"


def write_eval_log(record: Record, path: "str | Path", *, scene: str = "A") -> Path:
    """Write the EvalLog v1 file. Returns the path.

    The bytes are ``json.dumps(..., indent=2, sort_keys=True)``, which is what
    upstream's own ``_sanitize`` round trip produces, so a re-dump comparison
    can be exact.
    """
    log = to_eval_log(record, scene=scene)
    log.pop("_checkpoints_passed", None)
    # `checkpoints_passed` belongs in metrics, where their reducer can see it.
    passed = sum(
        1
        for cp in record.to_dict()["checkpoints"]
        if cp["ok"] is True and cp["id"] != "C7"
    )
    log["results"]["metrics"] = {
        **log["results"]["metrics"],
        "elapsed_s": float(record.elapsed_s),
        "checkpoints_passed": float(passed),
    }
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # No trailing newline: upstream's own round trip is
    # `json.dumps(_sanitize(log.to_dict()), indent=2, sort_keys=True)`, and one
    # extra byte is the difference between "their writer reproduces this file"
    # and a red parity gate. Measured against inspect-robots 0.58.0.
    out.write_text(
        json.dumps(log, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8"
    )
    return out
