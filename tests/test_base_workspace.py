"""Workspace limits for a mobile base (castor.safety.workspace) and their use in SafetyLayer."""

import math

import pytest

from castor.fs.namespace import Namespace
from castor.fs.permissions import PermissionTable
from castor.fs.safety import POLICIES, SafetyLayer
from castor.safety.workspace import BaseWorkspace, BaseWorkspacePolicy

BAY = [(0.0, 0.0), (6.0, 0.0), (6.0, 4.0), (0.0, 4.0)]
KEEP_OUT = [[(3.0, 1.5), (4.0, 1.5), (4.0, 2.5), (3.0, 2.5)]]


class Localizer:
    """Stands in for independent localization: x, y, heading, speed."""

    def __init__(self, pose=(1.0, 2.0, 0.0, 0.0)):
        self.current = pose

    def pose(self):
        return self.current


def _policy(pose=(1.0, 2.0, 0.0, 0.0), keep_out=None):
    loc = Localizer(pose)
    ws = BaseWorkspace(
        keep_in=BAY,
        keep_out=keep_out or [],
        top_speed_mps=1.5,
        max_decel_mps2=1.0,
        reaction_s=0.05,
        margin_m=0.05,
    )
    return BaseWorkspacePolicy(ws, loc.pose), loc


def _safety(policy):
    sl = SafetyLayer(
        Namespace(), PermissionTable(), limits={"motor_rate_hz": 1000.0}, workspace_policy=policy
    )
    sl.ns.write("/dev/motor", {"type": "move", "linear": 0.0, "angular": 0.0})
    return sl


# ----- the policy on its own --------------------------------------------------------------------
def test_move_with_room_to_stop_is_allowed():
    policy, _ = _policy(pose=(1.0, 2.0, 0.0, 0.0))
    assert policy.check({"linear": 0.3, "angular": 0.0}) == (True, "")


def test_move_whose_stopping_path_leaves_the_bay_is_refused():
    policy, _ = _policy(pose=(5.7, 2.0, 0.0, 0.0))  # 0.3 m from the wall, facing it
    allowed, why = policy.check({"linear": 0.5, "angular": 0.0})  # 0.75 m/s: stops in 0.37 m
    assert not allowed
    assert "leaves the workspace" in why


def test_reversing_checks_behind_the_robot():
    policy, _ = _policy(pose=(0.3, 2.0, 0.0, 0.0))  # wall behind
    assert policy.check({"linear": 0.5})[0]
    assert not policy.check({"linear": -0.5})[0]


def test_current_speed_counts_even_when_the_command_is_slow():
    policy, _ = _policy(pose=(5.0, 2.0, 0.0, 1.4))  # already doing 1.4 m/s toward the wall
    assert not policy.check({"linear": 0.05})[0]


def test_keep_out_is_respected():
    policy, _ = _policy(pose=(2.7, 2.0, 0.0, 0.0), keep_out=KEEP_OUT)  # 0.3 m short of the keep-out
    assert not policy.check({"linear": 0.5})[0]
    assert policy.check({"linear": -0.5})[0]


def test_turning_in_place_needs_no_pose():
    policy, loc = _policy()
    loc.current = None
    assert policy.check({"linear": 0.0, "angular": 0.8}) == (True, "")


@pytest.mark.parametrize("pose", [None, (math.nan, 1.0, 0.0, 0.0), ("a", 1, 0, 0), (1.0, 2.0)])
def test_no_usable_pose_refuses_motion(pose):
    policy, loc = _policy()
    loc.current = pose
    allowed, why = policy.check({"linear": 0.2})
    assert not allowed
    assert "no pose" in why


def test_localizer_that_raises_refuses_motion():
    def broken():
        raise RuntimeError("localizer crashed")

    ws = BaseWorkspace(keep_in=BAY)
    assert not BaseWorkspacePolicy(ws, broken).check({"linear": 0.2})[0]


def test_dict_pose_is_accepted():
    ws = BaseWorkspace(keep_in=BAY, top_speed_mps=1.5)
    policy = BaseWorkspacePolicy(ws, lambda: {"x": 1.0, "y": 2.0, "theta": 0.0, "v": 0.0})
    assert policy.check({"linear": 0.3})[0]


def test_bad_workspace_is_rejected_at_construction():
    with pytest.raises(ValueError):
        BaseWorkspace(keep_in=[(0, 0), (1, 0)])
    with pytest.raises(ValueError):
        BaseWorkspace(keep_in=BAY, max_decel_mps2=0.0)


# ----- inside SafetyLayer -----------------------------------------------------------------------
def test_refused_move_writes_zero_translation_not_the_old_command():
    policy, _ = _policy(pose=(5.7, 2.0, 0.0, 0.0))
    sl = _safety(policy)
    sl.ns.write(
        "/dev/motor", {"type": "move", "linear": 0.4, "angular": 0.0}
    )  # last command running
    refused = sl.write(
        "/dev/motor", {"type": "move", "linear": 0.5, "angular": 0.3}, principal="brain"
    )
    assert refused is False
    assert sl.ns.read("/dev/motor") == {"type": "move", "linear": 0.0, "angular": 0.3}
    assert sl.last_write_denial.startswith("Workspace:")


def test_allowed_move_is_written_clamped():
    policy, _ = _policy(pose=(1.0, 2.0, 0.0, 0.0))
    sl = _safety(policy)
    assert sl.write(
        "/dev/motor", {"type": "move", "linear": 0.3, "angular": 0.0}, principal="brain"
    )
    assert sl.ns.read("/dev/motor")["linear"] == pytest.approx(0.3)


def test_stops_always_go_through():
    policy, loc = _policy()
    loc.current = None  # even with no pose
    sl = _safety(policy)
    assert sl.write("/dev/motor", {"type": "stop"}, principal="brain")
    assert sl.write("/dev/motor", {"type": "move", "linear": 0, "angular": 0}, principal="brain")


def test_without_a_policy_behaviour_is_unchanged():
    sl = SafetyLayer(Namespace(), PermissionTable(), limits={"motor_rate_hz": 1000.0})
    assert sl.write(
        "/dev/motor", {"type": "move", "linear": 0.5, "angular": 0.0}, principal="brain"
    )


def test_policy_switch():
    policy, _ = _policy(pose=(5.7, 2.0, 0.0, 0.0))
    sl = _safety(policy)
    POLICIES["workspace_motor"]["enabled"] = False
    try:
        assert sl.write("/dev/motor", {"type": "move", "linear": 0.5}, principal="brain")
    finally:
        POLICIES["workspace_motor"]["enabled"] = True


# ----- the standing command ---------------------------------------------------------------------
def test_refused_write_rechecks_the_standing_command():
    """A write refused for any reason used to leave the previous command running unchecked. The
    EV-03 hostile-model test found this: the role rate limit refused writes while an earlier,
    then-safe full-speed command drove the rover out of its bay."""
    policy, loc = _policy(pose=(1.0, 2.0, 0.0, 0.0))
    sl = _safety(policy)
    assert sl.write(
        "/dev/motor", {"type": "move", "linear": 0.4, "angular": 0.0}, principal="brain"
    )
    loc.current = (5.85, 2.0, 0.0, 0.6)  # the robot has driven up to the wall since
    assert sl.write("/dev/motor", {"type": "move", "linear": 0.4}, principal="nobody") is False
    assert sl.ns.read("/dev/motor") == {"type": "move", "linear": 0.0, "angular": 0.0}


def test_enforce_workspace_leaves_a_safe_command_alone():
    policy, _ = _policy(pose=(1.0, 2.0, 0.0, 0.0))
    sl = _safety(policy)
    sl.ns.write("/dev/motor", {"type": "move", "linear": 0.3, "angular": 0.2})
    assert sl.enforce_workspace() is True
    assert sl.ns.read("/dev/motor")["linear"] == 0.3


def test_enforce_workspace_replaces_an_unsafe_command():
    policy, _ = _policy(pose=(5.85, 2.0, 0.0, 0.5))
    sl = _safety(policy)
    sl.ns.write("/dev/motor", {"type": "move", "linear": 0.3, "angular": 0.2})
    assert sl.enforce_workspace() is False
    assert sl.ns.read("/dev/motor") == {"type": "move", "linear": 0.0, "angular": 0.2}
    events = [row.get("event") for row in sl.ns.read("/var/log/safety") or []]
    assert "workspace_enforced" in events


def test_enforce_workspace_stops_an_invalid_standing_command():
    policy, _ = _policy()
    sl = _safety(policy)
    sl.ns.write("/dev/motor", {"type": "move", "linear": math.nan, "angular": 0.0})
    assert sl.enforce_workspace() is False
    assert sl.ns.read("/dev/motor") == {"type": "stop"}


def test_enforce_workspace_without_a_policy_does_nothing():
    sl = SafetyLayer(Namespace(), PermissionTable(), limits={"motor_rate_hz": 1000.0})
    sl.ns.write("/dev/motor", {"type": "move", "linear": 0.9})
    assert sl.enforce_workspace() is True
    assert sl.ns.read("/dev/motor")["linear"] == 0.9
