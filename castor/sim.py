"""
castor/sim.py — Gazebo/Webots simulation launcher (issue #265).

Provides the ``castor sim`` CLI command: detects available simulators,
starts the castor gateway in the background, then launches the simulator
with the robot's RCAN config.

Supported simulators:
  - ``gazebo``  — Gazebo (classic or Gz/Harmonic)
  - ``webots``  — Webots R2023+

RCAN config::

    simulation:
      enabled: true
      backend: gazebo   # or webots

CLI::

    castor sim gazebo --config robot.rcan.yaml [--headless]
    castor sim webots --config robot.rcan.yaml [--headless]

The gateway process is started in the background (via subprocess), then the
simulator is launched in the foreground.  On exit, the gateway is terminated.

``CASTOR_GATEWAY_URL`` env var is injected into the simulator process so
plugins can connect to the local gateway.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from typing import Optional

logger = logging.getLogger("OpenCastor.Sim")

# ---------------------------------------------------------------------------
# Simulator detection
# ---------------------------------------------------------------------------

_GAZEBO_BINARIES = ["gz", "gazebo", "gzserver"]
_WEBOTS_BINARIES = ["webots", "webots-bin"]


def find_simulator(backend: str) -> Optional[str]:
    """Return the full path to the simulator binary, or None if not found.

    Args:
        backend: ``"gazebo"`` or ``"webots"``.

    Returns:
        Full path to the binary, or ``None`` if not in PATH.
    """
    candidates = _GAZEBO_BINARIES if backend.lower() == "gazebo" else _WEBOTS_BINARIES
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    return None


def list_available_simulators() -> list[str]:
    """Return the list of simulator names detectable on this system.

    Returns:
        List of available simulator name strings (e.g. ``["gazebo"]``).
    """
    found = []
    for backend in ("gazebo", "webots"):
        if find_simulator(backend) is not None:
            found.append(backend)
    return found


# ---------------------------------------------------------------------------
# Gateway launcher (background subprocess)
# ---------------------------------------------------------------------------


def _start_gateway(config_path: str, port: int = 8000) -> subprocess.Popen:
    """Start the castor gateway in the background.

    Args:
        config_path: Path to the RCAN config YAML file.
        port:        Gateway port (default 8000).

    Returns:
        Running :class:`subprocess.Popen` process handle.
    """
    cmd = [sys.executable, "-m", "castor", "gateway", "--config", config_path, "--port", str(port)]
    logger.info("Starting castor gateway: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "OPENCASTOR_SIM_MODE": "1"},
    )
    return proc


def _wait_for_gateway(url: str, timeout: float = 10.0, interval: float = 0.5) -> bool:
    """Wait until the gateway HTTP endpoint responds.

    Args:
        url:      Gateway health URL (e.g. ``"http://localhost:8000/health"``).
        timeout:  Max wait time in seconds.
        interval: Poll interval in seconds.

    Returns:
        True if the gateway responded before timeout, False otherwise.
    """
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1.0)
            return True
        except Exception:
            time.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# Simulator launcher
# ---------------------------------------------------------------------------


def _build_sim_args(
    binary: str,
    backend: str,
    config_path: str,
    headless: bool = False,
    extra_args: Optional[list[str]] = None,
) -> list[str]:
    """Build the simulator command-line argument list.

    Args:
        binary:      Path to the simulator binary.
        backend:     ``"gazebo"`` or ``"webots"``.
        config_path: Path to RCAN config (passed as env; not always as CLI arg).
        headless:    True to add headless/no-GUI flag.
        extra_args:  Additional CLI arguments to append.

    Returns:
        Command list suitable for :class:`subprocess.Popen`.
    """
    cmd = [binary]

    if backend == "gazebo":
        if headless:
            cmd.append("--headless")
        # Gz Sim (Harmonic) expects a world file; fall back to empty world
        cmd.extend(extra_args or ["empty.sdf"])

    elif backend == "webots":
        if headless:
            cmd.extend(["--headless", "--stdout", "--stderr"])
        cmd.extend(extra_args or [])

    return cmd


def launch_simulator(
    backend: str,
    config_path: str,
    headless: bool = False,
    gateway_port: int = 8000,
    extra_args: Optional[list[str]] = None,
    start_gateway: bool = True,
) -> int:
    """Launch the simulator with the castor gateway running in the background.

    Args:
        backend:       Simulator backend (``"gazebo"`` or ``"webots"``).
        config_path:   Path to the RCAN config YAML.
        headless:      True to launch without a GUI window.
        gateway_port:  Port for the background gateway.
        extra_args:    Additional CLI arguments forwarded to the simulator.
        start_gateway: When False, skip gateway startup (useful in tests).

    Returns:
        Simulator exit code.

    Raises:
        RuntimeError: When the simulator binary is not found in PATH.
    """
    binary = find_simulator(backend)
    if binary is None:
        raise RuntimeError(
            f"Simulator '{backend}' not found in PATH. "
            f"Install it or check your PATH. "
            f"Detected: {list_available_simulators() or ['none']}"
        )

    gateway_url = f"http://localhost:{gateway_port}"
    gateway_proc: Optional[subprocess.Popen] = None

    if start_gateway:
        logger.info("Starting gateway on port %d …", gateway_port)
        gateway_proc = _start_gateway(config_path, port=gateway_port)
        ok = _wait_for_gateway(f"{gateway_url}/health", timeout=15.0)
        if not ok:
            logger.warning("Gateway did not respond within 15 s — proceeding anyway")

    sim_cmd = _build_sim_args(binary, backend, config_path, headless, extra_args)
    sim_env = {
        **os.environ,
        "CASTOR_GATEWAY_URL": gateway_url,
        "CASTOR_RCAN_CONFIG": str(config_path),
    }

    logger.info("Launching %s: %s", backend, " ".join(sim_cmd))
    try:
        sim_proc = subprocess.Popen(sim_cmd, env=sim_env)
        sim_proc.wait()
        exit_code = sim_proc.returncode
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt — shutting down simulator")
        try:
            sim_proc.send_signal(signal.SIGINT)
            sim_proc.wait(timeout=5)
        except Exception:
            sim_proc.kill()
        exit_code = 130  # Ctrl+C
    finally:
        if gateway_proc is not None:
            logger.info("Stopping gateway …")
            try:
                gateway_proc.send_signal(signal.SIGINT)
                gateway_proc.wait(timeout=5)
            except Exception:
                gateway_proc.kill()

    return exit_code


# ---------------------------------------------------------------------------
# CLI handler (called from castor/cli.py)
# ---------------------------------------------------------------------------


def cmd_sim(args) -> None:
    """CLI handler for ``castor sim <backend>``.

    Args:
        args: argparse namespace with ``backend``, ``config``, ``headless``, ``port`` attrs.
    """
    backend = getattr(args, "backend", "gazebo")
    config = getattr(args, "config", "robot.rcan.yaml")
    headless = getattr(args, "headless", False)
    port = int(getattr(args, "port", 8000))
    extra = getattr(args, "extra", None)

    logger.info("castor sim %s --config %s headless=%s", backend, config, headless)
    try:
        rc = launch_simulator(
            backend=backend,
            config_path=config,
            headless=headless,
            gateway_port=port,
            extra_args=extra,
        )
        sys.exit(rc)
    except RuntimeError as exc:
        logger.error("Sim launch failed: %s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
