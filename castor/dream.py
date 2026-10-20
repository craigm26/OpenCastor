"""Dream Labs' Dream-1, reached with the user's own API key. Standard library only.

WHAT DREAM-1 IS. A vision-language-action model: an observation goes out (each
camera's last frames, the robot's state, a task in words) and a chunk of
actions comes back, 48 rows ahead at the rate the model was trained on. The
public base model, `dreamlabs/dream-1-beta`, drives two-arm YAM robots. Any
other body runs a model fine-tuned on that body's own recordings
(`dream-1-ft:<account>-<suffix>`), which the user makes on
https://platform.dreamlabs.ai.

WHY NOT THE `dreamlabs-client` PACKAGE. Two reasons, both on purpose. The
bridge that carries this (`castor.microduck_bridge`) runs anywhere Python 3.9
does with nothing installed, and that package needs numpy and Python 3.11.
And that package's high-level `DreamClient` records every camera tick and
state it is handed to Dream Labs' live service unless an internal flag says
not to. A robot in someone's home sends its pictures only in the request that
asks for an action, so this speaks the wire itself: `POST /v1/get_action`,
`POST /v1/end_session` and `GET /v1/models`, which is all a control loop
needs, and nothing else ever leaves the machine.

THE KEY. It lives where the SDK keeps it, `~/.dreamlabs/credentials` (or the
file `DREAM_CREDENTIALS` names), mode 0600, as a `DREAM_API_KEY=` line, so the
user's own `dreamcli` and this read the same file. `DREAM_API_KEY` in the
environment wins over the file, as it does for the SDK. Nothing here logs,
prints or returns the key: `hint()` shows its last four characters.
"""

from __future__ import annotations

import json
import os
import pathlib
import stat
import urllib.error
import urllib.request
from typing import Any, Optional

GATEWAY_URL = "https://api.dreamlabs.ai"
PLATFORM_URL = "https://platform.dreamlabs.ai"
BASE_MODEL = "dreamlabs/dream-1-beta"
KEY_ENV = "DREAM_API_KEY"
FILE_ENV = "DREAM_CREDENTIALS"
USER_AGENT = "opencastor-dream/1"

OK = "DA-0000"
OK_NEW_SESSION = "DA-0001"
UNAUTHORIZED = "DA-1000"

#: Frames each camera keeps, oldest first: what a fine-tuned model reads.
OBS_WINDOW = 9


class DreamError(Exception):
    """A refusal or a fault, in Dream Labs' own words, never with the key in it."""

    def __init__(
        self,
        message: str,
        code: str = "",
        reason: str = "",
        retryable: bool = False,
        retry_after_s: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason
        self.retryable = retryable
        self.retry_after_s = retry_after_s

    def as_dict(self) -> dict:
        return {
            "message": str(self),
            "code": self.code,
            "reason": self.reason,
            "retryable": self.retryable,
            "retry_after_s": self.retry_after_s,
        }


# ---------------------------------------------------------------------------
# The key
# ---------------------------------------------------------------------------


def credentials_path() -> pathlib.Path:
    named = os.environ.get(FILE_ENV)
    return (
        pathlib.Path(named).expanduser()
        if named
        else pathlib.Path.home() / ".dreamlabs" / "credentials"
    )


def _file_lines(path: pathlib.Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def load_key() -> Optional[str]:
    """The environment's key, else the credentials file's, else None."""
    env = os.environ.get(KEY_ENV, "").strip()
    if env:
        return env
    for line in _file_lines(credentials_path()):
        name, sep, value = line.partition("=")
        if sep and name.strip() == KEY_ENV and value.strip():
            return value.strip()
    return None


def check_key_text(key: str) -> str:
    """The key as it will be sent, or ValueError naming what is wrong (never the key)."""
    key = (key or "").strip()
    if not key:
        raise ValueError("the key is empty")
    if any(c in key for c in "\r\n\t ") or not key.isascii() or not key.isprintable():
        raise ValueError(
            "the key holds a space, a line break or a character such as a typographic quote; copy it again, whole"
        )
    if len(key) > 256:
        raise ValueError("that is longer than any Dream Labs key")
    return key


def save_key(key: str) -> pathlib.Path:
    """Writes the key into the credentials file, 0600, keeping every other line.

    The directory is made 0700. Any other line (the SDK's URL lines) stays as
    it was; an earlier `DREAM_API_KEY=` line is replaced.
    """
    key = check_key_text(key)
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent == pathlib.Path.home() / ".dreamlabs":
        os.chmod(path.parent, 0o700)
    kept = [line for line in _file_lines(path) if line.partition("=")[0].strip() != KEY_ENV]
    text = "\n".join(kept + [f"{KEY_ENV}={key}"]) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(text)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return path


def clear_key() -> bool:
    """Removes the key line from the credentials file; True when there was one."""
    path = credentials_path()
    lines = _file_lines(path)
    kept = [line for line in lines if line.partition("=")[0].strip() != KEY_ENV]
    if len(kept) == len(lines):
        return False
    if any(line.strip() for line in kept):
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
    else:
        path.unlink()
    return True


def hint(key: Optional[str]) -> Optional[str]:
    """Enough of the key to tell two apart: its last four characters."""
    return f"...{key[-4:]}" if key and len(key) >= 8 else ("set" if key else None)


# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------


def _call(method: str, url: str, key: str, body: Any = None, timeout_s: float = 15.0) -> Any:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {check_key_text(key)}")
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as reply:
            raw = reply.read()
    except urllib.error.HTTPError as error:
        # Both services answer refusals with a JSON body; read it rather than the status line.
        raw = error.read()
        if not raw:
            raise DreamError(
                f"Dream Labs answered HTTP {error.code}", code=f"HTTP_{error.code}"
            ) from None
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise DreamError(
            f"cannot reach Dream Labs: {reason}", code="network", retryable=True
        ) from None
    try:
        return json.loads(raw)
    except ValueError:
        raise DreamError(
            "Dream Labs answered something that is not JSON", code="protocol"
        ) from None


def list_models(key: str, timeout_s: float = 15.0) -> list[dict]:
    """The checkpoints this key may run, as the platform lists them. Also the key check."""
    answer = _call("GET", f"{PLATFORM_URL}/v1/models", key, timeout_s=timeout_s)
    if isinstance(answer, dict) and isinstance(answer.get("error"), dict):
        err = answer["error"]
        raise DreamError(str(err.get("message") or "refused"), code=str(err.get("code") or ""))
    if not isinstance(answer, list):
        raise DreamError(
            "the Dream platform answered something that is not a list of models", code="protocol"
        )
    return [m for m in answer if isinstance(m, dict) and isinstance(m.get("id"), str)]


def model_summary(model: dict, fits: Optional[dict] = None) -> dict:
    """What a person choosing a model needs: id, whose, runnable, cameras, and whether it fits `fits`."""
    out = {
        "id": model.get("id"),
        "owner": model.get("owner"),
        "servable": bool(model.get("servable")),
        "cameras": model.get("cameras"),
        "embodiment": model.get("embodiment"),
    }
    if fits is not None:
        out["fits"] = fits_embodiment(model, fits)
    return out


def fits_embodiment(model: dict, embodiment: dict) -> bool:
    """True when the model was trained on a body whose state, action and cameras are `embodiment`'s.

    Compares part names and widths, and camera roles. A model whose config the
    platform withholds (`parts_withheld`) or does not list cannot be shown to
    fit, so it does not.
    """
    config = model.get("embodiment_config")
    if not isinstance(config, dict):
        return False

    def widths(spans: Any) -> dict:
        if not isinstance(spans, dict):
            return {}
        out = {}
        for name, span in spans.items():
            if isinstance(span, (list, tuple)) and len(span) == 2:
                out[name] = int(span[1]) - int(span[0])
        return out

    for side in ("state", "action"):
        if widths(config.get(side)) != widths(embodiment.get(side)):
            return False
    return set((config.get("video") or {}).keys()) == set((embodiment.get("video") or {}).keys())


def _error_for(status: dict) -> DreamError:
    code = str(status.get("code") or "")
    name = str(status.get("name") or code)
    reason = str(status.get("reason") or "")
    message = f"Dream-1 refused the request: {code} {name}" + (f" ({reason})" if reason else "")
    if code == UNAUTHORIZED:
        message += ": the key was refused; check it on platform.dreamlabs.ai"
    retry_after = status.get("retry_after_s")
    return DreamError(
        message,
        code=code,
        reason=reason,
        retryable=bool(status.get("retryable")),
        retry_after_s=float(retry_after) if isinstance(retry_after, (int, float)) else None,
    )


class Session:
    """One episode against one checkpoint: the session id the gateway assigns, and the calls.

    Not thread-safe; one caller at a time, as the gateway expects.
    """

    def __init__(
        self,
        key: str,
        checkpoint: str = BASE_MODEL,
        robot: Optional[str] = None,
        timeout_s: float = 15.0,
    ) -> None:
        self.key = check_key_text(key)
        if not isinstance(checkpoint, str) or not checkpoint:
            raise ValueError("a checkpoint is a model id from the platform's model list")
        self.checkpoint = checkpoint
        self.robot = robot
        self.timeout_s = timeout_s
        self.session_id: Optional[str] = None
        self.last_timing: Optional[dict] = None

    def get_action(self, observation: dict) -> dict:
        """One observation out, the reply's `actions` back (`per_key`: one `action.<part>` per part)."""
        obs = dict(observation)
        if self.robot:
            obs["embodiment"] = self.robot
        body = {
            "checkpoint": self.checkpoint,
            "session_id": self.session_id,
            "reset": False,
            "observation": obs,
        }
        reply = _call(
            "POST", f"{GATEWAY_URL}/v1/get_action", self.key, body, timeout_s=self.timeout_s
        )
        status = reply.get("status") if isinstance(reply, dict) else None
        if not isinstance(status, dict) or not isinstance(status.get("code"), str):
            raise DreamError("the Dream gateway answered without a status", code="protocol")
        self.last_timing = reply.get("timing") if isinstance(reply.get("timing"), dict) else None
        returned = reply.get("session_id")
        sent = self.session_id
        if isinstance(returned, str) and returned:
            self.session_id = returned
        code = status["code"]
        if code == OK or (
            code == OK_NEW_SESSION and (sent is None or status.get("reason") == "new")
        ):
            actions = reply.get("actions")
            if not isinstance(actions, dict):
                raise DreamError("the Dream gateway answered OK with no actions", code="protocol")
            return actions
        if code == OK_NEW_SESSION:
            raise DreamError(
                "the episode was restarted on Dream Labs' side; start it again from the robot as it is now",
                code=code,
                reason=str(status.get("reason") or ""),
                retryable=True,
            )
        raise _error_for(status)

    def end(self) -> None:
        """Frees the gateway's seat now rather than on idle eviction; best effort, never raises."""
        if not self.session_id:
            return
        sid, self.session_id = self.session_id, None
        try:
            _call(
                "POST",
                f"{GATEWAY_URL}/v1/end_session",
                self.key,
                {"checkpoint": self.checkpoint, "session_id": sid},
                timeout_s=3.0,
            )
        except DreamError:
            pass


def chunk_rows(actions: dict, parts: dict) -> list[list[float]]:
    """`per_key` as rows of the action vector, one per step ahead.

    @param parts The embodiment's `action` spans, `{part: [start, end)}`.
    """
    per_key = actions.get("per_key")
    if not isinstance(per_key, dict):
        raise DreamError("the reply carries no per_key actions", code="protocol")
    width = max(int(end) for _, end in parts.values())
    steps = None
    rows: list[list[float]] = []
    for part, (start, end) in parts.items():
        values = per_key.get(f"action.{part}")
        if not isinstance(values, list):
            raise DreamError(f"the reply has no action.{part}", code="protocol")
        if steps is None:
            steps = len(values)
            rows = [[0.0] * width for _ in range(steps)]
        if len(values) != steps:
            raise DreamError(f"action.{part} has {len(values)} rows, not {steps}", code="protocol")
        span = int(end) - int(start)
        for i, row in enumerate(values):
            row = row if isinstance(row, list) else [row]
            if len(row) != span:
                raise DreamError(
                    f"action.{part} row {i} is {len(row)} wide, not {span}", code="protocol"
                )
            rows[i][int(start) : int(end)] = [float(v) for v in row]
    return rows


#: THE MICRODUCK AS DREAM-1 READS IT, castor's convention for a duck's
#: recordings and the models fine-tuned on them. State and action are the
#: walk command `[vx, vy, vyaw]` (m/s, m/s, rad/s): the action is what to
#: command next, the state what was commanded last, so the model drives the
#: gait the duck's own policy already walks rather than its servos. The camera
#: is the head's. `dream.key.check` with this as `embodiment` marks the models
#: a duck can run.
MICRODUCK_EMBODIMENT = {
    "state": {"velocity": [0, 3]},
    "action": {"velocity": [0, 3]},
    "video": {"head": "observation.images.head"},
}
