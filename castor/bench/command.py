"""``castor bench`` — the CLI entry point, kept out of ``castor/cli.py``.

``castor/cli.py`` carries the parser and one dispatch line; everything the
command actually does lives here, so the ten-minute benchmark can be read,
tested and changed without touching a ten-thousand-line file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

from castor.bench import evallog as evallog_mod
from castor.bench.record import default_out_path, git_sha
from castor.bench.ten_minutes import (
    DEFAULT_REQUEST,
    ROBOTS,
    BenchError,
    ProviderBrain,
    ScriptedBrain,
    build_target,
    run,
)


def _say(line: str) -> None:
    print(line, flush=True)


def add_parser(sub: Any) -> Any:
    """Register ``castor bench`` on an argparse subparsers object.

    Args:
        sub: The ``add_subparsers()`` object from ``castor/cli.py``.

    Returns:
        The ``bench`` parser, so a caller can inspect it in a test.
    """
    import argparse

    parser = sub.add_parser(
        "bench",
        help="Run a named OpenCastor benchmark and write its JSON record",
        epilog=(
            "Examples:\n"
            "  castor bench ten-minutes --robot microduck --ci\n"
            "  castor bench ten-minutes --robot microduck --host 192.168.1.42 "
            "--brain anthropic\n"
            "  castor bench ten-minutes --robot microduck --sim --floor\n"
            "  castor bench ten-minutes --robot rc-car        # prints the checkpoints it "
            "would use\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    bench_sub = parser.add_subparsers(dest="bench_cmd")

    p10 = bench_sub.add_parser(
        "ten-minutes",
        help="Box to a robot moving under an LLM, timed against a 600 s budget",
        description=(
            "Seven checkpoints, six mandatory, in order, with C6.t - T0 under the budget. "
            "The clock starts when the runner writes its first command and ends at the "
            "first robot.move OpenCastor originated from a model's answer, accepted with "
            "the deadman armed. Mock targets report ci-pass and are never a pass of the "
            "ten-minute goal. Wi-Fi onboarding is excluded and reported."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p10.add_argument(
        "--robot",
        default="microduck",
        choices=sorted(ROBOTS),
        help="Which robot (default: microduck)",
    )
    p10.add_argument(
        "--transport",
        default="auto",
        choices=("auto", "unix", "ssh", "tcp", "webrtc", "mock"),
        help="Transport. 'mock' is refused unless --ci",
    )
    p10.add_argument("--host", default=None, help="Skip discovery; the duck's address")
    p10.add_argument("--user", default=None, help="SSH login, for --transport ssh")
    p10.add_argument("--port", type=int, default=None, help="TCP port, for --transport tcp")
    p10.add_argument(
        "--socket", default="/run/robotd.sock", help="robotd socket path on the robot"
    )
    p10.add_argument(
        "--brain",
        default=None,
        help="PROVIDER[:MODEL] under test; default is the registry's own. "
        "'scripted' answers a canned plan and can never produce a pass",
    )
    p10.add_argument(
        "--ci",
        action="store_true",
        help="Run against a mock robotd. The verdict is ci-pass, never pass",
    )
    p10.add_argument(
        "--mock-cmd",
        default=None,
        help="Command that serves the mock; <tmp> is replaced with the socket path. "
        "Default: python3 -u -m castor.bench.mock_robotd --socket <tmp>",
    )
    p10.add_argument(
        "--sim",
        action="store_true",
        help="Run against Pollen's scripts/duck-sim: the real robotd on a MuJoCo body",
    )
    p10.add_argument("--sim-repo", default=None, help="The microduck checkout, for --sim")
    p10.add_argument("--sim-rl", default=None, help="The microduck_rl checkout, for --sim")
    p10.add_argument(
        "--floor",
        action="store_true",
        help="Enable C7. Asks once before the duck moves",
    )
    p10.add_argument("--yes", action="store_true", help="Do not ask before --floor moves the duck")
    p10.add_argument("--budget-s", type=float, default=600.0, help="The clock, in seconds")
    p10.add_argument(
        "--exclude-wifi",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Record Wi-Fi onboarding as an excluded interval of this many seconds",
    )
    p10.add_argument(
        "--exclude-wifi-reason", default=None, help="One line saying why that number is what it is"
    )
    p10.add_argument(
        "--request", default=DEFAULT_REQUEST, help="What the brain is asked for at C4"
    )
    p10.add_argument(
        "--fresh-venv",
        default=None,
        metavar="PATH",
        help="A venv built after T0, for C1. Without it C1 times the running interpreter "
        "and the record says so",
    )
    p10.add_argument("--out", default=None, help="Where the JSON record goes")
    p10.add_argument("--evallog", default=None, help="Also emit an inspect-robots EvalLog v1")
    p10.add_argument(
        "--scene",
        default="A",
        choices=("A", "B", "C"),
        help="Which route the run took, for the EvalLog scene_id",
    )
    return parser


def cmd_bench(args: Any) -> int:
    """Dispatch ``castor bench``. Returns the process exit code."""
    if getattr(args, "bench_cmd", None) != "ten-minutes":
        _say("castor bench: name a benchmark. The one that exists is `ten-minutes`.")
        _say("  castor bench ten-minutes --robot microduck --ci")
        return 2
    return cmd_ten_minutes(args)


def cmd_ten_minutes(args: Any) -> int:
    """Run the ten-minute benchmark. 0 on a pass or a ci-pass, 1 otherwise."""
    robot = getattr(args, "robot", "microduck")
    spec = ROBOTS.get(robot)
    if spec is not None and not spec.implemented:
        _say(f"castor bench ten-minutes --robot {robot}: {spec.note}")
        _say("")
        _say("The checkpoints it would use:")
        for cid, text in spec.checkpoints.items():
            _say(f"  {cid:<3} {text}")
        return 2

    if getattr(args, "floor", False) and not getattr(args, "yes", False):
        if not sys.stdin.isatty():
            _say("castor bench: --floor moves the robot; pass --yes to confirm non-interactively")
            return 2
        answer = input("C7 walks the duck. Is it on a clear floor? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            _say("Not moving it. Run again without --floor for C1 through C6.")
            return 2

    try:
        target = build_target(
            transport=getattr(args, "transport", "auto"),
            host=getattr(args, "host", None),
            user=getattr(args, "user", None),
            port=getattr(args, "port", None),
            socket_path=getattr(args, "socket", "/run/robotd.sock"),
            ci=bool(getattr(args, "ci", False)),
            sim=bool(getattr(args, "sim", False)),
            mock_cmd=getattr(args, "mock_cmd", None),
            sim_repo=getattr(args, "sim_repo", None),
            sim_rl=getattr(args, "sim_rl", None),
        )
    except BenchError as exc:
        _say(f"castor bench: {exc}")
        return 2

    brain_spec: Optional[str] = getattr(args, "brain", None)
    if brain_spec == "scripted" or (brain_spec is None and getattr(args, "ci", False)):
        brain: Any = ScriptedBrain()
    else:
        brain = ProviderBrain(brain_spec)

    _say(f"castor bench ten-minutes — {robot} against the {target.kind} target")
    try:
        record = run(
            robot=robot,
            target=target,
            brain=brain,
            request=getattr(args, "request", DEFAULT_REQUEST),
            budget_s=float(getattr(args, "budget_s", 600.0)),
            floor=bool(getattr(args, "floor", False)),
            wifi_onboarding_s=getattr(args, "exclude_wifi", None),
            wifi_reason=getattr(args, "exclude_wifi_reason", None),
            fresh_venv=getattr(args, "fresh_venv", None),
            repo_shas=_repo_shas(args),
            say=_say,
        )
    except BenchError as exc:
        _say(f"castor bench: {exc}")
        return 2

    out = Path(getattr(args, "out", None) or default_out_path(robot))
    record.write(out)
    _say("")
    _say(f"  verdict: {record.verdict} — {record.verdict_reason}")
    _say(f"  elapsed: {record.elapsed_s:.1f} s of {record.budget_s:.0f} s")
    _say(f"  record:  {out}")

    evallog_path = getattr(args, "evallog", None)
    if evallog_path:
        written = evallog_mod.write_eval_log(
            record, evallog_path, scene=getattr(args, "scene", "A")
        )
        _say(f"  evallog: {written}")

    for note in record.notes:
        _say(f"  note: {note}")

    return 0 if record.verdict in ("pass", "ci-pass") else 1


def _repo_shas(args: Any) -> dict:
    """The shas that decide a result, for ``environment.repo_shas``."""
    import castor

    shas = {"opencastor-runtime": git_sha(Path(castor.__file__).resolve().parent.parent)}
    repo = getattr(args, "sim_repo", None)
    if repo:
        shas["pollen/microduck"] = git_sha(Path(repo))
    return shas
