"""Hailo-8 AI accelerator vision module for OpenCastor.

Runs YOLOv8 object detection at ~20ms per frame on the Hailo-8 NPU.
Used by the reactive layer for instant obstacle/person/object detection
without any API calls.

Uses per-call context managers to avoid segfaults from persistent VDevice.
"""

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

logger = logging.getLogger("OpenCastor.HailoVision")

DEFAULT_MODEL = "/usr/share/hailo-models/yolov8s_h8.hef"

COCO_NAMES = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
    13: "bench",
    14: "bird",
    15: "cat",
    16: "dog",
    24: "backpack",
    56: "chair",
    57: "couch",
    58: "potted_plant",
    59: "bed",
    60: "dining_table",
    62: "tv",
    63: "laptop",
    72: "refrigerator",
    73: "book",
}

OBSTACLE_CLASSES = {0, 1, 2, 3, 5, 7, 13, 15, 16, 56, 57, 59, 60}

# Default distance-estimation calibration constant.
# distance_m ≈ AREA_CALIBRATION / area_fraction
# Tuned so area=0.25 → ~1.0m, area=0.50 → ~0.5m.
DEFAULT_AREA_CALIBRATION = 0.25


@dataclass
class ObstacleEvent:
    """Structured obstacle event for the safety monitor.

    Produced by HailoDetection.to_obstacle_event() and consumed by
    the reactive safety layer to trigger speed reduction or e-stop.
    """

    distance_m: float
    confidence: float
    label: str
    area: float
    bbox: List[float]


class HailoDetection:
    """A single detection result."""

    __slots__ = ("class_id", "class_name", "score", "bbox")

    def __init__(self, class_id: int, score: float, bbox: List[float]):
        self.class_id = class_id
        self.class_name = COCO_NAMES.get(class_id, f"class_{class_id}")
        self.score = score
        self.bbox = bbox

    def is_obstacle(self) -> bool:
        return self.class_id in OBSTACLE_CLASSES

    def center_x(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    def area(self) -> float:
        return (self.bbox[2] - self.bbox[0]) * (self.bbox[3] - self.bbox[1])

    def estimate_distance_m(self, calibration: float = DEFAULT_AREA_CALIBRATION) -> float:
        """Estimate distance in metres from bounding-box area.

        Uses a simple inverse-area model: ``distance ≈ calibration / area``.
        The calibration constant can be tuned per-camera via
        ``reactive.hailo_calibration`` in the RCAN config.

        Returns ``inf`` for zero-area detections.
        """
        a = self.area()
        if a <= 0:
            return math.inf
        return calibration / a

    def to_obstacle_event(self, calibration: float = DEFAULT_AREA_CALIBRATION) -> ObstacleEvent:
        """Convert to an ObstacleEvent for the safety monitor."""
        return ObstacleEvent(
            distance_m=self.estimate_distance_m(calibration),
            confidence=self.score,
            label=self.class_name,
            area=self.area(),
            bbox=list(self.bbox),
        )

    def __repr__(self):
        return f"{self.class_name}({self.score:.2f})"


class HailoVision:
    """Hailo-8 accelerated object detection.

    Uses per-call VDevice to avoid segfaults from persistent connections.
    First call ~100ms (device init), subsequent ~20ms.
    """

    def __init__(self, model_path: str = DEFAULT_MODEL, confidence: float = 0.4):
        self._model_path = model_path
        self._input_name = None
        self._input_hw = (640, 640)
        self.confidence = confidence
        self.available = False

        try:
            from hailo_platform import HEF

            if not Path(model_path).exists():
                logger.warning(f"Hailo model not found: {model_path}")
                return

            hef = HEF(model_path)
            input_info = hef.get_input_vstream_infos()[0]
            self._input_name = input_info.name
            self._input_hw = (input_info.shape[0], input_info.shape[1])

            self.available = True
            logger.info(
                "Hailo-8 vision ready: %s (%dx%d)",
                Path(model_path).name,
                *self._input_hw,
            )
        except ImportError:
            logger.debug("hailo_platform not installed — Hailo vision disabled")
        except Exception as e:
            logger.warning(f"Hailo-8 init failed: {e}")

    def detect(self, frame: np.ndarray) -> List[HailoDetection]:
        """Run object detection on a BGR frame."""
        if not self.available:
            return []

        try:
            import cv2
            from hailo_platform import (
                HEF,
                FormatType,
                InferVStreams,
                InputVStreamParams,
                OutputVStreamParams,
                VDevice,
            )

            h, w = self._input_hw
            resized = cv2.resize(frame, (w, h))
            input_data = {self._input_name: np.expand_dims(resized, axis=0)}

            hef = HEF(self._model_path)
            with VDevice(VDevice.create_params()) as vdevice:
                ng = vdevice.configure(hef)[0]
                ip = InputVStreamParams.make(ng, quantized=False, format_type=FormatType.UINT8)
                op = OutputVStreamParams.make(ng, quantized=False, format_type=FormatType.FLOAT32)
                with ng.activate():
                    with InferVStreams(ng, ip, op) as pipeline:
                        result = pipeline.infer(input_data)

            detections = []
            for _, data in result.items():
                batch = data[0]
                for cls_id, dets in enumerate(batch):
                    if isinstance(dets, np.ndarray) and dets.size > 0:
                        for det in dets:
                            score = float(det[4]) if len(det) > 4 else 0
                            if score >= self.confidence:
                                bbox = [
                                    float(det[1]) / w,
                                    float(det[0]) / h,
                                    float(det[3]) / w,
                                    float(det[2]) / h,
                                ]
                                detections.append(HailoDetection(cls_id, score, bbox))

            return sorted(detections, key=lambda d: d.score, reverse=True)
        except Exception as e:
            logger.debug(f"Hailo detection error: {e}")
            return []

    def detect_obstacles(self, frame: np.ndarray) -> Dict[str, Any]:
        """High-level obstacle detection for the reactive layer."""
        detections = self.detect(frame)
        obstacles = [d for d in detections if d.is_obstacle()]
        center_obstacles = [d for d in obstacles if 0.33 < d.center_x() < 0.67]
        nearest = max(obstacles, key=lambda d: d.area(), default=None)

        return {
            "obstacles": obstacles,
            "nearest_obstacle": nearest,
            "clear_path": len(center_obstacles) == 0,
            "all_detections": detections,
        }

    def close(self):
        """No persistent resources to release with per-call approach."""
        self.available = False


# ---------------------------------------------------------------------------
# Issue #201 — Hailo-8 NPU acceleration with TFLite / EdgeTPU fallback
# ---------------------------------------------------------------------------

# Optional SDK guards
HAS_HAILO: bool = False
try:
    import hailo_platform  # noqa: F401  # type: ignore[import]

    HAS_HAILO = True
except ImportError:
    pass

HAS_TFLITE: bool = False
try:
    import importlib.util

    if importlib.util.find_spec("tflite_runtime") is not None:
        HAS_TFLITE = True
    elif importlib.util.find_spec("tensorflow") is not None:
        import tensorflow as _tf  # type: ignore[import]

        HAS_TFLITE = hasattr(_tf, "lite")
except Exception:
    pass

# Track whether EdgeTPU delegate is available
HAS_EDGETPU: bool = False
try:
    import importlib.util as _iu

    HAS_EDGETPU = _iu.find_spec("pycoral") is not None
except Exception:
    pass


class TFLiteDetector:
    """TFLite object detector with optional EdgeTPU delegate fallback.

    Falls back to CPU TFLite when the EdgeTPU delegate is unavailable.

    Args:
        model_path:       Path to the TFLite ``.tflite`` model file.
        conf_threshold:   Minimum confidence score (0.0–1.0).
        use_edgetpu:      Attempt EdgeTPU delegate first.
        num_threads:      CPU inference threads (ignored with EdgeTPU).
    """

    def __init__(
        self,
        model_path: str,
        conf_threshold: float = 0.5,
        use_edgetpu: bool = True,
        num_threads: int = 4,
    ) -> None:
        self.model_path = model_path
        self.conf_threshold = conf_threshold
        self._interpreter = None
        self._input_details = None
        self._output_details = None
        self.backend = "none"

        if not HAS_TFLITE:
            logger.info(
                "TFLite runtime not installed — detector unavailable. "
                "Install: pip install tflite-runtime"
            )
            return

        self._load(use_edgetpu, num_threads)

    def _load(self, use_edgetpu: bool, num_threads: int) -> None:
        """Load the TFLite interpreter, preferring EdgeTPU when available."""
        try:
            if use_edgetpu and HAS_EDGETPU:
                from pycoral.utils.edgetpu import make_interpreter

                self._interpreter = make_interpreter(self.model_path)
                self.backend = "edgetpu"
                logger.info("TFLite interpreter loaded with EdgeTPU delegate")
            elif HAS_TFLITE:
                try:
                    import tflite_runtime.interpreter as tflite

                    self._interpreter = tflite.Interpreter(
                        model_path=self.model_path, num_threads=num_threads
                    )
                except ImportError:
                    import tensorflow as tf

                    self._interpreter = tf.lite.Interpreter(
                        model_path=self.model_path, num_threads=num_threads
                    )
                self.backend = "tflite-cpu"
                logger.info("TFLite interpreter loaded on CPU")

            if self._interpreter:
                self._interpreter.allocate_tensors()
                self._input_details = self._interpreter.get_input_details()
                self._output_details = self._interpreter.get_output_details()
        except Exception as exc:
            logger.warning("TFLite load failed: %s", exc)
            self._interpreter = None

    @property
    def available(self) -> bool:
        """True if the interpreter loaded successfully."""
        return self._interpreter is not None

    def detect(self, frame: "np.ndarray") -> list:
        """Run inference on *frame* and return :class:`HailoDetection` objects.

        Args:
            frame: BGR or RGB ``uint8`` numpy array (H×W×3).

        Returns:
            List of :class:`HailoDetection` sorted by confidence (descending).
        """
        if not self.available:
            return []

        try:
            import cv2
            import numpy as np

            ih = self._input_details[0]["shape"][1]
            iw = self._input_details[0]["shape"][2]
            resized = cv2.resize(frame, (iw, ih))
            input_data = np.expand_dims(resized, axis=0)
            if self._input_details[0]["dtype"] is float:
                input_data = input_data.astype("float32") / 255.0

            self._interpreter.set_tensor(self._input_details[0]["index"], input_data)
            self._interpreter.invoke()

            boxes = self._interpreter.get_tensor(self._output_details[0]["index"])[0]
            class_ids = self._interpreter.get_tensor(self._output_details[1]["index"])[0]
            scores = self._interpreter.get_tensor(self._output_details[2]["index"])[0]

            detections = []
            for i, score in enumerate(scores):
                if float(score) >= self.conf_threshold:
                    cls_id = int(class_ids[i])
                    y1, x1, y2, x2 = boxes[i]
                    detections.append(
                        HailoDetection(
                            cls_id, float(score), [float(x1), float(y1), float(x2), float(y2)]
                        )
                    )
            return sorted(detections, key=lambda d: d.score, reverse=True)
        except Exception as exc:
            logger.debug("TFLite inference error: %s", exc)
            return []


def detect_objects(
    frame: "np.ndarray",
    conf_threshold: float = 0.5,
    hailo_model: str = DEFAULT_MODEL,
    tflite_model: str = "",
) -> list:
    """Route object detection to the best available backend.

    Priority: Hailo-8 NPU → TFLite (EdgeTPU) → TFLite CPU → mock empty.

    Args:
        frame:          Input ``uint8`` numpy array (H×W×3).
        conf_threshold: Minimum confidence score to return.
        hailo_model:    Path to the Hailo .hef model file.
        tflite_model:   Path to the TFLite .tflite model file.

    Returns:
        List of :class:`HailoDetection` sorted by confidence descending.
        Returns an empty list when no backend is available.
    """
    # Try Hailo-8 NPU first
    if HAS_HAILO:
        try:
            hv = HailoVision(model_path=hailo_model, confidence=conf_threshold)
            if hv.available:
                return hv.detect(frame)
        except Exception as exc:
            logger.debug("Hailo backend failed, falling back: %s", exc)

    # Try TFLite (EdgeTPU > CPU)
    if HAS_TFLITE and tflite_model:
        try:
            det = TFLiteDetector(model_path=tflite_model, conf_threshold=conf_threshold)
            if det.available:
                return det.detect(frame)
        except Exception as exc:
            logger.debug("TFLite backend failed: %s", exc)

    # Mock fallback
    logger.debug("No vision backend available — returning empty detections")
    return []
