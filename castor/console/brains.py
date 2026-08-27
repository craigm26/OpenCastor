"""The robot's own brains: Anthropic (subscription) and Gemini Robotics-ER 2.0.

Both run ON THE ROBOT rather than on the phone, for different reasons:

  * Anthropic uses the operator's CLAUDE SUBSCRIPTION, which is already logged
    in on this machine. Shipping subscription credentials to a phone would be
    both fragile and wrong; the robot asks on the user's behalf instead.
  * Gemini Robotics-ER is IMAGE-native — it reasons about a camera frame and
    replies with points on that image. The frames originate here, so sending
    them from here avoids a pointless round trip through the phone.

Neither key is ever returned to a client. ``/models/providers`` reports only
whether a provider is configured, never a key, and never a pricing or signup
URL — a link to a provider's purchase page in an iOS binary is the classic
anti-steering rejection.

THE ONE INVARIANT THIS FILE EXISTS TO KEEP: the `claude` child process runs with
ANTHROPIC_API_KEY REMOVED from its environment. A metered key in the ambient
environment (this bench has had one in /etc/environment for months) silently
converts every chat turn from "the subscription the operator already pays for"
into per-token billing, with an identical-looking answer. `test_console.py`
pins it.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .config import robot_home

#: The env var that must never reach the CLI child. Named once, so the test and
#: the code assert against the same string.
METERED_KEY_ENV = "ANTHROPIC_API_KEY"

GEMINI_ER_MODEL = os.environ.get("GEMINI_ER_MODEL", "gemini-robotics-er-2-preview")
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def secrets_file() -> Path:
    """Operator-managed key file inside the robot home (written 0600)."""
    return robot_home() / "brain-secrets.env"


def _secret(name: str) -> str:
    """Read a secret from the operator-managed file, falling back to env."""
    value = os.environ.get(name, "")
    if value:
        return value
    try:
        for line in secrets_file().read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() == name:
                return val.strip()
    except OSError:
        pass
    return ""


# --------------------------------------------------------------------------- #
# Anthropic — via the operator's logged-in subscription
# --------------------------------------------------------------------------- #


def claude_cli() -> Path | None:
    """The `claude` binary this robot should shell, or None if there is none.

    ``CLAUDE_CLI`` wins, then the standard user install, then PATH. The bench
    module hard-coded ``~/.local/bin/claude``, which is right on this Pi and
    wrong on a host that installed it anywhere else — a robot with a perfectly
    good subscription then reported "claude CLI not installed".
    """
    override = os.environ.get("CLAUDE_CLI")
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None
    standard = Path.home() / ".local" / "bin" / "claude"
    if standard.is_file():
        return standard
    found = shutil.which("claude")
    return Path(found) if found else None


def anthropic_available() -> bool:
    return Path.home().joinpath(".claude", ".credentials.json").is_file()


def anthropic_chat(
    system: str,
    message: str,
    history: list[dict],
    timeout: float = 180.0,
    image_jpeg: bytes | None = None,
) -> dict:
    """One turn through the local `claude` CLI, using the subscription.

    ANTHROPIC_API_KEY is explicitly REMOVED from the child environment: if one
    is set, the CLI bills per token against that key instead of using the
    subscription the operator intends — and the answer looks exactly the same,
    so nothing surfaces the mistake except the invoice.
    """
    exe = claude_cli()
    if exe is None:
        raise RuntimeError("claude CLI not installed")

    env = {k: v for k, v in os.environ.items() if k != METERED_KEY_ENV}
    transcript = []
    for turn in history[-8:]:
        role = turn.get("role")
        if role in ("user", "assistant") and turn.get("content"):
            transcript.append(f"{role}: {turn['content']}")
    transcript.append(f"user: {message}")
    prompt = "\n".join(transcript)

    home = robot_home()
    # Vision via a file on disk rather than an inline attachment: this bridge
    # drives the `claude` CLI, which reads images with its own Read tool. The
    # file is written inside the CLI's working directory so a bare relative
    # name resolves, and removed in the `finally` below — a frame from
    # someone's phone camera should not outlive the question it answered.
    frame_path = None
    if image_jpeg:
        if not image_jpeg.startswith(b"\xff\xd8"):
            raise RuntimeError("image must be a JPEG")
        frame_dir = home / "frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        frame_path = frame_dir / f"phone-{uuid.uuid4().hex[:10]}.jpg"
        frame_path.write_bytes(image_jpeg)
        prompt = (
            f"The user's phone camera is showing this image: "
            f"frames/{frame_path.name}\n"
            f"Read that image file, then answer.\n\n" + prompt
        )

    try:
        proc = subprocess.run(
            [str(exe), "-p", prompt, "--append-system-prompt", system, "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=str(home),
        )
    finally:
        if frame_path is not None:
            frame_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "claude failed")[:300])
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        # Older CLI builds print bare text; treat it as the answer.
        return {"content": proc.stdout.strip(), "thinking": ""}
    content = payload.get("result") or payload.get("content") or ""
    if isinstance(content, list):
        content = "".join(block.get("text", "") for block in content if isinstance(block, dict))
    return {"content": str(content).strip(), "thinking": ""}


# --------------------------------------------------------------------------- #
# Gemini Robotics-ER 2.0 — embodied reasoning over a camera frame
# --------------------------------------------------------------------------- #


def gemini_available() -> bool:
    return bool(_secret("GEMINI_API_KEY"))


def gemini_er(
    prompt: str,
    image_jpeg: bytes | None = None,
    *,
    thinking_level: str = "medium",
    timeout: float = 120.0,
) -> dict:
    """Ask Gemini Robotics-ER about the scene.

    The model answers spatial questions about an image — pointing at objects,
    bounding them, sketching trajectories. Points come back as [y, x] normalized
    to 0-1000, which is NOT the usual (x, y) order and not pixels; callers must
    scale by the real frame size before treating them as image coordinates.

    thinking_level: "high" for hard spatial problems, "medium" for interactive
    latency. Google recommends medium for most embodied use.
    """
    key = _secret("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("no GEMINI_API_KEY configured on the robot")

    parts: list[dict] = []
    if image_jpeg:
        parts.append(
            {
                "inlineData": {
                    "mimeType": "image/jpeg",
                    "data": base64.b64encode(image_jpeg).decode("ascii"),
                }
            }
        )
    parts.append({"text": prompt})

    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"thinkingConfig": {"thinkingLevel": thinking_level}},
    }
    req = urllib.request.Request(
        GEMINI_ENDPOINT.format(model=GEMINI_ER_MODEL),
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        # Never echo the key back, even if the upstream error quotes the request.
        raise RuntimeError(f"gemini {exc.code}: {detail.replace(key, '<redacted>')}") from None

    text = ""
    for candidate in payload.get("candidates", []):
        for part in (candidate.get("content") or {}).get("parts", []):
            if "text" in part:
                text += part["text"]
    return {
        "content": text.strip(),
        "thinking": "",
        "points": _parse_points(text),
        "model": GEMINI_ER_MODEL,
    }


def set_gemini_key(key: str) -> tuple[bool, str]:
    """Persist (or clear) the Gemini key, verifying it before saving.

    Saving an unverified key would surface much later as a confusing chat
    failure, so a cheap call confirms the key works first. On failure the
    stored key is left untouched.
    """
    if key:
        try:
            _probe_gemini(key)
        except Exception as exc:  # noqa: BLE001 - any failure means "not saved"
            return False, f"key rejected by Google: {exc}"

    path = secrets_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if path.is_file():
        lines = [
            ln
            for ln in path.read_text().splitlines()
            if not ln.strip().startswith("GEMINI_API_KEY=")
        ]
    lines.append(f"GEMINI_API_KEY={key}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)
    # The file is read per call, so this takes effect with no restart.
    os.environ.pop("GEMINI_API_KEY", None)
    return True, "key verified and saved on the robot" if key else "key cleared"


def _probe_gemini(key: str) -> None:
    """Cheapest possible call that proves the key is accepted."""
    body = {"contents": [{"parts": [{"text": "ok"}]}]}
    req = urllib.request.Request(
        GEMINI_ENDPOINT.format(model=GEMINI_ER_MODEL),
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:200]
        raise RuntimeError(f"{exc.code} {detail.replace(key, '<redacted>')}") from None


def _parse_points(text: str) -> list[dict]:
    """Pull ``{point: [y, x], label}`` objects out of the reply.

    The model is asked for JSON but, like any model, may wrap it in prose or a
    fenced block — so scan for the first JSON array rather than trusting the
    whole string to parse.
    """
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        point = item.get("point")
        if (
            isinstance(point, list)
            and len(point) == 2
            and all(isinstance(n, (int, float)) for n in point)
        ):
            out.append(
                {
                    "y": float(point[0]),
                    "x": float(point[1]),
                    "label": str(item.get("label", "")),
                }
            )
    return out
