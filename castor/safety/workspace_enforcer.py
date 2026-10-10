"""
Re-check the standing motor command every control cycle, whatever the brain's step rate.

``SafetyLayer.enforce_workspace()`` re-checks the command standing on ``/dev/motor`` against the
workspace policy (:mod:`castor.safety.workspace`), but until now nothing called it except a refused
write. ``castor/main.py`` writes ``/dev/motor`` once per brain step, so with a slow brain (2 Hz is
ordinary for a cloud model) a full-speed command that was safe when it was accepted kept running
for the rest of the step after it stopped being safe. That is the gap the EV-03 hostile-model test
left open after the workspace policy was added.

:class:`WorkspaceEnforcer` closes it with a thread that calls ``enforce_workspace()`` at a fixed
rate (50 Hz by default, ``safety.workspace.enforce_hz``). When the standing command has to go, the
SafetyLayer replaces it on ``/dev/motor`` with the policy's refusal command (no translation) and
audits a ``workspace_enforced`` row, and the enforcer calls ``halt()`` to stop the motors.

THE ENFORCER ONLY EVER REMOVES MOTION. ``halt()`` stops the motors; the enforcer never hands the
driver the refusal command's turn rate. It cannot know whether the motors are still running the
standing command (the watchdog, a bounds stop or an e-stop may have stopped them already), and a
safety monitor must never be what starts a motor after one of those.

THE LOCK. The brain step writes ``/dev/motor``, reads it back and hands it to the driver. A re-check
between the read-back and the driver call would replace a command the driver is about to be given;
the driver would then run the old command while ``/dev/motor`` held the safe replacement, and no
later re-check would see it. So whoever commands the motors holds ``SafetyLayer.motor_lock`` from
the write to the driver call, and :meth:`WorkspaceEnforcer.step` holds it for the re-check and the
halt.

TIMING. A command that passes one re-check runs until the next, so the workspace's ``reaction_s``
has to cover one enforcement period plus the latency to the motors. :func:`start_workspace_enforcer`
refuses a rate whose period is longer than ``reaction_s``.

Usage::

    enforcer = start_workspace_enforcer(fs.safety, hz=50.0, halt=driver.stop)  # None if no policy
    ...
    enforcer.stop()                                                             # on shutdown
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("OpenCastor.Safety.WorkspaceEnforcer")

#: Default re-check rate, Hz: one cycle of a 50 Hz motor control loop.
DEFAULT_ENFORCE_HZ = 50.0


class WorkspaceEnforcer:
    """Calls ``safety.enforce_workspace()`` every control cycle and halts the motors when it acts.

    Args:
        safety: The SafetyLayer (``enforce_workspace(path)``, ``motor_lock`` and ``ns``).
        hz:     Re-check rate; one cycle every ``1 / hz`` seconds.
        halt:   Stops the motors (``driver.stop``). None when nothing drives hardware (simulation);
                then only ``/dev/motor`` changes.
        path:   The motor node to re-check.

    :meth:`step` runs one cycle and can be called directly (a simulation stepping its own clock
    does); :meth:`start` and :meth:`stop` run it on a thread at ``hz``.
    """

    def __init__(
        self,
        safety: Any,
        *,
        hz: float = DEFAULT_ENFORCE_HZ,
        halt: Optional[Callable[[], Any]] = None,
        path: str = "/dev/motor",
    ) -> None:
        if isinstance(hz, bool) or not isinstance(hz, (int, float)) or not math.isfinite(hz):
            raise ValueError(f"hz must be a positive number, got {hz!r}")
        if hz <= 0:
            raise ValueError(f"hz must be a positive number, got {hz!r}")
        self.safety = safety
        self.hz = float(hz)
        self.period_s = 1.0 / self.hz
        self.path = path
        self._halt = halt
        #: The SafetyLayer's motor lock (see the module docstring).
        self.lock = getattr(safety, "motor_lock", None) or threading.RLock()
        self._stop_requested = threading.Event()
        self._thread: Optional[threading.Thread] = None
        #: Cycles run, cycles that replaced the standing command, cycles that started late.
        self.cycles = 0
        self.enforced = 0
        self.overruns = 0

    @property
    def running(self) -> bool:
        """True while the re-check thread is alive."""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def step(self) -> bool:
        """Run one control cycle. Returns True if the standing command may keep running.

        False means the standing command was replaced (or could not be checked, in which case a
        stop was written) and ``halt()`` was called.
        """
        with self.lock:
            self.cycles += 1
            try:
                if self.safety.enforce_workspace(self.path):
                    return True
            except Exception as exc:  # noqa: BLE001 - a re-check that cannot run is not a pass
                logger.error("Workspace re-check failed, stopping the motors: %s", exc)
                try:
                    self.safety.ns.write(self.path, {"type": "stop"})
                except Exception as write_exc:  # noqa: BLE001
                    logger.error("Could not write the stop to %s: %s", self.path, write_exc)
            self.enforced += 1
            if self._halt is not None:
                try:
                    self._halt()
                except Exception as exc:  # noqa: BLE001 - keep enforcing; say so loudly
                    logger.critical("Workspace enforcer could not stop the motors: %s", exc)
            return False

    def start(self) -> None:
        """Start the re-check thread. Does nothing if it is already running."""
        if self.running:
            return
        self._stop_requested.clear()
        self._thread = threading.Thread(target=self._run, name="workspace-enforcer", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the re-check thread and wait for its current cycle to finish."""
        self._stop_requested.set()
        thread = self._thread
        if thread is None or thread is threading.current_thread():
            return
        thread.join(timeout)
        if thread.is_alive():
            logger.warning("Workspace enforcer did not stop within %.1f s", timeout)
        else:
            self._thread = None

    def _run(self) -> None:
        logger.info("Workspace enforcer running at %.0f Hz", self.hz)
        next_due = time.monotonic()
        while not self._stop_requested.is_set():
            try:
                self.step()
            except Exception:  # noqa: BLE001 - step() guards itself; the thread must not die
                logger.exception("Workspace enforcer cycle failed")
            next_due += self.period_s
            delay = next_due - time.monotonic()
            if delay < 0:
                # Late: do not burst to catch up, start the next cycle now.
                self.overruns += 1
                if self.overruns == 1 or self.overruns % 1000 == 0:
                    logger.warning(
                        "Workspace enforcer ran late (%d cycles so far); reaction_s assumes a "
                        "re-check every %.3f s",
                        self.overruns,
                        self.period_s,
                    )
                next_due = time.monotonic()
                delay = 0.0
            self._stop_requested.wait(delay)
        logger.info("Workspace enforcer stopped")


def start_workspace_enforcer(
    safety: Any,
    *,
    hz: float = DEFAULT_ENFORCE_HZ,
    halt: Optional[Callable[[], Any]] = None,
) -> Optional[WorkspaceEnforcer]:
    """Start per-cycle enforcement if *safety* has a workspace policy; otherwise return None.

    Off unless configured: with no workspace policy there is nothing to re-check, so no thread is
    started. Raises ValueError if one period at *hz* is longer than the workspace's reaction time,
    because a command that passed a re-check could then run past the stopping path it was checked
    against.
    """
    policy = getattr(safety, "workspace_policy", None)
    if policy is None:
        return None
    enforcer = WorkspaceEnforcer(safety, hz=hz, halt=halt)
    reaction_s = getattr(getattr(policy, "workspace", None), "reaction_s", None)
    if isinstance(reaction_s, (int, float)) and enforcer.period_s > reaction_s:
        raise ValueError(
            f"enforce_hz {enforcer.hz:g} re-checks every {enforcer.period_s:.3f} s, longer than "
            f"the workspace reaction_s ({reaction_s:g} s)"
        )
    enforcer.start()
    return enforcer
