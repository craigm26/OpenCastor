"""
castor/behaviors.py — Behavior script runner for OpenCastor.

A behavior is a YAML file that describes a named sequence of steps to execute.
Steps are dispatched through a table keyed on ``type``, so new step types can
be added without growing an if/elif chain.

Example behavior file::

    name: patrol
    steps:
      - type: think
        instruction: "Scan the room and describe what you see"
      - type: wait
        seconds: 2
      - type: speak
        text: "Patrol complete"
      - type: stop

Usage::

    from castor.behaviors import BehaviorRunner
    runner = BehaviorRunner(driver=driver, brain=brain, speaker=speaker, config=cfg)
    behavior = runner.load("patrol.behavior.yaml")
    runner.run(behavior)
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("OpenCastor.Behaviors")

REQUIRED_KEYS = {"name", "steps"}


class BehaviorRunner:
    """Execute named behavior scripts that drive the robot through a sequence of steps.

    Parameters
    ----------
    driver:
        A ``DriverBase`` instance (or None for brain-only / speaker-only runs).
    brain:
        A ``BaseProvider`` instance (or None if no LLM needed).
    speaker:
        A ``Speaker`` instance (or None if TTS disabled).
    config:
        Raw RCAN config dict (used for future extensions).
    """

    def __init__(
        self,
        driver=None,
        brain=None,
        speaker=None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.driver = driver
        self.brain = brain
        self.speaker = speaker
        self.config = config or {}

        self._running: bool = False
        self._current_name: Optional[str] = None

        # Dispatch table: step type -> handler method
        self._step_handlers: Dict[str, Any] = {
            "waypoint": self._step_waypoint,
            "wait": self._step_wait,
            "think": self._step_think,
            "speak": self._step_speak,
            "stop": self._step_stop,
            "command": self._step_think,  # alias for think
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """True while a behavior is being executed."""
        return self._running

    @property
    def current_name(self) -> Optional[str]:
        """Name of the currently-running behavior (or None)."""
        return self._current_name

    def load(self, path: str) -> dict:
        """Load and validate a YAML behavior file.

        Parameters
        ----------
        path:
            File-system path to the ``.behavior.yaml`` file.

        Returns
        -------
        dict
            Parsed behavior dict with at minimum ``name`` and ``steps``.

        Raises
        ------
        FileNotFoundError
            If the file does not exist.
        ValueError
            If required keys (``name``, ``steps``) are missing.
        yaml.YAMLError
            If the file is not valid YAML.
        """
        try:
            import yaml
        except ImportError as exc:
            raise ImportError("pyyaml is required to load behavior files") from exc

        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Behavior file not found: {path}")

        with open(p) as fh:
            data = yaml.safe_load(fh)

        if not isinstance(data, dict):
            raise ValueError(f"Behavior file must be a YAML mapping, got {type(data).__name__}")

        missing = REQUIRED_KEYS - set(data.keys())
        if missing:
            raise ValueError(f"Behavior file missing required keys: {missing}")

        if not isinstance(data["steps"], list):
            raise ValueError("'steps' must be a list")

        logger.info("Loaded behavior '%s' with %d step(s)", data["name"], len(data["steps"]))
        return data

    def run(self, behavior: dict) -> None:
        """Execute all steps in *behavior* sequentially.

        Sets ``_running = True`` before the first step and calls ``stop()``
        in a ``finally`` block so the driver always halts on completion or
        on error.

        Parameters
        ----------
        behavior:
            A behavior dict as returned by :meth:`load`.
        """
        name = behavior.get("name", "<unnamed>")
        steps = behavior.get("steps", [])

        self._running = True
        self._current_name = name
        logger.info("Starting behavior '%s' (%d steps)", name, len(steps))

        try:
            for i, step in enumerate(steps):
                if not self._running:
                    logger.info("Behavior '%s' stopped at step %d", name, i)
                    break

                step_type = step.get("type", "")
                handler = self._step_handlers.get(step_type)
                if handler is None:
                    logger.warning("Unknown step type '%s' at index %d — skipping", step_type, i)
                    continue

                logger.debug("Step %d: %s %r", i, step_type, step)
                try:
                    handler(step)
                except Exception as exc:
                    logger.error("Step %d (%s) raised: %s", i, step_type, exc)
        finally:
            self.stop()

    def stop(self) -> None:
        """Stop the current behavior and halt the driver (if available)."""
        self._running = False
        self._current_name = None
        if self.driver is not None:
            try:
                self.driver.stop()
            except Exception as exc:
                logger.warning("Driver stop error: %s", exc)

    # ------------------------------------------------------------------
    # Step handlers
    # ------------------------------------------------------------------

    def _step_waypoint(self, step: dict) -> None:
        """Move to a named or coordinate waypoint.

        Tries to use ``castor.nav.WaypointNav`` if available.  Falls back to
        a timed ``driver.move()`` using step ``duration`` (default: 1 s) and
        step ``direction`` (default: 'forward').
        """
        try:
            from castor.nav import WaypointNav  # type: ignore

            nav = WaypointNav(self.driver, self.config)
            nav.go(step)
        except (ImportError, AttributeError):
            # Fallback: timed drive in a direction
            direction = step.get("direction", "forward")
            duration = float(step.get("duration", 1.0))
            speed = float(step.get("speed", 0.5))
            logger.debug(
                "Waypoint fallback: move %s for %.1fs at speed %.2f",
                direction,
                duration,
                speed,
            )
            if self.driver is not None:
                self.driver.move(direction=direction, speed=speed)
                time.sleep(duration)
                self.driver.stop()
            else:
                logger.warning("Waypoint step: no driver available, sleeping %.1fs", duration)
                time.sleep(duration)

    def _step_wait(self, step: dict) -> None:
        """Sleep for ``step['seconds']`` (default: 1 s)."""
        seconds = float(step.get("seconds", 1.0))
        logger.debug("Wait %.2fs", seconds)
        time.sleep(seconds)

    def _step_think(self, step: dict) -> None:
        """Send an instruction to the brain and log the result.

        Uses empty image bytes (b"") so the behavior can run without a live
        camera feed.  The step must contain an ``instruction`` key.
        """
        instruction = step.get("instruction", "")
        if self.brain is None:
            logger.warning("Think step: no brain available, skipping")
            return
        thought = self.brain.think(b"", instruction)
        logger.info("Think result: %s", thought.raw_text[:200])

    def _step_speak(self, step: dict) -> None:
        """Speak ``step['text']`` via the TTS speaker."""
        text = step.get("text", "")
        if self.speaker is None:
            logger.warning("Speak step: no speaker available, skipping")
            return
        if hasattr(self.speaker, "enabled") and not self.speaker.enabled:
            logger.debug("Speak step: speaker disabled, skipping")
            return
        self.speaker.say(text)

    def _step_stop(self, step: dict) -> None:  # noqa: ARG002
        """Immediately stop the driver."""
        if self.driver is not None:
            self.driver.stop()
        else:
            logger.debug("Stop step: no driver available")
