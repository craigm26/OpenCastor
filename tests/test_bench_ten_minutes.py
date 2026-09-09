"""The ten-minute benchmark, run whole against a mock robotd in this process.

WHAT THESE TESTS ARE FOR. The benchmark exists because nothing in this project
ever ran OpenCastor against a ``robotd``, and four wire keys were wrong for
weeks as a result. A test suite that mocked the runner would reproduce exactly
that mistake one layer up. So these run the real ``run()`` against a real Unix
socket serving replies transcribed from ``duck-ipc-proto``, with the real
``MicroduckDriver``, the real ``DuckChoreographer`` and the real intent loop.
The only thing faked is the duck.

Every test that asserts a pass has a sibling that asserts the same machinery
produces a fail, because a checkpoint that cannot fail is not a checkpoint.
"""

from __future__ import annotations

import copy
import json

import pytest

from castor.bench import evallog as evallog_mod
from castor.bench import mock_robotd, wire
from castor.bench.record import BENCHMARK, SCHEMA_VERSION
from castor.bench.targets import MockModeTarget, MockTarget, RealTarget, TargetUnavailable
from castor.bench.ten_minutes import (
    MANDATORY,
    ROBOTS,
    BenchError,
    ScriptedBrain,
    build_target,
    run,
)


def _run(**kwargs):
    """One whole benchmark against an in-process mock, with a scripted brain."""
    target = kwargs.pop("target", None) or MockTarget(in_process=True)
    try:
        return run(target=target, brain=ScriptedBrain(), say=lambda _l: None, **kwargs)
    finally:
        target.teardown()


# ---------------------------------------------------------------------------
# a pass
# ---------------------------------------------------------------------------


def test_mock_run_reaches_every_mandatory_checkpoint():
    record = _run()
    for cid in MANDATORY:
        checkpoint = record.checkpoint(cid)
        assert checkpoint is not None, f"{cid} was never stamped"
        assert checkpoint.ok is True, f"{cid} failed: {checkpoint.evidence}"


def test_a_mock_run_is_ci_pass_and_never_pass():
    """The mock is for CI wiring. It is never a pass of the ten-minute goal."""
    record = _run()
    assert record.verdict == "ci-pass"
    assert record.verdict != "pass"
    assert "mock robotd" in record.verdict_reason


def test_checkpoints_are_in_order_and_inside_the_budget():
    record = _run()
    times = [record.checkpoint(cid).t for cid in MANDATORY]
    assert times == sorted(times)
    assert record.elapsed_s < record.budget_s


def test_c7_is_null_on_the_mock_and_never_claims_a_step():
    """A run without a floor must say ``stepped: null``, never ``stepped: true``."""
    record = _run(floor=True)
    c7 = record.checkpoint("C7")
    assert c7.ok is None
    assert c7.evidence["stepped"] is None
    assert "no physics" in c7.evidence["reason"]


def test_the_record_carries_the_transcript_and_the_wire():
    record = _run()
    data = record.to_dict()
    assert data["benchmark"] == BENCHMARK
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["transcript"], "a run with no transcript is a number nobody can audit"
    assert all("t" in entry and "typed" in entry for entry in data["transcript"])

    methods = [json.loads(line["line"]).get("method") for line in data["wire"]]
    assert wire.M_MOVE in methods, "the C5 move is not on the wire"
    assert len([m for m in methods if m == wire.M_MOVE]) >= 3, (
        "the wire must carry the move, its re-send and the stop"
    )


def test_c5_records_the_envelope_and_a_measured_resend():
    record = _run()
    evidence = record.checkpoint("C5").evidence
    assert set(evidence["params"]) == set(wire.MOVE_KEYS)
    assert evidence["params"][wire.MOVE_VX] > 0
    assert evidence["resend_seen_at"] is not None
    assert evidence["intent_hz"] >= 1.0 / (wire.ROBOTD_DEADMAN_MS / 1000.0)


def test_c6_names_which_deadman_fired():
    record = _run()
    evidence = record.checkpoint("C6").evidence
    assert evidence["fired_by"] in ("driver_ttl", "explicit_stop")
    assert evidence["silence_to_stop_ms"] <= evidence["window_ms"]
    assert evidence["deadmen"]["robotd_ms"] == wire.ROBOTD_DEADMAN_MS
    assert evidence["deadmen"]["bridge_ms"] == wire.BRIDGE_DEADMAN_MS


def test_wifi_onboarding_is_excluded_and_always_reported():
    record = _run(wifi_onboarding_s=930.0, wifi_reason="a cold cargo build of duckctl")
    assert record.excluded["wifi_onboarding_s"] == 930.0
    assert record.excluded["reason"] == "a cold cargo build of duckctl"
    # And it is excluded: the clock is well under the budget despite it.
    assert record.elapsed_s < record.budget_s


def test_a_null_wifi_interval_still_carries_a_reason():
    record = _run()
    assert record.excluded["wifi_onboarding_s"] is None
    assert record.excluded["reason"]


# ---------------------------------------------------------------------------
# fails
# ---------------------------------------------------------------------------


def test_mock_mode_fails_c2_and_the_whole_run():
    """MicroduckDriver degrades to mock mode and keeps answering. C2 catches it."""
    record = _run(target=MockModeTarget())
    assert record.checkpoint("C2").ok is False
    assert record.checkpoint("C2").evidence["mode"] == "mock"
    assert record.verdict == "ci-fail"
    assert "C2 did not pass" in record.verdict_reason


def test_a_missing_health_key_fails_c3():
    """The review's whole finding: an absent key is a failure, never a '?'."""
    replies = copy.deepcopy(mock_robotd.REPLIES)
    del replies[wire.M_HEALTH][wire.HEALTH_CONTROL_LOOP]
    record = _run(target=MockTarget(in_process=True, replies=replies))

    c3 = record.checkpoint("C3")
    assert c3.ok is False
    assert any(wire.HEALTH_CONTROL_LOOP in p for p in c3.evidence["problems"])
    assert record.verdict == "ci-fail"


def test_a_missing_battery_fails_c3_even_though_the_robot_is_healthy():
    replies = copy.deepcopy(mock_robotd.REPLIES)
    del replies[wire.M_HEALTH][wire.HEALTH_BATTERY]
    record = _run(target=MockTarget(in_process=True, replies=replies))
    c3 = record.checkpoint("C3")
    assert c3.ok is False
    assert any(wire.HEALTH_BATTERY in p for p in c3.evidence["problems"])


def test_a_health_reply_shaped_like_the_old_mock_fails_c3():
    """``{"ok": true}`` for every method is what duck-studio's mock used to send."""
    replies = {method: {"ok": True} for method in mock_robotd.REPLIES}
    record = _run(target=MockTarget(in_process=True, replies=replies))
    c3 = record.checkpoint("C3")
    assert c3.ok is False
    assert c3.evidence["problems"], "an all-ok reply must not read as an identity"
    assert record.verdict == "ci-fail"


def test_a_flat_battery_fails_c3_at_the_choreographers_own_floor():
    replies = copy.deepcopy(mock_robotd.REPLIES)
    replies[wire.M_HEALTH][wire.HEALTH_BATTERY][wire.BATTERY_PERCENT] = 8.0
    record = _run(target=MockTarget(in_process=True, replies=replies))
    c3 = record.checkpoint("C3")
    assert c3.ok is False
    assert any("floor" in p for p in c3.evidence["problems"])


def test_no_walking_policy_fails_c3():
    """A board that could not reach the Hub has no gait, and this benchmark ends at a move."""
    replies = copy.deepcopy(mock_robotd.REPLIES)
    replies[wire.M_POLICIES][wire.POL_SLOTS] = [
        s for s in replies[wire.M_POLICIES][wire.POL_SLOTS] if s[wire.SLOT_SLOT] != "walk"
    ]
    record = _run(target=MockTarget(in_process=True, replies=replies))
    c3 = record.checkpoint("C3")
    assert c3.ok is False
    assert any(wire.WALK_SLOT in p for p in c3.evidence["problems"])


def test_an_unhealthy_robot_fails_c3():
    replies = copy.deepcopy(mock_robotd.REPLIES)
    replies[wire.M_HEALTH][wire.HEALTH_HEALTHY] = False
    replies[wire.M_HEALTH][wire.HEALTH_REASON] = "servo bus is not answering"
    record = _run(target=MockTarget(in_process=True, replies=replies))
    c3 = record.checkpoint("C3")
    assert c3.ok is False
    assert any("servo bus" in p for p in c3.evidence["problems"])


def test_a_refused_plan_fails_c4_and_the_refusal_is_recorded_as_correct():
    class Refuser(ScriptedBrain):
        def answer(self, prompt):
            answer = super().answer(prompt)
            answer.text = '[{"move": "backflip"}]'
            return answer

    record = run(target=MockTarget(in_process=True), brain=Refuser(), say=lambda _l: None)
    c4 = record.checkpoint("C4")
    assert c4.ok is False
    assert c4.evidence["refusal"] is True
    assert record.checkpoint("C5").ok is False
    assert record.verdict == "ci-fail"


def test_a_plan_that_never_moves_fails_c5():
    class Stiller(ScriptedBrain):
        def answer(self, prompt):
            answer = super().answer(prompt)
            answer.text = '[{"move": "wait", "seconds": 0.1}]'
            return answer

    record = run(target=MockTarget(in_process=True), brain=Stiller(), say=lambda _l: None)
    assert record.checkpoint("C4").ok is True
    assert record.checkpoint("C5").ok is False
    assert "never moves" in record.checkpoint("C5").evidence["reason"]


def test_an_over_budget_run_fails_even_with_every_checkpoint_green():
    record = _run(budget_s=0.001)
    for cid in MANDATORY:
        assert record.checkpoint(cid).ok is True
    assert record.verdict == "ci-fail"
    assert "budget" in record.verdict_reason


def test_a_skipped_checkpoint_is_a_fail_not_an_omission():
    record = _run(target=MockModeTarget())
    for cid in MANDATORY:
        assert record.checkpoint(cid) is not None
    assert record.checkpoint("C3").ok is False


# ---------------------------------------------------------------------------
# the wire keys themselves
# ---------------------------------------------------------------------------


def test_the_mock_never_sends_a_networks_key():
    """``microduck_driver.py:233`` reads one; ``SubscribeResult`` has no such key."""
    assert "networks" not in mock_robotd.SUBSCRIBE_RESULT
    assert wire.SUB_ACCEPTED in mock_robotd.SUBSCRIBE_RESULT
    assert wire.SUB_WALK in mock_robotd.SUBSCRIBE_RESULT


def test_the_mock_puts_the_loop_under_control_loop_and_the_battery_on_health():
    """``loop``/``hz`` are state-stream keys; ``robot.health`` uses different ones."""
    health = mock_robotd.HEALTH_RESULT
    assert wire.HEALTH_CONTROL_LOOP in health and "loop" not in health
    loop = health[wire.HEALTH_CONTROL_LOOP]
    assert wire.LOOP_TARGET_HZ in loop and "hz" not in loop
    assert wire.HEALTH_BATTERY in health


def test_every_wire_constant_names_its_source():
    for name in ("HelloResult", "HealthResult", "LoopHealth", "Battery", "SubscribeResult"):
        assert name in wire.SOURCES
        assert wire.SOURCES[name].startswith("duck-ipc-proto/src/lib.rs:")


def test_missing_keys_reports_everything_when_the_payload_is_not_an_object():
    assert wire.missing_keys(None, wire.HEALTH_REQUIRED) == list(wire.HEALTH_REQUIRED)
    assert wire.missing_keys({"healthy": True}, wire.HEALTH_REQUIRED) == [
        wire.HEALTH_CONTROL_LOOP,
        wire.HEALTH_BATTERY,
    ]


# ---------------------------------------------------------------------------
# EvalLog v1
# ---------------------------------------------------------------------------


def test_the_evallog_export_is_schema_v1_and_carries_the_checkpoints(tmp_path):
    record = _run()
    path = evallog_mod.write_eval_log(record, tmp_path / "log.json")
    log = json.loads(path.read_text())

    assert log["version"] == 1
    assert log["eval"]["task"] == BENCHMARK
    assert log["eval"]["embodiment"] == "microduck"
    assert log["eval"]["seed"] is None
    assert log["stats"]["mean_inference_latency_s"] is None
    assert log["stats"]["frames_dir"] is None
    assert log["results"]["metrics"]["checkpoints_passed"] == 6.0

    sample = log["samples"][0]
    assert sample["scene_id"] == "A-castor-duck"
    ids = [trial["checkpoint"] for trial in sample["trial_metadata"]]
    assert ids[: len(MANDATORY)] == list(MANDATORY)


def test_the_eight_parallel_arrays_agree(tmp_path):
    record = _run()
    log = json.loads(evallog_mod.write_eval_log(record, tmp_path / "log.json").read_text())
    sample = log["samples"][0]
    width = len(sample["epochs"])
    for key in (
        "operator_judgements",
        "judgement_sources",
        "operator_notes",
        "operator_messages",
        "trial_metadata",
        "termination_reasons",
        "policy_transcripts",
    ):
        assert len(sample[key]) == width, f"{key} is {len(sample[key])} against {width} epochs"


def test_no_success_scorer_without_motion_evidence(tmp_path):
    """The refusal carried over from the app: acceptance is not motion."""
    record = _run()
    log = json.loads(evallog_mod.write_eval_log(record, tmp_path / "log.json").read_text())
    metrics = log["results"]["metrics"]
    assert "success_at_end" not in metrics, "C7 did not run, so nothing may claim success"
    for epoch in log["samples"][0]["epochs"]:
        assert "success_at_end" not in epoch


def test_an_errored_trial_is_an_empty_epoch_beside_a_null_termination(tmp_path):
    record = _run()
    log = json.loads(evallog_mod.write_eval_log(record, tmp_path / "log.json").read_text())
    sample = log["samples"][0]
    empties = sum(1 for epoch in sample["epochs"] if epoch == {})
    nulls = sum(1 for reason in sample["termination_reasons"] if reason is None)
    assert empties == nulls == log["results"]["errored_trials"]


def test_a_failed_run_exports_as_status_error(tmp_path):
    record = _run(target=MockModeTarget())
    log = json.loads(evallog_mod.write_eval_log(record, tmp_path / "log.json").read_text())
    assert log["status"] == "error"
    assert log["error"]
    assert log["samples"][0]["status"] == "error"


def test_the_evallog_is_written_the_way_upstream_redumps_it(tmp_path):
    """No trailing newline: their round trip is json.dumps(indent=2, sort_keys=True)."""
    record = _run()
    path = evallog_mod.write_eval_log(record, tmp_path / "log.json")
    text = path.read_text()
    assert not text.endswith("\n")
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# targets and the CLI surface
# ---------------------------------------------------------------------------


def test_the_mock_target_is_refused_without_ci():
    with pytest.raises(BenchError, match="refused without --ci"):
        build_target(transport="mock", ci=False)


def test_webrtc_says_why_it_cannot_be_used_yet():
    with pytest.raises(TargetUnavailable, match="no webrtc transport"):
        RealTarget(transport_kind="webrtc", host="192.168.1.42")


def test_a_tcp_target_warns_about_the_bridges_own_deadman():
    target = RealTarget(transport_kind="tcp", host="192.168.1.42")
    assert target.driver_config["port"] == 7788
    assert any("700 ms" in note for note in target.notes)


def test_ssh_without_a_host_is_refused_rather_than_discovered():
    with pytest.raises(TargetUnavailable, match="needs --host"):
        RealTarget(transport_kind="ssh")


def test_the_sim_target_says_exactly_what_is_missing():
    from castor.bench.targets import SimTarget

    sim = SimTarget(repo="/nonexistent/microduck", rl="/nonexistent/microduck_rl")
    gaps = sim.missing()
    assert gaps
    assert "duck-sim" in gaps[0]


def test_rc_car_is_a_stub_that_still_names_its_checkpoints():
    spec = ROBOTS["rc-car"]
    assert spec.implemented is False
    assert set(spec.checkpoints) == {"T0", "C1", "C2", "C3", "C4", "C5", "C6", "C7"}
    assert "PCA9685" in spec.checkpoints["C2"]

    with pytest.raises(BenchError, match="not implemented"):
        run(robot="rc-car", target=MockTarget(in_process=True))


def test_an_unknown_robot_is_refused():
    with pytest.raises(BenchError, match="unknown robot"):
        run(robot="tortoise", target=MockTarget(in_process=True))


def test_the_cli_registers_bench_ten_minutes():
    import argparse

    from castor.bench.command import add_parser

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_parser(sub)
    args = parser.parse_args(["bench", "ten-minutes", "--robot", "microduck", "--ci"])
    assert args.command == "bench"
    assert args.bench_cmd == "ten-minutes"
    assert args.ci is True
    assert args.budget_s == 600.0
