"""The ten-minute benchmark's record: one JSON object per run.

THE RULE THIS FILE ENFORCES IS THAT EVERY FIELD IS MEASURED OR EXPLICITLY NULL.
There is no default that stands in for a reading nobody took. A checkpoint that
did not run is ``ok: null`` with a reason; a checkpoint that ran and failed is
``ok: false`` with the evidence that failed it; ``stepped`` is never ``true``
unless odometry moved.

``transcript`` is the load-bearing field and the one most likely to be dropped.
Every command the operator typed, with its timestamp, is what makes a claimed
number auditable by someone who was not there. A run that reports 412 s and
lists four commands is a different artifact from one that reports 412 s and
lists twenty-two.

``wire`` is the only part of the record that can prove the driver spoke the
protocol rather than logged that it did, which is the exact class of bug the
review's traps 1, 3 and 4 belong to. It carries, at minimum, the C5 move, its
first re-send, and the C6 stop.

Schema: ``docs/benchmarks/ten-minutes.md``.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

#: The benchmark's name, which is also its EvalLog ``eval.task``.
BENCHMARK = "opencastor/ten-minutes"

#: Bumped when a field changes meaning. A reader that does not know this number
#: should refuse the file rather than guess.
SCHEMA_VERSION = 1

#: The ten minutes, in seconds.
DEFAULT_BUDGET_S = 600.0

#: Every verdict this benchmark can reach.
#:
#: ``pass`` / ``fail``   a real duck or the simulator, with a real brain.
#: ``ci-pass`` / ``ci-fail``  a run whose target or brain was not real. **A
#: ci-pass is never counted as a pass of the ten-minute goal**, and the record
#: says why in ``verdict_reason``.
VERDICTS = ("pass", "fail", "ci-pass", "ci-fail")


def utc_now_iso() -> str:
    """``datetime.now(UTC).isoformat()`` with a ``Z``, the way EvalLog spells it."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def git_sha(repo: Path) -> Optional[str]:
    """The HEAD sha of ``repo``, or None when it is not a checkout.

    Args:
        repo: A directory that may be inside a git working tree.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:  # noqa: BLE001 — no sha is a null, never a crash
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


@dataclass
class Checkpoint:
    """One checkpoint: a single timestamp, a verdict and its evidence.

    Args:
        id: ``C1`` .. ``C7``.
        t: Seconds since T0, or None when the checkpoint did not run.
        ok: True passed, False failed, None did not run. **A skipped mandatory
            checkpoint is a fail, not an omission** — the runner records None
            only for C7, which is optional by design.
        evidence: What was read, in the wire's own words.
    """

    id: str
    t: Optional[float] = None
    ok: Optional[bool] = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "t": self.t, "ok": self.ok, "evidence": self.evidence}


@dataclass
class Record:
    """The whole run, as it will be written.

    Times are seconds since T0 unless the field name says otherwise. T0 is the
    moment the runner wrote its first command to the transcript.
    """

    robot: str
    target: str  # "mock" | "sim" | "real"
    budget_s: float = DEFAULT_BUDGET_S
    started_at: str = field(default_factory=utc_now_iso)

    environment: dict[str, Any] = field(default_factory=dict)
    duck: dict[str, Any] = field(default_factory=dict)
    brain: dict[str, Any] = field(default_factory=dict)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    excluded: dict[str, Any] = field(
        default_factory=lambda: {"wifi_onboarding_s": None, "reason": None}
    )
    transcript: list[dict] = field(default_factory=list)
    wire: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    verdict: str = "fail"
    verdict_reason: str = ""
    elapsed_s: float = 0.0

    _t0: float = field(default_factory=time.monotonic, repr=False)

    # ------------------------------------------------------------------
    # clock
    # ------------------------------------------------------------------

    def now(self) -> float:
        """Seconds since T0."""
        return time.monotonic() - self._t0

    def reset_clock(self) -> None:
        """Set T0 to now. Called once, as the first command is written."""
        self._t0 = time.monotonic()
        self.started_at = utc_now_iso()

    # ------------------------------------------------------------------
    # the three logs
    # ------------------------------------------------------------------

    def typed(self, command: str) -> float:
        """Record a command the runner or the operator typed. Returns its ``t``.

        Every command, including the ones that failed and the ones that were
        retried. This is the field that makes the number auditable.
        """
        t = self.now()
        self.transcript.append({"t": round(t, 3), "typed": command})
        return t

    def wire_line(self, direction: str, obj: Any, *, t: Optional[float] = None) -> None:
        """Record one line of JSON-RPC, exactly as it went over the socket.

        Args:
            direction: ``"out"`` (this process wrote it) or ``"in"``.
            obj: The decoded object, or a str already on the wire.
            t: Seconds since T0; measured now when omitted.
        """
        line = obj if isinstance(obj, str) else json.dumps(obj, separators=(",", ":"))
        self.wire.append(
            {"t": round(self.now() if t is None else t, 4), "dir": direction, "line": line}
        )

    def note(self, sentence: str) -> None:
        """Something a reader has to know that is not a measurement."""
        self.notes.append(sentence)

    # ------------------------------------------------------------------
    # checkpoints
    # ------------------------------------------------------------------

    def checkpoint(self, cid: str) -> Optional[Checkpoint]:
        for cp in self.checkpoints:
            if cp.id == cid:
                return cp
        return None

    def mark(
        self,
        cid: str,
        ok: Optional[bool],
        evidence: Optional[dict] = None,
        *,
        t: Optional[float] = None,
    ) -> Checkpoint:
        """Stamp a checkpoint. Replaces any earlier stamp of the same id."""
        cp = self.checkpoint(cid)
        if cp is None:
            cp = Checkpoint(cid)
            self.checkpoints.append(cp)
        cp.ok = ok
        cp.t = None if ok is None and t is None else (self.now() if t is None else t)
        cp.evidence = dict(evidence or {})
        return cp

    # ------------------------------------------------------------------
    # the pass rule
    # ------------------------------------------------------------------

    def decide(self, mandatory: "tuple[str, ...]", *, real: bool, real_reason: str) -> str:
        """Apply the pass rule and set ``verdict``, ``verdict_reason``, ``elapsed_s``.

        The rule: **all mandatory checkpoints, in order, with the last one's
        ``t`` minus T0 under the budget.** Not the sum of the steps: wall clock
        from the first command, including every retry, prompt and wait.

        Args:
            mandatory: Checkpoint ids that must all be ``ok`` and in order.
            real: Whether this run may claim a plain ``pass``. False for a mock
                target or a scripted brain — those can only reach ``ci-pass``.
            real_reason: One sentence saying why, carried into the record.

        Returns:
            The verdict.
        """
        reasons: list[str] = []
        passed = True

        stamped: list[Checkpoint] = []
        for cid in mandatory:
            cp = self.checkpoint(cid)
            if cp is None or cp.ok is not True:
                passed = False
                reasons.append(f"{cid} did not pass")
                continue
            stamped.append(cp)

        # In order. A checkpoint whose timestamp precedes the one before it did
        # not happen when the record says it did.
        times = [cp.t for cp in stamped if cp.t is not None]
        if times != sorted(times):
            passed = False
            reasons.append("checkpoints are not in order")

        last = self.checkpoint(mandatory[-1]) if mandatory else None
        self.elapsed_s = round(last.t, 3) if last is not None and last.t is not None else round(
            self.now(), 3
        )
        if passed and self.elapsed_s >= self.budget_s:
            passed = False
            reasons.append(f"{self.elapsed_s:.1f} s is over the {self.budget_s:.0f} s budget")

        if real:
            self.verdict = "pass" if passed else "fail"
        else:
            self.verdict = "ci-pass" if passed else "ci-fail"

        if passed:
            head = (
                f"all of {', '.join(mandatory)} in order in {self.elapsed_s:.1f} s "
                f"of {self.budget_s:.0f} s"
            )
            self.verdict_reason = head if real else f"{head}; {real_reason}"
        else:
            self.verdict_reason = "; ".join(reasons) if real else (
                "; ".join(reasons) + f"; {real_reason}"
            )
        return self.verdict

    # ------------------------------------------------------------------
    # writing
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "benchmark": BENCHMARK,
            "schema_version": SCHEMA_VERSION,
            "robot": self.robot,
            "target": self.target,
            "verdict": self.verdict,
            "verdict_reason": self.verdict_reason,
            "started_at": self.started_at,
            "elapsed_s": self.elapsed_s,
            "budget_s": self.budget_s,
            "environment": self.environment,
            "duck": self.duck,
            "brain": self.brain,
            "checkpoints": [cp.to_dict() for cp in self.checkpoints],
            "excluded": self.excluded,
            "transcript": self.transcript,
            "wire": self.wire,
            "notes": self.notes,
        }

    def write(self, path: "str | Path") -> Path:
        """Write the record as pretty JSON. Returns the path written."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        return out


def host_environment() -> dict:
    """The machine the benchmark ran on, and the versions that decide the result."""
    try:
        from importlib.metadata import version as _v

        castor_version: Optional[str] = _v("opencastor")
    except Exception:  # noqa: BLE001 — running from a tree with no metadata
        castor_version = None

    import castor

    castor_dir = Path(castor.__file__).resolve().parent
    return {
        "host": {
            "platform": sys.platform,
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "opencastor": {
            "version": castor_version,
            "git_sha": git_sha(castor_dir.parent),
            "path": str(castor_dir),
            "wheel": None,
        },
        "repo_shas": {},
        "transport": {},
    }


def default_out_path(robot: str) -> str:
    """``./ten-minutes-<robot>-<iso>.json``, the CLI's documented default."""
    stamp = utc_now_iso().replace(":", "-").replace(".", "-")
    return f"ten-minutes-{robot}-{stamp}.json"
