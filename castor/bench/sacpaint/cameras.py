"""Frame fetchers: pull the latest picture of the sheet over plain HTTP.

Deliberately protocol-thin, because the three sources that exist today agree on
nothing except "an HTTP GET eventually yields a JPEG":

* Bob's console — ``GET :8002/camera/<name>/snapshot`` returns raw ``image/jpeg``
  and wants ``Authorization: Bearer <CONSOLE_TOKEN>`` (it also accepts
  ``?token=``, which is how ``<img>`` tags load it);
* the carbot phone rig — ``GET :8100/look`` refreshes the frame on disk as a
  side effect and ``GET :8100/snapshot.jpg`` then serves it, no auth. That is
  what ``prime_url`` is for: one throwaway GET before the real one;
* the OpenCastor iOS app, which is being pointed at the same shape of endpoint.

So a source is a URL, an optional priming URL, an optional bearer, and a
timeout. A JSON response is dug through for an inline base64 image or a
single follow-on image URL, because a phone bridge is as likely to answer
``{"image_b64": ...}`` as it is to answer with bytes.

Every failure raises :class:`FrameFetchError`, an
[`EmbodimentFault`][inspect_robots.errors.EmbodimentFault] subclass, so a dead
stream halts the eval with the URL in the message instead of quietly scoring a
blank canvas.
"""

from __future__ import annotations

import base64
import binascii
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import cv2
import numpy as np
from inspect_robots.errors import EmbodimentFault

#: Response fields that plausibly carry an inline image, most specific first.
_B64_KEYS = ("image_b64", "image_base64", "jpeg_b64", "jpeg_base64", "frame_b64", "b64")
#: Response fields that plausibly carry either inline base64 or a follow-on URL.
_AMBIGUOUS_KEYS = ("image", "frame", "snapshot", "data", "url", "href")

#: ``(status, content_type, body)`` — the seam tests replace instead of sockets.
Fetcher = Callable[[str, dict[str, str], float], "tuple[int, str, bytes]"]


class FrameFetchError(EmbodimentFault):
    """A camera stream is down, empty, or answering with something that is not a picture."""


class FrameSource(Protocol):
    """Anything that can hand the embodiment one RGB frame."""

    name: str

    def fetch(self) -> np.ndarray:
        """Return the latest frame as ``(H, W, 3)`` RGB uint8."""
        ...


def _urllib_fetch(url: str, headers: dict[str, str], timeout_s: float) -> tuple[int, str, bytes]:
    """Default transport: one GET through the standard library, no redirect surprises."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return (
            int(response.status),
            str(response.headers.get("Content-Type", "")),
            response.read(),
        )


def _decode_image(data: bytes, *, url: str, name: str) -> np.ndarray:
    """Decode JPEG/PNG bytes to RGB uint8, naming the source in any failure."""
    if not data:
        raise FrameFetchError(f"camera {name!r} at {url} returned an empty body")
    array = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if array is None:
        head = data[:16].hex()
        raise FrameFetchError(
            f"camera {name!r} at {url} returned {len(data)} bytes that are not a decodable "
            f"image (first bytes {head}). A JPEG starts ffd8ff, a PNG 89504e47."
        )
    return cv2.cvtColor(array, cv2.COLOR_BGR2RGB)


def _maybe_b64(value: str) -> bytes | None:
    """Decode a base64 or ``data:`` string to bytes, or ``None`` if it is not one."""
    text = value.strip()
    if text.startswith("data:"):
        _, _, text = text.partition(",")
    if len(text) < 32:
        return None
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None


class HttpFrameSource:
    """One camera behind an HTTP GET that yields the latest frame.

    ``prime_url`` is fetched (and its body discarded) immediately before the
    frame URL, for rigs whose "latest frame" file is only written as the side
    effect of another endpoint.
    """

    def __init__(
        self,
        url: str,
        *,
        name: str = "overhead",
        token: str | None = None,
        timeout_s: float = 5.0,
        prime_url: str | None = None,
        fetcher: Fetcher | None = None,
    ) -> None:
        if not url:
            raise FrameFetchError(f"camera {name!r} was configured with an empty URL")
        self.url = url
        self.name = name
        self.prime_url = prime_url
        self.timeout_s = float(timeout_s)
        self._token = token
        self._fetch_fn: Fetcher = fetcher if fetcher is not None else _urllib_fetch

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "image/jpeg, image/png, application/json", "Cache-Control": "no-store"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _get(self, url: str) -> tuple[int, str, bytes]:
        """One GET, with every transport failure translated to a named, actionable fault."""
        try:
            status, content_type, body = self._fetch_fn(url, self._headers(), self.timeout_s)
        except urllib.error.HTTPError as exc:  # a real response, just not a good one
            detail = exc.read()[:200].decode("utf-8", "replace")
            raise FrameFetchError(
                f"camera {self.name!r} at {url} returned HTTP {exc.code}: {detail or exc.reason}. "
                + _remedy(exc.code)
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise FrameFetchError(
                f"camera {self.name!r} at {url} is unreachable ({exc}). Is the console "
                "or phone bridge running, and is the host/port right?"
            ) from exc
        if status >= 400:
            detail = body[:200].decode("utf-8", "replace")
            raise FrameFetchError(
                f"camera {self.name!r} at {url} returned HTTP {status}: {detail}. "
                + _remedy(status)
            )
        return status, content_type, body

    def fetch(self) -> np.ndarray:
        """Return the latest frame as ``(H, W, 3)`` RGB uint8."""
        if self.prime_url:
            self._get(self.prime_url)
        _, content_type, body = self._get(self.url)
        if "json" in content_type.lower():
            return self._from_json(body)
        return _decode_image(body, url=self.url, name=self.name)

    def _from_json(self, body: bytes) -> np.ndarray:
        """Dig an inline base64 image, or one follow-on image URL, out of a JSON reply."""
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FrameFetchError(
                f"camera {self.name!r} at {self.url} said it was JSON but is not: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise FrameFetchError(
                f"camera {self.name!r} at {self.url} returned JSON {type(payload).__name__}, "
                "expected an object with an image field or raw image bytes"
            )
        for key in _B64_KEYS:
            value = payload.get(key)
            if isinstance(value, str):
                data = _maybe_b64(value)
                if data is None:
                    raise FrameFetchError(
                        f"camera {self.name!r} at {self.url} has {key!r} but it is not base64"
                    )
                return _decode_image(data, url=self.url, name=self.name)
        for key in _AMBIGUOUS_KEYS:
            value = payload.get(key)
            if not isinstance(value, str):
                continue
            if value.startswith(("http://", "https://")):
                _, _, follow = self._get(value)
                return _decode_image(follow, url=value, name=self.name)
            data = _maybe_b64(value)
            if data is not None:
                return _decode_image(data, url=self.url, name=self.name)
        raise FrameFetchError(
            f"camera {self.name!r} at {self.url} returned JSON with no image in it "
            f"(keys: {sorted(payload)}). Point -E overhead_url at an endpoint that "
            "returns JPEG bytes, or one whose JSON carries image_b64 or an image URL."
        )


class StaticFrameSource:
    """A fixed frame, for the ``reference`` stream that never changes during a run."""

    def __init__(self, frame: np.ndarray, *, name: str = "reference") -> None:
        self.name = name
        self._frame = np.asarray(frame, dtype=np.uint8)

    def fetch(self) -> np.ndarray:
        """Return a copy, so a consumer mutating the observation cannot poison the source."""
        return self._frame.copy()


def _remedy(status: int) -> str:
    """The one-line fix for the status codes these rigs actually return."""
    if status == 401:
        return "That is a bad bearer: check -E camera_token / the CONSOLE_TOKEN it came from."
    if status == 403:
        return "The camera endpoint refused the token: check which token that service wants."
    if status == 404:
        return (
            "No such camera. List them with `curl -s -H 'Authorization: Bearer $CONSOLE_TOKEN' "
            "http://<host>:8002/camera/list`. On the carbot rig, GET /look before /snapshot.jpg "
            "(pass it as -E overhead_prime_url)."
        )
    if status == 503:
        return "The camera is cold or absent: start it, then retry. No frame means no score."
    return ""


# -- canvas corners ----------------------------------------------------------


class CornerFetchError(EmbodimentFault):
    """The tapped canvas corners could not be read or made sense of."""


def parse_corners(payload: Any) -> tuple[tuple[float, float], ...]:
    """Normalise whatever the corner service said into four ``(x, y)`` pairs, TL TR BR BL.

    Accepts a bare list of four pairs, a flat list of eight numbers, an object
    with ``canvas_corners``/``corners``/``points``, or an object keyed
    ``tl``/``tr``/``br``/``bl`` (each a pair or an ``{"x": .., "y": ..}``).
    Pixel coordinates are converted when the payload also states the image size;
    otherwise anything outside 0..1 is rejected rather than guessed at.
    """
    data = payload
    width = height = None
    if isinstance(data, dict):
        width = _number(data.get("image_width") or data.get("width"))
        height = _number(data.get("image_height") or data.get("height"))
        named = [data.get(key) for key in ("tl", "tr", "br", "bl")]
        if all(v is not None for v in named):
            data = named
        else:
            for key in ("canvas_corners", "corners", "points", "quad"):
                if key in data:
                    data = data[key]
                    break
            else:
                raise CornerFetchError(
                    f"no canvas corners in the reply (keys: {sorted(payload)}); expected "
                    "'canvas_corners' or 'corners', or tl/tr/br/bl"
                )
    if not isinstance(data, (list, tuple)):
        raise CornerFetchError(f"canvas corners must be a list, got {type(data).__name__}")
    flat: list[float] = []
    for item in data:
        if isinstance(item, dict):
            x, y = _number(item.get("x")), _number(item.get("y"))
            if x is None or y is None:
                raise CornerFetchError(f"corner {item!r} has no numeric x/y")
            flat.extend([x, y])
        elif isinstance(item, (list, tuple)):
            if len(item) != 2:
                raise CornerFetchError(f"corner {item!r} must be a pair")
            flat.extend(float(v) for v in item)
        else:
            value = _number(item)
            if value is None:
                raise CornerFetchError(f"canvas corner entry {item!r} is not a number or a pair")
            flat.append(value)
    if len(flat) != 8:
        raise CornerFetchError(
            f"expected 8 numbers (4 corners, TL TR BR BL), got {len(flat)}: {flat}"
        )
    pairs = [(flat[i], flat[i + 1]) for i in range(0, 8, 2)]
    out_of_unit = any(not (0.0 <= v <= 1.0) for pair in pairs for v in pair)
    if out_of_unit:
        if width and height:
            pairs = [(x / width, y / height) for x, y in pairs]
        else:
            raise CornerFetchError(
                f"canvas corners {pairs} are outside 0..1 and the reply did not say the "
                "image size, so they cannot be normalised. Post normalised corners, or "
                "include image_width/image_height."
            )
    if any(not (0.0 <= v <= 1.0) for pair in pairs for v in pair):
        raise CornerFetchError(f"canvas corners {pairs} are still outside 0..1 after scaling")
    return tuple(pairs)


def parse_corner_flag(text: str) -> tuple[tuple[float, float], ...]:
    """Parse the ``-E canvas_corners=x,y,x,y,x,y,x,y`` fallback flag."""
    parts = [p for p in text.replace(";", ",").split(",") if p.strip()]
    try:
        numbers = [float(p) for p in parts]
    except ValueError as exc:
        raise CornerFetchError(f"canvas_corners must be 8 numbers, got {text!r}: {exc}") from exc
    return parse_corners(numbers)


class HttpCornerSource:
    """The tapped canvas corners, fetched from wherever the phone app posts them."""

    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        timeout_s: float = 5.0,
        fetcher: Fetcher | None = None,
    ) -> None:
        self.url = url
        self.timeout_s = float(timeout_s)
        self._token = token
        self._fetch_fn: Fetcher = fetcher if fetcher is not None else _urllib_fetch

    def fetch(self) -> tuple[tuple[float, float], ...]:
        """Return four normalised ``(x, y)`` corners in TL TR BR BL order."""
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            status, _, body = self._fetch_fn(self.url, headers, self.timeout_s)
        except urllib.error.HTTPError as exc:
            raise CornerFetchError(
                f"canvas corners at {self.url} returned HTTP {exc.code}: {exc.reason}. "
                "Tap the four sheet corners in the app first, or drop -E corners_url "
                "and let the scorer find the sheet itself."
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise CornerFetchError(f"canvas corners at {self.url} are unreachable ({exc})") from exc
        if status >= 400:
            raise CornerFetchError(f"canvas corners at {self.url} returned HTTP {status}")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CornerFetchError(f"canvas corners at {self.url} are not JSON: {exc}") from exc
        return parse_corners(payload)


class StaticCornerSource:
    """Corners fixed once from the ``-E canvas_corners=`` flag."""

    def __init__(self, corners: Sequence[Sequence[float]]) -> None:
        self._corners = parse_corners([list(c) for c in corners])

    def fetch(self) -> tuple[tuple[float, float], ...]:
        """Return the configured corners."""
        return self._corners


def _number(value: Any) -> float | None:
    """Coerce to float, or ``None`` when the value is not a number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
