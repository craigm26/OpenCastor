"""
Real-time Object Detection module.

Supports YOLOv8 (ultralytics), MobileNet SSD (OpenCV DNN), and a mock
fallback for environments without ML libraries.

Returns annotated JPEG frames and structured detection results
suitable for obstacle avoidance and scene understanding.

API:
  GET /api/detection/frame       — JPEG with bounding box overlays
  GET /api/detection/latest      — JSON {detections: [...], latency_ms}
  POST /api/detection/configure  — {model, conf_threshold, classes}

Env:
  CASTOR_DETECTION_MODEL      — "yolov8n" | "mobilenet" | "mock" (default auto)
  CASTOR_DETECTION_THRESHOLD  — confidence threshold 0.0-1.0 (default 0.5)
  CASTOR_DETECTION_DEVICE     — "cpu" | "cuda" | "mps" (default cpu)

Install:  pip install ultralytics   (YOLOv8 — recommended)
          pip install opencv-python  (already a dep)
"""

import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger("OpenCastor.Detection")

_DEFAULT_MODEL = os.getenv("CASTOR_DETECTION_MODEL", "auto")
_CONF_THRESHOLD = float(os.getenv("CASTOR_DETECTION_THRESHOLD", "0.5"))
_DEVICE = os.getenv("CASTOR_DETECTION_DEVICE", "cpu")

try:
    from ultralytics import YOLO

    HAS_YOLO = True
except ImportError:
    HAS_YOLO = False

try:
    import cv2

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

_singleton: Optional["ObjectDetector"] = None
_lock = threading.Lock()


class Detection:
    """Single object detection result."""

    __slots__ = ("class_name", "confidence", "bbox", "class_id")

    def __init__(
        self,
        class_name: str,
        confidence: float,
        bbox: tuple[int, int, int, int],
        class_id: int = 0,
    ):
        self.class_name = class_name
        self.confidence = confidence
        self.bbox = bbox  # (x1, y1, x2, y2) pixels
        self.class_id = class_id

    def to_dict(self) -> dict:
        x1, y1, x2, y2 = self.bbox
        return {
            "class": self.class_name,
            "confidence": round(self.confidence, 3),
            "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
            "center": {"x": (x1 + x2) // 2, "y": (y1 + y2) // 2},
        }


class ObjectDetector:
    """Unified object detection interface (YOLOv8 / MobileNet / mock)."""

    _MOBILENET_CLASSES = [
        "background", "aeroplane", "bicycle", "bird", "boat", "bottle",
        "bus", "car", "cat", "chair", "cow", "diningtable", "dog",
        "horse", "motorbike", "person", "pottedplant", "sheep", "sofa",
        "train", "tvmonitor",
    ]

    def __init__(self, model_name: str = "auto", conf_threshold: float = 0.5):
        self._conf = conf_threshold
        self._mode = "mock"
        self._yolo = None
        self._net = None
        self._last: list[Detection] = []
        self._last_latency_ms = 0.0
        self._model_name = model_name

        if model_name == "auto":
            model_name = "yolov8n" if HAS_YOLO else ("mobilenet" if HAS_CV2 else "mock")

        if model_name.startswith("yolo") and HAS_YOLO:
            try:
                self._yolo = YOLO(model_name)
                self._yolo.to(_DEVICE)
                self._mode = "yolo"
                self._model_name = model_name
                logger.info("ObjectDetector ready (YOLOv8 %s, device=%s)", model_name, _DEVICE)
            except Exception as exc:
                logger.warning("YOLOv8 init failed: %s — trying MobileNet", exc)

        if self._mode == "mock" and model_name == "mobilenet" and HAS_CV2:
            self._try_mobilenet()

        if self._mode == "mock":
            logger.info("ObjectDetector running in mock mode")

    def _try_mobilenet(self):
        """Try to load MobileNet SSD from OpenCV DNN module."""
        try:
            prototxt = os.path.join(
                os.path.dirname(__file__), "models", "MobileNetSSD_deploy.prototxt"
            )
            caffemodel = os.path.join(
                os.path.dirname(__file__), "models", "MobileNetSSD_deploy.caffemodel"
            )
            if os.path.exists(prototxt) and os.path.exists(caffemodel):
                self._net = cv2.dnn.readNetFromCaffe(prototxt, caffemodel)
                self._mode = "mobilenet"
                logger.info("ObjectDetector ready (MobileNet SSD)")
            else:
                logger.info("MobileNet model files not found — mock mode")
        except Exception as exc:
            logger.warning("MobileNet init failed: %s", exc)

    # ── Public API ────────────────────────────────────────────────────

    def detect(self, jpeg_bytes: bytes) -> list[Detection]:
        """Run detection on a JPEG image. Returns list of Detection objects."""
        t0 = time.monotonic()
        results = []

        if self._mode == "yolo" and self._yolo is not None:
            results = self._detect_yolo(jpeg_bytes)
        elif self._mode == "mobilenet" and self._net is not None:
            results = self._detect_mobilenet(jpeg_bytes)
        else:
            results = self._mock_detections()

        self._last_latency_ms = (time.monotonic() - t0) * 1000
        self._last = results
        return results

    def detect_and_annotate(self, jpeg_bytes: bytes) -> bytes:
        """Run detection and return annotated JPEG with bounding boxes."""
        detections = self.detect(jpeg_bytes)
        if not HAS_CV2 or not jpeg_bytes:
            return jpeg_bytes

        try:
            import cv2
            import numpy as np

            nparr = np.frombuffer(jpeg_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return jpeg_bytes

            for det in detections:
                x1, y1, x2, y2 = det.bbox
                color = (0, 255, 0) if det.confidence > 0.7 else (0, 165, 255)
                cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
                label = f"{det.class_name} {det.confidence:.0%}"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                cv2.rectangle(img, (x1, y1 - th - 4), (x1 + tw, y1), color, -1)
                cv2.putText(
                    img, label, (x1, y1 - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA
                )

            _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return buf.tobytes()
        except Exception as exc:
            logger.error("Annotation error: %s", exc)
            return jpeg_bytes

    @property
    def latest(self) -> list[dict]:
        return [d.to_dict() for d in self._last]

    @property
    def latency_ms(self) -> float:
        return self._last_latency_ms

    @property
    def mode(self) -> str:
        return self._mode

    def configure(self, conf_threshold: float | None = None, model: str | None = None):
        if conf_threshold is not None:
            self._conf = conf_threshold
        if model is not None and model != self._model_name:
            self.__init__(model_name=model, conf_threshold=self._conf)

    # ── Backend implementations ───────────────────────────────────────

    def _detect_yolo(self, jpeg_bytes: bytes) -> list[Detection]:
        import cv2
        import numpy as np

        nparr = np.frombuffer(jpeg_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return []

        results = self._yolo(img, conf=self._conf, verbose=False)[0]
        detections = []
        for box in results.boxes:
            conf = float(box.conf[0])
            cls_id = int(box.cls[0])
            name = self._yolo.names.get(cls_id, str(cls_id))
            x1, y1, x2, y2 = (int(v) for v in box.xyxy[0])
            detections.append(Detection(name, conf, (x1, y1, x2, y2), cls_id))
        return detections

    def _detect_mobilenet(self, jpeg_bytes: bytes) -> list[Detection]:
        import cv2
        import numpy as np

        nparr = np.frombuffer(jpeg_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return []

        h, w = img.shape[:2]
        blob = cv2.dnn.blobFromImage(
            cv2.resize(img, (300, 300)), 0.007843, (300, 300), 127.5
        )
        self._net.setInput(blob)
        out = self._net.forward()
        detections = []
        for i in range(out.shape[2]):
            conf = float(out[0, 0, i, 2])
            if conf < self._conf:
                continue
            cls_id = int(out[0, 0, i, 1])
            name = self._MOBILENET_CLASSES[cls_id] if cls_id < len(self._MOBILENET_CLASSES) else str(cls_id)
            x1 = int(out[0, 0, i, 3] * w)
            y1 = int(out[0, 0, i, 4] * h)
            x2 = int(out[0, 0, i, 5] * w)
            y2 = int(out[0, 0, i, 6] * h)
            detections.append(Detection(name, conf, (x1, y1, x2, y2), cls_id))
        return detections

    def _mock_detections(self) -> list[Detection]:
        import random
        classes = ["person", "chair", "bottle", "laptop", "cup"]
        n = random.randint(0, 3)
        dets = []
        for _ in range(n):
            cls = random.choice(classes)
            conf = round(random.uniform(0.5, 0.95), 2)
            x1, y1 = random.randint(50, 300), random.randint(50, 200)
            x2, y2 = x1 + random.randint(50, 150), y1 + random.randint(50, 150)
            dets.append(Detection(cls, conf, (x1, y1, x2, y2)))
        return dets


def get_detector(model: str = "auto", conf_threshold: float = _CONF_THRESHOLD) -> ObjectDetector:
    """Return the process-wide ObjectDetector singleton."""
    global _singleton
    with _lock:
        if _singleton is None:
            _singleton = ObjectDetector(model_name=model, conf_threshold=conf_threshold)
    return _singleton
