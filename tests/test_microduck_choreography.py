"""Tests for the choreography layer — the thing that turns a sentence into a
sequence the duck can actually perform.

The interesting cases are the refusals and the aborts: a plan that cannot be
performed must be rejected whole before anything moves, and a duck that falls
over mid-plan must not have the remaining eleven steps run at it.
"""

from __future__ import annotations

import pytest

from castor.microduck_choreography import (
    PRIMITIVES,
    ROUTINES,
    ChoreographyError,
    DuckChoreographer,
    register_duck_tools,
)


class FakeDuck:
    """Records what the choreographer asked the driver to do."""

    def __init__(self, state: dict | None = None):
        self.calls: list[tuple] = []
        self._state = state or {}
        self.fail_on: str | None = None

    def _record(self, name, *args, **kwargs):
        if self.fail_on == name:
            raise RuntimeError(f"{name} refused")
        self.calls.append((name, args, kwargs))

    def move(self, linear=0.0, angular=0.0): self._record("move", linear, angular)
    def strafe(self, lateral): self._record("strafe", lateral)
    def stop(self): self._record("stop")
    def look_at(self, x, y, z, neck_pitch=0.0): self._record("look_at", x, y, z)
    def mouth(self, open=0.0): self._record("mouth", open)
    def pose(self, z=0.0, roll=0.0, pitch=0.0, active=True): self._record("pose", z, roll, pitch)
    def sound(self, tag="chirp", hold=None): self._record("sound", tag)
    def kick(self, left=False): self._record("kick", left)
    def ground_pick(self): self._record("ground_pick")
    def sit_toggle(self): self._record("sit_toggle")
    def roulade(self): self._record("roulade")
    def get_state(self): return self._state

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


@pytest.fixture
def duck():
    return FakeDuck()


@pytest.fixture
def choreographer(duck):
    slept: list[float] = []
    c = DuckChoreographer(duck, sleep=slept.append, clock=lambda: 0.0)
    c.slept = slept  # type: ignore[attr-defined]
    return c


# ── The vocabulary an LLM plans against ───────────────────────────────────────


def test_the_vocabulary_tells_a_planner_what_it_needs_to_plan(choreographer):
    text = choreographer.vocabulary()
    # Durations and exclusivity are the two facts a planner cannot work without.
    assert "takes 0.5s, holds the robot" in text, "a kick's cost must be visible"
    assert "takes 3s, holds the robot" in text, "a pick's cost must be visible"
    # Every move and routine is offered by name.
    for name in PRIMITIVES:
        assert name in text
    for name in ROUTINES:
        assert name in text
    # And the traps the duck actually has.
    assert "kick is blind" in text


def test_the_tool_schema_carries_types_and_defaults(choreographer):
    schema = choreographer.tool_schema()
    assert schema["primitives"]["kick"]["exclusive"] is True
    assert schema["primitives"]["kick"]["duration_s"] == 0.5
    assert schema["primitives"]["say"]["params"]["tag"]["default"] == "chirp"
    assert "fetch" in schema["routines"]


# ── Expansion: one word becomes a sequence ────────────────────────────────────


def test_a_routine_expands_into_primitives(choreographer):
    """greet is look-up, say hello, nod — and the nod is itself a routine."""
    steps = choreographer.expand([{"move": "greet"}])
    assert [s["move"] for s in steps] == [
        "look_at", "say",
        "look_at", "wait", "look_at", "wait", "look_at", "wait", "look_at",
    ]
    assert all(s["move"] in PRIMITIVES for s in steps), "expansion must bottom out in primitives"


def test_fetch_is_ten_primitives_from_one_word(choreographer):
    steps = choreographer.expand([{"move": "fetch", "metres": 0.5}])
    assert [s["move"] for s in steps] == [
        "look_at", "walk", "stop", "pick", "turn", "stop", "walk", "stop", "mouth", "say",
    ]


def test_routine_parameters_reach_the_primitives(choreographer):
    near = choreographer.expand([{"move": "approach", "metres": 0.12}])
    far = choreographer.expand([{"move": "approach", "metres": 1.2}])
    assert far[0]["seconds"] > near[0]["seconds"] * 5, "walking further takes longer"


def test_turning_right_reverses_the_rate(choreographer):
    left = choreographer.expand([{"move": "turn_by", "degrees": 90}])
    right = choreographer.expand([{"move": "turn_by", "degrees": -90}])
    assert left[0]["rate"] > 0 and right[0]["rate"] < 0
    assert left[0]["seconds"] == pytest.approx(right[0]["seconds"])


def test_nested_routines_expand(choreographer):
    steps = choreographer.expand([{"move": "patrol", "side_metres": 0.4}])
    assert all(s["move"] in PRIMITIVES for s in steps)
    assert len(steps) > 20, "a patrol is a lot of duck"


# ── Refusals: a bad plan is refused whole, before anything moves ──────────────


def test_an_unknown_move_is_refused_and_names_what_is_known(choreographer, duck):
    with pytest.raises(ChoreographyError, match="unknown move 'backflip'"):
        choreographer.perform([{"move": "walk", "speed": 0.5}, {"move": "backflip"}])
    assert duck.calls == [], "nothing may move when a later step is invalid"


def test_a_bad_parameter_type_is_refused(choreographer):
    with pytest.raises(ChoreographyError, match="must be a float"):
        choreographer.expand([{"move": "walk", "speed": "quickly"}])
    with pytest.raises(ChoreographyError, match="must be true or false"):
        choreographer.expand([{"move": "kick", "left": "yes"}])


def test_a_step_with_no_move_is_refused(choreographer):
    with pytest.raises(ChoreographyError, match="names no move"):
        choreographer.expand([{"seconds": 1.0}])


def test_a_plan_that_is_not_a_list_is_refused(choreographer):
    with pytest.raises(ChoreographyError, match="a list of steps"):
        choreographer.expand({"move": "walk"})


# ── Performing ────────────────────────────────────────────────────────────────


def test_performing_a_plan_drives_the_duck_in_order(choreographer, duck):
    result = choreographer.perform([
        {"move": "approach", "metres": 0.24},
        {"move": "nudge"},
        {"move": "say", "tag": "chirp"},
    ])
    assert result.completed
    assert duck.names() == [
        "move", "stop",            # approach
        "stop",                    # approach's explicit stop
        "look_at", "kick",         # nudge (its wait sleeps, sends nothing)
        "sound",                   # say
    ]


def test_a_timed_move_is_held_then_released(choreographer, duck):
    choreographer.perform([{"move": "walk", "speed": 0.5, "seconds": 2.0}])
    assert duck.calls[0] == ("move", (0.5, 0.0), {})
    assert duck.calls[-1][0] == "stop", "a held intent must be released"
    assert 2.0 in choreographer.slept


def test_exclusive_skills_are_waited_out_for_their_real_duration(choreographer):
    choreographer.perform([{"move": "kick"}, {"move": "pick"}, {"move": "roll"}])
    assert 0.5 in choreographer.slept, "a kick takes half a second"
    assert 3.0 in choreographer.slept, "a ground pick takes three"
    assert 1.0 in choreographer.slept, "a roll takes one"


def test_a_dry_run_moves_nothing(choreographer, duck):
    result = choreographer.perform([{"move": "fetch"}], dry_run=True)
    assert result.completed
    assert len(result.steps) == 10
    assert duck.calls == []


# ── Aborts: the duck gets to end the performance ──────────────────────────────


def test_a_fallen_duck_ends_the_performance(duck):
    duck._state = {"safety": {"fallen": True}}
    c = DuckChoreographer(duck, sleep=lambda s: None, clock=lambda: 0.0)
    result = c.perform([{"move": "patrol"}])
    assert not result.completed
    assert result.aborted_because == "the duck fell over"
    assert duck.names() == ["stop"], "a fallen duck gets stopped, not patrolled"


def test_a_limp_duck_ends_the_performance(duck):
    duck._state = {"safety": {"limp": True}}
    c = DuckChoreographer(duck, sleep=lambda s: None, clock=lambda: 0.0)
    result = c.perform([{"move": "celebrate"}])
    assert result.aborted_because == "the duck went limp"


def test_a_flat_battery_ends_the_performance(duck):
    duck._state = {"battery": {"percent": 4}}
    c = DuckChoreographer(duck, sleep=lambda s: None, clock=lambda: 0.0)
    result = c.perform([{"move": "dance"}])
    assert not result.completed
    assert "battery down to 4%" in result.aborted_because


def test_a_healthy_duck_is_not_aborted(duck):
    duck._state = {"safety": {"fallen": False}, "battery": {"percent": 80}}
    c = DuckChoreographer(duck, sleep=lambda s: None, clock=lambda: 0.0)
    assert c.perform([{"move": "greet"}]).completed


def test_no_state_at_all_is_not_a_reason_to_stop(duck):
    duck._state = {}
    c = DuckChoreographer(duck, sleep=lambda s: None, clock=lambda: 0.0)
    assert c.perform([{"move": "nod"}]).completed


def test_a_failing_step_stops_the_plan_and_stops_the_duck(duck):
    duck.fail_on = "kick"
    c = DuckChoreographer(duck, sleep=lambda s: None, clock=lambda: 0.0)
    result = c.perform([{"move": "walk", "seconds": 1}, {"move": "kick"}, {"move": "roll"}])
    assert not result.completed
    assert "kick" in result.aborted_because
    assert "roulade" not in duck.names(), "the rest of the plan must not run"
    assert duck.names()[-1] == "stop"


# ── The LLM-facing tools ──────────────────────────────────────────────────────


class FakeRegistry:
    def __init__(self):
        self.tools: dict = {}

    def register(self, name, fn, description="", parameters=None, returns=""):
        self.tools[name] = fn


def test_registering_tools_exposes_discovery_and_performance(duck):
    registry = FakeRegistry()
    register_duck_tools(registry, duck)
    assert set(registry.tools) == {"duck_vocabulary", "duck_perform"}
    schema = registry.tools["duck_vocabulary"]()
    assert "fetch" in schema["routines"]


def test_the_perform_tool_answers_rather_than_raising(duck):
    registry = FakeRegistry()
    register_duck_tools(registry, duck)
    answer = registry.tools["duck_perform"](plan=[{"move": "backflip"}])
    assert answer["ok"] is False
    assert "unknown move" in answer["refused"]


def test_the_perform_tool_reports_each_step(duck):
    registry = FakeRegistry()
    register_duck_tools(registry, duck)
    answer = registry.tools["duck_perform"](plan=[{"move": "say", "tag": "greet"}])
    assert answer["ok"] is True
    assert answer["steps"] == [{"move": "say", "ok": True, "detail": "greet"}]


# ── The CLI verb: English, a routine name, or a literal plan ──────────────────


def test_a_routine_name_needs_no_brain_at_all():
    """A duck that can only be choreographed by an LLM stops working offline."""
    from castor.cli import _duck_plan_from_request

    duck = DuckChoreographer(FakeDuck())
    assert _duck_plan_from_request(duck, "fetch", lambda *a: None) == [{"move": "fetch"}]


def test_a_literal_json_plan_is_accepted():
    from castor.cli import _duck_plan_from_request

    duck = DuckChoreographer(FakeDuck())
    plan = _duck_plan_from_request(duck, '[{"move": "say", "tag": "greet"}]', lambda *a: None)
    assert plan == [{"move": "say", "tag": "greet"}]


def test_broken_json_is_reported_not_raised():
    from castor.cli import _duck_plan_from_request

    duck = DuckChoreographer(FakeDuck())
    said: list = []
    assert _duck_plan_from_request(duck, '[{"move":', said.append) is None
    assert any("not a plan" in str(m) for m in said)


def test_english_goes_through_the_brain_and_the_plan_is_extracted(monkeypatch):
    """The model may wrap its answer in prose; the plan is still in there."""
    from castor import cli

    class Thought:
        text = 'Sure! Here you go:\n[{"move": "greet"}, {"move": "patrol"}]\nEnjoy.'

    class Provider:
        def think(self, prompt):
            assert "MOVES:" in prompt, "the model must be given the vocabulary"
            assert "walk to the ball" in prompt
            return Thought()

    monkeypatch.setattr("castor.providers.get_provider", lambda cfg: Provider())
    duck = DuckChoreographer(FakeDuck())
    plan = cli._duck_plan_from_request(duck, "walk to the ball", lambda *a: None)
    assert plan == [{"move": "greet"}, {"move": "patrol"}]


def test_a_brain_that_answers_with_no_plan_is_reported(monkeypatch):
    from castor import cli

    class Provider:
        def think(self, prompt):
            return type("T", (), {"text": "I would rather not."})()

    monkeypatch.setattr("castor.providers.get_provider", lambda cfg: Provider())
    duck = DuckChoreographer(FakeDuck())
    said: list = []
    assert cli._duck_plan_from_request(duck, "do a barrel roll", said.append) is None
    assert any("No plan in that answer" in str(m) for m in said)


def test_a_brain_that_fails_falls_back_to_naming_routines(monkeypatch):
    from castor import cli

    class Provider:
        def think(self, prompt):
            raise RuntimeError("no API key")

    monkeypatch.setattr("castor.providers.get_provider", lambda cfg: Provider())
    duck = DuckChoreographer(FakeDuck())
    said: list = []
    assert cli._duck_plan_from_request(duck, "dance for me", said.append) is None
    assert any("fetch" in str(m) for m in said), "the offline vocabulary must be offered"
