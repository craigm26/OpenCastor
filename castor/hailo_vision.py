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
