"""NavigatorAgent — potential-field path planner for OpenCastor robots.

Takes a :class:`~castor.agents.observer.SceneGraph` from SharedState
plus a navigation goal, then produces :class:`Waypoint` objects and an
RCAN-compatible action dict via attractive/repulsive potential fields.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .base import AgentStatus, BaseAgent
from .observer import SceneGraph
from .shared_state import SharedState

logger = logging.getLogger("OpenCastor.Agents.Navigator")

# ---- Potential-field tuning constants ----
GOAL_ATTRACT: float = 1.0  # attractive gain
OBSTACLE_REPEL: float = 2.0  # repulsive gain
OBSTACLE_INFLUENCE_M: float = 1.5  # repulsion radius in metres
BLOCKED_THRESHOLD: float = 0.25  # free_space_pct below which path is "blocked"


@dataclass
class Waypoint:
    """A single navigation step in the normalised robot reference frame.

    Attributes:
        x: Lateral offset, normalised [-1 (left), +1 (right)].
        y: Longitudinal offset, normalised [-1 (backward), +1 (forward)].
        heading_deg: Desired heading in degrees (0 = forward, +90 = right).
        confidence: Planner confidence in this waypoint [0, 1].
        reason: Why this waypoint was generated.
            One of ``"obstacle_avoidance"``, ``"goal_seeking"``, ``"idle"``.
    """

    x: float
    y: float
    heading_deg: float
    confidence: float
    reason: str


@dataclass
class NavigationPlan:
    """Full plan produced by one NavigatorAgent tick.

    Attributes:
        waypoints: Ordered list of waypoints to execute.
        estimated_distance_m: Estimated traversable distance to next obstacle.
        is_blocked: True when the planner cannot find a viable path.
        replan_reason: Human-readable explanation when ``is_blocked`` is True.
    """

    waypoints: List[Waypoint] = field(default_factory=list)
    estimated_distance_m: float = 0.0
    is_blocked: bool = False
    replan_reason: Optional[str] = None


class NavigatorAgent(BaseAgent):
    """Potential-field navigator that consumes a SceneGraph and produces Waypoints.

    Reads the current :class:`~castor.agents.observer.SceneGraph` from
    :class:`~castor.agents.shared_state.SharedState` (key ``"scene_graph"``),
    applies a goal-attractive + obstacle-repulsive potential field, and
    publishes a :class:`NavigationPlan` under ``"nav_plan"``.

    :meth:`act` also returns an RCAN-compatible action dict::

        {"action": "move", "direction": "forward", "speed": 0.45}

    Configuration keys (all optional):

    * ``goal`` — dict with ``x``/``y`` in normalised coords (default ``{x:0, y:1}``).
    * ``max_speed`` — float 0-1, caps output speed (default ``0.6``).
    * ``min_obstacle_m`` — minimum safe clearance in metres (default ``0.4``).

    Example::

        state = SharedState()
        observer = ObserverAgent(shared_state=state)
        navigator = NavigatorAgent(config={"max_speed": 0.5}, shared_state=state)

        await observer.observe(sensor_data)
        action = await navigator.act({})
        # {"action": "move", "direction": "forward", "speed": 0.45}
    """

    name = "navigator"

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        shared_state: Optional[SharedState] = None,
    ) -> None:
        super().__init__(config)
        self._state: SharedState = shared_state or SharedState()
        self._max_speed: float = float(self.config.get("max_speed", 0.6))
        self._min_obstacle_m: float = float(self.config.get("min_obstacle_m", 0.4))
        goal_cfg = self.config.get("goal", {})
        self._goal_x: float = float(goal_cfg.get("x", 0.0))
        self._goal_y: float = float(goal_cfg.get("y", 1.0))

    async def observe(self, sensor_data: Dict[str, Any]) -> Dict[str, Any]:
        """Read the latest SceneGraph from SharedState.

        Args:
            sensor_data: Ignored; navigator reads indirectly via SharedState.

        Returns:
            Dict with key ``"scene_graph"`` (or ``None`` if not yet published).
        """
        scene = self._state.get("scene_graph")
        return {"scene_graph": scene}

    async def act(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Compute a NavigationPlan and return an RCAN-compatible action dict.

        Goal coordinates can be overridden per-call via ``context["goal_x"]``
        and ``context["goal_y"]``.  The SceneGraph can also be passed directly
        in ``context["scene_graph"]`` to bypass SharedState lookup.

        Args:
            context: Dict optionally containing:
                - ``goal_x`` / ``goal_y``: override navigation goal.
                - ``scene_graph``: override SceneGraph source.

        Returns:
            ``{"action": "move", "direction": str, "speed": float}``
        """
        goal_x = float(context.get("goal_x", self._goal_x))
        goal_y = float(context.get("goal_y", self._goal_y))

        scene: Optional[SceneGraph] = context.get("scene_graph") or self._state.get("scene_graph")
        plan = self._plan(scene, goal_x, goal_y)

        self._state.set("nav_plan", plan)
        self.status = AgentStatus.RUNNING
        return self._plan_to_action(plan)

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    def _plan(self, scene: Optional["SceneGraph"], goal_x: float, goal_y: float) -> NavigationPlan:
        """Run potential-field planning and return a :class:`NavigationPlan`.

        Args:
            scene: Current world model, or ``None`` if unavailable.
            goal_x: Desired destination X (normalised).
            goal_y: Desired destination Y (normalised).

        Returns:
            :class:`NavigationPlan` with at least one waypoint.
        """
        if scene is None:
            wp = Waypoint(x=0.0, y=0.0, heading_deg=0.0, confidence=0.0, reason="idle")
            return NavigationPlan(
                waypoints=[wp],
                is_blocked=False,
                replan_reason="no_scene_graph",
            )

        # Hard-block: almost no navigable space
        if scene.free_space_pct < BLOCKED_THRESHOLD:
            wp = Waypoint(
                x=0.0,
                y=0.0,
                heading_deg=0.0,
                confidence=0.5,
                reason="obstacle_avoidance",
            )
            return NavigationPlan(
                waypoints=[wp],
                is_blocked=True,
                replan_reason="path_blocked",
            )

        # ---- Potential-field summation ----
        fx, fy = self._attractive_force(goal_x, goal_y)
        rx, ry = self._repulsive_force(scene)
        total_x = fx + rx
        total_y = fy + ry

        # Normalise to unit vector so speed is controlled separately
        mag = math.sqrt(total_x**2 + total_y**2)
        if mag > 1e-6:
            total_x /= mag
            total_y /= mag
        else:
            total_x, total_y = 0.0, 1.0  # default: straight ahead

        heading = math.degrees(math.atan2(total_x, total_y))
        # Reason is "obstacle_avoidance" if any obstacle is close enough to repel,
        # even if its position is symmetric and the net force vector is zero.
        has_close_obstacles = any(
            d.is_obstacle
            and (d.distance_m if d.distance_m is not None else OBSTACLE_INFLUENCE_M)
            < OBSTACLE_INFLUENCE_M
            for d in scene.detections
        )
        reason = "obstacle_avoidance" if has_close_obstacles else "goal_seeking"
        confidence = float(min(1.0, scene.free_space_pct * 1.2))

        wp = Waypoint(
            x=float(total_x),
            y=float(total_y),
            heading_deg=float(heading),
            confidence=confidence,
            reason=reason,
        )
        dist = self._estimate_distance(scene)
        return NavigationPlan(
            waypoints=[wp],
            estimated_distance_m=dist,
            is_blocked=False,
        )

    def _attractive_force(self, goal_x: float, goal_y: float) -> Tuple[float, float]:
        """Compute normalised goal-attractive force vector."""
        mag = math.sqrt(goal_x**2 + goal_y**2) + 1e-9
        return GOAL_ATTRACT * goal_x / mag, GOAL_ATTRACT * goal_y / mag

    def _repulsive_force(self, scene: "SceneGraph") -> Tuple[float, float]:
        """Sum repulsive force vectors from all nearby obstacle detections.

        Obstacles with known distance < OBSTACLE_INFLUENCE_M contribute;
        those beyond that radius (or without depth data) are ignored.
        """
        rx, ry = 0.0, 0.0
        for det in scene.detections:
            if not det.is_obstacle:
                continue
            dist = det.distance_m if det.distance_m is not None else OBSTACLE_INFLUENCE_M
            if dist >= OBSTACLE_INFLUENCE_M:
                continue
            # Repulsion magnitude grows as 1/d − 1/d_influence
            strength = OBSTACLE_REPEL * (1.0 / max(dist, 0.01) - 1.0 / OBSTACLE_INFLUENCE_M)
            # Derive direction from bbox centre (offset from image centre)
            cx = (det.bbox[0] + det.bbox[2]) / 2 - 0.5  # -0.5 to +0.5
            cy = (det.bbox[1] + det.bbox[3]) / 2 - 0.5
            mag = math.sqrt(cx**2 + cy**2) + 1e-9
            # Repel away from obstacle (negate direction to obstacle)
            rx += -strength * cx / mag
            ry += -strength * cy / mag
        return rx, ry

    def _estimate_distance(self, scene: "SceneGraph") -> float:
        """Rough traversable distance estimate to the next obstacle in metres."""
        if scene.closest_obstacle_m is not None:
            return max(0.0, scene.closest_obstacle_m - self._min_obstacle_m)
        return 5.0  # assume clear path when no depth data

    # ------------------------------------------------------------------
    # RCAN action output
    # ------------------------------------------------------------------

    def _plan_to_action(self, plan: NavigationPlan) -> Dict[str, Any]:
        """Convert a :class:`NavigationPlan` to an RCAN-compatible action dict.

        Args:
            plan: The plan produced by :meth:`_plan`.

        Returns:
            ``{"action": "move", "direction": str, "speed": float}``
        """
        if plan.is_blocked or not plan.waypoints:
            return {"action": "move", "direction": "stop", "speed": 0.0}

        wp = plan.waypoints[0]
        direction = self._direction_from_waypoint(wp)
        speed = round(float(wp.confidence) * self._max_speed, 3)
        return {"action": "move", "direction": direction, "speed": speed}

    def _direction_from_waypoint(self, wp: Waypoint) -> str:
        """Map a normalised (x, y) waypoint to a cardinal direction string.

        Args:
            wp: Waypoint with x (lateral) and y (longitudinal).

        Returns:
            One of ``"forward"``, ``"backward"``, ``"left"``, ``"right"``, ``"stop"``.
        """
        if abs(wp.x) < 0.2 and wp.y > 0.1:
            return "forward"
        if abs(wp.x) < 0.2 and wp.y < -0.1:
            return "backward"
        if wp.x < -0.2:
            return "left"
        if wp.x > 0.2:
            return "right"
        return "stop"
