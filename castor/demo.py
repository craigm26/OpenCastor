"""
OpenCastor Demo — Full Agent Stack Pipeline.

Runs a 5-act demo showcasing the entire OpenCastor stack end-to-end on
simulated sensor data.  No hardware, no API keys required.

Acts:
  1. System Init       — initialise all layers and agents
  2. Perception Loop   — N ticks of observe → brain → act
  3. Task Dispatch     — TaskPlanner + ManipulatorSpecialist grasp demo
  4. Sisyphus Loop     — mock self-improving episode analysis
  5. Summary           — stats and next-step links

Usage:
    castor demo
    castor demo --steps 5 --delay 0.5
    castor demo --layout minimal
    castor demo --no-color
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any

# ---------------------------------------------------------------------------
# Rich — graceful fallback to plain-text
# ---------------------------------------------------------------------------

_NO_COLOR = bool(os.environ.get("NO_COLOR", ""))

try:
    if _NO_COLOR:
        raise ImportError("NO_COLOR env set")
    from rich.console import Console
    from rich.rule import Rule

    _RICH = True
    _console = Console()
except ImportError:
    _RICH = False
    _console = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Mock thought library — 20+ rich responses for each brain layer
# ---------------------------------------------------------------------------

_FAST_THOUGHTS = [
    ("move forward @ {speed:.2f}", "move", 0.45),
    ("path clear — accelerating to {speed:.2f}", "move", 0.55),
    ("veering right — obstacle left, speed {speed:.2f}", "move", 0.35),
    ("veering left — obstacle right, speed {speed:.2f}", "move", 0.35),
    ("wide open — cruise at {speed:.2f}", "move", 0.6),
    ("navigating cautiously @ {speed:.2f}", "move", 0.2),
    ("slight course correction → speed {speed:.2f}", "move", 0.4),
    ("diagonal traverse — speed {speed:.2f}", "move", 0.3),
    ("person detected — slow to {speed:.2f}", "move", 0.15),
    ("gap spotted — threading @ {speed:.2f}", "move", 0.25),
    ("heading toward goal @ {speed:.2f}", "move", 0.5),
    ("scanning boundary — speed {speed:.2f}", "move", 0.18),
    ("room edge → sweep right @ {speed:.2f}", "move", 0.3),
    ("backtrack maneuver @ {speed:.2f}", "move", 0.2),
    ("corridor clear — fast pass @ {speed:.2f}", "move", 0.58),
    ("dog in path — skirting @ {speed:.2f}", "move", 0.12),
    ("table detected — under-clearance check, speed {speed:.2f}", "move", 0.1),
    ("bottle on floor — route around @ {speed:.2f}", "move", 0.28),
    ("re-centering after drift @ {speed:.2f}", "move", 0.38),
    ("obstacle avoidance complete — resuming @ {speed:.2f}", "move", 0.42),
]

_FAST_STOP_THOUGHTS = [
    "obstacle < 0.4 m — initiating E-STOP",
    "safety threshold breached — halting",
    "emergency stop: proximity alert",
    "collision imminent — full stop",
]

_PLANNER_THOUGHTS = [
    "exploring room systematically",
    "updating occupancy map from recent detections",
    "goal re-evaluated: sweep remaining quadrants",
    "strategic re-evaluation: prioritise open corridor",
    "replanning: obstacle cluster detected east side",
    "coverage progress 43% — continuing sweep",
    "long-range path optimised for efficiency",
    "multi-step plan: reach charging dock via south route",
    "mission phase 2: item localisation",
    "terrain analysis complete — waypoint sequence updated",
]

_REACTIVE_CLEAR = [
    "CLEAR — no e-stop",
    "CLEAR — safe to proceed",
    "CLEAR — all bounds nominal",
    "CLEAR — proximity OK",
]

# ---------------------------------------------------------------------------
# Sensor data simulation
# ---------------------------------------------------------------------------

_HAILO_LABELS = ["person", "chair", "bottle", "dog", "table", "bicycle", "cat", "backpack"]
_OBSTACLE_LABELS = {"person", "bicycle", "cat", "dog", "chair", "table"}


def _generate_mock_sensor_data(tick: int) -> dict[str, Any]:
    """Return a realistic simulated sensor package for one tick.

    Keys:
        hailo_detections: list of detection dicts
        frame_shape: (height, width) tuple
        frame_size_kb: approx KB size of captured frame
        timestamp: float unix time
    """
    n_detections = random.randint(1, 4)
    detections = []
    for _ in range(n_detections):
        label = random.choice(_HAILO_LABELS)
        conf = round(random.uniform(0.40, 0.97), 2)
        x1 = round(random.uniform(0.0, 0.6), 2)
        y1 = round(random.uniform(0.0, 0.6), 2)
        x2 = round(min(x1 + random.uniform(0.05, 0.35), 1.0), 2)
        y2 = round(min(y1 + random.uniform(0.05, 0.45), 1.0), 2)
        det: dict[str, Any] = {
            "label": label,
            "confidence": conf,
            "bbox": [x1, y1, x2, y2],
        }
        # ~40% chance of having depth
        if random.random() < 0.4:
            det["distance_m"] = round(random.uniform(0.3, 4.5), 2)
        detections.append(det)

    return {
        "hailo_detections": detections,
        "frame_shape": (480, 640),
        "frame_size_kb": random.randint(28, 68),
        "timestamp": time.time(),
        "tick": tick,
    }


# ---------------------------------------------------------------------------
# Helpers: printing
# ---------------------------------------------------------------------------

_SEP = "━" * 39


def _print(msg: str = "", style: str = "") -> None:
    """Print with rich markup if available, else plain text."""
    if _RICH and _console:
        _console.print(msg, highlight=False)
    else:
        # Strip basic rich markup for plain text
        import re

        plain = re.sub(r"\[/?[^\]]*\]", "", msg)
        print(plain)


def _rule(title: str = "") -> None:
    if _RICH and _console:
        _console.print(Rule(title, style="dim"))
    else:
        if title:
            pad = max(0, 39 - len(title) - 2)
            print(f"── {title} {'─' * pad}")
        else:
            print(_SEP)


def _sleep(secs: float) -> None:
    if secs > 0:
        time.sleep(secs)


# ---------------------------------------------------------------------------
# ACT 1: System Init
# ---------------------------------------------------------------------------

_INIT_DELAY = 0.25  # seconds per init line


def _act1_init(no_color: bool = False) -> dict[str, Any]:
    """Initialise and return the stack components."""
    global _RICH, _console  # noqa: PLW0603
    if no_color and _RICH:
        _RICH = False
        _console = None

    _print()
    if _RICH and _console:
        _console.print(f"[bold cyan]🤖  OpenCastor Demo — Full Agent Stack[/]\n[dim]{_SEP}[/]")
    else:
        print(f"🤖  OpenCastor Demo — Full Agent Stack\n{_SEP}")

    _print("[dim]Initializing...[/dim]" if _RICH else "Initializing...")

    def _ok(label: str, note: str) -> None:
        _sleep(_INIT_DELAY)
        if _RICH and _console:
            _console.print(f"  [green]✅[/] [bold]{label:<35}[/] [dim]{note}[/]")
        else:
            print(f"  ✅  {label:<35} {note}")

    _ok("Layer 0: Reactive safety", "rule-based, <1ms")
    _ok("Layer 1: Fast brain", "mock: Qwen2.5-VL")
    _ok("Layer 2: Planner", "mock: Claude")

    # Instantiate real agents
    from castor.agents.navigator import NavigatorAgent
    from castor.agents.observer import ObserverAgent
    from castor.agents.shared_state import SharedState

    shared_state = SharedState()
    observer = ObserverAgent(shared_state=shared_state)
    navigator = NavigatorAgent(config={"max_speed": 0.6}, shared_state=shared_state)

    _ok("Observer Agent", "spawned")
    _ok("Navigator Agent", "spawned")

    # Instantiate TaskPlanner with specialists
    from castor.specialists.dock import DockSpecialist
    from castor.specialists.manipulator import ManipulatorSpecialist
    from castor.specialists.responder import ResponderSpecialist
    from castor.specialists.scout import ScoutSpecialist
    from castor.specialists.task_planner import TaskPlanner

    specialists = [
        ManipulatorSpecialist(),
        ScoutSpecialist(),
        DockSpecialist(),
        ResponderSpecialist(),
    ]
    task_planner = TaskPlanner(specialists=specialists)

    _ok("Task Specialists (4)", "ready")
    _ok("Swarm", "solo mode")

    _print()

    return {
        "shared_state": shared_state,
        "observer": observer,
        "navigator": navigator,
        "task_planner": task_planner,
    }


# ---------------------------------------------------------------------------
# ACT 2: Perception Loop
# ---------------------------------------------------------------------------


async def _tick(
    tick_n: int,
    steps: int,
    observer: Any,
    navigator: Any,
    delay: float,
) -> dict[str, Any]:
    """Run one perception-action tick. Returns tick summary dict."""
    tick_start = time.monotonic()

    # Generate sensor data
    sensor_pkg = _generate_mock_sensor_data(tick_n)
    frame_kb = sensor_pkg["frame_size_kb"]
    h, w = sensor_pkg["frame_shape"]

    # ---- Observer ----
    scene = await observer.observe(sensor_pkg)

    # Build detection summary string
    det_parts = []
    for d in sorted(scene.detections, key=lambda x: -x.confidence)[:3]:
        det_parts.append(f"{d.label} ({d.confidence:.2f})")
    det_str = ", ".join(det_parts) if det_parts else "none"

    closest_m = scene.closest_obstacle_m
    free_pct = int(scene.free_space_pct * 100)

    # Choose closest_m display — if not from depth, use min obstacle dist heuristic
    if closest_m is None:
        # Estimate from detections that have distance_m
        dists = [d["distance_m"] for d in sensor_pkg["hailo_detections"] if "distance_m" in d]
        closest_m = min(dists) if dists else random.uniform(0.8, 3.5)

    closest_display = f"{closest_m:.1f}m"

    # ---- Reactive layer (Layer 0) ----
    estop = closest_m < 0.4
    if estop:
        reactive_msg = "⚠️  E-STOP: obstacle < 0.4 m"
        action_type = "stop"
        linear = 0.0
        angular = 0.0
        direction = "stop"
    else:
        reactive_msg = random.choice(_REACTIVE_CLEAR)

        # ---- Fast brain (Layer 1) ----
        thought_tmpl, action_type, base_speed = random.choice(_FAST_THOUGHTS)
        # Scale speed by free space
        speed = round(base_speed * (free_pct / 100) + 0.1, 2)
        speed = min(speed, 0.6)
        fast_msg = thought_tmpl.format(speed=speed)
        linear = speed
        angular = round(random.uniform(-0.15, 0.15), 2)

        # ---- Navigator ----
        nav_action = await navigator.act({})
        direction = nav_action.get("direction", "forward")

    # ---- Planner (Layer 2) — fires every 5 ticks ----
    planner_msg = None
    if tick_n % 5 == 0:
        planner_msg = random.choice(_PLANNER_THOUGHTS)

    # ---- Print tick output ----
    _rule(f"Tick {tick_n}/{steps}")

    if _RICH and _console:
        _console.print(
            f"  [cyan]📷[/]  Camera frame captured                [dim][{w}×{h}, {frame_kb}KB][/]"
        )
        _console.print("  [yellow]👁️[/]  [bold]ObserverAgent:[/]")
        _console.print(f"       detected: [italic]{det_str}[/]")
        _console.print(
            f"       free_space: [green]{free_pct}%[/]   "
            f"closest_obstacle: [{'red' if estop else 'green'}]{closest_display}[/]"
        )
        if estop:
            _console.print(f"  [red]🧠  Layer 0 (reactive):[/]               {reactive_msg}")
        else:
            _console.print(
                f"  [blue]🧠[/]  Layer 0 (reactive):               [green]{reactive_msg}[/]"
            )
            _console.print(f"  [blue]🧠[/]  Layer 1 (fast):                   → {fast_msg}")
        if planner_msg:
            _console.print(
                f"  [blue]🧠[/]  Layer 2 (planner, tick {tick_n}/{steps}):     → {planner_msg}"
            )
        if not estop:
            nav_plan = navigator._state.get("nav_plan")
            if nav_plan and nav_plan.waypoints:
                wp = nav_plan.waypoints[0]
                _console.print("  [magenta]🦾[/]  [bold]NavigatorAgent:[/]")
                _console.print(f"       direction: [bold]{direction}[/]   speed: {linear:.2f}")
                _console.print(f"       plan: [wp1: {direction} {linear:.1f}, wp2: {wp.reason}]")
            _console.print(
                f"  [bold yellow]⚡[/]  Action: [bold]move[/]  "
                f"linear=[cyan]{linear:.2f}[/]  angular=[cyan]{angular:.2f}[/]"
            )
            _console.print("     Safety: [green]✅ within bounds[/]")
        else:
            _console.print("  [bold red]⚡  Action: STOP  linear=0.00  angular=0.00[/]")
            _console.print("     Safety: [red]⚠️  e-stop engaged[/]")
    else:
        print(f"  📷  Camera frame captured                [{w}×{h}, {frame_kb}KB]")
        print("  👁️  ObserverAgent:")
        print(f"       detected: {det_str}")
        print(f"       free_space: {free_pct}%   closest_obstacle: {closest_display}")
        if estop:
            print(f"  🧠  Layer 0 (reactive):               {reactive_msg}")
        else:
            print(f"  🧠  Layer 0 (reactive):               {reactive_msg}")
            print(f"  🧠  Layer 1 (fast):                   → {fast_msg}")
        if planner_msg:
            print(f"  🧠  Layer 2 (planner, tick {tick_n}/{steps}):     → {planner_msg}")
        if not estop:
            print("  🦾  NavigatorAgent:")
            print(f"       direction: {direction}   speed: {linear:.2f}")
            print(f"  ⚡  Action: move  linear={linear:.2f}  angular={angular:.2f}")
            print("     Safety: ✅ within bounds")
        else:
            print("  ⚡  Action: STOP  linear=0.00  angular=0.00")
            print("     Safety: ⚠️  e-stop engaged")

    _print()

    # Pace to delay
    elapsed = time.monotonic() - tick_start
    remaining = delay - elapsed
    if remaining > 0:
        await asyncio.sleep(remaining)

    return {
        "estop": estop,
        "action_type": action_type if not estop else "stop",
        "closest_m": closest_m,
    }


async def _act2_perception_loop(
    steps: int,
    delay: float,
    observer: Any,
    navigator: Any,
) -> dict[str, Any]:
    """Run Act 2. Returns summary counts."""
    _print()
    if _RICH and _console:
        _console.print("[bold]Act 2 — Perception Loop[/]  [dim](live sensor simulation)[/]")
    else:
        print("Act 2 — Perception Loop  (live sensor simulation)")

    move_count = 0
    stop_count = 0
    obstacles_avoided = 0

    for tick_n in range(1, steps + 1):
        result = await _tick(tick_n, steps, observer, navigator, delay)
        if result["action_type"] == "stop":
            stop_count += 1
            if result["estop"]:
                obstacles_avoided += 1
        else:
            move_count += 1

    return {
        "move_count": move_count,
        "stop_count": stop_count,
        "obstacles_avoided": obstacles_avoided,
    }


# ---------------------------------------------------------------------------
# ACT 3: Task Dispatch
# ---------------------------------------------------------------------------


async def _act3_task_dispatch(task_planner: Any) -> dict[str, Any]:
    """Dispatch a grasp task and return result summary."""
    from castor.specialists.base_specialist import Task, TaskStatus

    _print()
    if _RICH and _console:
        _console.print("[bold]Act 3 — Task Dispatch[/]")
        _console.print()
        _console.print("  [cyan]📋[/] [bold]Task Dispatch Demo[/]")
    else:
        print("Act 3 — Task Dispatch")
        print()
        print("  📋 Task Dispatch Demo")

    task = Task(
        type="grasp",
        goal="grasp the red block",
        priority=4,
        params={"object_position": [0.3, 0.5, 0.1]},
    )

    specialist = task_planner.best_specialist(task)
    spec_name = specialist.name if specialist else "none"
    spec_display = spec_name.capitalize()
    est_s = specialist.estimate_duration_s(task) if specialist else 0.0
    can_handle = specialist is not None

    if _RICH and _console:
        _console.print("    Submitting: [italic]grasp the red block[/] (priority=4)")
        _console.print(
            f"    → [bold]{spec_display}Specialist[/] selected "
            f"(can_handle={can_handle}, est={est_s:.1f}s)"
        )
        _console.print("    → Executing...")
    else:
        print("    Submitting: grasp the red block (priority=4)")
        print(
            f"    → {spec_display}Specialist selected (can_handle={can_handle}, est={est_s:.1f}s)"
        )
        print("    → Executing...")

    task_planner.submit(task)
    result = await task_planner.run_next()

    success = result is not None and result.status == TaskStatus.SUCCESS
    joint_angles = None
    if success and result.output:
        joint_angles = result.output.get("joint_angles")

    if success:
        ja_str = "[" + ", ".join(f"{a:.1f}" for a in joint_angles) + "]" if joint_angles else "N/A"
        if _RICH and _console:
            _console.print(f"    [green]✅ TaskResult: SUCCESS[/]  joint_angles={ja_str}")
        else:
            print(f"    ✅ TaskResult: SUCCESS  joint_angles={ja_str}")
    else:
        err = result.error if result else "unknown error"
        if _RICH and _console:
            _console.print(f"    [red]❌ TaskResult: FAILED[/]  error={err}")
        else:
            print(f"    ❌ TaskResult: FAILED  error={err}")

    _print()
    return {"task_success": success, "specialist": spec_name}


# ---------------------------------------------------------------------------
# ACT 4: Sisyphus / Self-Improving Loop (all mock — no API calls)
# ---------------------------------------------------------------------------


def _act4_sisyphus(tick_count: int, obstacles_avoided: int) -> dict[str, Any]:
    """Mock self-improving loop analysis."""
    _print()
    if _RICH and _console:
        _console.print("[bold]Act 4 — Self-Improving Loop[/]")
        _console.print()
        _console.print("  [cyan]🔄[/] [bold]Sisyphus Loop (Self-Improving)[/]")
    else:
        print("Act 4 — Self-Improving Loop")
        print()
        print("  🔄 Sisyphus Loop (Self-Improving)")

    _sleep(0.3)

    if _RICH and _console:
        _console.print(f"    Analyzing {tick_count} episodes...")
        _sleep(0.4)
        _console.print("    → PM: identified 2 suboptimalities")
        _console.print(
            f"      [dim]•[/] obstacle avoidance triggered {obstacles_avoided}x "
            "— min_obstacle_m may be too conservative"
        )
        _console.print("      [dim]•[/] path efficiency: 18% below optimal")
        _sleep(0.3)
        _console.print("    → Dev: generated 1 config patch")
        _console.print("      [dim]•[/] min_obstacle_m: 0.40 → 0.30")
        _sleep(0.3)
        _console.print("    → QA: bounds check [green]✅[/]  regression [green]✅[/]")
        _sleep(0.2)
        _console.print(
            "    → Applied: patch [bold]#demo-001[/]  "
            "[dim][rollback: castor improve --rollback demo-001][/]"
        )
    else:
        print(f"    Analyzing {tick_count} episodes...")
        _sleep(0.4)
        print("    → PM: identified 2 suboptimalities")
        print(
            f"      • obstacle avoidance triggered {obstacles_avoided}x"
            " — min_obstacle_m may be too conservative"
        )
        print("      • path efficiency: 18% below optimal")
        _sleep(0.3)
        print("    → Dev: generated 1 config patch")
        print("      • min_obstacle_m: 0.40 → 0.30")
        _sleep(0.3)
        print("    → QA: bounds check ✅  regression ✅")
        _sleep(0.2)
        print("    → Applied: patch #demo-001  [rollback: castor improve --rollback demo-001]")

    _print()
    return {"patches_applied": 1}


# ---------------------------------------------------------------------------
# ACT 5: Summary
# ---------------------------------------------------------------------------


def _act5_summary(
    elapsed_s: float,
    tick_count: int,
    move_count: int,
    stop_count: int,
    obstacles_avoided: int,
    tasks_ok: int,
    tasks_total: int,
    specialist_name: str,
    patches: int,
) -> None:
    _print()
    if _RICH and _console:
        _console.print(f"[dim]{_SEP}[/]")
        _console.print(f"[bold green]✨ Demo complete in {elapsed_s:.1f}s[/]")
        _console.print()
        _console.print(
            f"  Ticks: [bold]{tick_count}[/]    "
            f"Actions: [bold]{move_count} move, {stop_count} stop[/]"
        )
        _console.print(f"  Obstacles avoided: [bold]{obstacles_avoided}[/]")
        _console.print(
            f"  Task completed: [bold]{tasks_ok}/{tasks_total}[/] "
            f"([italic]{specialist_name}Specialist[/])"
        )
        _console.print(f"  Improvement patches: [bold]{patches} applied[/]")
        _console.print()
        _console.print("Start with real hardware:")
        _console.print("  [cyan]castor wizard[/]                     # configure your robot")
        _console.print("  [cyan]castor run --config bot.rcan.yaml --dashboard[/]")
        _console.print()
        _console.print(
            "Community: [link=https://opencastor.com/hub]https://opencastor.com/hub[/link]"
        )
        _console.print(f"[dim]{_SEP}[/]")
    else:
        print(_SEP)
        print(f"✨ Demo complete in {elapsed_s:.1f}s")
        print()
        print(f"  Ticks: {tick_count}    Actions: {move_count} move, {stop_count} stop")
        print(f"  Obstacles avoided: {obstacles_avoided}")
        print(f"  Task completed: {tasks_ok}/{tasks_total} ({specialist_name}Specialist)")
        print(f"  Improvement patches: {patches} applied")
        print()
        print("Start with real hardware:")
        print("  castor wizard                     # configure your robot")
        print("  castor run --config bot.rcan.yaml --dashboard")
        print()
        print("Community: https://opencastor.com/hub")
        print(_SEP)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_demo(
    steps: int = 10,
    delay: float = 0.8,
    layout: str = "full",
    no_color: bool = False,
) -> dict[str, Any]:
    """Run the OpenCastor full-stack demo.

    Args:
        steps: Number of perception-loop ticks (Act 2).
        delay: Seconds between each tick (0 = as fast as possible).
        layout: ``"full"`` (all 5 acts) or ``"minimal"`` (skips Acts 3 & 4).
        no_color: Disable rich colour output even if rich is installed.

    Returns:
        Summary dict with keys:
            tick_count, move_count, stop_count, obstacles_avoided,
            tasks_completed, patches_applied, elapsed_s.
    """
    # Honour env variable in addition to arg
    global _NO_COLOR  # noqa: PLW0603
    if no_color:
        _NO_COLOR = True

    demo_start = time.monotonic()

    # ---- ACT 1 ----
    stack = _act1_init(no_color=no_color)
    observer = stack["observer"]
    navigator = stack["navigator"]
    task_planner = stack["task_planner"]

    # ---- ACT 2 ----
    loop2 = asyncio.run(
        _act2_perception_loop(
            steps=steps,
            delay=delay,
            observer=observer,
            navigator=navigator,
        )
    )
    move_count = loop2["move_count"]
    stop_count = loop2["stop_count"]
    obstacles_avoided = loop2["obstacles_avoided"]

    tasks_ok = 0
    specialist_name = "Manipulator"
    patches = 0

    if layout == "full":
        # ---- ACT 3 ----
        act3 = asyncio.run(_act3_task_dispatch(task_planner))
        tasks_ok = 1 if act3["task_success"] else 0
        specialist_name = act3["specialist"].capitalize()

        # ---- ACT 4 ----
        act4 = _act4_sisyphus(tick_count=steps, obstacles_avoided=obstacles_avoided)
        patches = act4["patches_applied"]

    elapsed_s = time.monotonic() - demo_start

    # ---- ACT 5 ----
    _act5_summary(
        elapsed_s=elapsed_s,
        tick_count=steps,
        move_count=move_count,
        stop_count=stop_count,
        obstacles_avoided=obstacles_avoided,
        tasks_ok=tasks_ok,
        tasks_total=1 if layout == "full" else 0,
        specialist_name=specialist_name,
        patches=patches,
    )

    return {
        "tick_count": steps,
        "move_count": move_count,
        "stop_count": stop_count,
        "obstacles_avoided": obstacles_avoided,
        "tasks_completed": tasks_ok,
        "patches_applied": patches,
        "elapsed_s": round(elapsed_s, 2),
    }
