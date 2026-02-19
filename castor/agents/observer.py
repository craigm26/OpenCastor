"""ObserverAgent — converts raw sensor data into a structured SceneGraph.

Processes Hailo-8 NPU detections and optional depth data into a
machine-readable world model that downstream agents (e.g. NavigatorAgent)
can consume from SharedState.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .base import AgentStatus, BaseAgent
from .shared_state import SharedState

logger = logging.getLogger("OpenCastor.Agents.Observer")

# Labels treated as navigation obstacles (COCO-derived subset)
OBSTACLE_LABELS: frozenset = frozenset(
    {
        "person",
        "bicycle",
        "car",
        "motorcycle",
        "bus",
        "truck",
        "bench",
        "cat",
        "dog",
        "chair",
        "couch",
        "bed",
        "dining_table",
    }
)


@dataclass
class Detection:
    """A single detected object in the scene.

    Attributes:
        label: Human-readable class label (e.g. ``"person"``).
        confidence: Detection score in [0, 1].
        bbox: Normalised bounding box ``(x1, y1, x2, y2)`` in [0, 1].
        distance_m: Estimated distance from depth sensor, or ``None``.
        is_obstacle: True if this object is a navigation hazard.
    """

    label: str
    confidence: float
    bbox: tuple  # (x1, y1, x2, y2) normalised 0-1
    distance_m: Optional[float]
    is_obstacle: bool


@dataclass
class SceneGraph:
    """Structured world model built from one sensor tick.

    Attributes:
        timestamp: Unix timestamp when this graph was constructed.
        detections: All parsed detections for this frame.
        free_space_pct: Estimated fraction of navigable space [0, 1].
        closest_obstacle_m: Distance to nearest obstacle (metres), or ``None``.
        dominant_objects: Top-3 object labels by detection confidence.
        raw_sensor_keys: Which sensor sources contributed to this graph.
    """

    timestamp: float
    detections: List[Detection] = field(default_factory=list)
    free_space_pct: float = 1.0
    closest_obstacle_m: Optional[float] = None
    dominant_objects: List[str] = field(default_factory=list)
    raw_sensor_keys: List[str] = field(default_factory=list)


class ObserverAgent(BaseAgent):
    """Converts raw sensor data into a SceneGraph and publishes it to SharedState.

    Accepted keys in *sensor_data*:

    * ``hailo_detections`` — ``list[dict]`` from :mod:`castor.hailo_vision`
      (each dict: ``label``/``class_name``, ``confidence``/``score``, ``bbox``).
    * ``depth_map`` — ``numpy.ndarray`` of shape ``(H, W)`` with depth in metres,
      or ``None`` to skip depth enrichment.
    * ``frame_shape`` — ``(height, width)`` of the source camera frame.

    Publishes the resulting :class:`SceneGraph` under ``"scene_graph"`` in
    :class:`~castor.agents.shared_state.SharedState`.

    Example::

        state = SharedState()
        observer = ObserverAgent(shared_state=state)

        scene = await observer.observe({
            "hailo_detections": [
                {"label": "person", "confidence": 0.91, "bbox": [0.2, 0.1, 0.4, 0.9]},
            ],
        })
        print(scene.closest_obstacle_m)   # None (no depth data)
    """

    name = "observer"

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        shared_state: Optional[SharedState] = None,
    ) -> None:
        super().__init__(config)
        self._state: SharedState = shared_state or SharedState()
        # Build the obstacle label set from defaults + optional config extension
        self._obstacle_labels: frozenset = OBSTACLE_LABELS | frozenset(
            self.config.get("obstacle_labels", [])
        )

    async def observe(self, sensor_data: Dict[str, Any]) -> "SceneGraph":
        """Build and return a :class:`SceneGraph` from *sensor_data*.

        Handles missing or ``None`` sensor keys gracefully — unknown keys are
        ignored and incomplete detection dicts are skipped with a debug log.

        Args:
            sensor_data: Dict of sensor readings.  All keys are optional.

        Returns:
            :class:`SceneGraph` for the current tick.
        """
        if sensor_data is None:
            sensor_data = {}

        raw_keys: List[str] = []
        detections: List[Detection] = []

        # ---- Parse Hailo detections ----
        hailo_raw = sensor_data.get("hailo_detections")
        if hailo_raw:
            raw_keys.append("hailo_detections")
            for det in hailo_raw:
                parsed = self._parse_detection(det)
                if parsed is not None:
                    detections.append(parsed)

        # ---- Enrich with depth data ----
        depth_map = sensor_data.get("depth_map")
        if depth_map is not None:
            raw_keys.append("depth_map")
            detections = self._enrich_with_depth(detections, depth_map)

        # ---- Compute scene statistics ----
        closest = self._closest_obstacle(detections)
        free_pct = self._estimate_free_space(detections, depth_map)
        dominant = self._dominant_objects(detections)

        scene = SceneGraph(
            timestamp=time.time(),
            detections=detections,
            free_space_pct=free_pct,
            closest_obstacle_m=closest,
            dominant_objects=dominant,
            raw_sensor_keys=raw_keys,
        )

        self._state.set("scene_graph", scene)
        self.status = AgentStatus.RUNNING
        return scene

    async def act(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Return a simple action based on the most recent SceneGraph.

        The observer does not drive motors directly; it signals whether
        the scene is safe to navigate.

        Args:
            context: Unused (scene is read from SharedState).

        Returns:
            Action dict — ``"stop"`` when an obstacle is very close,
            ``"observe"`` otherwise.
        """
        scene: Optional[SceneGraph] = self._state.get("scene_graph")
        if scene is None:
            return {"action": "wait", "reason": "no_scene_graph"}
        if scene.closest_obstacle_m is not None and scene.closest_obstacle_m < 0.3:
            return {"action": "stop", "reason": "obstacle_close"}
        return {"action": "observe", "free_space_pct": scene.free_space_pct}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_detection(self, det: Any) -> Optional[Detection]:
        """Parse one raw detection dict into a :class:`Detection`."""
        try:
            label = str(det.get("label", det.get("class_name", "unknown")))
            confidence = float(det.get("confidence", det.get("score", 0.0)))
            bbox_raw = det.get("bbox", [0.0, 0.0, 0.0, 0.0])
            # Accept list or tuple; pad/truncate to 4 elements
            bbox_list = [float(v) for v in bbox_raw]
            while len(bbox_list) < 4:
                bbox_list.append(0.0)
            bbox: tuple = tuple(bbox_list[:4])
            is_obs = label.lower() in self._obstacle_labels
            return Detection(
                label=label,
                confidence=confidence,
                bbox=bbox,
                distance_m=None,
                is_obstacle=is_obs,
            )
        except Exception as exc:
            logger.debug(f"Skipping malformed detection {det!r}: {exc}")
            return None

    def _enrich_with_depth(
        self, detections: List[Detection], depth_map: Any
    ) -> List[Detection]:
        """Assign depth-derived distances using bbox centre pixel lookup.

        Args:
            detections: Detections without distance information.
            depth_map: numpy array of shape ``(H, W)`` in metres.

        Returns:
            Detections with ``distance_m`` set where depth is valid (> 0).
        """
        try:
            h, w = depth_map.shape[:2]
            enriched: List[Detection] = []
            for det in detections:
                x1, y1, x2, y2 = det.bbox
                cx = int(((x1 + x2) / 2) * w)
                cy = int(((y1 + y2) / 2) * h)
                cx = max(0, min(cx, w - 1))
                cy = max(0, min(cy, h - 1))
                raw_dist = float(depth_map[cy, cx])
                dist: Optional[float] = raw_dist if raw_dist > 0 else None
                enriched.append(
                    Detection(
                        label=det.label,
                        confidence=det.confidence,
                        bbox=det.bbox,
                        distance_m=dist,
                        is_obstacle=det.is_obstacle,
                    )
                )
            return enriched
        except Exception as exc:
            logger.debug(f"Depth enrichment failed: {exc}")
            return detections

    def _closest_obstacle(self, detections: List[Detection]) -> Optional[float]:
        """Return the minimum known distance among obstacle detections."""
        distances = [
            d.distance_m
            for d in detections
            if d.is_obstacle and d.distance_m is not None
        ]
        return min(distances) if distances else None

    def _estimate_free_space(
        self, detections: List[Detection], depth_map: Any
    ) -> float:
        """Heuristic estimate of free navigable space fraction [0, 1].

        Uses depth map if available (fraction of pixels with depth > 1 m),
        otherwise falls back to bbox-area overlap of obstacle detections.
        """
        if depth_map is not None:
            try:
                close_mask = (depth_map > 0) & (depth_map < 1.0)
                return float(max(0.0, 1.0 - float(close_mask.mean())))
            except Exception:
                pass  # fall through to bbox heuristic

        occupied = 0.0
        for det in detections:
            if det.is_obstacle:
                x1, y1, x2, y2 = det.bbox
                occupied += max(0.0, x2 - x1) * max(0.0, y2 - y1)
        return float(max(0.0, 1.0 - min(occupied, 1.0)))

    def _dominant_objects(self, detections: List[Detection]) -> List[str]:
        """Return the top-3 unique object labels ranked by confidence."""
        sorted_dets = sorted(detections, key=lambda d: d.confidence, reverse=True)
        seen: List[str] = []
        for det in sorted_dets:
            if det.label not in seen:
                seen.append(det.label)
            if len(seen) == 3:
                break
        return seen
