"""Hailo-8 AI accelerator vision module for OpenCastor.

Runs YOLOv8 object detection at ~20ms per frame on the Hailo-8 NPU.
Used by the reactive layer for instant obstacle/person/object detection
without any API calls.

COCO class IDs: https://docs.ultralytics.com/datasets/detect/coco/
"""

import logging
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

logger = logging.getLogger("OpenCastor.HailoVision")

# Default model path (shipped with hailo-models package)
DEFAULT_MODEL = "/usr/share/hailo-models/yolov8s_h8.hef"

# Key COCO classes for robot navigation
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

# Classes that are obstacles (should stop/avoid)
OBSTACLE_CLASSES = {0, 1, 2, 3, 5, 7, 13, 15, 16, 56, 57, 59, 60}


class HailoDetection:
    """A single detection result."""

    __slots__ = ("class_id", "class_name", "score", "bbox")

    def __init__(self, class_id: int, score: float, bbox: List[float]):
        self.class_id = class_id
        self.class_name = COCO_NAMES.get(class_id, f"class_{class_id}")
        self.score = score
        self.bbox = bbox  # [x1, y1, x2, y2] normalized 0-1

    def is_obstacle(self) -> bool:
        return self.class_id in OBSTACLE_CLASSES

    def center_x(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    def area(self) -> float:
        return (self.bbox[2] - self.bbox[0]) * (self.bbox[3] - self.bbox[1])

    def __repr__(self):
        return f"{self.class_name}({self.score:.2f})"


class HailoVision:
    """Hailo-8 accelerated object detection.

    Provides ~20ms YOLOv8 inference for the reactive layer.
    Falls back gracefully if Hailo hardware is not available.
    """

    def __init__(self, model_path: str = DEFAULT_MODEL, confidence: float = 0.4):
        self._pipeline = None
        self._ng = None
        self._vdevice = None
        self._input_name = None
        self._input_hw = (640, 640)
        self.confidence = confidence
        self.available = False

        try:
            from hailo_platform import (
                HEF,
                FormatType,
                InferVStreams,
                InputVStreamParams,
                OutputVStreamParams,
                VDevice,
            )

            if not Path(model_path).exists():
                logger.warning(f"Hailo model not found: {model_path}")
                return

            hef = HEF(model_path)
            input_info = hef.get_input_vstream_infos()[0]
            self._input_name = input_info.name
            self._input_hw = (input_info.shape[0], input_info.shape[1])

            self._vdevice = VDevice(VDevice.create_params())
            self._ng = self._vdevice.configure(hef)[0]

            ip = InputVStreamParams.make(self._ng, quantized=False, format_type=FormatType.UINT8)
            op = OutputVStreamParams.make(self._ng, quantized=False, format_type=FormatType.FLOAT32)

            self._activation = self._ng.activate()
            self._activation.__enter__()

            self._pipeline = InferVStreams(self._ng, ip, op)
            self._pipeline.__enter__()

            # Warm-up inference
            dummy = np.zeros((1, *self._input_hw, 3), dtype=np.uint8)
            self._pipeline.infer({self._input_name: dummy})

            self.available = True
            logger.info(
                "Hailo-8 vision online: %s (%dx%d)",
                Path(model_path).name,
                *self._input_hw,
            )

        except ImportError:
            logger.debug("hailo_platform not installed — Hailo vision disabled")
        except Exception as e:
            logger.warning(f"Hailo-8 init failed: {e}")

    def detect(self, frame: np.ndarray) -> List[HailoDetection]:
        """Run object detection on a BGR frame. Returns list of detections."""
        if not self.available or self._pipeline is None:
            return []

        import cv2

        h, w = self._input_hw
        resized = cv2.resize(frame, (w, h))
        input_data = {self._input_name: np.expand_dims(resized, axis=0)}

        result = self._pipeline.infer(input_data)

        detections = []
        for _, data in result.items():
            batch = data[0]  # NMS output: list of 80 classes
            for cls_id, dets in enumerate(batch):
                if isinstance(dets, np.ndarray) and dets.size > 0:
                    for det in dets:
                        score = float(det[4]) if len(det) > 4 else 0
                        if score >= self.confidence:
                            # Normalize bbox to 0-1
                            bbox = [
                                float(det[1]) / w,
                                float(det[0]) / h,
                                float(det[3]) / w,
                                float(det[2]) / h,
                            ]
                            detections.append(HailoDetection(cls_id, score, bbox))

        return sorted(detections, key=lambda d: d.score, reverse=True)

    def detect_obstacles(self, frame: np.ndarray) -> Dict[str, Any]:
        """High-level obstacle detection for the reactive layer.

        Returns:
            dict with keys:
                - obstacles: list of obstacle detections
                - nearest_obstacle: closest obstacle (by bbox area = proxy for distance)
                - clear_path: True if no obstacles in center third
                - all_detections: all detections including non-obstacles
        """
        detections = self.detect(frame)
        obstacles = [d for d in detections if d.is_obstacle()]

        # Check center third for obstacles
        center_obstacles = [d for d in obstacles if 0.33 < d.center_x() < 0.67]
        clear_path = len(center_obstacles) == 0

        nearest = max(obstacles, key=lambda d: d.area(), default=None)

        return {
            "obstacles": obstacles,
            "nearest_obstacle": nearest,
            "clear_path": clear_path,
            "all_detections": detections,
        }

    def close(self):
        """Release Hailo resources."""
        try:
            if self._pipeline:
                self._pipeline.__exit__(None, None, None)
            if self._activation:
                self._activation.__exit__(None, None, None)
            if self._vdevice:
                self._vdevice.release()
            logger.debug("Hailo-8 vision released")
        except Exception:
            pass
        self.available = False
