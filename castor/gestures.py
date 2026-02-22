"""MediaPipe hand gesture and pose commands for OpenCastor.

Recognize hand gestures and body poses from camera frames and translate them
into robot action commands.  Useful for hands-free, natural control of robots
without a keyboard or mobile app.

Recognized gestures (default mapping, override via config):
    - Open palm (5 fingers extended)  → stop
    - Closed fist                      → forward
    - Thumbs up                        → forward_fast
    - Thumbs down                      → backward
    - Index finger pointing up         → speed_increase
    - Index finger pointing down       → speed_decrease
    - Peace/V sign                     → turn_left
    - OK sign                          → turn_right

Install::

    pip install mediapipe

API endpoint:
    POST /api/gesture/frame  — {image_base64} → {gesture, action, confidence}
"""

import base64
import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("OpenCastor.Gestures")

try:
    import mediapipe as mp  # type: ignore

    HAS_MEDIAPIPE = True
    _mp_hands = mp.solutions.hands
    _mp_drawing = mp.solutions.drawing_utils
    logger.debug("mediapipe %s available", mp.__version__)
except ImportError:
    HAS_MEDIAPIPE = False
    logger.info("mediapipe not installed. GestureController will run in mock mode.")

try:
    import cv2  # type: ignore

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# Default gesture → robot action mapping
DEFAULT_GESTURE_ACTIONS: Dict[str, Dict[str, Any]] = {
    "open_palm": {"action": "stop", "speed": 0},
    "closed_fist": {"action": "forward", "speed": 0.6},
    "thumbs_up": {"action": "forward", "speed": 1.0},
    "thumbs_down": {"action": "backward", "speed": 0.6},
    "pointing_up": {"action": "stop", "speed": 0},
    "peace_sign": {"action": "turn_left", "speed": 0.5},
    "ok_sign": {"action": "turn_right", "speed": 0.5},
    "none": {"action": "none", "speed": 0},
}


# ---------------------------------------------------------------------------
# Landmark helper functions
# ---------------------------------------------------------------------------


def _dist(a: Any, b: Any) -> float:
    """Euclidean distance between two MediaPipe landmarks."""
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2)


def _finger_extended(landmarks: List[Any], tip_idx: int, pip_idx: int) -> bool:
    """Return True if the finger tip is above (y < ) the pip joint."""
    return landmarks[tip_idx].y < landmarks[pip_idx].y


def _classify_gesture(landmarks: List[Any]) -> Tuple[str, float]:
    """Classify hand gesture from 21 MediaPipe landmarks.

    Returns (gesture_name, confidence_0_to_1).
    """
    # Finger tip and pip joint indices
    # [thumb, index, middle, ring, pinky]
    tips = [4, 8, 12, 16, 20]
    pips = [3, 6, 10, 14, 18]

    # Which fingers are extended (tip above pip)
    extended = [
        landmarks[tips[i]].y < landmarks[pips[i]].y for i in range(1, 5)
    ]
    thumb_extended = landmarks[4].x < landmarks[3].x  # left-hand approx

    n_extended = sum(extended)

    # Thumb direction (up/down)
    thumb_tip = landmarks[4]
    wrist = landmarks[0]
    thumb_pointing_up = thumb_tip.y < wrist.y - 0.05

    # Peace / V-sign: index + middle extended, others not
    if extended[0] and extended[1] and not extended[2] and not extended[3]:
        return "peace_sign", 0.85

    # OK sign: thumb + index close together, others extended
    thumb_index_dist = _dist(landmarks[4], landmarks[8])
    if thumb_index_dist < 0.06 and extended[1] and extended[2]:
        return "ok_sign", 0.80

    # Open palm: all 4 fingers extended
    if n_extended == 4:
        return "open_palm", 0.90

    # Closed fist: no fingers extended
    if n_extended == 0 and not thumb_extended:
        return "closed_fist", 0.88

    # Thumbs up: thumb pointing up, fingers curled
    if thumb_pointing_up and n_extended == 0:
        return "thumbs_up", 0.82

    # Thumbs down: thumb pointing down, fingers curled
    if not thumb_pointing_up and n_extended == 0 and landmarks[4].y > wrist.y:
        return "thumbs_down", 0.78

    # Pointing up: only index extended, tip above wrist
    if extended[0] and not extended[1] and not extended[2] and not extended[3]:
        if landmarks[8].y < wrist.y:
            return "pointing_up", 0.80

    return "none", 0.5


class GestureController:
    """Recognize hand gestures from JPEG frames and map them to actions.

    Args:
        gesture_actions: Dict mapping gesture name → action dict.
            Override to customize the gesture→command mapping.
        min_detection_confidence: MediaPipe detection confidence threshold.
        min_tracking_confidence: MediaPipe tracking confidence threshold.
    """

    def __init__(
        self,
        gesture_actions: Optional[Dict[str, Dict[str, Any]]] = None,
        min_detection_confidence: float = 0.7,
        min_tracking_confidence: float = 0.5,
    ):
        self._gesture_actions = gesture_actions or DEFAULT_GESTURE_ACTIONS.copy()
        self._mode = "hardware" if HAS_MEDIAPIPE else "mock"
        self._hands: Optional[Any] = None
        self._last_gesture: str = "none"
        self._last_time: float = 0.0

        if HAS_MEDIAPIPE:
            self._hands = _mp_hands.Hands(
                static_image_mode=False,
                max_num_hands=1,
                min_detection_confidence=min_detection_confidence,
                min_tracking_confidence=min_tracking_confidence,
            )
            logger.info("GestureController ready (MediaPipe hands)")
        else:
            logger.warning("GestureController running in mock mode (mediapipe not installed)")

    def recognize_from_jpeg(self, jpeg_bytes: bytes) -> Dict[str, Any]:
        """Classify gesture from a JPEG image.

        Args:
            jpeg_bytes: Raw JPEG frame bytes.

        Returns:
            Dict with keys: gesture, action_dict, confidence, latency_ms.
        """
        t0 = time.time()

        if self._mode == "mock" or not HAS_CV2:
            return {
                "gesture": "none",
                "action": {"action": "none", "speed": 0},
                "confidence": 0.0,
                "latency_ms": 0.0,
                "mode": "mock",
            }

        try:
            import numpy as np  # type: ignore

            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                raise ValueError("Could not decode JPEG")

            # Convert BGR → RGB for MediaPipe
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self._hands.process(rgb)

            gesture = "none"
            confidence = 0.0

            if results.multi_hand_landmarks:
                lm_list = results.multi_hand_landmarks[0].landmark
                gesture, confidence = _classify_gesture(lm_list)

            action = self._gesture_actions.get(gesture, {"action": "none", "speed": 0})
            self._last_gesture = gesture
            self._last_time = time.time()

            return {
                "gesture": gesture,
                "action": action,
                "confidence": round(confidence, 3),
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "mode": "hardware",
            }

        except Exception as exc:
            logger.error("Gesture recognition error: %s", exc)
            return {
                "gesture": "none",
                "action": {"action": "none", "speed": 0},
                "confidence": 0.0,
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "error": str(exc),
                "mode": "error",
            }

    def recognize_from_base64(self, b64_image: str) -> Dict[str, Any]:
        """Convenience wrapper: base64-encoded JPEG → gesture dict."""
        try:
            jpeg_bytes = base64.b64decode(b64_image)
        except Exception as exc:
            return {"gesture": "none", "action": {"action": "none"}, "error": str(exc)}
        return self.recognize_from_jpeg(jpeg_bytes)

    def set_gesture_action(self, gesture: str, action: Dict[str, Any]) -> None:
        """Override the action mapped to a gesture name."""
        self._gesture_actions[gesture] = action

    def list_gestures(self) -> Dict[str, Dict[str, Any]]:
        """Return all gesture → action mappings."""
        return dict(self._gesture_actions)

    def close(self) -> None:
        """Release MediaPipe resources."""
        if self._hands is not None:
            self._hands.close()
            self._hands = None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_controller: Optional[GestureController] = None


def get_controller() -> GestureController:
    """Return the process-wide GestureController."""
    global _controller
    if _controller is None:
        _controller = GestureController()
    return _controller
