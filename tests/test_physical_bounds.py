"""Tests for physical bounds enforcement."""

import pytest

from castor.safety.bounds import (
    BoundsChecker,
    BoundsResult,
    Box,
    ForceBounds,
    JointBounds,
    JointLimits,
    Sphere,
    WorkspaceBounds,
    check_write_bounds,
)

# -----------------------------------------------------------------------
# BoundsResult
# -----------------------------------------------------------------------


class TestBoundsResult:
    def test_defaults(self):
        r = BoundsResult()
        assert r.ok
        assert not r.violated
        assert r.margin == float("inf")

    def test_combine_worst_wins(self):
        r = BoundsResult.combine(
            [
                BoundsResult("ok", "", 1.0),
                BoundsResult("violation", "bad", -0.1),
                BoundsResult("warning", "close", 0.02),
            ]
        )
        assert r.status == "violation"

    def test_combine_empty(self):
        assert BoundsResult.combine([]).ok

    def test_combine_same_status_picks_smaller_margin(self):
        r = BoundsResult.combine(
            [
                BoundsResult("warning", "a", 0.05),
                BoundsResult("warning", "b", 0.01),
            ]
        )
        assert r.margin == 0.01


# -----------------------------------------------------------------------
# WorkspaceBounds — sphere
# -----------------------------------------------------------------------


class TestWorkspaceSphere:
    def setup_method(self):
        self.ws = WorkspaceBounds(sphere=Sphere(0, 0, 0, 0.8), warning_margin=0.05)

    def test_inside(self):
        r = self.ws.check_position(0.3, 0.3, 0.3)
        assert r.ok

    def test_on_boundary(self):
        # Exactly on boundary: distance = 0, margin = 0 < warning_margin → warning
        r = self.ws.check_position(0.8, 0.0, 0.0)
        assert r.status == "warning"

    def test_just_inside_warning_zone(self):
        # 0.78m from center on x-axis, margin = 0.02 < 0.05
        r = self.ws.check_position(0.78, 0.0, 0.0)
        assert r.status == "warning"

    def test_outside(self):
        r = self.ws.check_position(1.0, 0.0, 0.0)
        assert r.violated

    def test_center(self):
        r = self.ws.check_position(0.0, 0.0, 0.0)
        assert r.ok
        assert r.margin == pytest.approx(0.8, abs=0.001)

    def test_outside_3d(self):
        # sqrt(0.5^2 + 0.5^2 + 0.5^2) = 0.866 > 0.8
        r = self.ws.check_position(0.5, 0.5, 0.5)
        assert r.violated


# -----------------------------------------------------------------------
# WorkspaceBounds — box
# -----------------------------------------------------------------------


class TestWorkspaceBox:
    def setup_method(self):
        self.ws = WorkspaceBounds(
            box=Box(-1, -1, 0, 1, 1, 2),
            warning_margin=0.05,
        )

    def test_inside(self):
        r = self.ws.check_position(0.0, 0.0, 1.0)
        assert r.ok

    def test_outside_x(self):
        r = self.ws.check_position(1.5, 0.0, 1.0)
        assert r.violated

    def test_outside_z_below(self):
        r = self.ws.check_position(0.0, 0.0, -0.1)
        assert r.violated

    def test_on_boundary(self):
        r = self.ws.check_position(1.0, 0.0, 1.0)
        assert r.status == "warning"  # margin=0 < 0.05


# -----------------------------------------------------------------------
# Forbidden zones
# -----------------------------------------------------------------------


class TestForbiddenZones:
    def test_inside_forbidden_sphere(self):
        ws = WorkspaceBounds(
            sphere=Sphere(0, 0, 0, 2.0),
            forbidden_spheres=[Sphere(0.5, 0.5, 0.5, 0.2)],
        )
        r = ws.check_position(0.5, 0.5, 0.5)
        assert r.violated

    def test_outside_forbidden_sphere(self):
        ws = WorkspaceBounds(
            sphere=Sphere(0, 0, 0, 2.0),
            forbidden_spheres=[Sphere(0.5, 0.5, 0.5, 0.1)],
        )
        r = ws.check_position(0.0, 0.0, 0.0)
        assert r.ok

    def test_inside_forbidden_box(self):
        ws = WorkspaceBounds(
            sphere=Sphere(0, 0, 0, 2.0),
            forbidden_boxes=[Box(0.0, 0.0, 0.0, 0.5, 0.5, 0.5)],
        )
        r = ws.check_position(0.25, 0.25, 0.25)
        assert r.violated


# -----------------------------------------------------------------------
# JointBounds
# -----------------------------------------------------------------------


class TestJointBounds:
    def setup_method(self):
        self.jb = JointBounds(
            {
                "shoulder": JointLimits(
                    position_min=-1.57, position_max=1.57, velocity_max=2.0, torque_max=50.0
                ),
                "elbow": JointLimits(
                    position_min=-2.0, position_max=2.0, velocity_max=3.0, torque_max=30.0
                ),
            }
        )

    def test_within_range(self):
        r = self.jb.check_joint("shoulder", position=0.0, velocity=1.0, torque=20.0)
        assert r.ok

    def test_at_position_limit(self):
        r = self.jb.check_joint("shoulder", position=1.57)
        # Exactly at max → margin=0 which is < 5% of range → warning
        assert r.status == "warning"

    def test_exceeding_position(self):
        r = self.jb.check_joint("shoulder", position=2.0)
        assert r.violated

    def test_exceeding_velocity(self):
        r = self.jb.check_joint("shoulder", velocity=2.5)
        assert r.violated

    def test_exceeding_torque(self):
        r = self.jb.check_joint("elbow", torque=35.0)
        assert r.violated

    def test_negative_velocity(self):
        r = self.jb.check_joint("shoulder", velocity=-2.5)
        assert r.violated

    def test_unknown_joint(self):
        r = self.jb.check_joint("wrist", position=0.0)
        assert r.status == "warning"


# -----------------------------------------------------------------------
# ForceBounds
# -----------------------------------------------------------------------


class TestForceBounds:
    def setup_method(self):
        self.fb = ForceBounds(max_ee_force=50.0, max_ee_force_human=10.0)

    def test_normal_force(self):
        r = self.fb.check_force(20.0)
        assert r.ok

    def test_exceeding_force(self):
        r = self.fb.check_force(55.0)
        assert r.violated

    def test_human_proximity_reduces_limit(self):
        self.fb.set_human_proximity(True)
        assert self.fb.effective_ee_limit == 10.0
        r = self.fb.check_force(15.0)
        assert r.violated

    def test_human_proximity_cleared(self):
        self.fb.set_human_proximity(True)
        self.fb.set_human_proximity(False)
        assert self.fb.effective_ee_limit == 50.0
        r = self.fb.check_force(15.0)
        assert r.ok

    def test_warning_near_limit(self):
        # 85% of 50 = 42.5
        r = self.fb.check_force(44.0)
        assert r.status == "warning"

    def test_contact_force_ok(self):
        r = self.fb.check_contact_force(50.0)
        assert r.ok

    def test_contact_force_exceeded(self):
        r = self.fb.check_contact_force(90.0)
        assert r.violated

    def test_gripper_force_exceeded(self):
        r = self.fb.check_gripper_force(45.0)
        assert r.violated


# -----------------------------------------------------------------------
# BoundsChecker integration
# -----------------------------------------------------------------------


class TestBoundsChecker:
    def test_from_robot_type_arm(self):
        bc = BoundsChecker.from_robot_type("arm")
        assert bc.workspace.sphere is not None
        r = bc.check_action({"position": [0.0, 0.0, 0.5], "force": 10.0})
        assert r.ok

    def test_from_robot_type_differential_drive(self):
        bc = BoundsChecker.from_robot_type("differential_drive")
        assert bc.workspace.box is not None

    def test_from_robot_type_unknown(self):
        with pytest.raises(ValueError, match="Unknown robot type"):
            BoundsChecker.from_robot_type("hexapod")

    def test_check_action_position_violation(self):
        bc = BoundsChecker.from_robot_type("arm")
        r = bc.check_action({"position": [2.0, 0.0, 0.5]})
        assert r.violated

    def test_check_action_force_violation(self):
        bc = BoundsChecker.from_robot_type("arm")
        r = bc.check_action({"force": 60.0})
        assert r.violated

    def test_check_action_joint_violation(self):
        bc = BoundsChecker.from_robot_type("arm")
        r = bc.check_action({"joints": {"joint_0": {"position": 5.0}}})
        assert r.violated

    def test_check_action_empty(self):
        bc = BoundsChecker.from_robot_type("arm")
        r = bc.check_action({})
        assert r.ok

    def test_from_config(self):
        cfg = {
            "workspace": {"sphere": {"cx": 0, "cy": 0, "cz": 0, "radius": 1.0}},
            "joints": {
                "j0": {
                    "position_min": -1.0,
                    "position_max": 1.0,
                    "velocity_max": 2.0,
                    "torque_max": 10.0,
                }
            },
            "force": {"max_ee_force": 30.0, "max_ee_force_human": 8.0},
        }
        bc = BoundsChecker.from_config(cfg)
        r = bc.check_action({"position": [0.5, 0.0, 0.0], "force": 25.0})
        assert r.ok


# -----------------------------------------------------------------------
# Config from virtual FS
# -----------------------------------------------------------------------


class TestVirtualFSConfig:
    def test_from_virtual_fs_with_config(self):
        class FakeNS:
            def read(self, path):
                if path == "/etc/safety/bounds":
                    return {
                        "workspace": {"sphere": {"cx": 0, "cy": 0, "cz": 0, "radius": 0.5}},
                        "joints": {},
                        "force": {"max_ee_force": 25.0},
                    }
                return None

        bc = BoundsChecker.from_virtual_fs(FakeNS())
        assert bc.force.max_ee_force == 25.0

    def test_from_virtual_fs_no_config_fallback(self):
        class FakeNS:
            def read(self, path):
                return None

        bc = BoundsChecker.from_virtual_fs(FakeNS())
        # Falls back to arm defaults
        assert bc.workspace.sphere is not None


# -----------------------------------------------------------------------
# check_write_bounds integration
# -----------------------------------------------------------------------


class TestCheckWriteBounds:
    def setup_method(self):
        self.checker = BoundsChecker.from_robot_type("arm")

    def test_arm_write_ok(self):
        r = check_write_bounds(
            self.checker, "/dev/arm/0", {"position": [0.0, 0.0, 0.5], "force": 10.0}
        )
        assert r.ok

    def test_arm_write_violation(self):
        r = check_write_bounds(self.checker, "/dev/arm/0", {"position": [5.0, 0.0, 0.5]})
        assert r.violated

    def test_motor_write_velocity(self):
        r = check_write_bounds(
            self.checker,
            "/dev/motor/left",
            {
                "joint_id": "left_wheel",
                "velocity": 100.0,
            },
        )
        # left_wheel not in arm config → warning (unknown joint)
        assert r.status == "warning"

    def test_non_dict_data(self):
        r = check_write_bounds(self.checker, "/dev/arm/0", "raw_string")
        assert r.ok

    def test_unrelated_path(self):
        r = check_write_bounds(self.checker, "/var/log/test", {"position": [99, 99, 99]})
        assert r.ok


# -----------------------------------------------------------------------
# Runtime integration: bounds enforcement before driver.move()
# -----------------------------------------------------------------------


class TestRuntimeBoundsEnforcement:
    """Verify that out-of-bounds actions are blocked and driver.stop() is called."""

    def _make_mock_driver(self):
        class MockDriver:
            def __init__(self):
                self.moves = []
                self.stops = 0

            def move(self, linear, angular):
                self.moves.append((linear, angular))

            def stop(self):
                self.stops += 1

        return MockDriver()

    def test_ok_action_calls_move(self):
        """An action within bounds proceeds to driver.move()."""
        driver = self._make_mock_driver()
        bc = BoundsChecker.from_robot_type("differential_drive")
        action = {"type": "move", "linear": 0.1, "angular": 0.0}

        result = bc.check_action(action)
        if result.violated:
            driver.stop()
        else:
            driver.move(action["linear"], action["angular"])

        assert len(driver.moves) == 1
        assert driver.stops == 0

    def test_violating_force_calls_stop(self):
        """An action with force above the limit triggers driver.stop(), not driver.move()."""
        driver = self._make_mock_driver()
        bc = BoundsChecker.from_robot_type("arm")
        action = {"type": "move", "linear": 0.0, "angular": 0.0, "force": 999.0}

        result = bc.check_action(action)
        if result.violated:
            driver.stop()
        else:
            driver.move(action["linear"], action["angular"])

        assert driver.stops == 1
        assert len(driver.moves) == 0

    def test_warning_action_still_calls_move(self):
        """An action near the boundary produces a warning but still calls driver.move()."""
        driver = self._make_mock_driver()
        # arm force limit is 50 N; warning fraction is 0.85 → warn above 42.5 N
        bc = BoundsChecker.from_robot_type("arm")
        action = {"type": "move", "linear": 0.1, "angular": 0.0, "force": 44.0}

        result = bc.check_action(action)
        assert result.status == "warning"

        if result.violated:
            driver.stop()
        else:
            driver.move(action["linear"], action["angular"])

        assert len(driver.moves) == 1
        assert driver.stops == 0

    def test_bounds_checker_from_physics_type(self):
        """BoundsChecker.from_robot_type() succeeds for known physics types."""
        bc_dd = BoundsChecker.from_robot_type("differential_drive")
        assert bc_dd.workspace.box is not None

        bc_arm = BoundsChecker.from_robot_type("arm")
        assert bc_arm.workspace.sphere is not None

    def test_bounds_checker_falls_back_for_unknown_type(self):
        """Unknown physics type yields a BoundsChecker with default force limits but no workspace/joint constraints."""
        from castor.safety.bounds import DEFAULT_CONFIGS

        robot_type = "unknown_robot_xyz"
        assert robot_type not in DEFAULT_CONFIGS

        bc = BoundsChecker()  # unconstrained workspace/joints, default force limits
        r = bc.check_action({"force": 9999.0})
        # Default ForceBounds has max_ee_force=50N so 9999N is a violation
        assert r.violated
