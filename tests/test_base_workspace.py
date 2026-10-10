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


@pytest.mark.parametrize(
    "limits",
    [
        {"reaction_s": -0.05},
        {"reaction_s": math.nan},
        {"margin_m": -0.01},
        {"margin_m": math.inf},
        {"top_speed_mps": True},
        {"max_decel_mps2": math.inf},
        {"reaction_s": "0.05"},
    ],
)
def test_every_limit_is_validated_at_construction(limits):
    """A negative or non-finite reaction time or margin would shorten the stopping path or make it
    NaN, and a NaN comparison never refuses; a bool is not a speed."""
    with pytest.raises(ValueError):
        BaseWorkspace(keep_in=BAY, **limits)


# ----- the path: both directions, exact edges, a margin on every side ---------------------------
def test_a_reversal_also_checks_the_way_the_base_is_still_moving():
    """Moving forward at 1 m/s, 0.5 m from the wall ahead, and told to reverse: the base keeps
    going forward while it reacts and brakes (0.05 m + 0.5 m), so it reaches the wall."""
    policy, _ = _policy(pose=(5.5, 2.0, 0.0, 1.0))
    allowed, why = policy.check({"linear": -1.0})
    assert not allowed
    assert "leaves the workspace" in why
    policy, _ = _policy(pose=(5.7, 2.0, 0.0, 1.0))
    assert not policy.check({"linear": -0.2})[0]


def test_reversing_away_from_a_wall_behind_is_still_allowed():
    policy, _ = _policy(pose=(5.0, 2.0, 0.0, 0.0))  # 1 m from the wall ahead, standing still
    assert policy.check({"linear": -0.3})[0]


def test_a_narrow_keep_out_cannot_hide_between_samples():
    """The old check sampled eight points; a 2 cm keep-out between two of them was missed."""
    sliver = [[(1.17, 1.9), (1.19, 1.9), (1.19, 2.1), (1.17, 2.1)]]
    policy, _ = _policy(pose=(1.0, 2.0, 0.0, 0.0), keep_out=sliver)
    # 1 m/s: a 0.60 m path with the margin, sampled every 7.5 cm at x = 1.075, 1.15, 1.225 ...
    assert not policy.check({"linear": 1.0 / 1.5})[0]


def test_a_concave_keep_in_is_checked_along_the_whole_path():
    """A U-shaped bay with a 10 cm slot between its arms: both ends of the path are inside, and
    the old samples (every 16 cm) landed either side of the slot."""
    u_bay = [(0, 0), (3, 0), (3, 3), (1.1, 3), (1.1, 1), (1, 1), (1, 3), (0, 3)]
    ws = BaseWorkspace(keep_in=u_bay, top_speed_mps=1.5, max_decel_mps2=1.0, margin_m=0.05)
    policy = BaseWorkspacePolicy(ws, lambda: (0.5, 2.0, 0.0, 1.5))  # in the left arm, heading +x
    assert not policy.check({"linear": 1.0})[0]  # its stopping path crosses the slot


def test_the_margin_applies_sideways_too():
    """A path along the wall 3 cm away was accepted with a 5 cm margin: the margin was only added
    to the length of the path. Now no point of the path may come within the margin of an edge."""
    policy, _ = _policy(pose=(1.0, 0.03, 0.0, 0.0))  # 3 cm from the y = 0 wall, heading along it
    assert not policy.check({"linear": 0.3})[0]
    policy, _ = _policy(pose=(1.0, 0.10, 0.0, 0.0))  # 10 cm away: clear of the margin
    assert policy.check({"linear": 0.3})[0]
    # 10 cm away, angled in: the path (0.12 m) ends 3 cm from the wall. The old check added the
    # margin to the length only, and that point was still 2 mm inside, so it was allowed.
    policy, _ = _policy(pose=(1.0, 0.10, -0.6, 0.0))
    assert not policy.check({"linear": 0.3})[0]


def test_inside_the_margin_only_a_move_away_is_allowed():
    policy, _ = _policy(pose=(1.0, 0.03, math.pi / 2, 0.0))  # 3 cm from the wall, facing away
    assert policy.check({"linear": 0.2})[0]
    assert not policy.check({"linear": -0.2})[0]  # backing into it


def test_a_pose_outside_the_workspace_refuses_translation():
    policy, _ = _policy(pose=(6.1, 2.0, math.pi, 0.0))  # 10 cm past the wall, facing back in
    allowed, why = policy.check({"linear": 0.2})
    assert not allowed
    assert "outside the workspace" in why
    assert policy.check({"linear": 0.0, "angular": 0.5})[0]  # turning in place still works


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


def test_a_refused_move_also_stops_the_motors():
    """/dev/motor is only a node: a refusal has to reach the driver, or the last command it was
    given keeps running (found by review on the API's direct-action path)."""
    policy, _ = _policy(pose=(5.7, 2.0, 0.0, 0.0))
    sl = _safety(policy)
    stops = []
    sl.add_motor_halt(lambda: stops.append("stop"))
    assert sl.write("/dev/motor", {"type": "move", "linear": 0.5}, principal="brain") is False
    assert stops == ["stop"]


def test_an_allowed_move_does_not_stop_the_motors():
    policy, _ = _policy(pose=(1.0, 2.0, 0.0, 0.0))
    sl = _safety(policy)
    stops = []
    sl.add_motor_halt(lambda: stops.append("stop"))
    assert sl.write("/dev/motor", {"type": "move", "linear": 0.3}, principal="brain")
    assert stops == []


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


def test_replacing_the_standing_command_also_stops_the_motors():
    policy, _ = _policy(pose=(5.85, 2.0, 0.0, 0.5))
    sl = _safety(policy)
    stops = []
    sl.add_motor_halt(lambda: stops.append("stop"))
    sl.ns.write("/dev/motor", {"type": "move", "linear": 0.3, "angular": 0.2})
    assert sl.enforce_workspace() is False
    assert stops == ["stop"]


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
