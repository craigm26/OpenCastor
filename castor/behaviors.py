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
import threading
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
            "nav_mission": self._step_nav_mission,
            "parallel": self._step_parallel,
            "loop": self._step_loop,
            "condition": self._step_condition,
            "waypoint_mission": self._step_waypoint_mission,
            "repeat_until": self._step_repeat_until,
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

    def _step_nav_mission(self, step: dict) -> None:
        """Execute an inline waypoint sequence using :class:`castor.mission.MissionRunner`.

        The step dict must contain a ``waypoints`` key — a list of dicts with at
        least ``distance_m``.  Optional per-waypoint keys: ``heading_deg``,
        ``speed``, ``dwell_s``, ``label``.

        An optional ``loop`` key (default ``False``) causes the waypoint list to
        repeat until this behavior is stopped.

        Example step::

            - type: nav_mission
              waypoints:
                - {distance_m: 0.5, heading_deg: 0, speed: 0.6, dwell_s: 0, label: forward}
                - {distance_m: 0.3, heading_deg: 90, speed: 0.5, dwell_s: 1.0, label: turn}
              loop: false
        """
        from castor.mission import MissionRunner  # lazy import to avoid circular deps

        waypoints = step.get("waypoints")
        if not waypoints:
            logger.warning("nav_mission step: 'waypoints' is missing or empty — skipping")
            return

        loop: bool = bool(step.get("loop", False))

        logger.info(
            "nav_mission step: starting mission with %d waypoint(s), loop=%s",
            len(waypoints),
            loop,
        )

        runner = MissionRunner(self.driver, self.config)
        runner.start(waypoints, loop=loop)

        done_event = threading.Event()

        def _wait_for_finish() -> None:
            while True:
                if not self._running or runner.status()["running"] is False:
                    done_event.set()
                    return
                time.sleep(0.1)

        watcher = threading.Thread(target=_wait_for_finish, daemon=True, name="nav-mission-watcher")
        watcher.start()

        while self._running and runner.status()["running"]:
            time.sleep(0.1)

        runner.stop()
        done_event.set()

        logger.info(
            "nav_mission step: mission finished (running=%s)",
            runner.status()["running"],
        )

    def _step_parallel(self, step: dict) -> None:
        """Run multiple inner steps concurrently in daemon threads.

        All inner steps are dispatched via ``_step_handlers`` and execute
        simultaneously.  The method blocks until every thread has finished or
        until ``timeout_s`` seconds have elapsed (default: 10.0).  Any threads
        still alive after the timeout are logged as warnings but are not
        forcibly killed (daemon flag means they die with the process).

        Each inner step's exception is caught and logged as a warning so that
        one failing step does not prevent the others from running.

        Example step::

            - type: parallel
              timeout_s: 5.0
              steps:
                - type: speak
                  text: "Going forward"
                - type: wait
                  seconds: 1.0

        Parameters
        ----------
        step:
            The step dict.  Must contain a ``steps`` key with a list of inner
            step dicts.  May contain ``timeout_s`` (float, default 10.0).
        """
        if not self._running:
            return

        inner_steps = step.get("steps")
        if not inner_steps:
            logger.warning("parallel step: 'steps' is missing or empty — skipping")
            return

        timeout_s = float(step.get("timeout_s", 10.0))
        logger.info(
            "parallel step: launching %d inner step(s) with timeout=%.1fs",
            len(inner_steps),
            timeout_s,
        )

        def _run_inner(inner_step: dict) -> None:
            step_type = inner_step.get("type", "")
            handler = self._step_handlers.get(step_type)
            if handler is None:
                logger.warning("parallel step: unknown inner step type '%s' — skipping", step_type)
                return
            try:
                handler(inner_step)
            except Exception as exc:
                logger.warning("parallel step: inner step '%s' raised: %s", step_type, exc)

        threads = [
            threading.Thread(
                target=_run_inner, args=(inner_step,), daemon=True, name=f"parallel-step-{i}"
            )
            for i, inner_step in enumerate(inner_steps)
        ]

        deadline = time.monotonic() + timeout_s
        for t in threads:
            t.start()

        for t in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            t.join(timeout=remaining)

        alive = [t.name for t in threads if t.is_alive()]
        if alive:
            logger.warning(
                "parallel step: %d thread(s) still alive after timeout: %s", len(alive), alive
            )
        else:
            logger.info("parallel step: all inner steps completed")

    def _step_loop(self, step: dict) -> None:
        """Repeat a sequence of inner steps N times or indefinitely.

        Parameters
        ----------
        step:
            The step dict.  Must contain a ``steps`` key with a list of inner
            step dicts.  May contain ``count`` (int, default 1).  A ``count``
            of ``-1`` means loop indefinitely until :meth:`stop` is called.

        Example step::

            - type: loop
              count: 3
              steps:
                - type: wait
                  seconds: 0.5
                - type: speak
                  text: "Beep"

            - type: loop
              count: -1
              steps:
                - type: wait
                  seconds: 1.0
        """
        inner_steps = step.get("steps")
        if not inner_steps:
            logger.warning("loop step: 'steps' is missing or empty — skipping")
            return

        count = int(step.get("count", 1))
        logger.info(
            "loop step: starting loop count=%s with %d inner step(s)",
            "indefinite" if count == -1 else count,
            len(inner_steps),
        )

        iteration = 1
        while True:
            if not self._running:
                break
            if count != -1 and iteration > count:
                break

            for inner_step in inner_steps:
                if not self._running:
                    break
                step_type = inner_step.get("type", "")
                handler = self._step_handlers.get(step_type)
                if handler is None:
                    logger.warning("loop step: unknown inner step type '%s' — skipping", step_type)
                    continue
                try:
                    handler(inner_step)
                except Exception as exc:
                    logger.warning("loop step: inner step '%s' raised: %s", step_type, exc)

            iteration += 1

        logger.info("loop step: done after %d iteration(s)", iteration - 1)

    # ------------------------------------------------------------------
    # Shared sensor/condition helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_sensor(sensor: str) -> dict:
        """Query a named sensor and return its data dict.

        Parameters
        ----------
        sensor:
            ``"lidar"``, ``"thermal"``, ``"imu"``, or ``"none"``.

        Returns
        -------
        dict
            The sensor reading dict, or ``{}`` on failure / unknown sensor.
        """
        sensor_data: dict = {}
        if sensor == "lidar":
            try:
                from castor.drivers.lidar_driver import get_lidar  # type: ignore

                sensor_data = get_lidar().obstacles()
            except (ImportError, Exception) as exc:
                logger.warning("sensor read: lidar query failed (%s) — using {}", exc)
        elif sensor == "thermal":
            try:
                from castor.drivers.thermal_driver import get_thermal  # type: ignore

                sensor_data = get_thermal().get_hotspot()
            except (ImportError, Exception) as exc:
                logger.warning("sensor read: thermal query failed (%s) — using {}", exc)
        elif sensor == "imu":
            try:
                from castor.drivers.imu_driver import get_imu  # type: ignore

                sensor_data = get_imu().read()
            except (ImportError, Exception) as exc:
                logger.warning("sensor read: imu query failed (%s) — using {}", exc)
        elif sensor != "none":
            logger.warning("sensor read: unknown sensor '%s' — using {}", sensor)
        return sensor_data

    @staticmethod
    def _eval_condition(sensor: str, field: Optional[str], op: str, value: Any) -> bool:
        """Read *sensor*, extract *field* via dot-path, and evaluate *op* against *value*.

        Parameters
        ----------
        sensor:
            Sensor name: ``"lidar"``, ``"thermal"``, ``"imu"``, or ``"none"``.
        field:
            Dot-separated key path into the sensor reading dict
            (e.g. ``"sectors.front"``).  ``None`` always returns ``False``.
        op:
            Comparison operator: ``"lt"``, ``"gt"``, ``"lte"``, ``"gte"``,
            ``"eq"``, ``"neq"``.
        value:
            The threshold value to compare against.

        Returns
        -------
        bool
            Result of the comparison, or ``False`` if the field is missing /
            the operator is unknown.
        """
        _OPS = {
            "lt": lambda a, b: a < b,
            "gt": lambda a, b: a > b,
            "lte": lambda a, b: a <= b,
            "gte": lambda a, b: a >= b,
            "eq": lambda a, b: a == b,
            "neq": lambda a, b: a != b,
        }

        if field is None:
            return False

        sensor_data = BehaviorRunner._read_sensor(sensor)

        # Support dot-path traversal (e.g. "sectors.front")
        actual: Any = sensor_data
        for part in field.split("."):
            if not isinstance(actual, dict):
                actual = None
                break
            actual = actual.get(part)

        if actual is None:
            logger.warning(
                "_eval_condition: field '%s' not found in sensor '%s' data — returning False",
                field,
                sensor,
            )
            return False

        op_fn = _OPS.get(op)
        if op_fn is None:
            logger.warning("_eval_condition: unknown op '%s' — returning False", op)
            return False

        result = bool(op_fn(actual, value))
        logger.debug(
            "_eval_condition: sensor=%s field=%s actual=%s op=%s value=%s → %s",
            sensor,
            field,
            actual,
            op,
            value,
            result,
        )
        return result

    def _run_step_list(self, inner_steps: list, context: str) -> None:
        """Execute a list of steps with the standard per-step try/except pattern.

        Parameters
        ----------
        inner_steps:
            List of step dicts to execute sequentially.
        context:
            Label used in warning/error log messages (e.g. ``"loop step"``).
        """
        for inner_step in inner_steps:
            if not self._running:
                break
            step_type = inner_step.get("type", "")
            handler = self._step_handlers.get(step_type)
            if handler is None:
                logger.warning("%s: unknown inner step type '%s' — skipping", context, step_type)
                continue
            try:
                handler(inner_step)
            except Exception as exc:
                logger.warning("%s: inner step '%s' raised: %s", context, step_type, exc)

    # ------------------------------------------------------------------
    # Step handlers (continued)
    # ------------------------------------------------------------------

    def _step_condition(self, step: dict) -> None:
        """Evaluate a sensor condition and branch into ``then_steps`` or ``else_steps``.

        The sensor is queried lazily at runtime; if the sensor driver is
        unavailable the field lookup falls through to ``else_steps``.

        Supported sensors (``sensor`` key):

        * ``"lidar"`` — calls ``castor.drivers.lidar_driver.get_lidar().obstacles()``
        * ``"thermal"`` — calls ``castor.drivers.thermal_driver.get_thermal().get_hotspot()``
        * ``"imu"`` — calls ``castor.drivers.imu_driver.get_imu().read()``
        * ``"none"`` (default) — empty dict; ``then_steps`` always runs when
          ``sensor`` is ``"none"`` and ``field`` is absent or ``None``.

        Supported operators (``op`` key): ``lt``, ``gt``, ``lte``, ``gte``,
        ``eq``, ``neq``.

        Example step::

            - type: condition
              sensor: lidar
              field: center_cm
              op: lt
              value: 300
              then_steps:
                - type: stop
              else_steps:
                - type: wait
                  seconds: 0.5

        Parameters
        ----------
        step:
            The step dict.  Required keys: ``field``, ``op``, ``value``.
            Optional keys: ``sensor`` (default ``"none"``),
            ``then_steps`` (default ``[]``), ``else_steps`` (default ``[]``).
        """
        sensor = step.get("sensor", "none")
        field = step.get("field")
        op = step.get("op", "")
        value = step.get("value")
        then_steps: list = step.get("then_steps") or []
        else_steps: list = step.get("else_steps") or []

        # Delegate to shared helper; unknown sensor/field → actual=None → else_steps
        sensor_data = self._read_sensor(sensor)

        # Single-level field lookup (legacy behaviour — no dot-path here so
        # that existing tests that pass plain field names continue to work).
        actual = sensor_data.get(field) if field is not None else None
        if actual is None:
            if sensor != "none" or field is not None:
                logger.warning(
                    "condition step: field '%s' not found in sensor '%s' data — running else_steps",
                    field,
                    sensor,
                )
            branch = else_steps
            result = False
        else:
            _OPS = {
                "lt": lambda a, b: a < b,
                "gt": lambda a, b: a > b,
                "lte": lambda a, b: a <= b,
                "gte": lambda a, b: a >= b,
                "eq": lambda a, b: a == b,
                "neq": lambda a, b: a != b,
            }
            op_fn = _OPS.get(op)
            if op_fn is None:
                logger.warning("condition step: unknown op '%s' — treating condition as False", op)
                result = False
            else:
                result = bool(op_fn(actual, value))

            logger.debug(
                "condition step: sensor=%s field=%s actual=%s op=%s value=%s → %s",
                sensor,
                field,
                actual,
                op,
                value,
                result,
            )
            branch = then_steps if result else else_steps

        # --- Execute branch ----------------------------------------------
        branch_name = "then_steps" if branch is then_steps else "else_steps"
        logger.info(
            "condition step: executing %s (%d step(s))",
            branch_name,
            len(branch),
        )
        self._run_step_list(branch, "condition step")

    def _step_repeat_until(self, step: dict) -> None:
        """Repeat ``inner_steps`` in a loop until a sensor condition becomes true.

        Each iteration executes all steps in ``inner_steps`` sequentially, then
        evaluates the condition.  The loop stops when:

        * The condition evaluates to ``True``, **or**
        * ``_running`` is ``False`` (behavior was stopped externally), **or**
        * The iteration count reaches ``max_count`` (when ``max_count != -1``).

        Supported sensors (``sensor`` key): ``"lidar"``, ``"thermal"``,
        ``"imu"``, ``"none"`` (default).  Condition evaluation uses
        :meth:`_eval_condition`.

        Example step::

            - type: repeat_until
              sensor: lidar
              field: sectors.front
              op: lt
              value: 300
              max_count: 10
              dwell_s: 0.5
              inner_steps:
                - type: wait
                  seconds: 0.2
                - type: speak
                  text: "Scanning"

        Parameters
        ----------
        step:
            The step dict.

            ``inner_steps`` (list, required):
                Steps to execute each iteration.
            ``sensor`` (str, default ``"none"``):
                Sensor to query for the exit condition.
            ``field`` (str, default ``None``):
                Dot-path key into the sensor reading (e.g. ``"sectors.front"``).
            ``op`` (str, default ``"lt"``):
                Comparison operator: ``"lt"``, ``"gt"``, ``"lte"``, ``"gte"``,
                ``"eq"``, ``"neq"``.
            ``value`` (any, default ``0``):
                Threshold value.
            ``max_count`` (int, default ``100``):
                Maximum iterations before the loop exits regardless of the
                condition.  Pass ``-1`` for unlimited.
            ``dwell_s`` (float, default ``0``):
                Pause between iterations (seconds).  Checked against
                ``_running`` every 50 ms so that a stop request is honoured
                promptly.
        """
        inner_steps: list = step.get("inner_steps") or []
        if not inner_steps:
            logger.warning("repeat_until step: 'inner_steps' is missing or empty — skipping")
            return

        sensor: str = step.get("sensor", "none")
        field: Optional[str] = step.get("field")
        op: str = step.get("op", "lt")
        value: Any = step.get("value", 0)
        max_count: int = int(step.get("max_count", 100))
        dwell_s: float = float(step.get("dwell_s", 0))

        logger.info(
            "repeat_until step: starting loop max_count=%s sensor=%s field=%s op=%s value=%s"
            " dwell_s=%.2f with %d inner step(s)",
            "unlimited" if max_count == -1 else max_count,
            sensor,
            field,
            op,
            value,
            dwell_s,
            len(inner_steps),
        )

        iteration = 1
        while self._running:
            if max_count != -1 and iteration > max_count:
                logger.info("repeat_until step: max_count=%d reached — exiting loop", max_count)
                break

            # Execute all inner steps for this iteration.
            self._run_step_list(inner_steps, "repeat_until step")

            # Check exit condition.
            condition_met = self._eval_condition(sensor, field, op, value)
            logger.info(
                "repeat_until step: iteration %d/%s (condition=%s)",
                iteration,
                "unlimited" if max_count == -1 else max_count,
                condition_met,
            )

            if condition_met:
                break

            # Optional dwell between iterations, checked at 50 ms granularity.
            if dwell_s > 0:
                elapsed = 0.0
                while self._running and elapsed < dwell_s:
                    sleep_chunk = min(0.05, dwell_s - elapsed)
                    time.sleep(sleep_chunk)
                    elapsed += sleep_chunk

            iteration += 1

        logger.info("repeat_until step: done after %d iteration(s)", iteration)

    def _step_waypoint_mission(self, step: dict) -> None:
        """Execute an inline waypoint mission using :class:`castor.mission.MissionRunner`.

        Embeds a full ``MissionRunner`` mission as a single behavior step.  The
        step dict must contain a ``waypoints`` key — a list of dicts with at
        least ``distance_m``.  Optional per-waypoint keys: ``heading_deg``,
        ``speed``, ``dwell_s``, ``label``.

        An optional ``loop`` key (default ``False``) causes the waypoint list to
        repeat until this behavior is stopped or ``timeout_s`` is reached.

        An optional ``timeout_s`` key sets a maximum wall-clock budget for the
        whole mission.  If the mission is still running when the budget expires
        it is cancelled via :meth:`MissionRunner.stop`.

        Example step::

            - type: waypoint_mission
              waypoints:
                - distance_m: 1.0
                  heading_deg: 0
                - distance_m: 0.5
                  heading_deg: 90
              loop: false
              timeout_s: 30.0

        Parameters
        ----------
        step:
            The step dict.  Required key: ``waypoints``.
            Optional keys: ``loop`` (bool, default ``False``),
            ``timeout_s`` (float, default ``None`` = no timeout).
        """
        waypoints = step.get("waypoints", [])
        if not waypoints:
            logger.warning("waypoint_mission step: 'waypoints' is missing or empty — skipping")
            return

        if self.driver is None:
            logger.warning("waypoint_mission step: no driver available — skipping")
            return

        loop: bool = bool(step.get("loop", False))
        timeout_s = step.get("timeout_s")
        if timeout_s is not None:
            timeout_s = float(timeout_s)

        try:
            from castor.mission import MissionRunner  # lazy import to avoid circular deps
        except ImportError as exc:
            logger.warning(
                "waypoint_mission step: castor.mission not available (%s) — skipping", exc
            )
            return

        logger.info(
            "waypoint_mission step: starting mission with %d waypoint(s), loop=%s, timeout_s=%s",
            len(waypoints),
            loop,
            timeout_s,
        )

        mission_runner = MissionRunner(driver=self.driver, config=self.config)
        mission_runner.start(waypoints, loop=loop)

        start_time = time.monotonic()
        timed_out = False

        while self._running and mission_runner.status()["running"]:
            time.sleep(0.1)
            if timeout_s is not None and (time.monotonic() - start_time) > timeout_s:
                timed_out = True
                logger.warning(
                    "waypoint_mission step: timeout of %.1fs exceeded — aborting mission",
                    timeout_s,
                )
                mission_runner.stop()
                break

        mission_runner.stop()

        if timed_out:
            logger.info("waypoint_mission step: mission aborted due to timeout")
        elif not self._running:
            logger.info("waypoint_mission step: mission stopped externally")
        else:
            logger.info(
                "waypoint_mission step: mission completed (running=%s)",
                mission_runner.status()["running"],
            )
