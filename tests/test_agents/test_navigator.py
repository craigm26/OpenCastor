"""Tests for NavigatorAgent — potential field planning, RCAN output."""

import asyncio
import time

from castor.agents.base import AgentStatus
from castor.agents.navigator import NavigationPlan, NavigatorAgent, Waypoint
from castor.agents.observer import Detection, SceneGraph
from castor.agents.shared_state import SharedState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_scene(
    detections=None,
    free_space_pct=1.0,
    closest_obstacle_m=None,
    dominant_objects=None,
):
    return SceneGraph(
        timestamp=time.time(),
        detections=detections or [],
        free_space_pct=free_space_pct,
        closest_obstacle_m=closest_obstacle_m,
        dominant_objects=dominant_objects or [],
        raw_sensor_keys=[],
    )


def make_obstacle(distance_m=0.5, cx=0.5):
    """Create an obstacle Detection centred at normalised horizontal position cx."""
    half = 0.1
    return Detection(
        label="person",
        confidence=0.9,
        bbox=(cx - half, 0.2, cx + half, 0.8),
        distance_m=distance_m,
        is_obstacle=True,
    )


def make_non_obstacle(cx=0.5):
    half = 0.1
    return Detection(
        label="book",
        confidence=0.7,
        bbox=(cx - half, 0.2, cx + half, 0.8),
        distance_m=1.0,
        is_obstacle=False,
    )


def act(agent, context=None):
    """Run agent.act synchronously."""
    return asyncio.run(agent.act(context or {}))


def observe(agent, sensor_data=None):
    """Run agent.observe synchronously."""
    return asyncio.run(agent.observe(sensor_data or {}))


# ---------------------------------------------------------------------------
# Waypoint dataclass
# ---------------------------------------------------------------------------


class TestWaypoint:
    def test_fields_accessible(self):
        wp = Waypoint(x=0.5, y=0.8, heading_deg=30.0, confidence=0.9, reason="goal_seeking")
        assert wp.x == 0.5
        assert wp.y == 0.8
        assert wp.heading_deg == 30.0
        assert wp.confidence == 0.9
        assert wp.reason == "goal_seeking"

    def test_reason_values(self):
        for reason in ("goal_seeking", "obstacle_avoidance", "idle"):
            wp = Waypoint(x=0.0, y=1.0, heading_deg=0.0, confidence=0.5, reason=reason)
            assert wp.reason == reason


# ---------------------------------------------------------------------------
# NavigationPlan dataclass
# ---------------------------------------------------------------------------


class TestNavigationPlan:
    def test_default_waypoints_empty(self):
        plan = NavigationPlan()
        assert plan.waypoints == []

    def test_default_distance_zero(self):
        plan = NavigationPlan()
        assert plan.estimated_distance_m == 0.0

    def test_default_not_blocked(self):
        plan = NavigationPlan()
        assert plan.is_blocked is False

    def test_default_replan_reason_none(self):
        plan = NavigationPlan()
        assert plan.replan_reason is None


# ---------------------------------------------------------------------------
# Init / config
# ---------------------------------------------------------------------------


class TestNavigatorInit:
    def test_name_attribute(self):
        agent = NavigatorAgent()
        assert agent.name == "navigator"

    def test_default_max_speed(self):
        agent = NavigatorAgent()
        assert agent._max_speed == 0.6

    def test_custom_max_speed(self):
        agent = NavigatorAgent(config={"max_speed": 0.3})
        assert agent._max_speed == 0.3

    def test_custom_goal(self):
        agent = NavigatorAgent(config={"goal": {"x": 0.5, "y": 0.5}})
        assert agent._goal_x == 0.5
        assert agent._goal_y == 0.5

    def test_default_goal_forward(self):
        agent = NavigatorAgent()
        assert agent._goal_x == 0.0
        assert agent._goal_y == 1.0


# ---------------------------------------------------------------------------
# RCAN action output format
# ---------------------------------------------------------------------------


class TestNavigatorRCANFormat:
    def test_action_key_present(self):
        result = act(NavigatorAgent(), {"scene_graph": make_scene()})
        assert "action" in result

    def test_direction_key_present(self):
        result = act(NavigatorAgent(), {"scene_graph": make_scene()})
        assert "direction" in result

    def test_speed_key_present(self):
        result = act(NavigatorAgent(), {"scene_graph": make_scene()})
        assert "speed" in result

    def test_action_value_is_move(self):
        result = act(NavigatorAgent(), {"scene_graph": make_scene(free_space_pct=0.9)})
        assert result["action"] == "move"

    def test_direction_is_string(self):
        result = act(NavigatorAgent(), {"scene_graph": make_scene()})
        assert isinstance(result["direction"], str)

    def test_speed_is_float(self):
        result = act(NavigatorAgent(), {"scene_graph": make_scene()})
        assert isinstance(result["speed"], float)

    def test_speed_in_valid_range(self):
        result = act(NavigatorAgent(), {"scene_graph": make_scene()})
        assert 0.0 <= result["speed"] <= 1.0

    def test_direction_valid_value(self):
        valid = {"forward", "backward", "left", "right", "stop"}
        result = act(NavigatorAgent(), {"scene_graph": make_scene(free_space_pct=0.9)})
        assert result["direction"] in valid


# ---------------------------------------------------------------------------
# Goal seeking
# ---------------------------------------------------------------------------


class TestNavigatorGoalSeeking:
    def test_clear_path_straight_goes_forward(self):
        result = act(
            NavigatorAgent(),
            {"scene_graph": make_scene(free_space_pct=1.0), "goal_x": 0.0, "goal_y": 1.0},
        )
        assert result["direction"] == "forward"

    def test_goal_left_gives_left(self):
        result = act(
            NavigatorAgent(),
            {"scene_graph": make_scene(free_space_pct=1.0), "goal_x": -1.0, "goal_y": 0.0},
        )
        assert result["direction"] == "left"

    def test_goal_right_gives_right(self):
        result = act(
            NavigatorAgent(),
            {"scene_graph": make_scene(free_space_pct=1.0), "goal_x": 1.0, "goal_y": 0.0},
        )
        assert result["direction"] == "right"

    def test_speed_positive_when_moving(self):
        result = act(
            NavigatorAgent(),
            {"scene_graph": make_scene(free_space_pct=1.0), "goal_x": 0.0, "goal_y": 1.0},
        )
        assert result["speed"] > 0.0

    def test_speed_capped_by_max_speed(self):
        result = act(
            NavigatorAgent(config={"max_speed": 0.4}),
            {"scene_graph": make_scene(free_space_pct=1.0), "goal_x": 0.0, "goal_y": 1.0},
        )
        assert result["speed"] <= 0.4

    def test_plan_goal_seeking_reason(self):
        agent = NavigatorAgent()
        scene = make_scene(free_space_pct=1.0)
        plan = agent._plan(scene, 0.0, 1.0)
        assert plan.waypoints[0].reason == "goal_seeking"


# ---------------------------------------------------------------------------
# Obstacle avoidance
# ---------------------------------------------------------------------------


class TestNavigatorObstacleAvoidance:
    def test_blocked_when_low_free_space(self):
        agent = NavigatorAgent()
        scene = make_scene(free_space_pct=0.1)
        plan = agent._plan(scene, 0.0, 1.0)
        assert plan.is_blocked is True

    def test_replan_reason_set_when_blocked(self):
        agent = NavigatorAgent()
        scene = make_scene(free_space_pct=0.1)
        plan = agent._plan(scene, 0.0, 1.0)
        assert plan.replan_reason == "path_blocked"

    def test_not_blocked_when_clear(self):
        agent = NavigatorAgent()
        scene = make_scene(free_space_pct=0.9)
        plan = agent._plan(scene, 0.0, 1.0)
        assert plan.is_blocked is False

    def test_blocked_stops_robot(self):
        result = act(NavigatorAgent(), {"scene_graph": make_scene(free_space_pct=0.05)})
        assert result["direction"] == "stop"
        assert result["speed"] == 0.0

    def test_obstacle_left_biases_right(self):
        """Obstacle on left side should push waypoint x positive (rightward)."""
        agent = NavigatorAgent()
        det = make_obstacle(distance_m=0.5, cx=0.1)  # obstacle on left
        scene = make_scene(detections=[det], free_space_pct=0.7)
        plan = agent._plan(scene, 0.0, 1.0)
        assert plan.waypoints[0].x >= 0

    def test_obstacle_right_biases_left(self):
        """Obstacle on right side should push waypoint x negative (leftward)."""
        agent = NavigatorAgent()
        det = make_obstacle(distance_m=0.5, cx=0.9)  # obstacle on right
        scene = make_scene(detections=[det], free_space_pct=0.7)
        plan = agent._plan(scene, 0.0, 1.0)
        assert plan.waypoints[0].x <= 0

    def test_non_obstacle_does_not_repel(self):
        """Non-obstacle detections should not trigger obstacle_avoidance."""
        agent = NavigatorAgent()
        no_det_scene = make_scene(free_space_pct=1.0)
        non_obs_scene = make_scene(detections=[make_non_obstacle(cx=0.1)], free_space_pct=1.0)
        plan_no = agent._plan(no_det_scene, 0.0, 1.0)
        plan_non = agent._plan(non_obs_scene, 0.0, 1.0)
        assert plan_no.waypoints[0].reason == "goal_seeking"
        assert plan_non.waypoints[0].reason == "goal_seeking"

    def test_obstacle_avoidance_reason(self):
        """Close obstacle should mark waypoint as obstacle_avoidance."""
        agent = NavigatorAgent()
        det = make_obstacle(distance_m=0.5, cx=0.5)  # centred obstacle
        scene = make_scene(detections=[det], free_space_pct=0.7)
        plan = agent._plan(scene, 0.0, 1.0)
        assert plan.waypoints[0].reason == "obstacle_avoidance"

    def test_plan_has_waypoints(self):
        agent = NavigatorAgent()
        scene = make_scene(free_space_pct=0.9)
        plan = agent._plan(scene, 0.0, 1.0)
        assert len(plan.waypoints) >= 1

    def test_waypoint_x_in_range(self):
        agent = NavigatorAgent()
        plan = agent._plan(make_scene(free_space_pct=0.9), 0.0, 1.0)
        for wp in plan.waypoints:
            assert -1.0 <= wp.x <= 1.0

    def test_waypoint_y_in_range(self):
        agent = NavigatorAgent()
        plan = agent._plan(make_scene(free_space_pct=0.9), 0.0, 1.0)
        for wp in plan.waypoints:
            assert -1.0 <= wp.y <= 1.0


# ---------------------------------------------------------------------------
# No scene / idle
# ---------------------------------------------------------------------------


class TestNavigatorNoScene:
    def test_plan_none_scene_returns_idle_waypoint(self):
        agent = NavigatorAgent()
        plan = agent._plan(None, 0.0, 1.0)
        assert plan.waypoints[0].reason == "idle"

    def test_plan_none_scene_not_blocked(self):
        agent = NavigatorAgent()
        plan = agent._plan(None, 0.0, 1.0)
        assert plan.is_blocked is False

    def test_act_without_scene_stops(self):
        agent = NavigatorAgent()
        agent._state = SharedState()  # empty state
        result = act(agent)
        assert result["action"] == "move"
        assert result["direction"] == "stop"

    def test_observe_returns_dict(self):
        agent = NavigatorAgent()
        result = observe(agent)
        assert isinstance(result, dict)
        assert "scene_graph" in result


# ---------------------------------------------------------------------------
# SharedState integration
# ---------------------------------------------------------------------------


class TestNavigatorSharedState:
    def test_publishes_nav_plan(self):
        state = SharedState()
        agent = NavigatorAgent(shared_state=state)
        state.set("scene_graph", make_scene(free_space_pct=0.9))
        act(agent)
        plan = state.get("nav_plan")
        assert isinstance(plan, NavigationPlan)

    def test_reads_scene_from_shared_state(self):
        state = SharedState()
        agent = NavigatorAgent(shared_state=state)
        state.set("scene_graph", make_scene(free_space_pct=0.9))
        result = act(agent)
        # High free space → should not stop
        assert result["direction"] != "stop"

    def test_status_running_after_act(self):
        state = SharedState()
        agent = NavigatorAgent(shared_state=state)
        act(agent, {"scene_graph": make_scene()})
        assert agent.status == AgentStatus.RUNNING

    def test_goal_override_via_context(self):
        agent = NavigatorAgent(config={"goal": {"x": 0.0, "y": 1.0}})
        scene = make_scene(free_space_pct=1.0)
        result = act(agent, {"scene_graph": scene, "goal_x": 1.0, "goal_y": 0.0})
        assert result["direction"] == "right"


# ---------------------------------------------------------------------------
# Distance estimation
# ---------------------------------------------------------------------------


class TestNavigatorDistanceEstimation:
    def test_distance_from_closest_obstacle(self):
        agent = NavigatorAgent()
        scene = make_scene(closest_obstacle_m=2.0)
        dist = agent._estimate_distance(scene)
        assert dist == 2.0 - agent._min_obstacle_m

    def test_distance_default_when_no_obstacle(self):
        agent = NavigatorAgent()
        scene = make_scene(closest_obstacle_m=None)
        dist = agent._estimate_distance(scene)
        assert dist == 5.0

    def test_distance_non_negative(self):
        agent = NavigatorAgent(config={"min_obstacle_m": 0.4})
        scene = make_scene(closest_obstacle_m=0.1)
        dist = agent._estimate_distance(scene)
        assert dist >= 0.0
