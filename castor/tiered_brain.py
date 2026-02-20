"""
Tiered Brain Architecture for OpenCastor.

Three layers, fastest first:

  Layer 0 — Reactive (rule-based, <1ms)
    Hardcoded safety: obstacle too close → stop, blank frame → wait,
    e-stop → halt. No LLM needed.

  Layer 1 — Fast Brain (Gemini Flash / Ollama, ~1-2s)
    Primary perception-action loop. Processes camera frames,
    produces JSON actions. Handles routine navigation.

  Layer 2 — Planner (Claude / Opus, ~10-15s)
    Complex reasoning, scene understanding, conversation,
    multi-step planning. Called periodically or on escalation.

The control loop runs Layer 0 every tick, Layer 1 every tick (async),
and Layer 2 every N ticks or when Layer 1 signals uncertainty.
"""

import logging
import time

from .providers.base import Thought

logger = logging.getLogger("OpenCastor.TieredBrain")


class ReactiveLayer:
    """Layer 0: Rule-based reactive safety controller.

    Combines hardcoded rules (<1ms) with optional Hailo-8 NPU
    object detection (~20ms) for obstacle avoidance without API calls.
    Returns an action if triggered, None to pass to next layer.
    """

    def __init__(self, config: dict):
        self.min_obstacle_m = config.get("reactive", {}).get("min_obstacle_m", 0.3)
        self.blank_threshold = config.get("reactive", {}).get("blank_threshold", 100)
        self.hailo_enabled = config.get("reactive", {}).get("hailo_vision", False)
        # If camera_required=False, blank/missing frames are NOT a blocking condition.
        # The brain will run text-only (messaging, sensor data) without a live frame.
        self.camera_required = config.get("camera", {}).get("camera_required", True)
        self._hailo = None
        self.last_detections = []  # Expose for telemetry/logging

        if self.hailo_enabled:
            try:
                from .hailo_vision import HailoVision

                model = config.get("reactive", {}).get(
                    "hailo_model", "/usr/share/hailo-models/yolov8s_h8.hef"
                )
                confidence = config.get("reactive", {}).get("hailo_confidence", 0.4)
                self._hailo = HailoVision(model_path=model, confidence=confidence)
                if not self._hailo.available:
                    self._hailo = None
            except Exception as e:
                logger.debug(f"Hailo vision not available: {e}")

    def evaluate(self, frame_bytes: bytes, sensor_data: dict | None = None) -> dict | None:
        """Check reactive safety rules. Returns action dict or None."""
        # Rule 1: Blank/missing frame → wait (skipped if camera_required=False)
        if not frame_bytes or len(frame_bytes) < self.blank_threshold:
            if self.camera_required:
                return {"type": "wait", "duration_ms": 500, "reason": "no_camera_data"}
            # camera_required=False: pass through to fast brain (text/sensor-only mode)
            return None

        # Rule 2: All-black frame (camera blocked/failed) — skipped if camera_required=False
        if frame_bytes == b"\x00" * len(frame_bytes):
            if self.camera_required:
                return {"type": "wait", "duration_ms": 500, "reason": "blank_frame"}
            return None

        # Rule 3: Depth-based obstacle proximity
        if sensor_data:
            distance = sensor_data.get("front_distance_m")
            if distance is not None and distance < self.min_obstacle_m:
                logger.warning(f"Reactive: obstacle at {distance:.2f}m — stopping!")
                return {"type": "stop", "reason": f"obstacle_{distance:.2f}m"}

        # Rule 4: Battery critical
        if sensor_data and sensor_data.get("battery_critical"):
            return {"type": "stop", "reason": "battery_critical"}

        # Rule 5: Hailo-8 NPU object detection (~20ms)
        if self._hailo is not None:
            try:
                import cv2
                import numpy as np

                # Decode JPEG to frame
                arr = np.frombuffer(frame_bytes, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    result = self._hailo.detect_obstacles(frame)
                    self.last_detections = result.get("all_detections", [])

                    nearest = result.get("nearest_obstacle")
                    if nearest and nearest.area() > 0.25:
                        # Large obstacle taking >25% of frame = too close
                        logger.warning(
                            f"Reactive: {nearest} filling {nearest.area():.0%} of frame — stopping!"
                        )
                        return {
                            "type": "stop",
                            "reason": f"hailo_{nearest.class_name}_{nearest.area():.0%}",
                        }

                    if not result["clear_path"] and result["obstacles"]:
                        # Obstacles in center path — slow down
                        names = [d.class_name for d in result["obstacles"][:3]]
                        return {
                            "type": "move",
                            "linear": 0.0,
                            "angular": 0.3,  # Turn to avoid
                            "reason": f"hailo_avoid_{','.join(names)}",
                        }
            except Exception as e:
                logger.debug(f"Hailo detection error: {e}")

        # No reactive trigger — pass to next layer
        return None

    def close(self):
        """Release Hailo resources."""
        if self._hailo:
            self._hailo.close()


class TieredBrain:
    """Orchestrates the three brain layers.

    The fast brain runs every tick. The planner runs every
    `planner_interval` ticks or when the fast brain signals
    uncertainty (action confidence < threshold).
    """

    def __init__(self, fast_provider, planner_provider=None, config: dict | None = None):
        config = config or {}
        self.fast = fast_provider
        self.planner = planner_provider
        self.reactive = ReactiveLayer(config)

        # Planner runs every N ticks (0 = never auto-run)
        self.planner_interval = config.get("tiered_brain", {}).get("planner_interval", 10)
        self.uncertainty_threshold = config.get("tiered_brain", {}).get(
            "uncertainty_threshold", 0.3
        )
        self.tick_count = 0
        self.last_plan = None
        self.last_plan_time = 0

        # Stats
        self.stats = {
            "reactive_count": 0,
            "fast_count": 0,
            "planner_count": 0,
            "total_ticks": 0,
        }

    def think(
        self, image_bytes: bytes, instruction: str, sensor_data: dict | None = None
    ) -> Thought:
        """Run the tiered brain pipeline."""
        self.tick_count += 1
        self.stats["total_ticks"] += 1

        # Layer 0: Reactive (instant)
        reactive_action = self.reactive.evaluate(image_bytes, sensor_data)
        if reactive_action:
            self.stats["reactive_count"] += 1
            logger.debug(
                f"Reactive: {reactive_action['type']} ({reactive_action.get('reason', '')})"
            )
            return Thought(f"Reactive: {reactive_action.get('reason', '')}", reactive_action)

        # Layer 1: Fast brain
        t0 = time.time()
        thought = self.fast.think(image_bytes, instruction)
        fast_ms = (time.time() - t0) * 1000
        self.stats["fast_count"] += 1

        if thought.action:
            logger.info(f"Fast brain ({fast_ms:.0f}ms): {thought.action.get('type', '?')}")

        # Layer 2: Planner (periodic or on escalation)
        should_plan = False
        if self.planner and self.planner_interval > 0:
            if self.tick_count % self.planner_interval == 0:
                should_plan = True
                logger.info("Planner: periodic check (tick %d)", self.tick_count)

        # Also escalate if fast brain produced no action
        if self.planner and not thought.action:
            should_plan = True
            logger.info("Planner: escalation (fast brain produced no action)")

        if should_plan and self.planner:
            try:
                # Inject dynamic sensor state into the USER message (not the system prompt).
                # This keeps the system prompt prefix stable across ticks so cache hits occur.
                # Per Claude Code's cache-first lesson: static content in system, dynamic in user.
                from castor.prompt_cache import build_sensor_reminder

                sensor_reminder = build_sensor_reminder(sensor_data or {})
                plan_instruction = (f"{sensor_reminder}\n\n" if sensor_reminder else "") + (
                    f"You are the strategic planner for a robot. "
                    f"The fast brain's last response: {thought.raw_text[:200]}\n\n"
                    f"Current task: {instruction}\n\n"
                    f"Provide a high-level plan or corrected action as JSON."
                )
                t0 = time.time()
                plan_thought = self.planner.think(image_bytes, plan_instruction)
                plan_ms = (time.time() - t0) * 1000
                self.stats["planner_count"] += 1
                if plan_thought.action:
                    self.last_plan = plan_thought.action
                    self.last_plan_time = time.time()
                    logger.info(
                        f"Planner ({plan_ms:.0f}ms): {plan_thought.action.get('type', '?')}"
                    )
                    # Planner overrides fast brain when it has a plan
                    return plan_thought
            except Exception as e:
                logger.warning(f"Planner error (non-fatal): {e}")

        return thought

    def get_stats(self) -> dict:
        """Return brain layer usage stats."""
        total = max(self.stats["total_ticks"], 1)
        stats = {
            **self.stats,
            "reactive_pct": round(self.stats["reactive_count"] / total * 100, 1),
            "fast_pct": round(self.stats["fast_count"] / total * 100, 1),
            "planner_pct": round(self.stats["planner_count"] / total * 100, 1),
        }
        # Include prompt cache stats from planner if available
        if self.planner and hasattr(self.planner, "cache_stats"):
            stats["cache"] = self.planner.cache_stats
        return stats
