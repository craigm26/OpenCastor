"""A localhost OpenAI/Anthropic endpoint backed by the Claude Code CLI.

Each HTTP request is fulfilled by exactly one ``claude -p`` subprocess, so the
owner's Claude subscription answers robot-policy calls that would otherwise
need a metered ``ANTHROPIC_API_KEY``. Nothing is cached or carried between
requests: ``inspect-robots`` resends the whole conversation every turn, which
is what makes a stateless one-shot CLI call a faithful stand-in for the API.

What this is not
----------------
Answers come back through Claude Code's own harness, so they are labelled
``wire=claude-code-cli`` and are **not** comparable to raw-API results. See
``docs/subscription.md``.

Run it::

    sacpaint-claude-shim --port 8931 --model haiku

Then point a client at ``http://127.0.0.1:8931/v1``.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from castor.bench.sacpaint.claude_shim.translate import (
    Prepared,
    TranslationError,
    anthropic_response,
    anthropic_to_chat_body,
    chat_response,
    prepare,
)

log = logging.getLogger("sacpaint.claude_shim")

#: Body bigger than this is refused outright rather than read into memory.
MAX_BODY_BYTES = 64 * 1024 * 1024

#: Environment variable the per-run token arrives in when `sacpaint run
#: --subscription` starts the shim. Read once at startup and popped, so it is
#: never inherited by the `claude -p` children.
TOKEN_ENV = "SACPAINT_SHIM_TOKEN"

#: How many `claude -p` children may be in flight at once by default. The bench
#: drives one call at a time, so two leaves headroom without letting a
#: co-resident caller fork the machine flat.
DEFAULT_MAX_CONCURRENT_CHILDREN = 2


class ClaudeCLIError(RuntimeError):
    """The CLI could not be run, or returned something unusable."""


class ShimBusy(RuntimeError):
    """Every child slot is taken; the caller should retry rather than queue."""


class ClaudeRunner:
    """Runs one ``claude -p`` per request and returns the parsed envelope."""

    def __init__(
        self,
        *,
        claude_bin: str = "claude",
        model: str = "haiku",
        max_turns: int = 6,
        timeout_s: float = 300.0,
        workdir: Path | None = None,
        keep_frames: bool = False,
        max_concurrent_children: int = DEFAULT_MAX_CONCURRENT_CHILDREN,
    ):
        self.claude_bin = claude_bin
        self.model = model
        self.max_turns = max_turns
        self.timeout_s = timeout_s
        self.keep_frames = keep_frames
        self.workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="sacpaint-shim-"))
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.calls = 0
        self._lock = threading.Lock()
        if max_concurrent_children < 1:
            raise ValueError("max_concurrent_children must be at least 1")
        self.max_concurrent_children = max_concurrent_children
        # A hard ceiling, not a queue: ThreadingHTTPServer will happily start a
        # thread per connection, and each thread wants its own `claude -p`.
        # Without this a burst of requests forks the robot's Pi flat.
        self._slots = threading.BoundedSemaphore(max_concurrent_children)
        self.children = 0

    @contextlib.contextmanager
    def child_slot(self):
        """Hold one of the ``max_concurrent_children`` slots, or raise ShimBusy."""
        if not self._slots.acquire(blocking=False):
            raise ShimBusy(
                f"all {self.max_concurrent_children} claude child slots are busy; retry shortly"
            )
        with self._lock:
            self.children += 1
        try:
            yield
        finally:
            with self._lock:
                self.children -= 1
            self._slots.release()

    # -- command construction -------------------------------------------------

    def build_argv(self, plan: Prepared, image_dir: Path) -> list[str]:
        """The exact argv for one invocation. Split out so tests can assert it."""
        argv = [
            self.claude_bin,
            "-p",
            "--model",
            plan.model or self.model,
            "--output-format",
            "json",
            "--max-turns",
            str(self.max_turns),
            # `--restricted` drops Bash and the other code-running tools and
            # confines file reads to the working directories. It also cuts the
            # harness system prompt roughly in half, which matters because every
            # request pays for it again.
            "--restricted",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--setting-sources",
            "",
            "--permission-prompts",
            "none",
            "--no-session-persistence",
            "--add-dir",
            str(image_dir),
        ]
        if plan.system_prompt:
            argv += ["--system-prompt", plan.system_prompt]
        if plan.json_schema is not None:
            argv += ["--json-schema", json.dumps(plan.json_schema, ensure_ascii=False)]
        # Read is the only tool the model needs, and only to open camera frames.
        argv += ["--allowedTools", "Read" if plan.needs_read_tool else ""]
        return argv

    # -- execution ------------------------------------------------------------

    def run(self, plan: Prepared, image_dir: Path) -> dict[str, Any]:
        """Invoke the CLI once and return its decoded JSON envelope."""
        argv = self.build_argv(plan, image_dir)
        env = dict(os.environ)
        # The whole point is the subscription: never let a stray key meter this.
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)

        with self._lock:
            self.calls += 1
            call_index = self.calls

        log.info(
            "call %d -> %s (%d images)",
            call_index,
            plan.model or self.model,
            len(plan.image_paths),
        )
        with self.child_slot():
            try:
                completed = subprocess.run(
                    argv,
                    input=plan.prompt,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=self.timeout_s,
                    cwd=str(image_dir),
                    env=env,
                )
            except FileNotFoundError as exc:
                raise ClaudeCLIError(
                    f"claude CLI not found at {self.claude_bin!r}; pass --claude-bin with the full path (often ~/.local/bin/claude)"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise ClaudeCLIError(f"claude CLI timed out after {self.timeout_s}s") from exc

        stdout = (completed.stdout or "").strip()
        # Parse before judging the exit code. The CLI routinely exits non-zero
        # while still printing a complete envelope containing a perfectly good
        # answer (a schema-validation retry, a max-turns stop). Throwing that
        # away turned one paid call into a 502, and the caller's 5xx retry then
        # spent a second one. Only a genuinely unusable envelope is an error.
        envelope: dict[str, Any] | None = None
        if stdout:
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                envelope = parsed

        if envelope is None:
            if completed.returncode != 0:
                detail = (completed.stderr or "").strip() or stdout or "<no output>"
                raise ClaudeCLIError(f"claude CLI exited {completed.returncode}: {detail[:800]}")
            if not stdout:
                raise ClaudeCLIError("claude CLI produced no output")
            raise ClaudeCLIError(f"claude CLI output was not JSON: {stdout[:400]}")

        if completed.returncode != 0:
            log.warning(
                "call %d: CLI exited %d (%s) but printed a usable envelope",
                call_index,
                completed.returncode,
                envelope.get("subtype", "no subtype"),
            )
        if envelope.get("is_error"):
            errors = envelope.get("errors") or [envelope.get("subtype", "unknown error")]
            # A max-turns stop still carries a usable result when the model
            # answered before spending its last turn, so only a truly empty
            # result is fatal.
            if not envelope.get("result") and not envelope.get("structured_output"):
                raise ClaudeCLIError(f"claude CLI reported an error: {errors}")
            log.warning("call %d: CLI flagged %s but returned a result", call_index, errors)
        return envelope

    def scratch_for_request(self) -> Path:
        """A fresh per-request directory for camera frames."""
        path = self.workdir / uuid.uuid4().hex[:12]
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cleanup(self, path: Path) -> None:
        if not self.keep_frames:
            shutil.rmtree(path, ignore_errors=True)


class ShimHandler(BaseHTTPRequestHandler):
    """Routes the two endpoints the agent policies actually speak."""

    server_version = "sacpaint-claude-shim/1.0"
    runner: ClaudeRunner  # injected by make_server
    auth_token: str  # injected by make_server; every POST must present it

    # -- plumbing -------------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, status: int, message: str, *, error_type: str = "shim_error") -> None:
        log.error("HTTP %d: %s", status, message)
        self._send(status, {"error": {"message": message, "type": error_type}})

    def _presented_token(self) -> str | None:
        """The bearer token on this request, from either wire's usual header."""
        header = self.headers.get("Authorization")
        if header:
            scheme, _, value = header.partition(" ")
            if scheme.lower() == "bearer" and value.strip():
                return value.strip()
            return header.strip() or None
        # The Anthropic Messages wire sends the key as x-api-key instead.
        key = self.headers.get("x-api-key")
        return key.strip() if key and key.strip() else None

    def _authorized(self) -> bool:
        """Constant-time check of the per-run token.

        The shim binds loopback, but every agent session on this robot runs as
        the same uid, so loopback is not a boundary: without this check any
        co-resident process can spend the owner's subscription.
        """
        presented = self._presented_token()
        if presented is None:
            return False
        return hmac.compare_digest(presented, self.auth_token)

    def _reject_unauthorized(self) -> None:
        # Drain nothing and keep no connection: an unauthenticated caller gets
        # one answer and the socket back.
        self.close_connection = True
        self._error(
            401,
            "this shim needs the run's bearer token (Authorization: Bearer ...)",
            error_type="authentication_error",
        )

    def _read_body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._error(400, "invalid Content-Length")
            return None
        if length <= 0:
            self._error(400, "empty request body")
            return None
        if length > MAX_BODY_BYTES:
            self._error(413, f"request body over {MAX_BODY_BYTES} bytes")
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._error(400, f"request body was not JSON: {exc}")
            return None

    # -- routes ---------------------------------------------------------------

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("/healthz", "/v1/healthz"):
            self._send(
                200,
                {
                    "status": "ok",
                    "model": self.runner.model,
                    "calls": self.runner.calls,
                    "children": self.runner.children,
                    "max_concurrent_children": self.runner.max_concurrent_children,
                    "wire": "claude-code-cli",
                },
            )
        elif path in ("/v1/models", "/models"):
            self._send(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": name, "object": "model", "owned_by": "claude-code-cli"}
                        for name in ("haiku", "sonnet", "opus", "fable")
                    ],
                },
            )
        else:
            self._error(404, f"no route for GET {self.path}")

    def do_POST(self) -> None:
        if not self._authorized():
            self._reject_unauthorized()
            return
        path = self.path.split("?", 1)[0].rstrip("/")
        if path.endswith("/chat/completions"):
            self._complete(anthropic=False)
        elif path.endswith("/messages"):
            self._complete(anthropic=True)
        else:
            self._error(404, f"no route for POST {self.path}")

    def _complete(self, *, anthropic: bool) -> None:
        body = self._read_body()
        if body is None:
            return
        if anthropic:
            try:
                body = anthropic_to_chat_body(body)
            except TranslationError as exc:
                self._error(400, str(exc))
                return

        scratch = self.runner.scratch_for_request()
        try:
            try:
                plan = prepare(body, scratch)
            except TranslationError as exc:
                self._error(400, str(exc))
                return
            try:
                envelope = self.runner.run(plan, scratch)
            except ShimBusy as exc:
                # 429, not a queue: the caller decides whether to wait. Holding
                # the connection open would let a burst pile up unbounded.
                self._error(429, str(exc), error_type="rate_limit_error")
                return
            except ClaudeCLIError as exc:
                # 502: the upstream (the CLI) failed, not the caller's request.
                # The agent policy retries 5xx, which is the behaviour we want
                # for a transient CLI hiccup.
                self._error(502, str(exc))
                return
            model = plan.model or self.runner.model
            builder = anthropic_response if anthropic else chat_response
            self._send(200, builder(envelope, plan.tool_names, model))
        finally:
            self.runner.cleanup(scratch)


def make_server(
    runner: ClaudeRunner,
    host: str = "127.0.0.1",
    port: int = 8931,
    *,
    auth_token: str,
) -> ThreadingHTTPServer:
    """Build (but do not start) the HTTP server bound to ``host:port``.

    ``auth_token`` is required on purpose: there is no such thing as an
    unauthenticated shim, because loopback is not a boundary between processes
    that share a uid.
    """
    if not auth_token:
        raise ValueError("the shim needs a non-empty auth_token")
    handler = type(
        "BoundShimHandler", (ShimHandler,), {"runner": runner, "auth_token": auth_token}
    )
    return ThreadingHTTPServer((host, port), handler)


def resolve_auth_token(explicit: str | None = None) -> tuple[str, bool]:
    """The token for this shim, and whether it had to be invented.

    Order: the argument, then ``SACPAINT_SHIM_TOKEN`` (popped from the
    environment so no child inherits it), then a fresh one.
    """
    if explicit:
        os.environ.pop(TOKEN_ENV, None)
        return explicit, False
    from_env = os.environ.pop(TOKEN_ENV, "").strip()
    if from_env:
        return from_env, False
    return secrets.token_urlsafe(32), True


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8931,
    model: str = "haiku",
    claude_bin: str = "claude",
    max_turns: int = 6,
    timeout_s: float = 300.0,
    keep_frames: bool = False,
    auth_token: str | None = None,
    max_concurrent_children: int = DEFAULT_MAX_CONCURRENT_CHILDREN,
) -> None:
    """Run the shim until interrupted."""
    runner = ClaudeRunner(
        claude_bin=claude_bin,
        model=model,
        max_turns=max_turns,
        timeout_s=timeout_s,
        keep_frames=keep_frames,
        max_concurrent_children=max_concurrent_children,
    )
    token, invented = resolve_auth_token(auth_token)
    httpd = make_server(runner, host, port, auth_token=token)
    log.info(
        "sacpaint-claude-shim on http://%s:%d/v1 (model=%s, claude=%s, frames in %s)",
        host,
        port,
        model,
        claude_bin,
        runner.workdir,
    )
    print(
        f"sacpaint-claude-shim listening on http://{host}:{port}/v1 (model={model}, "
        f"wire=claude-code-cli, max_concurrent_children={max_concurrent_children})",
        file=sys.stderr,
        flush=True,
    )
    if invented:
        # Hand-started shim: nobody passed a token, so print the one it made.
        # `sacpaint run --subscription` passes its own and never lands here.
        print(
            f"bearer token for this shim (send it as Authorization: Bearer ...): {token}",
            file=sys.stderr,
            flush=True,
        )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
