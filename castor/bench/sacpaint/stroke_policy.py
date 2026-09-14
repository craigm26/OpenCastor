"""``agent_strokes``: the agent policy, with the body's stroke tool on its toolset.

The stroke primitive lives on the body
(:mod:`castor.bench.sacpaint.strokes`), but a body cannot put a tool in front
of a model: the model-facing tool list is built by the agent plugin from the
embodiment's action box, and that plugin has no seam for an
embodiment-contributed tool. So this module adds one, in the only place a
policy may: it subclasses the agent policy, and at ``bind()`` wraps the toolset
the plugin built.

``agent_strokes`` is ``agent``. Same wire, same client, same base URL, key
environment variable, model, image policy, call budget and subscription shim,
because it is the same class with the same constructor. The one difference is
its toolset: ``stroke`` sits beside ``move_to``, ``done``, ``give_up`` and
``take_pic``, and the per-target tool keeps working exactly as it did.

**A stroke reaches the arm as the targets the body already sends.** A ``stroke``
call is planned by :func:`castor.bench.sacpaint.strokes.plan` into the same
target list the body's own ``stroke()`` plans (travel over the first point,
down, each point in turn, up after the last), and then each of those targets is
handed to the plugin's *own* move tool, one at a time, with the previous target
as its starting pose. So every millimetre of interpolation, every bound check,
every speed limit and any configured motion pre-check is the plugin's, not
ours, and one gateway call per target with three millimetre coordinates is
exactly what leaves the robot. What batches is the policy's turn and the
photograph: only the last action of the whole stroke carries
:data:`castor.bench.sacpaint.strokes.FINAL_KEY`, so the body looks at the sheet
once, at the end.

**A stroke is checked before anything moves.** Planning happens here, before a
single action is returned to the rollout, so a point off the sheet, an empty
list, a non-finite number or more than
:data:`castor.bench.sacpaint.strokes.MAX_POINTS` points comes back as a tool
error the model can correct, with the arm exactly where it was.

**A body that was not started with ``-E strokes=true`` is refused at bind.** It
would otherwise be handed a tool whose observations lie: the body would
photograph the sheet after every target, and the prompt would never have
mentioned the primitive. The refusal names the flag.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from inspect_robots.embodiment import EmbodimentInfo
from inspect_robots.errors import ConfigError
from inspect_robots.policy import PolicyInfo
from inspect_robots.registry import policy as register_policy
from inspect_robots.types import Action, ActionChunk, Observation

from castor.bench.sacpaint import strokes as stroke_lib

try:  # the agent plugin is the `paintbench-agent` extra, not a hard dependency
    from inspect_robots_agent.policy import LLMAgentPolicy

    HAS_AGENT = True
except ImportError:  # pragma: no cover - exercised only where the extra is absent
    HAS_AGENT = False
    LLMAgentPolicy = object  # type: ignore[assignment, misc]

#: The registry name the CLI and the console pass to ``--policy``.
POLICY_NAME = "agent_strokes"

#: Tools the plugin builds that are not the move tool. Whatever is left is it.
_NON_MOVE_TOOLS = frozenset({"done", "give_up", "take_pic"})


@dataclass(frozen=True)
class _Call:
    """One tool call, in the shape the plugin's toolset reads (name + JSON arguments)."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class _Result:
    """One tool result, in the shape the agent policy's loop reads.

    Duck-typed on purpose: the plugin's own ``ToolResult`` lives in a private
    module, and a stroke needs nothing from it but these five fields.
    """

    chunk: ActionChunk | None = None
    error: str | None = None
    note: str = ""
    capture: tuple[str, ...] | None = None
    target: Any = None


@dataclass
class _Stroke:
    """One planned stroke: the plugin's actions, and where each target starts."""

    actions: list[Action] = field(default_factory=list)
    #: Index of the planned target each action belongs to, parallel to ``actions``.
    points: list[int] = field(default_factory=list)
    target: Any = None


class StrokeToolset:
    """The plugin's toolset with ``stroke`` added, and everything else delegated.

    The wrapper owns no motion knowledge. A stroke's targets are executed
    through the wrapped toolset's own move tool, so the waypoints, the bounds
    checks, the speed limit and any pre-check are the ones the per-target tool
    would have applied to the same targets.
    """

    def __init__(
        self,
        inner: Any,
        *,
        canvas_mm: tuple[float, float],
        low: Any,
        high: Any,
        labels: tuple[str, ...],
        state_key: str,
        move_tool: str,
        values_key: str,
        colored: bool,
        control_hz: float | None = None,
        pen_down_z: float = stroke_lib.PEN_DOWN_Z,
        travel_z: float = stroke_lib.TRAVEL_Z,
        max_points: int = stroke_lib.MAX_POINTS,
    ) -> None:
        self._inner = inner
        self._canvas_mm = canvas_mm
        self._low = np.asarray(low, dtype=np.float64).reshape(-1)
        self._high = np.asarray(high, dtype=np.float64).reshape(-1)
        self._labels = labels
        self._state_key = state_key
        self._move_tool = move_tool
        self._values_key = values_key
        self._colored = bool(colored)
        self._control_hz = control_hz
        self._pen_down_z = float(pen_down_z)
        self._travel_z = float(travel_z)
        self._max_points = int(max_points)
        self._stroke_id = 0

    # -- delegation --------------------------------------------------------

    def state_labels(self) -> Any:
        """The state field and per-element labels the plugin selected."""
        return self._inner.state_labels()

    def residual(self, target: Any, observation: Observation) -> Any:
        """The largest remaining offset from a target, measured by the plugin."""
        return self._inner.residual(target, observation)

    def __getattr__(self, name: str) -> Any:
        """Anything this wrapper does not name is the wrapped toolset's."""
        return getattr(self._inner, name)

    # -- the tool surface --------------------------------------------------

    def schemas(self) -> list[dict[str, Any]]:
        """The plugin's tools, then ``stroke``."""
        return [
            *self._inner.schemas(),
            stroke_lib.tool_schema(
                canvas_mm=self._canvas_mm,
                colored=self._colored,
                max_points=self._max_points,
            ),
        ]

    def execute(self, call: Any, observation: Observation) -> Any:
        """Route ``stroke`` here; every other tool is the plugin's."""
        if getattr(call, "name", None) != stroke_lib.TOOL_NAME:
            return self._inner.execute(call, observation)
        return self._stroke(call, observation)

    # -- the stroke tool ---------------------------------------------------

    def _stroke(self, call: Any, observation: Observation) -> _Result:
        try:
            arguments = json.loads(call.arguments)
        except (TypeError, ValueError):
            return _Result(error=f"arguments for {stroke_lib.TOOL_NAME} are not valid JSON")
        if not isinstance(arguments, dict):
            return _Result(error=f"arguments for {stroke_lib.TOOL_NAME} must be a JSON object")
        note = arguments.get("note")
        if not isinstance(note, str) or not note.strip():
            return _Result(
                error="note is required: describe what you see and why you drew this stroke"
            )
        try:
            targets = stroke_lib.plan(
                arguments.get("points"),
                low=self._low,
                high=self._high,
                pen_down_z=self._pen_down_z,
                travel_z=self._travel_z,
                # On a colour task the plan must be four wide or the box clip
                # cannot broadcast; a stroke that names no colour is black, the
                # same default a bare per-target move gets.
                color=(arguments.get("color") if arguments.get("color") is not None else 0)
                if self._colored
                else None,
                max_points=self._max_points,
            )
        except stroke_lib.StrokeError as exc:
            # Nothing has been emitted, so nothing moved: the arm is where it was.
            return _Result(error=str(exc))

        planned = self._plan_actions(targets, note, observation)
        if isinstance(planned, _Result):
            return planned

        self._stroke_id += 1
        total = len(planned.actions)
        actions = [
            Action(
                data=action.data,
                meta={
                    **{k: v for k, v in action.meta.items() if k != "chunk_final"},
                    **stroke_lib.meta_for(self._stroke_id, point, len(targets)),
                    stroke_lib.FINAL_KEY: index == total - 1,
                    **({"chunk_final": True} if index == total - 1 else {}),
                },
            )
            for index, (action, point) in enumerate(zip(planned.actions, planned.points))
        ]
        points = len(targets) - 2  # the travel in and the lift out are not drawn points
        note_text = (
            f"drawing stroke {self._stroke_id}: {points} point(s) as {len(targets)} targets "
            f"over {total} step(s); one look at the sheet when it finishes"
        )
        return _Result(
            chunk=ActionChunk(actions=actions, control_hz=self._control_hz),
            note=note_text,
            target=planned.target,
        )

    def _plan_actions(
        self, targets: list[np.ndarray], note: str, observation: Observation
    ) -> _Stroke | _Result:
        """Run each planned target through the plugin's own move tool, in order.

        The first target starts from the real observation; every later one
        starts from the target before it, because that is where the arm will be
        by the time it runs. A refusal anywhere refuses the whole stroke: the
        actions are still only planned, so nothing has moved.
        """
        out = _Stroke()
        at: Observation = observation
        for index, target in enumerate(targets):
            call = _Call(
                id=f"stroke-{self._stroke_id + 1}-{index}",
                name=self._move_tool,
                arguments=json.dumps(
                    {
                        self._values_key: {
                            label: float(value)
                            for label, value in zip(self._labels, target.tolist())
                        },
                        "note": note,
                    }
                ),
            )
            result = self._inner.execute(call, at)
            if result.error is not None:
                return _Result(
                    error=(
                        f"stroke refused at point {index} of {len(targets)}: {result.error} "
                        "Nothing moved; send the stroke again."
                    )
                )
            if result.chunk is None:  # pragma: no cover - a move always yields a chunk
                return _Result(error=f"stroke target {index} produced no motion; send it again")
            out.actions.extend(result.chunk.actions)
            out.points.extend([index] * len(result.chunk.actions))
            out.target = result.target if result.target is not None else target
            at = Observation(images={}, state={self._state_key: np.asarray(target)})
        return out


class StrokeAgentPolicy(LLMAgentPolicy):  # type: ignore[misc, valid-type]
    """``agent`` with the body's stroke tool on its toolset, and nothing else changed.

    ``pen_down_z`` and ``travel_z`` are the heights a stroke is planned at.
    Leave them unset: the body advertises its own in the capability it
    declares, and only a body that advertises none falls back to the heights
    :mod:`castor.bench.sacpaint.strokes` documents. They exist so a body with
    unusual pen geometry and no capability can still be driven.
    """

    def __init__(
        self,
        pen_down_z: float | str | None = None,
        travel_z: float | str | None = None,
        **kwargs: Any,
    ) -> None:
        if not HAS_AGENT:  # pragma: no cover - the factory refuses first
            raise ConfigError(
                f"--policy {POLICY_NAME} needs the agent plugin.\n"
                "fix: pip install 'opencastor[paintbench-agent]'"
            )
        super().__init__(**kwargs)
        self._pen_down_z = _height("pen_down_z", pen_down_z)
        self._travel_z = _height("travel_z", travel_z)
        if (
            self._pen_down_z is not None
            and self._travel_z is not None
            and self._travel_z <= self._pen_down_z
        ):
            raise ConfigError(
                f"-P travel_z={self._travel_z} must be above -P pen_down_z={self._pen_down_z}, "
                "or the travel to a stroke's first point would draw"
            )

    def bind(self, embodiment_info: EmbodimentInfo) -> None:
        """Build the plugin's tool surface, then add ``stroke`` to it."""
        super().bind(embodiment_info)
        if not stroke_lib.offered_by(embodiment_info):
            raise ConfigError(
                f"--policy {POLICY_NAME} needs a body that offers the stroke primitive, and "
                f"{embodiment_info.name!r} was not started with it.\n"
                "fix: pass -E strokes=true (the console does this from "
                '"strokes": true in paint.json), or run --policy agent'
            )
        inner = self._toolset
        labels = self._stroke_labels(inner, embodiment_info)
        move_tool, values_key = self._move_tool(inner)
        state_labels = inner.state_labels()
        high = np.asarray(embodiment_info.action_space.high, dtype=np.float64).reshape(-1)
        declared = stroke_lib.geometry_of(embodiment_info) or (
            stroke_lib.PEN_DOWN_Z,
            stroke_lib.TRAVEL_Z,
        )
        self._toolset = StrokeToolset(
            inner,
            canvas_mm=(float(high[0]) * 1000.0, float(high[1]) * 1000.0),
            low=embodiment_info.action_space.low,
            high=embodiment_info.action_space.high,
            labels=labels,
            state_key=state_labels[0],
            move_tool=move_tool,
            values_key=values_key,
            colored=embodiment_info.action_space.dim == 4,
            control_hz=embodiment_info.control_hz,
            pen_down_z=self._pen_down_z if self._pen_down_z is not None else declared[0],
            travel_z=self._travel_z if self._travel_z is not None else declared[1],
        )
        self.info = PolicyInfo(
            name=POLICY_NAME,
            action_space=embodiment_info.action_space,
            observation_space=embodiment_info.observation_space,
            control_hz=embodiment_info.control_hz,
        )

    @staticmethod
    def _stroke_labels(inner: Any, embodiment_info: EmbodimentInfo) -> tuple[str, ...]:
        """The action's dimension labels, refusing a body whose state the plugin cannot locate."""
        state_labels = inner.state_labels()
        if not state_labels:
            raise ConfigError(
                f"--policy {POLICY_NAME} needs an embodiment whose proprioceptive state field "
                "the agent plugin could name, because a stroke's later targets start from the "
                f"target before them; {embodiment_info.name!r} declares none.\n"
                "fix: run --policy agent, whose per-target tool reads the live observation"
            )
        return tuple(state_labels[1])

    @staticmethod
    def _move_tool(inner: Any) -> tuple[str, str]:
        """The plugin's move tool and the key its targets go under, read off its own schemas."""
        for schema in inner.schemas():
            function = schema.get("function", {})
            name = function.get("name")
            if name in _NON_MOVE_TOOLS or not name:
                continue
            properties = function.get("parameters", {}).get("properties", {})
            keys = [key for key in properties if key != "note"]
            if len(keys) == 1:
                return str(name), str(keys[0])
        raise ConfigError(  # pragma: no cover - the plugin always builds a move tool
            f"--policy {POLICY_NAME} could not find the agent plugin's move tool; "
            "run --policy agent"
        )


def _height(name: str, value: float | str | None) -> float | None:
    """Coerce a ``-P`` height string, refusing anything that is not a finite number."""
    if value is None:
        return None
    try:
        height = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"-P {name} must be a number in metres, got {value!r}") from exc
    if not np.isfinite(height) or height < 0:
        raise ConfigError(f"-P {name} must be a finite height in metres >= 0, got {value!r}")
    return height


@register_policy(POLICY_NAME)
def agent_strokes_policy(**kwargs: Any) -> StrokeAgentPolicy:
    """Registry factory for ``--policy agent_strokes``.

    Takes exactly the keyword arguments ``agent`` takes; the CLI forwards
    ``-P key=value`` pairs here unchanged.
    """
    if not HAS_AGENT:
        raise ConfigError(
            f"--policy {POLICY_NAME} needs the agent plugin.\n"
            "fix: pip install 'opencastor[paintbench-agent]'"
        )
    return StrokeAgentPolicy(**kwargs)
