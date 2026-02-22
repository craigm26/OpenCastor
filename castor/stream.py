"""
castor/stream.py — WebRTC camera stream (issue #108).

Provides a low-latency P2P video stream via WebRTC using ``aiortc``.
Falls back gracefully to MJPEG when aiortc is not installed.

API endpoint: ``POST /api/stream/webrtc/offer``
  Body:  ``{"sdp": "<offer SDP>", "type": "offer"}``
  Reply: ``{"sdp": "<answer SDP>", "type": "answer"}``

ICE servers can be configured in the RCAN config::

    network:
      ice_servers:
        - urls: [stun:stun.l.google.com:19302]

Install::

    pip install opencastor[webrtc]   # or: pip install aiortc
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("OpenCastor.Stream.WebRTC")

try:
    import cv2

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    import av
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from aiortc.contrib.media import MediaStreamTrack

    HAS_AIORTC = True
except ImportError:
    HAS_AIORTC = False
    logger.debug("aiortc not installed — WebRTC stream unavailable. Fallback: MJPEG")

# Registry of active peer connections (for cleanup on shutdown)
_peer_connections: List[Any] = []


# ---------------------------------------------------------------------------
# Camera video track
# ---------------------------------------------------------------------------


class CameraTrack:
    """Wraps an OpenCV VideoCapture as an aiortc VideoStreamTrack.

    Captures frames from a USB/CSI camera and delivers them to WebRTC peers.
    If cv2 is unavailable, delivers a blank frame.
    """

    kind = "video"

    def __init__(self, camera_index: int = 0) -> None:
        if HAS_AIORTC:
            super().__init__()  # type: ignore[call-arg]
        self._camera_index = camera_index
        self._cap: Optional[Any] = None
        self._frame_count = 0

    def _ensure_open(self) -> None:
        if HAS_CV2 and (self._cap is None or not self._cap.isOpened()):
            self._cap = cv2.VideoCapture(self._camera_index)

    async def recv(self) -> Any:
        """Deliver the next video frame (called by aiortc internals)."""
        pts, time_base = await self.next_timestamp()  # type: ignore[attr-defined]
        self._ensure_open()

        if HAS_CV2 and self._cap and self._cap.isOpened():
            ok, bgr = self._cap.read()
            if ok:
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            else:
                import numpy as np

                rgb = np.zeros((480, 640, 3), dtype=np.uint8)
        else:
            import numpy as np

            rgb = np.zeros((480, 640, 3), dtype=np.uint8)

        frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        frame.pts = pts
        frame.time_base = time_base
        self._frame_count += 1
        return frame

    def close(self) -> None:
        if self._cap:
            self._cap.release()
            self._cap = None


# Patch CameraTrack to inherit from VideoStreamTrack when aiortc is available
if HAS_AIORTC:

    _orig_init = CameraTrack.__init__

    class CameraTrack(MediaStreamTrack):  # type: ignore[no-redef]
        kind = "video"

        def __init__(self, camera_index: int = 0) -> None:
            super().__init__()
            self._camera_index = camera_index
            self._cap = None
            self._frame_count = 0

        def _ensure_open(self) -> None:
            if HAS_CV2 and (self._cap is None or not self._cap.isOpened()):
                self._cap = cv2.VideoCapture(self._camera_index)

        async def recv(self) -> Any:
            pts, time_base = await self.next_timestamp()
            self._ensure_open()
            if HAS_CV2 and self._cap and self._cap.isOpened():
                ok, bgr = self._cap.read()
                if ok:
                    import numpy as np

                    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                else:
                    import numpy as np

                    rgb = np.zeros((480, 640, 3), dtype=np.uint8)
            else:
                import numpy as np

                rgb = np.zeros((480, 640, 3), dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            frame.pts = pts
            frame.time_base = time_base
            return frame

        def close(self) -> None:
            if self._cap:
                self._cap.release()
                self._cap = None


# ---------------------------------------------------------------------------
# WebRTC offer/answer handler
# ---------------------------------------------------------------------------


async def handle_webrtc_offer(
    offer_sdp: str,
    offer_type: str,
    camera_index: int = 0,
    ice_servers: Optional[List[Dict]] = None,
) -> Dict[str, str]:
    """Process a WebRTC SDP offer and return an answer.

    Args:
        offer_sdp: The SDP string from the client.
        offer_type: Should be "offer".
        camera_index: Camera device index (default 0).
        ice_servers: List of ICE server dicts, e.g. [{"urls": ["stun:..."]}].

    Returns:
        Dict with "sdp" and "type" keys for the answer.

    Raises:
        RuntimeError: If aiortc is not installed.
    """
    if not HAS_AIORTC:
        raise RuntimeError(
            "aiortc is required for WebRTC. Install: pip install opencastor[webrtc]"
        )

    # Build RTCConfiguration from ice_servers list
    from aiortc import RTCConfiguration, RTCIceServer

    ice_cfg_servers = []
    for s in (ice_servers or [{"urls": ["stun:stun.l.google.com:19302"]}]):
        urls = s.get("urls", [])
        if isinstance(urls, str):
            urls = [urls]
        ice_cfg_servers.append(RTCIceServer(urls=urls))

    config = RTCConfiguration(iceServers=ice_cfg_servers)
    pc = RTCPeerConnection(configuration=config)
    _peer_connections.append(pc)

    # Add camera track
    track = CameraTrack(camera_index=camera_index)
    pc.addTrack(track)

    # Cleanup on connection close
    @pc.on("connectionstatechange")
    async def _on_state():
        if pc.connectionState in ("closed", "failed"):
            await pc.close()
            if pc in _peer_connections:
                _peer_connections.remove(pc)
            track.close()

    # Process offer
    offer = RTCSessionDescription(sdp=offer_sdp, type=offer_type)
    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


async def close_all_peers() -> None:
    """Close all active WebRTC peer connections (called on gateway shutdown)."""
    for pc in list(_peer_connections):
        try:
            await pc.close()
        except Exception:
            pass
    _peer_connections.clear()


def webrtc_available() -> bool:
    """Return True if aiortc is installed and WebRTC is available."""
    return HAS_AIORTC
