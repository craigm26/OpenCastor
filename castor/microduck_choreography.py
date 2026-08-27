"""Turning "go and get it" into something a Microduck can actually do.

THE DUCK'S VOCABULARY IS ATOMIC AND THE INTERESTING BEHAVIOUR IS NOT. Pollen
ships walking, standing, two kicks, a ground pick, a sit toggle, a forward
roll and seven sounds. Each is a one-shot that holds the robot while it runs —
``robot.do`` takes exactly one skill, and a refusal names the move already in
possession. There is no sequencing, no branching on what the duck can see, and
no way to say "walk to the ball, line up, knock it toward the couch, then
celebrate". Every one of those verbs exists. The sentence does not.

That sentence is what OpenCastor is for. This module is the layer between an
LLM's intent and robotd's primitives:

  * **A vocabulary the model can plan against.** Every move carries how long it
    occupies the duck and whether it holds the robot exclusively — a planner
    that does not know a kick takes half a second and blocks cannot sequence
    around one.
  * **Compound routines built from primitives.** ``fetch``, ``greet``,
    ``celebrate``, ``nudge`` — the duck cannot do any of them, and can be made
    to do all of them.
  * **A performer that respects the robot.** Steps are timed against the real
    durations, aborted on a fall or a flat battery, and each one is recorded.

WHAT THIS DELIBERATELY IS NOT. It computes no permission. Every motion still
goes out through :class:`~castor.drivers.microduck_driver.MicroduckDriver`,
which routes through the SafetyLayer, and the duck's own limits apply on top
and are reported back in ``robot.state.limited_by``. A plan is a proposal;
robotd remains the only thing that decides what a servo does.

Usage::

    from castor.microduck_choreography import DuckChoreographer

    duck = DuckChoreographer(driver)
    print(duck.vocabulary())          # the prompt an LLM plans against
    result = duck.perform([
        {"move": "approach", "metres": 0.4},
        {"move": "nudge", "left": False},
        {"move": "celebrate"},
    ])
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("OpenCastor.MicroduckChoreography")

__all__ = [
    "Move",
    "PRIMITIVES",
    "ROUTINES",
    "ChoreographyError",
    "StepResult",
    "Performance",
    "DuckChoreographer",
    "register_duck_tools",
]


class ChoreographyError(ValueError):
    """A plan that cannot be performed, refused before anything moves."""


@dataclass(frozen=True)
class Move:
    """One verb in the duck's vocabulary.

    Args:
        name: How a plan names it.
        description: What an LLM is told it does. Written for a planner, not
            for a manual — it says what the move is *for*.
        duration_s: How long the duck is busy. Nominal for skills (robotd's
            own numbers), computed for the timed ones.
        exclusive: True when the move holds the robot and nothing may overlap
            it — the scripted skills do, continuous intents do not.
        params: ``{name: (type, default, description)}``.
    """

    name: str
    description: str
    duration_s: float = 0.0
    exclusive: bool = False
    params: dict[str, tuple[type, Any, str]] = field(default_factory=dict)


#: The primitives — each one maps to a single driver call.
PRIMITIVES: dict[str, Move] = {
    "walk": Move(
        "walk",
        "Walk forward or backward at a fraction of top speed for a number of seconds.",
        params={
            "speed": (float, 0.6, "-1..1; negative walks backward"),
            "seconds": (float, 1.0, "how long to keep walking"),
        },
    ),
    "turn": Move(
        "turn",
        "Turn in place. Positive is left.",
        params={
            "rate": (float, 0.6, "-1..1 turn rate"),
            "seconds": (float, 1.0, "how long to keep turning"),
        },
    ),
    "strafe": Move(
        "strafe",
        "Step sideways without turning. Positive is left.",
        params={"speed": (float, 0.6, "-1..1"), "seconds": (float, 1.0, "duration")},
    ),
    "stop": Move("stop", "Stop moving and stand still. Standing, not limp."),
    "wait": Move(
        "wait",
        "Hold still for a moment — useful between moves, or to let something settle.",
        params={"seconds": (float, 1.0, "how long to wait")},
    ),
    "look_at": Move(
        "look_at",
        "Aim the head at a point in front of the duck, in metres from its trunk. "
        "The robot does the inverse kinematics.",
        params={
            "x": (float, 0.3, "forward"),
            "y": (float, 0.0, "left"),
            "z": (float, 0.0, "up; the floor is about -0.12"),
        },
    ),
    "mouth": Move(
        "mouth",
        "Open or close the beak. 0 is shut, 1 is wide.",
        params={"open": (float, 1.0, "0..1")},
    ),
    "pose": Move(
        "pose",
        "Lean the standing body — crouch, roll or pitch. Small offsets only.",
        params={
            "z": (float, 0.0, "height offset in metres; negative crouches"),
            "roll": (float, 0.0, "radians"),
            "pitch": (float, 0.0, "radians"),
        },
    ),
    "kick": Move(
        "kick",
        "Kick with one leg. Fast and blind — the duck does not look for the ball, "
        "so something has to be in front of the right foot already.",
        duration_s=0.5,
        exclusive=True,
        params={"left": (bool, False, "kick with the left leg instead of the right")},
    ),
    "pick": Move(
        "pick",
        "Reach down with the beak and pick up whatever is directly in front.",
        duration_s=3.0,
        exclusive=True,
    ),
    "sit_toggle": Move(
        "sit_toggle",
        "Sit down if standing, stand up if sitting.",
        duration_s=2.0,
        exclusive=True,
    ),
    "roll": Move(
        "roll",
        "A forward roll. Needs clear space ahead.",
        duration_s=1.0,
        exclusive=True,
    ),
    "say": Move(
        "say",
        "Make a sound. The duck has seven: alarm (sharp honk), greet (hello), "
        "inquire (a rising question), peck (a low goodbye), chirp (its quack), "
        "coo (drowsy and content), wheee (a held joy ride).",
        params={"tag": (str, "chirp", "one of alarm/greet/inquire/peck/chirp/coo/wheee")},
    ),
}

#: Compound routines — the sentences the duck cannot say by itself. Each is a
#: list of steps in the same shape a plan uses, so a routine is exactly a plan
#: someone wrote down, and nothing can appear here that a user could not write.
ROUTINES: dict[str, tuple[str, dict[str, tuple[type, Any, str]], Callable[..., list[dict]]]] = {
    "approach": (
        "Walk forward a distance in metres, at a careful pace.",
        {"metres": (float, 0.3, "how far to walk forward")},
        lambda metres=0.3: [
            {"move": "walk", "speed": 0.6, "seconds": max(0.1, abs(metres) / 0.12)},
            {"move": "stop"},
        ],
    ),
    "back_off": (
        "Walk backward a distance in metres.",
        {"metres": (float, 0.3, "how far to back away")},
        lambda metres=0.3: [
            {"move": "walk", "speed": -0.6, "seconds": max(0.1, abs(metres) / 0.12)},
            {"move": "stop"},
        ],
    ),
    "turn_by": (
        "Turn in place by an angle in degrees. Positive is left.",
        {"degrees": (float, 90.0, "how far to turn; positive is left")},
        lambda degrees=90.0: [
            {
                "move": "turn",
                "rate": 0.6 if degrees >= 0 else -0.6,
                "seconds": max(0.1, abs(degrees) * 0.0175 / 0.6),
            },
            {"move": "stop"},
        ],
    ),
    "scan": (
        "Look left, then right, then back to centre — a slow sweep of the head, "
        "for taking in a room without moving the feet.",
        {},
        lambda: [
            {"move": "look_at", "x": 0.3, "y": 0.25, "z": 0.0},
            {"move": "wait", "seconds": 0.8},
            {"move": "look_at", "x": 0.3, "y": -0.25, "z": 0.0},
            {"move": "wait", "seconds": 0.8},
            {"move": "look_at", "x": 0.4, "y": 0.0, "z": 0.0},
        ],
    ),
    "nod": (
        "Nod yes.",
        {},
        lambda: [
            {"move": "look_at", "x": 0.3, "y": 0.0, "z": -0.15},
            {"move": "wait", "seconds": 0.35},
            {"move": "look_at", "x": 0.3, "y": 0.0, "z": 0.1},
            {"move": "wait", "seconds": 0.35},
            {"move": "look_at", "x": 0.3, "y": 0.0, "z": -0.15},
            {"move": "wait", "seconds": 0.35},
            {"move": "look_at", "x": 0.35, "y": 0.0, "z": 0.0},
        ],
    ),
    "shake": (
        "Shake its head no.",
        {},
        lambda: [
            {"move": "look_at", "x": 0.3, "y": 0.2, "z": 0.0},
            {"move": "wait", "seconds": 0.3},
            {"move": "look_at", "x": 0.3, "y": -0.2, "z": 0.0},
            {"move": "wait", "seconds": 0.3},
            {"move": "look_at", "x": 0.3, "y": 0.2, "z": 0.0},
            {"move": "wait", "seconds": 0.3},
            {"move": "look_at", "x": 0.35, "y": 0.0, "z": 0.0},
        ],
    ),
    "greet": (
        "Greet someone: look up at them, say hello, and nod.",
        {},
        lambda: [
            {"move": "look_at", "x": 0.4, "y": 0.0, "z": 0.35},
            {"move": "say", "tag": "greet"},
            {"move": "routine", "routine": "nod"},
        ],
    ),
    "celebrate": (
        "Celebrate — a quack, a roll and a whoop. Needs clear space ahead.",
        {},
        lambda: [
            {"move": "say", "tag": "chirp"},
            {"move": "roll"},
            {"move": "say", "tag": "wheee"},
            {"move": "look_at", "x": 0.3, "y": 0.0, "z": 0.3},
        ],
    ),
    "nudge": (
        "Line up on something on the floor and knock it forward with a kick.",
        {"left": (bool, False, "use the left leg")},
        lambda left=False: [
            {"move": "look_at", "x": 0.2, "y": 0.0, "z": -0.12},
            {"move": "wait", "seconds": 0.4},
            {"move": "kick", "left": left},
        ],
    ),
    "fetch": (
        "Go to something, pick it up in the beak, and bring it back.",
        {"metres": (float, 0.4, "how far away it is")},
        lambda metres=0.4: [
            {"move": "look_at", "x": 0.3, "y": 0.0, "z": -0.1},
            {"move": "routine", "routine": "approach", "metres": metres},
            {"move": "pick"},
            {"move": "routine", "routine": "turn_by", "degrees": 180.0},
            {"move": "routine", "routine": "approach", "metres": metres},
            {"move": "mouth", "open": 1.0},
            {"move": "say", "tag": "chirp"},
        ],
    ),
    "patrol": (
        "Walk a square, looking around at each corner.",
        {"side_metres": (float, 0.5, "length of each side")},
        lambda side_metres=0.5: [
            step
            for _ in range(4)
            for step in (
                {"move": "routine", "routine": "approach", "metres": side_metres},
                {"move": "routine", "routine": "scan"},
                {"move": "routine", "routine": "turn_by", "degrees": 90.0},
            )
        ],
    ),
    "dance": (
        "A little dance — lean, sway, quack.",
        {},
        lambda: [
            {"move": "pose", "z": -0.02, "roll": 0.0, "pitch": 0.0},
            {"move": "say", "tag": "chirp"},
            {"move": "pose", "z": -0.01, "roll": 0.2, "pitch": 0.0},
            {"move": "wait", "seconds": 0.5},
            {"move": "pose", "z": -0.01, "roll": -0.2, "pitch": 0.0},
            {"move": "wait", "seconds": 0.5},
            {"move": "pose", "z": 0.0, "roll": 0.0, "pitch": 0.0},
            {"move": "say", "tag": "wheee"},
        ],
    ),
    "settle": (
        "Wind down for the night: sit, say goodnight, and go quiet.",
        {},
        lambda: [
            {"move": "look_at", "x": 0.3, "y": 0.0, "z": -0.05},
            {"move": "sit_toggle"},
            {"move": "say", "tag": "peck"},
            {"move": "stop"},
        ],
    ),
}


@dataclass
class StepResult:
    """What one step did."""

    step: dict
    ok: bool
    detail: str = ""
    elapsed_s: float = 0.0


@dataclass
class Performance:
    """The whole run — what happened, in order, and why it stopped."""

    steps: list[StepResult] = field(default_factory=list)
    completed: bool = False
    aborted_because: Optional[str] = None

    @property
    def summary(self) -> str:
        done = sum(1 for s in self.steps if s.ok)
        if self.completed:
            return f"performed {done} steps"
        return f"stopped after {done} steps: {self.aborted_because or 'unknown'}"


class DuckChoreographer:
    """Perform a plan on a Microduck, and stop if the duck needs it to stop.

    Args:
        driver: A connected ``MicroduckDriver`` (mock mode works — every step
            logs and returns, which is what makes a plan testable dry).
        min_battery_percent: Below this the performance stops between steps.
            The duck reads its own pack through the servo bus, so the number
            sags under load and recovers at rest — this is a floor to stop at,
            not a gauge to trust precisely.
        sleep: Injected for tests.
        clock: Injected for tests.
    """

    def __init__(
        self,
        driver: Any,
        *,
        min_battery_percent: float = 12.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._driver = driver
        self._min_battery = float(min_battery_percent)
        self._sleep = sleep
        self._clock = clock

    # ------------------------------------------------------------------
    # The vocabulary an LLM plans against
    # ------------------------------------------------------------------

    def vocabulary(self) -> str:
        """The duck's whole vocabulary as prompt text.

        Written for a planner: every move says what it is *for*, how long it
        takes, and whether it holds the robot — because a plan that overlaps
        two exclusive moves is a plan the duck will refuse.
        """
        lines = [
            "You are choreographing a Microduck: a 25 cm two-legged robot duck.",
            "It walks, turns, kicks, picks things up with its beak, rolls forward,",
            "sits, aims its head, leans, and has seven sounds.",
            "",
            "Answer with a plan: a list of steps, each naming one move.",
            "",
            "MOVES:",
        ]
        for move in PRIMITIVES.values():
            args = ", ".join(f"{n}={d!r}" for n, (_t, d, _desc) in move.params.items())
            timing = ""
            if move.duration_s:
                timing = f" [takes {move.duration_s:g}s"
                timing += ", holds the robot]" if move.exclusive else "]"
            lines.append(f"  {move.name}({args}){timing} — {move.description}")
        lines += ["", "ROUTINES (several moves at once):"]
        for name, (desc, params, _build) in ROUTINES.items():
            args = ", ".join(f"{n}={d!r}" for n, (_t, d, _desc) in params.items())
            lines.append(f"  {name}({args}) — {desc}")
        lines += [
            "",
            "Rules the duck actually enforces:",
            "  - A kick is blind: something must already be in front of the foot.",
            "  - Rolling and picking need clear space; they hold the robot while they run.",
            "  - Sounds and head aiming are free — they never interrupt walking.",
            "  - Prefer routines over long strings of primitives.",
        ]
        return "\n".join(lines)

    def tool_schema(self) -> dict:
        """The move vocabulary as a JSON-schema-ish dict, for tool calling."""
        return {
            "primitives": {
                m.name: {
                    "description": m.description,
                    "duration_s": m.duration_s,
                    "exclusive": m.exclusive,
                    "params": {
                        n: {"type": t.__name__, "default": d, "description": desc}
                        for n, (t, d, desc) in m.params.items()
                    },
                }
                for m in PRIMITIVES.values()
            },
            "routines": {
                name: {
                    "description": desc,
                    "params": {
                        n: {"type": t.__name__, "default": d, "description": pdesc}
                        for n, (t, d, pdesc) in params.items()
                    },
                }
                for name, (desc, params, _b) in ROUTINES.items()
            },
        }

    # ------------------------------------------------------------------
    # Validation — refuse before anything moves
    # ------------------------------------------------------------------

    def expand(self, plan: list[dict], _depth: int = 0) -> list[dict]:
        """Expand routines into primitives and check every parameter.

        Raises:
            ChoreographyError: on an unknown move, a bad parameter type, or a
                routine that nests too deep. A plan is refused whole — a duck
                halfway through a rejected plan is worse than one that never
                started.
        """
        if _depth > 4:
            raise ChoreographyError("routines nested too deeply")
        if not isinstance(plan, list):
            raise ChoreographyError("a plan is a list of steps")

        out: list[dict] = []
        for index, step in enumerate(plan):
            if not isinstance(step, dict):
                raise ChoreographyError(f"step {index} is not an object")
            name = step.get("move") or step.get("routine")
            if not name:
                raise ChoreographyError(f"step {index} names no move")

            if name == "routine":
                name = step.get("routine")
                if not name:
                    raise ChoreographyError(f"step {index} is a routine with no name")

            if name in ROUTINES:
                _desc, params, build = ROUTINES[name]
                kwargs = {}
                for key, (kind, default, _d) in params.items():
                    value = step.get(key, default)
                    kwargs[key] = self._coerce(value, kind, f"{name}.{key}")
                out.extend(self.expand(build(**kwargs), _depth + 1))
                continue

            move = PRIMITIVES.get(name)
            if move is None:
                known = ", ".join(sorted({*PRIMITIVES, *ROUTINES}))
                raise ChoreographyError(f"step {index}: unknown move {name!r}. Known: {known}")

            resolved = {"move": move.name}
            for key, (kind, default, _d) in move.params.items():
                resolved[key] = self._coerce(step.get(key, default), kind, f"{move.name}.{key}")
            out.append(resolved)
        return out

    @staticmethod
    def _coerce(value: Any, kind: type, where: str) -> Any:
        if kind is bool:
            if isinstance(value, bool):
                return value
            raise ChoreographyError(f"{where} must be true or false, got {value!r}")
        try:
            return kind(value)
        except (TypeError, ValueError) as exc:
            raise ChoreographyError(f"{where} must be a {kind.__name__}: {exc}") from None

    # ------------------------------------------------------------------
    # Performing
    # ------------------------------------------------------------------

    def perform(self, plan: list[dict], *, dry_run: bool = False) -> Performance:
        """Expand, then run the plan, stopping if the duck needs it to stop.

        Args:
            plan: Steps, primitives or routines.
            dry_run: Expand and time the plan without moving anything.
        """
        steps = self.expand(plan)
        result = Performance()

        if dry_run:
            for step in steps:
                result.steps.append(StepResult(step=step, ok=True, detail="dry run"))
            result.completed = True
            return result

        for step in steps:
            reason = self._abort_reason()
            if reason:
                result.aborted_because = reason
                self._safe_stop()
                return result

            started = self._clock()
            try:
                detail = self._perform_step(step)
                ok = True
            except Exception as exc:  # noqa: BLE001 — a step failing ends the plan, not the process
                detail = str(exc)
                ok = False
            elapsed = self._clock() - started
            result.steps.append(StepResult(step=step, ok=ok, detail=detail, elapsed_s=elapsed))
            if not ok:
                result.aborted_because = f"step {step['move']} failed: {detail}"
                self._safe_stop()
                return result

        result.completed = True
        return result

    def _perform_step(self, step: dict) -> str:
        name = step["move"]
        driver = self._driver

        if name == "walk":
            return self._hold(lambda: driver.move(step["speed"], 0.0), step["seconds"])
        if name == "turn":
            return self._hold(lambda: driver.move(0.0, step["rate"]), step["seconds"])
        if name == "strafe":
            return self._hold(lambda: driver.strafe(step["speed"]), step["seconds"])
        if name == "stop":
            driver.stop()
            return "standing"
        if name == "wait":
            self._sleep(step["seconds"])
            return f"waited {step['seconds']:g}s"
        if name == "look_at":
            driver.look_at(step["x"], step["y"], step["z"])
            return "looking"
        if name == "mouth":
            driver.mouth(step["open"])
            return f"beak {step['open']:g}"
        if name == "pose":
            driver.pose(z=step["z"], roll=step["roll"], pitch=step["pitch"])
            return "leaning"
        if name == "say":
            driver.sound(step["tag"])
            return step["tag"]
        if name == "kick":
            driver.kick(left=step["left"])
            self._sleep(PRIMITIVES["kick"].duration_s)
            return "kicked"
        if name == "pick":
            driver.ground_pick()
            self._sleep(PRIMITIVES["pick"].duration_s)
            return "picked"
        if name == "sit_toggle":
            driver.sit_toggle()
            self._sleep(PRIMITIVES["sit_toggle"].duration_s)
            return "sat or stood"
        if name == "roll":
            driver.roulade()
            self._sleep(PRIMITIVES["roll"].duration_s)
            return "rolled"
        raise ChoreographyError(f"no way to perform {name!r}")

    def _hold(self, send: Callable[[], None], seconds: float) -> str:
        """Send a continuous intent and hold it for *seconds*.

        Re-sending is the driver's job — its intent loop feeds robotd's deadman
        — so this only has to keep the command alive and then release it.
        """
        send()
        self._sleep(max(0.0, seconds))
        self._driver.stop()
        return f"held {seconds:g}s"

    def _abort_reason(self) -> Optional[str]:
        """Whether the duck needs the performance to stop, right now."""
        try:
            state = self._driver.get_state()
        except Exception:  # noqa: BLE001 — no state is not a reason to stop
            return None
        if not isinstance(state, dict) or not state:
            return None

        safety = state.get("safety") or {}
        if safety.get("fallen"):
            return "the duck fell over"
        if safety.get("limp"):
            return "the duck went limp"

        battery = state.get("battery") or {}
        percent = battery.get("percent")
        if isinstance(percent, (int, float)) and percent < self._min_battery:
            return f"battery down to {percent:g}%"
        return None

    def _safe_stop(self) -> None:
        try:
            self._driver.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not stop the duck after an abort: %s", exc)


def register_duck_tools(registry: Any, driver: Any) -> DuckChoreographer:
    """Expose the duck's vocabulary to the LLM as callable tools.

    Registers ``duck_vocabulary`` (what can it do?) and ``duck_perform``
    (do this) so a brain can discover the verbs and then use them — which is
    the whole point: the model composes, the duck executes, the SafetyLayer
    and robotd both still get their say.

    Returns:
        The choreographer, so a caller can perform plans directly too.
    """
    duck = DuckChoreographer(driver)

    registry.register(
        name="duck_vocabulary",
        fn=lambda: duck.tool_schema(),
        description=(
            "List every move and routine the duck can perform, with parameters, "
            "durations, and which ones hold the robot while they run."
        ),
        returns="object",
    )
    registry.register(
        name="duck_perform",
        fn=lambda plan=None: _perform_for_tool(duck, plan),
        description=(
            "Perform a sequence on the duck. 'plan' is a list of steps, each an object "
            "naming one move and its parameters, e.g. "
            '[{"move": "approach", "metres": 0.4}, {"move": "nudge"}, {"move": "celebrate"}]. '
            "Routines expand into primitives. The run stops early if the duck falls or "
            "the battery gets low, and reports why."
        ),
        parameters={
            "plan": {"type": "array", "description": "the steps to perform", "required": True}
        },
        returns="object",
    )
    return duck


def _perform_for_tool(duck: DuckChoreographer, plan: Any) -> dict:
    """Tool-facing wrapper: a refused plan is an answer, not an exception."""
    try:
        performance = duck.perform(plan or [])
    except ChoreographyError as exc:
        return {"ok": False, "refused": str(exc)}
    return {
        "ok": performance.completed,
        "summary": performance.summary,
        "aborted_because": performance.aborted_because,
        "steps": [
            {"move": s.step["move"], "ok": s.ok, "detail": s.detail} for s in performance.steps
        ],
    }
