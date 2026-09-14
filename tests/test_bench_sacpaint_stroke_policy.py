"""``agent_strokes``: the same agent, with the body's stroke tool in front of the model.

Driven here through the policy's own LLM client seam with a scripted model, so
no key, no subscription and no network are involved. The claims are that a
stroke call lands every segment on the sheet, that each planned target is still
its own step, that the sheet is photographed once per stroke, that the
per-target tool is untouched beside it, and that a body without the primitive
is refused at bind rather than quietly drawing one target per call.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip(
    "inspect_robots", reason="castor bench sacpaint needs pip install 'opencastor[paintbench]'"
)
pytest.importorskip(
    "inspect_robots_agent",
    reason="--policy agent_strokes needs pip install 'opencastor[paintbench-agent]'",
)

import numpy as np
from inspect_robots.errors import ConfigError
from inspect_robots.registry import registered
from inspect_robots.scene import Scene
from inspect_robots.types import Observation
from inspect_robots_agent._llm import AssistantMessage, ToolCall

from castor.bench.sacpaint import palette as pal
from castor.bench.sacpaint import strokes as stroke_lib
from castor.bench.sacpaint.mock import PlotterEmbodiment
from castor.bench.sacpaint.stroke_policy import POLICY_NAME, agent_strokes_policy

@pytest.fixture(autouse=True)
def _fresh_references() -> None:
    """Re-discover the reference set before each test.

    Other files in this suite point ``$SACPAINT_REFERENCES`` at a tmp dir and
    leave the discovery cache there; the environment is restored but the cache
    is not, so a later file can be told a real reference does not exist.
    """
    from castor.bench.sacpaint import reference as refmod

    refmod.refresh()


COLOR_REFERENCE = "starry-night"
MONO_REFERENCE = "sacramento-line-v0"
SCENE = Scene(id="strokes", instruction="Draw the reference image.")

#: Legs short enough that the plugin's speed limit splits none of them, so a
#: planned target is exactly one step and the count is readable by eye.
STROKE_A = [(0.015, 0.015), (0.030, 0.015), (0.030, 0.030)]
STROKE_B = [(0.040, 0.030), (0.040, 0.045)]


class FakeLLM:
    """The policy's own client seam, answering from a script instead of a server."""

    def __init__(self, *messages: AssistantMessage) -> None:
        self.queue = list(messages)
        self.tools_seen: list[list[dict]] = []

    def complete(self, messages, tools, temperature=None, reasoning_effort=None):  # noqa: ANN001
        self.tools_seen.append(tools)
        if not self.queue:
            raise AssertionError("the scripted model ran out of turns")
        return self.queue.pop(0)


def _call(name: str, **arguments) -> AssistantMessage:
    return AssistantMessage(
        content=None,
        tool_calls=(ToolCall(id=f"c{name}", name=name, arguments=json.dumps(arguments)),),
    )


def _policy(body: PlotterEmbodiment, **kwargs):  # noqa: ANN201 - the policy class is optional
    """A bound ``agent_strokes`` whose provider resolution never touches the network."""
    policy = agent_strokes_policy(
        model="fake/model",
        base_url="http://127.0.0.1:9/v1",
        api_key_env="SACPAINT_FAKE_KEY",
        env={"SACPAINT_FAKE_KEY": "not-a-real-key"},
        wire_capture=False,
        **kwargs,
    )
    policy.bind(body.info)
    return policy


def _drive(policy, body: PlotterEmbodiment) -> list[Observation]:  # noqa: ANN001
    """Play the policy against the body the way a rollout does; return every observation."""
    observation = body.reset(SCENE)
    policy.reset(SCENE)
    seen = [observation]
    while True:
        chunk = policy.act(observation)
        if chunk.actions[0].meta.get("request_stop"):
            return seen
        for action in chunk.actions:
            observation = body.step(action).observation
            seen.append(observation)


def _pixel(body: PlotterEmbodiment, x_m: float, y_m: float) -> tuple[int, int, int]:
    col, row = body.spec.mm_to_px((x_m * 1000.0, y_m * 1000.0))
    return tuple(int(v) for v in body.canvas()[row, col])


# --- the tool surface ----------------------------------------------------------


def test_the_toolset_offers_stroke_beside_the_plugins_own_tools() -> None:
    body = PlotterEmbodiment(reference=MONO_REFERENCE, strokes=True)
    policy = _policy(body)
    schemas = policy._toolset.schemas()
    names = [schema["function"]["name"] for schema in schemas]
    assert names == ["move_to", "done", "give_up", stroke_lib.TOOL_NAME]
    assert policy.info.name == POLICY_NAME

    description = schemas[-1]["function"]["description"]
    assert str(stroke_lib.MAX_POINTS) in description  # the cap the model must respect
    assert "Prefer long strokes" in description
    points = schemas[-1]["function"]["parameters"]["properties"]["points"]
    assert points["maxItems"] == stroke_lib.MAX_POINTS

    # The sheet in the schema is the sheet the body declared, not a constant.
    width, height = body.spec.canvas_mm
    assert f"{width / 1000.0:.2f}" in points["description"]
    assert f"{height / 1000.0:.2f}" in points["description"]


def test_the_policy_is_registered_under_its_own_name() -> None:
    assert POLICY_NAME in registered("policy")


def test_a_body_without_the_primitive_is_refused_at_bind() -> None:
    """No silent degrading: a run that cannot stroke says so before the arm moves."""
    plain = PlotterEmbodiment(reference=MONO_REFERENCE)
    assert not stroke_lib.offered_by(plain.info)
    with pytest.raises(ConfigError, match="strokes=true"):
        _policy(plain)


def test_the_body_advertises_the_primitive_and_its_pen_heights() -> None:
    body = PlotterEmbodiment(reference=MONO_REFERENCE, strokes=True)
    assert stroke_lib.offered_by(body.info)
    assert stroke_lib.geometry_of(body.info) == (body.pen_down_z, 0.005)
    assert stroke_lib.capability(0.002, 0.005) in body.info.capabilities


# --- the run -------------------------------------------------------------------


def test_a_scripted_model_draws_two_strokes_and_one_target_in_the_mock_world() -> None:
    body = PlotterEmbodiment(reference=MONO_REFERENCE, strokes=True)
    policy = _policy(body)
    policy._client = FakeLLM(
        _call(stroke_lib.TOOL_NAME, points=[list(p) for p in STROKE_A], note="the long diagonal"),
        _call(stroke_lib.TOOL_NAME, points=[list(p) for p in STROKE_B], note="the second line"),
        _call("move_to", targets={"x": 0.045, "y": 0.045, "z": 0.005}, note="park the pen"),
        _call("done", summary="drawn", hindsight="none"),
    )
    seen = _drive(policy, body)

    # Every segment of both strokes is on the sheet, in ink.
    for stroke in (STROKE_A, STROKE_B):
        for (x0, y0), (x1, y1) in zip(stroke, stroke[1:]):
            assert _pixel(body, (x0 + x1) / 2, (y0 + y1) / 2) == (0, 0, 0)
    # The travels between and around them marked nothing.
    assert _pixel(body, 0.035, 0.042) == (255, 255, 255)

    # One step per planned target: five targets for stroke A, four for stroke B,
    # one for the per-target move. Every leg here fits the body's own per-step
    # limit, so the plugin split none of them.
    assert body.num_steps == (len(STROKE_A) + 2) + (len(STROKE_B) + 2) + 1 == 10

    # The sheet was photographed once per stroke, not once per target: the reset
    # view, one at the end of each stroke, and one for the per-target move.
    assert len({id(observation) for observation in seen}) == 4


def test_a_stroke_marks_only_its_last_step_and_numbers_its_targets() -> None:
    body = PlotterEmbodiment(reference=MONO_REFERENCE, strokes=True)
    policy = _policy(body)
    policy._client = FakeLLM(
        _call(stroke_lib.TOOL_NAME, points=[list(p) for p in STROKE_A], note="one stroke")
    )
    chunk = policy.act(body.reset(SCENE))

    metas = [action.meta for action in chunk.actions]
    assert {meta[stroke_lib.STROKE_KEY] for meta in metas} == {1}
    assert [meta[stroke_lib.POINT_KEY] for meta in metas] == list(range(len(STROKE_A) + 2))
    assert [meta[stroke_lib.FINAL_KEY] for meta in metas].count(True) == 1
    assert metas[-1][stroke_lib.FINAL_KEY] is True
    assert all(stroke_lib.is_open(meta) for meta in metas[:-1])

    # The z profile is the body's own plan: travel in, down, along, and up again.
    heights = [float(action.data[2]) for action in chunk.actions]
    assert heights[0] == pytest.approx(0.005)
    assert heights[1:-1] == pytest.approx([0.002] * (len(chunk.actions) - 2))
    assert heights[-1] == pytest.approx(0.005)


def test_a_long_stroke_is_split_the_way_the_per_target_tool_splits_it() -> None:
    """A leg past the body's per-step limit becomes the same waypoints ``move_to`` makes."""
    body = PlotterEmbodiment(reference=MONO_REFERENCE, strokes=True)
    policy = _policy(body)
    far = [(0.02, 0.02), (0.14, 0.02)]  # 120 mm, far past the declared 20 mm step

    policy._client = FakeLLM(
        _call(stroke_lib.TOOL_NAME, points=[list(p) for p in far], note="edge to edge")
    )
    stroke = policy.act(body.reset(SCENE))
    drawn = [a for a in stroke.actions if a.meta[stroke_lib.POINT_KEY] == 2]
    assert len(drawn) > 1  # the long leg was split, not clamped away

    # The same leg asked for as a plain per-target move produces the same waypoints.
    fresh = PlotterEmbodiment(reference=MONO_REFERENCE, strokes=True)
    plain = _policy(fresh)
    observation = fresh.reset(SCENE)
    plain._client = FakeLLM(
        _call("move_to", targets={"x": 0.02, "y": 0.02, "z": 0.002}, note="down"),
        _call("move_to", targets={"x": 0.14, "y": 0.02, "z": 0.002}, note="across"),
    )
    for action in plain.act(observation).actions:
        observation = fresh.step(action).observation
    reference = plain.act(observation).actions
    assert len(drawn) == len(reference)
    assert np.allclose([a.data for a in drawn], [a.data for a in reference])


def test_a_colour_stroke_inks_the_whole_line_in_one_palette_colour() -> None:
    body = PlotterEmbodiment(reference=COLOR_REFERENCE, strokes=True)
    policy = _policy(body)
    gold = pal.index_of("gold")
    policy._client = FakeLLM(
        _call(
            stroke_lib.TOOL_NAME,
            points=[list(p) for p in STROKE_A],
            color=int(gold),
            note="the gold line",
        ),
        _call("done", summary="drawn", hindsight="none"),
    )
    _drive(policy, body)

    for (x0, y0), (x1, y1) in zip(STROKE_A, STROKE_A[1:]):
        assert _pixel(body, (x0 + x1) / 2, (y0 + y1) / 2) == pal.rgb_of("gold")
    assert body.color == gold


# --- refusal -------------------------------------------------------------------


def test_a_stroke_off_the_sheet_is_refused_as_a_tool_error_and_nothing_moves() -> None:
    body = PlotterEmbodiment(reference=MONO_REFERENCE, strokes=True)
    policy = _policy(body)
    width_m = body.spec.canvas_mm[0] / 1000.0
    policy._client = FakeLLM(
        _call(stroke_lib.TOOL_NAME, points=[[0.02, 0.02], [width_m * 3.0, 0.02]], note="too far"),
        _call(stroke_lib.TOOL_NAME, points=[], note="nothing at all"),
        _call("done", summary="gave up on that", hindsight="none"),
    )
    observation = body.reset(SCENE)
    before = body.canvas()
    policy.reset(SCENE)
    chunk = policy.act(observation)

    assert chunk.actions[0].meta.get("request_stop") is True  # only `done` produced motion
    assert body.num_steps == 0
    assert np.array_equal(body.canvas(), before)
    errors = [m["content"] for m in policy.transcript() if m.get("role") == "tool"]
    assert "off the sheet" in errors[0]
    assert "at least one point" in errors[1]


def test_a_stroke_missing_its_note_is_refused_before_it_is_planned() -> None:
    body = PlotterEmbodiment(reference=MONO_REFERENCE, strokes=True)
    policy = _policy(body)
    policy._client = FakeLLM(
        _call(stroke_lib.TOOL_NAME, points=[[0.02, 0.02], [0.03, 0.02]]),
        _call("done", summary="stopping", hindsight="none"),
    )
    policy.reset(SCENE)
    policy.act(body.reset(SCENE))
    errors = [m["content"] for m in policy.transcript() if m.get("role") == "tool"]
    assert "note is required" in errors[0]
    assert body.num_steps == 0


# --- the record ----------------------------------------------------------------


def test_the_transcript_records_the_stroke_calls_the_model_made() -> None:
    body = PlotterEmbodiment(reference=MONO_REFERENCE, strokes=True)
    policy = _policy(body)
    policy._client = FakeLLM(
        _call(stroke_lib.TOOL_NAME, points=[list(p) for p in STROKE_A], note="the long diagonal"),
        _call(stroke_lib.TOOL_NAME, points=[list(p) for p in STROKE_B], note="the second line"),
        _call("done", summary="drawn", hindsight="none"),
    )
    _drive(policy, body)

    calls = [
        call["function"]
        for message in policy.transcript()
        for call in message.get("tool_calls") or []
    ]
    strokes = [json.loads(c["arguments"]) for c in calls if c["name"] == stroke_lib.TOOL_NAME]
    assert len(strokes) == 2
    assert strokes[0]["points"] == [list(p) for p in STROKE_A]
    assert strokes[0]["note"] == "the long diagonal"
    assert [c["name"] for c in calls] == [stroke_lib.TOOL_NAME, stroke_lib.TOOL_NAME, "done"]

    # And the result the model was handed names the stroke and what it cost.
    notes = [m["content"] for m in policy.transcript() if m.get("role") == "tool"]
    assert "drawing stroke 1" in notes[0] and "targets" in notes[0]


def test_the_stroke_tool_is_offered_on_every_turn_beside_the_move_tool() -> None:
    body = PlotterEmbodiment(reference=MONO_REFERENCE, strokes=True)
    policy = _policy(body)
    client = FakeLLM(
        _call(stroke_lib.TOOL_NAME, points=[list(p) for p in STROKE_A], note="one"),
        _call("move_to", targets={"x": 0.05, "y": 0.05, "z": 0.005}, note="two"),
        _call("done", summary="drawn", hindsight="none"),
    )
    policy._client = client
    _drive(policy, body)
    for tools in client.tools_seen:
        names = [tool["function"]["name"] for tool in tools]
        assert stroke_lib.TOOL_NAME in names and "move_to" in names
