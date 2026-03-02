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

import concurrent.futures
import logging
import threading
import time
from concurrent.futures import wait as _futures_wait
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("OpenCastor.Behaviors")

REQUIRED_KEYS = {"name", "steps"}


class _BreakLoop(Exception):
    """Sentinel raised by ``break_on`` step to exit enclosing loops."""


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
            "for_each": self._step_for_each,
            "chain": self._step_chain,
            "while_true": self._step_while_true,
            "conditional": self._step_conditional,
            # Issue #346: pause until a sensor threshold is crossed
            "event_wait": self._step_event_wait,
            # Issue #341: iterate JSONL/CSV file rows as $item in nested steps
            "foreach_file": self._step_foreach_file,
            # Issue #360: execute steps at a wall-clock time or recurring interval
            "schedule": self._step_schedule,
            # Issue #365: retry inner steps with backoff on failure
            "retry": self._step_retry,
            # Issue #368: runtime variable store
            "set_var": self._step_set_var,
            "get_var": self._step_get_var,
            # Issue #373: assert condition — halt if false
            "assert": self._step_assert,
            # Issue #379: block until named event is set on runner
            "event_trigger": self._step_event_trigger,
            # Issue #383: append structured JSON log to file
            "log_step": self._step_log_step,
            # Issue #400: inverse condition — run inner_steps when condition is False
            "unless": self._step_unless,
            # Issue #402: raise _BreakLoop to exit enclosing loops
            "break_on": self._step_break_on,
            # Issue #411: run inner steps concurrently; stop all when first finishes
            "parallel_race": self._step_parallel_race,
            # Issue #419: block until named threading.Event is set via set_event()
            "wait_for_event": self._step_wait_for_event,
        }

        # Chain-recursion depth counter (not thread-local; behaviors run single-threaded)
        self._chain_depth: int = 0

        # Issue #360: schedule step uses this event for interruptible waits
        import threading as _threading

        self._stop_event: _threading.Event = _threading.Event()

        # Issue #368: runtime variable store (reset on stop())
        self._vars: Dict[str, Any] = {}

        # Issue #379: named event store for event_trigger step
        import threading as _threading2

        self._events: Dict[str, Any] = {}  # name → threading.Event
        self._events_lock: _threading2.Lock = _threading2.Lock()

        # Issue #387: robot tag set for tag-based step filtering
        robot_tags = self.config.get("robot_tags") or self.config.get("tags") or []
        self._robot_tags: set = set(robot_tags) if robot_tags else set()

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

                # Issue #387: tag-based step filtering
                if not self._step_tags_match(step):
                    logger.debug(
                        "Step %d (%s): tags %r not in robot tags %r — skipping",
                        i,
                        step_type,
                        step.get("tags"),
                        self._robot_tags,
                    )
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
        self._stop_event.set()
        self._stop_event.clear()
        self._vars.clear()
        with self._events_lock:
            for ev in self._events.values():
                ev.set()  # unblock any waiting event_trigger steps
            self._events.clear()
        if self.driver is not None:
            try:
                self.driver.stop()
            except Exception as exc:
                logger.warning("Driver stop error: %s", exc)

    # ------------------------------------------------------------------
    # Issue #387 — tag-based step filtering
    # ------------------------------------------------------------------

    def _step_tags_match(self, step: dict) -> bool:
        """Return True if *step* should execute given ``self._robot_tags``.

        A step is executed when:

        * The step has no ``tags`` key (unconditional).
        * The step's ``tags`` list is empty.
        * The step's ``tags`` list intersects ``self._robot_tags``.

        When ``self._robot_tags`` is empty *all* steps pass (the robot has no
        tag filter configured).

        Args:
            step: Step dict (may or may not have a ``"tags"`` key).

        Returns:
            ``True`` when the step should run, ``False`` to skip it.
        """
        step_tags = step.get("tags")
        if not step_tags:  # no tags on step → always run
            return True
        if not self._robot_tags:  # robot has no tags configured → run all
            return True
        return bool(set(step_tags) & self._robot_tags)

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

    def _run_step(self, step: dict) -> None:
        """Execute a single step dict by looking up its type in ``_step_handlers``.

        This is the callable target used by :meth:`_step_parallel` thread workers.
        Unknown step types are skipped with a warning (matching the behaviour of
        the top-level :meth:`run` loop).

        Parameters
        ----------
        step:
            A single step dict (must contain a ``"type"`` key).
        """
        step_type = step.get("type", "")
        handler = self._step_handlers.get(step_type)
        if handler is None:
            logger.warning("parallel step: unknown inner step type '%s' — skipping", step_type)
            return
        handler(step)

    def _step_parallel(self, step: dict) -> None:
        """Run multiple inner steps concurrently using threads.

        Step schema::

            type: parallel
            inner_steps: [{type: wait, seconds: 1}, {type: speak, text: "hi"}]
            timeout_s: 0      # 0 = no timeout; > 0 = cancel after N seconds

        Each inner step runs in its own daemon thread via
        :class:`concurrent.futures.ThreadPoolExecutor`.  All threads start
        simultaneously; we wait for all to finish (or timeout).  Exceptions in
        individual threads are logged but do not abort others.  The outer
        ``_running`` flag is checked before starting any threads.

        Parameters
        ----------
        step:
            The step dict.

            ``inner_steps`` (list, required):
                Steps to execute concurrently.  If empty the step is skipped
                with a warning.
            ``timeout_s`` (float, default ``0``):
                Maximum seconds to wait for all threads.  ``0`` means no
                timeout (wait indefinitely).
        """
        if not self._running:
            return

        inner_steps: list = step.get("inner_steps", [])
        if not inner_steps:
            logger.warning("parallel step: 'inner_steps' is missing or empty — skipping")
            return

        timeout_s: float = float(step.get("timeout_s", 0))

        logger.info(
            "parallel step: launching %d inner step(s) (timeout_s=%.1f)",
            len(inner_steps),
            timeout_s,
        )

        futures: list = []
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=len(inner_steps), thread_name_prefix="parallel-step"
        )
        for inner_step in inner_steps:
            futures.append(executor.submit(self._run_step, inner_step))

        done, not_done = _futures_wait(futures, timeout=timeout_s or None)
        # Shut down without waiting so we return promptly when timeout fires.
        executor.shutdown(wait=False)

        if not_done:
            logger.warning(
                "parallel step: %d future(s) did not complete within timeout_s=%.1f",
                len(not_done),
                timeout_s,
            )

        completed = 0
        for future in done:
            exc = future.exception()
            if exc is not None:
                logger.warning("parallel step: inner step raised: %s", exc)
            else:
                completed += 1

        logger.info(
            "parallel step: done — %d/%d inner step(s) completed successfully",
            completed,
            len(inner_steps),
        )

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
            # Issue #387: tag-based step filtering
            if not self._step_tags_match(inner_step):
                logger.debug(
                    "%s: step tags %r not in robot tags %r — skipping",
                    context,
                    inner_step.get("tags"),
                    self._robot_tags,
                )
                continue
            step_type = inner_step.get("type", "")
            handler = self._step_handlers.get(step_type)
            if handler is None:
                logger.warning("%s: unknown inner step type '%s' — skipping", context, step_type)
                continue
            try:
                handler(inner_step)
            except _BreakLoop:
                raise  # propagate to enclosing loop handler
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
            try:
                self._run_step_list(inner_steps, "repeat_until step")
            except _BreakLoop as bl:
                logger.info("repeat_until step: break_on triggered: %s", bl)
                break

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

    def _step_for_each(self, step: dict) -> None:
        """Iterate over a list of values and execute ``inner_steps`` for each.

        The current item is substituted into any inner-step dict value that
        equals *var* (default ``"$item"``).  Substitution is shallow — only
        top-level string values in each inner step dict are replaced.

        Example step::

            - type: for_each
              items: [1, 2, 3]
              var: "$item"
              dwell_s: 0.1
              inner_steps:
                - type: wait
                  seconds: "$item"

        Parameters
        ----------
        step:
            The step dict.

            ``items`` (list, required):
                Values to iterate over.  If empty the step is skipped with a
                warning.
            ``var`` (str, default ``"$item"``):
                The placeholder string that gets replaced with the current
                item value in each inner step dict.
            ``inner_steps`` (list, default ``[]``):
                Steps to execute per iteration.
            ``dwell_s`` (float, default ``0``):
                Pause between iterations.  Honoured at 50 ms granularity so
                that a :meth:`stop` request is not delayed.
        """
        items: list = step.get("items", [])
        if not items:
            logger.warning("for_each step: 'items' is missing or empty — skipping")
            return

        var: str = step.get("var", "$item")
        inner_steps: list = step.get("inner_steps") or []
        dwell_s: float = float(step.get("dwell_s", 0.0))

        logger.info(
            "for_each step: starting iteration over %d item(s), var=%s, dwell_s=%.2f,"
            " %d inner step(s)",
            len(items),
            var,
            dwell_s,
            len(inner_steps),
        )

        for idx, item in enumerate(items):
            if not self._running:
                logger.info("for_each step: stopped at item %d/%d", idx, len(items))
                break

            logger.debug("for_each step: iteration %d/%d, %s=%r", idx + 1, len(items), var, item)

            # Shallow substitution: replace string values equal to var with item.
            substituted: list = []
            for s in inner_steps:
                new_s = {k: (item if v == var else v) for k, v in s.items()}
                substituted.append(new_s)

            try:
                self._run_step_list(substituted, "for_each step")
            except _BreakLoop as bl:
                logger.info("for_each step: break_on triggered: %s", bl)
                break

            # Optional dwell between iterations, honoured at 50 ms granularity.
            if dwell_s > 0 and self._running and idx < len(items) - 1:
                elapsed = 0.0
                while self._running and elapsed < dwell_s:
                    sleep_chunk = min(0.05, dwell_s - elapsed)
                    time.sleep(sleep_chunk)
                    elapsed += sleep_chunk

        logger.info("for_each step: done after %d item(s)", len(items))

    _CHAIN_MAX_DEPTH: int = 5

    def _step_chain(self, step: dict) -> None:
        """Load and execute a named behavior from another behavior file.

        Enables composition: one behavior can invoke another as a single step.
        Recursion is capped at :attr:`_CHAIN_MAX_DEPTH` (5) to prevent infinite
        chains.

        Example step::

            - type: chain
              behavior_file: "patrol.behavior.yaml"
              behavior_name: "patrol_loop"

        Parameters
        ----------
        step:
            The step dict.

            ``behavior_file`` (str, required):
                Path to the ``.behavior.yaml`` file to load.
            ``behavior_name`` (str, required):
                Key inside the loaded file whose ``steps`` list will be
                executed.  The file is expected to be a mapping of
                ``{name: {name: ..., steps: [...]}, ...}`` **or** a single
                top-level behavior dict (``{name: ..., steps: [...]}``) — the
                latter is matched when ``behavior_name`` equals the file's
                ``name`` field.
        """
        if not self._running:
            return

        behavior_file: str = step.get("behavior_file", "")
        behavior_name: str = step.get("behavior_name", "")

        if not behavior_file:
            logger.warning("chain step: 'behavior_file' is missing — skipping")
            return
        if not behavior_name:
            logger.warning("chain step: 'behavior_name' is missing — skipping")
            return

        if self._chain_depth >= self._CHAIN_MAX_DEPTH:
            logger.warning(
                "chain step: max chain depth (%d) reached — skipping '%s'",
                self._CHAIN_MAX_DEPTH,
                behavior_name,
            )
            return

        self._chain_depth += 1
        try:
            try:
                data = self.load(behavior_file)
            except (FileNotFoundError, ValueError) as exc:
                logger.warning("chain step: failed to load '%s': %s — skipping", behavior_file, exc)
                return

            # Support two file layouts:
            # 1. Single behavior at top-level: {name: "foo", steps: [...]}
            # 2. Multi-behavior mapping:        {patrol_loop: {name: "patrol_loop", steps: [...]}}
            steps_to_run: Optional[list] = None
            if data.get("name") == behavior_name:
                # Layout 1: top-level single behavior
                steps_to_run = data.get("steps")
            elif isinstance(data.get(behavior_name), dict):
                # Layout 2: keyed sub-behavior
                sub = data[behavior_name]
                steps_to_run = sub.get("steps")

            if steps_to_run is None:
                logger.warning(
                    "chain step: behavior '%s' not found in '%s' — skipping",
                    behavior_name,
                    behavior_file,
                )
                return

            logger.info(
                "chain step: executing '%s' from '%s' (depth=%d, %d step(s))",
                behavior_name,
                behavior_file,
                self._chain_depth,
                len(steps_to_run),
            )

            self._run_step_list(steps_to_run, f"chain:{behavior_name}")

            logger.info("chain step: '%s' finished", behavior_name)
        finally:
            self._chain_depth -= 1

    def _step_while_true(self, step: dict) -> None:
        """Loop ``inner_steps`` indefinitely (or until a limit is reached).

        The loop body runs :meth:`_run_step_list` on *inner_steps* each
        iteration and stops when any of the following conditions are met:

        * ``_running`` becomes ``False`` (external :meth:`stop` call), **or**
        * ``timeout_s > 0`` and the elapsed wall-clock time has reached
          ``timeout_s`` seconds, **or**
        * ``max_iterations > 0`` and the iteration count has reached
          ``max_iterations``.

        An optional ``dwell_s`` pause is inserted between iterations; it is
        slept in 50 ms chunks so that a :meth:`stop` request is honoured
        promptly.

        Example step::

            - type: while_true
              inner_steps:
                - type: wait
                  duration_s: 1
              timeout_s: 0        # 0 = unlimited
              dwell_s: 0.0        # pause between iterations (seconds)
              max_iterations: 0   # 0 = unlimited

        Parameters
        ----------
        step:
            The step dict.

            ``inner_steps`` (list, required):
                Steps to execute each iteration.  If empty the step is
                skipped with a warning.
            ``timeout_s`` (float, default ``0``):
                Maximum wall-clock budget.  ``0`` means no timeout.
            ``dwell_s`` (float, default ``0.0``):
                Pause between iterations.  Honoured at 50 ms granularity.
            ``max_iterations`` (int, default ``0``):
                Maximum number of iterations.  ``0`` means unlimited.
        """
        inner_steps: list = step.get("inner_steps") or []
        if not inner_steps:
            logger.warning("while_true step: 'inner_steps' is missing or empty — skipping")
            return

        timeout_s: float = float(step.get("timeout_s", 0))
        dwell_s: float = float(step.get("dwell_s", 0.0))
        max_iterations: int = int(step.get("max_iterations", 0))

        # Build a human-readable summary for the start log.
        limits: list = []
        if timeout_s > 0:
            limits.append(f"timeout_s={timeout_s:.2f}")
        if max_iterations > 0:
            limits.append(f"max_iterations={max_iterations}")
        limit_str = ", ".join(limits) if limits else "unlimited"

        logger.info(
            "while_true step: starting loop (%s) with %d inner step(s)",
            limit_str,
            len(inner_steps),
        )

        start_t: float = time.monotonic()
        iteration: int = 0

        while self._running:
            # --- Timeout check (at top of each iteration) -----------------
            if timeout_s > 0 and (time.monotonic() - start_t) >= timeout_s:
                logger.info(
                    "while_true step: timeout of %.2fs reached after %d iteration(s)",
                    timeout_s,
                    iteration,
                )
                break

            # --- Max-iterations check -------------------------------------
            if max_iterations > 0 and iteration >= max_iterations:
                logger.info(
                    "while_true step: max_iterations=%d reached — exiting loop",
                    max_iterations,
                )
                break

            iteration += 1

            # --- Execute inner steps --------------------------------------
            try:
                self._run_step_list(inner_steps, "while_true step")
            except _BreakLoop as bl:
                logger.info("while_true step: break_on triggered: %s", bl)
                break

            # --- Optional dwell between iterations ------------------------
            if dwell_s > 0 and self._running:
                elapsed = 0.0
                while self._running and elapsed < dwell_s:
                    sleep_chunk = min(0.05, dwell_s - elapsed)
                    time.sleep(sleep_chunk)
                    elapsed += sleep_chunk

        logger.info("while_true step: done after %d iteration(s)", iteration)

    # ------------------------------------------------------------------
    # Conditional step helpers and handler
    # ------------------------------------------------------------------

    @staticmethod
    def _get_sensor_value(driver: str, field: str) -> Any:
        """Read a single field from a named sensor driver.

        Uses lazy imports so that missing hardware drivers do not prevent the
        module from loading.

        Parameters
        ----------
        driver:
            Sensor driver name: ``"battery"``, ``"imu"``, or ``"lidar"``.
        field:
            Key to extract from the driver's read/obstacles result dict.

        Returns
        -------
        Any
            The extracted value, or ``None`` if the driver is unavailable,
            the field is missing, or any exception occurs.
        """
        try:
            if driver == "battery":
                from castor.drivers.battery_driver import BatteryDriver  # type: ignore

                return BatteryDriver({}).read().get(field)
            if driver == "imu":
                from castor.drivers.imu_driver import IMUDriver  # type: ignore

                return IMUDriver({}).read().get(field)
            if driver == "lidar":
                from castor.drivers.lidar_driver import LidarDriver  # type: ignore

                return LidarDriver({}).obstacles().get(field)
        except Exception as exc:  # noqa: BLE001
            logger.warning("_get_sensor_value: driver=%s field=%s error: %s", driver, field, exc)
            return None

        logger.warning("_get_sensor_value: unknown driver '%s' — returning None", driver)
        return None

    def _step_conditional(self, step: dict) -> None:
        """Evaluate a sensor reading and branch into ``then`` or ``else`` steps.

        The sensor is specified as a dot-separated ``driver.field`` string
        (e.g. ``"battery.voltage_v"``).  The driver portion selects the hardware
        driver to query; the field portion selects the key within that driver's
        result dict.  Sensor reads are performed via :meth:`_get_sensor_value`.

        Supported operators (``op`` key): ``lt``, ``lte``, ``gt``, ``gte``,
        ``eq``, ``ne``.

        Example step::

            - type: conditional
              sensor: battery.voltage_v
              op: lt
              value: 3.5
              then:
                - type: stop
              else:
                - type: wait
                  seconds: 1

        Parameters
        ----------
        step:
            The step dict.

            ``sensor`` (str, required):
                Dot-separated ``driver.field`` path (e.g. ``"battery.voltage_v"``).
                Missing or non-dot-path values log a warning and skip both branches.
            ``op`` (str, required):
                Comparison operator: ``lt``, ``lte``, ``gt``, ``gte``, ``eq``, ``ne``.
                Unknown ops log a warning and skip both branches.
            ``value`` (any, required):
                Threshold value for the comparison.
            ``then`` (list, default ``[]``):
                Steps to execute when the condition is ``True``.
            ``else`` (list, default ``[]``):
                Steps to execute when the condition is ``False``.
        """
        if not self._running:
            return

        try:
            sensor_path: str = step.get("sensor", "")
            if not sensor_path or "." not in sensor_path:
                logger.warning(
                    "conditional step: 'sensor' key missing or not in 'driver.field' format"
                    " (got %r) — skipping",
                    sensor_path,
                )
                return

            driver_name, field = sensor_path.split(".", 1)

            op: str = step.get("op", "")
            value: Any = step.get("value")
            then_steps: list = step.get("then") or []
            else_steps: list = step.get("else") or []

            _OPS = {
                "lt": lambda a, b: a < b,
                "lte": lambda a, b: a <= b,
                "gt": lambda a, b: a > b,
                "gte": lambda a, b: a >= b,
                "eq": lambda a, b: a == b,
                "ne": lambda a, b: a != b,
            }

            # Read the sensor value.
            actual = self._get_sensor_value(driver_name, field)
            if actual is None:
                logger.warning(
                    "conditional step: sensor value for '%s.%s' is None — skipping both branches",
                    driver_name,
                    field,
                )
                return

            # Validate operator.
            op_fn = _OPS.get(op)
            if op_fn is None:
                logger.warning(
                    "conditional step: unknown op '%s' — skipping both branches",
                    op,
                )
                return

            # Evaluate condition and select branch.
            result = bool(op_fn(actual, value))
            logger.debug(
                "conditional step: driver=%s field=%s actual=%s op=%s value=%s → %s",
                driver_name,
                field,
                actual,
                op,
                value,
                result,
            )

            branch = then_steps if result else else_steps
            branch_name = "then" if result else "else"
            logger.info(
                "conditional step: condition=%s — executing '%s' branch (%d step(s))",
                result,
                branch_name,
                len(branch),
            )

            self._run_step_list(branch, "conditional step")

        except Exception as exc:  # noqa: BLE001
            logger.warning("conditional step: unexpected error: %s", exc)

    # ------------------------------------------------------------------
    # Issue #346 — event_wait step
    # ------------------------------------------------------------------

    def _step_event_wait(self, step: dict) -> None:
        """Pause behavior execution until a sensor condition is met or a timeout expires.

        Polls ``_get_sensor_value()`` at 100 ms intervals until the condition
        evaluates to ``True`` or ``timeout_s`` seconds have elapsed.

        Supported sensor format: ``"driver.field"`` (e.g. ``"imu.vibration_rms"``).
        Supported operators: ``gt``, ``lt``, ``gte``, ``lte``, ``eq``, ``ne``.

        Example step::

            - type: event_wait
              sensor: "imu.vibration_rms"
              op: gt
              value: 0.5
              timeout_s: 30

        Parameters
        ----------
        step:
            The step dict.

            ``sensor`` (str, required):
                Dot-separated ``driver.field`` path.
            ``op`` (str, required):
                Comparison operator: ``gt``, ``lt``, ``gte``, ``lte``, ``eq``, ``ne``.
            ``value`` (any, required):
                Threshold value for the comparison.
            ``timeout_s`` (float, default 30.0):
                Maximum seconds to wait.  Returns with a ``timeout`` result when
                elapsed time exceeds this value.

        Returns
        -------
        None
            Logs ``met`` when the condition is satisfied, ``timeout`` when the
            deadline is reached, and ``stopped`` when a stop() is requested.
        """

        sensor_path: str = step.get("sensor", "")
        op: str = step.get("op", "")
        threshold = step.get("value")
        timeout_s: float = float(step.get("timeout_s", 30.0))

        _OPS = {
            "gt": lambda a, b: a > b,
            "lt": lambda a, b: a < b,
            "gte": lambda a, b: a >= b,
            "lte": lambda a, b: a <= b,
            "eq": lambda a, b: a == b,
            "ne": lambda a, b: a != b,
        }

        if not sensor_path or "." not in sensor_path:
            logger.warning(
                "event_wait step: 'sensor' must be 'driver.field' (got %r) — skipping",
                sensor_path,
            )
            return

        op_fn = _OPS.get(op)
        if op_fn is None:
            logger.warning("event_wait step: unknown op %r — skipping", op)
            return

        driver_name, field = sensor_path.split(".", 1)
        deadline = time.monotonic() + timeout_s
        poll_interval_s = 0.1

        logger.info(
            "event_wait step: waiting for %s %s %s (timeout=%.1fs)",
            sensor_path,
            op,
            threshold,
            timeout_s,
        )

        while self._running:
            if time.monotonic() >= deadline:
                logger.info(
                    "event_wait step: timeout after %.1f s waiting for %s %s %s",
                    timeout_s,
                    sensor_path,
                    op,
                    threshold,
                )
                return

            try:
                actual = self._get_sensor_value(driver_name, field)
                if actual is not None:
                    try:
                        if op_fn(actual, threshold):
                            logger.info(
                                "event_wait step: condition met — %s=%r %s %r",
                                sensor_path,
                                actual,
                                op,
                                threshold,
                            )
                            return
                    except TypeError:
                        pass  # type mismatch — keep polling
            except Exception as exc:  # noqa: BLE001
                logger.debug("event_wait step: sensor read error: %s", exc)

            time.sleep(poll_interval_s)

        logger.info("event_wait step: stopped externally before condition met")

    # ------------------------------------------------------------------
    # Issue #341 — foreach_file step
    # ------------------------------------------------------------------

    def _step_foreach_file(self, step: dict) -> None:
        """Iterate over JSONL or CSV rows from a file, substituting each row into nested steps.

        Reads the file at ``file`` path.  For JSONL files each non-empty line is
        parsed as a JSON object.  For CSV files each row is parsed with
        :class:`csv.DictReader`.  Rows are made available as ``$item`` (a dict)
        in nested steps.  Field values from ``$item`` can be injected into step
        parameters using ``"$item.<key>"`` placeholders.

        Example step (JSONL)::

            - type: foreach_file
              file: "/data/waypoints.jsonl"
              limit: 5
              steps:
                - type: waypoint
                  distance_m: "$item.distance_m"
                  heading_deg: "$item.heading_deg"

        Example step (CSV)::

            - type: foreach_file
              file: "/data/waypoints.csv"
              max_rows: 10
              steps:
                - type: speak
                  text: "$item.label"

        Parameters
        ----------
        step:
            The step dict.

            ``file`` (str, required):
                Path to a JSONL file (one JSON object per line) or a CSV file.
                Format is auto-detected from the ``.csv`` extension or the
                optional ``format`` key (``"jsonl"`` / ``"csv"``).
            ``steps`` (list, default ``[]``):
                Inner steps to run per row.
            ``limit`` (int, default 0):
                Maximum rows to process (0 = unlimited).  Alias: ``max_rows``.
            ``max_rows`` (int, default 0):
                Alias for ``limit``.
            ``var`` (str, default ``"$item"``):
                Placeholder variable name (currently ``$item`` and
                ``$item.<key>`` are always the substitution targets).
        """
        import csv as _csv
        import json as _json

        file_path: str = step.get("file", "")
        inner_steps: list = step.get("steps") or []
        # max_rows is an alias for limit (Issue #355)
        limit: int = int(step.get("limit", step.get("max_rows", 0)))
        var: str = step.get("var", "$item")

        if not file_path:
            logger.warning("foreach_file step: 'file' key missing — skipping")
            return

        from pathlib import Path as _Path

        # Detect format: explicit 'format' key overrides extension
        fmt: str = step.get("format", "")
        if not fmt:
            fmt = "csv" if file_path.lower().endswith(".csv") else "jsonl"

        try:
            content = _Path(file_path).read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("foreach_file step: cannot open file %r: %s", file_path, exc)
            return

        # Build row iterator
        if fmt == "csv":
            import io as _io

            rows: list = list(_csv.DictReader(_io.StringIO(content)))
        else:
            rows = []
            for raw_line in content.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    item = _json.loads(line)
                except _json.JSONDecodeError as exc:
                    logger.warning("foreach_file step: JSON parse error: %s", exc)
                    continue
                if not isinstance(item, dict):
                    logger.warning(
                        "foreach_file step: row is not a JSON object (%r) — skipping",
                        type(item).__name__,
                    )
                    continue
                rows.append(item)

        processed = 0
        for item in rows:
            if not self._running:
                logger.info("foreach_file step: stopped at row %d", processed)
                break
            if limit and processed >= limit:
                logger.info("foreach_file step: limit %d reached", limit)
                break

            # Substitute $item and $item.<key> placeholders in inner step dicts.
            substituted: list = []
            for s in inner_steps:
                new_s: dict = {}
                for k, v in s.items():
                    if isinstance(v, str):
                        if v == var:
                            new_s[k] = item
                        elif v.startswith(f"{var}."):
                            field_key = v[len(var) + 1 :]
                            new_s[k] = item.get(field_key, v)
                        else:
                            new_s[k] = v
                    else:
                        new_s[k] = v
                substituted.append(new_s)

            logger.debug("foreach_file step: row %d → %r", processed, item)
            self._run_step_list(substituted, f"foreach_file row {processed}")
            processed += 1

        logger.info("foreach_file step: done — processed %d row(s) from %r", processed, file_path)

    # ------------------------------------------------------------------
    # Issue #360 — schedule step
    # ------------------------------------------------------------------

    def _step_schedule(self, step: dict) -> None:
        """Execute inner steps at a wall-clock time or on a recurring interval.

        Modes
        -----
        ``at``
            ``step["at"]`` is a 24-hour ``HH:MM`` string.  The runner waits
            until the next occurrence of that wall-clock minute, then executes
            ``step["steps"]`` once.

        ``every``
            ``step["every"]`` is a float number of seconds.  The runner
            executes ``step["steps"]`` repeatedly every N seconds while
            ``self._running`` is ``True``.  An optional ``step["count"]`` caps
            the total number of repetitions.

        Both modes use ``self._stop_event.wait(timeout=...)`` so that the
        runner can be interrupted cleanly.  Never raises.

        Example step::

            - type: schedule
              every: 30
              count: 5
              steps:
                - type: speak
                  text: "tick"

            - type: schedule
              at: "08:00"
              steps:
                - type: speak
                  text: "good morning"
        """
        import datetime as _dt

        inner = step.get("steps", [])
        if not inner:
            logger.warning("schedule step: 'steps' is empty — skipping")
            return

        at_str: str = step.get("at", "")
        every_s: float = float(step.get("every", 0))
        count: int = int(step.get("count", 0))  # 0 = unlimited

        if at_str:
            # ── 'at' mode: wait until the next HH:MM wall-clock occurrence ──
            try:
                target_h, target_m = (int(x) for x in at_str.split(":", 1))
            except (ValueError, TypeError):
                logger.warning("schedule step: invalid 'at' value %r — skipping", at_str)
                return

            now = _dt.datetime.now()
            target = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
            if target <= now:
                target += _dt.timedelta(days=1)
            wait_s = (target - _dt.datetime.now()).total_seconds()
            logger.info("schedule step: waiting %.0fs until %02d:%02d", wait_s, target_h, target_m)
            if wait_s > 0:
                self._stop_event.wait(timeout=wait_s)
            if not self._running:
                return
            logger.info("schedule step: executing at %s", at_str)
            self._run_step_list(inner, "schedule at")

        elif every_s > 0:
            # ── 'every' mode: execute repeatedly at a fixed interval ─────────
            executions = 0
            logger.info(
                "schedule step: repeating every %.1fs%s",
                every_s,
                f", count={count}" if count else "",
            )
            while self._running:
                if count and executions >= count:
                    logger.info("schedule step: count %d reached — stopping", count)
                    break
                logger.debug("schedule step: executing (iteration %d)", executions + 1)
                self._run_step_list(inner, f"schedule every iteration {executions}")
                executions += 1
                if count and executions >= count:
                    break
                self._stop_event.wait(timeout=every_s)
            logger.info("schedule step: done — %d execution(s)", executions)

        else:
            logger.warning("schedule step: neither 'at' nor 'every' specified — skipping")

    # ------------------------------------------------------------------
    # Issue #365 — retry step
    # ------------------------------------------------------------------

    def _step_retry(self, step: dict) -> None:
        """Re-run inner steps up to *max_attempts* times on failure.

        A "failure" is detected by ``self._running`` becoming ``False`` during
        execution of the inner step list (e.g. because a sub-step called
        ``stop()`` or an exception propagated).  Uses ``_stop_event.wait()``
        for interruptible backoff.

        Example step::

            - type: retry
              max_attempts: 3
              backoff_s: 2.0
              steps:
                - type: think
                  instruction: "navigate to dock"

        Parameters
        ----------
        step:
            ``max_attempts`` (int, default 3): Maximum attempts.
            ``backoff_s`` (float, default 1.0): Seconds to wait between tries.
            ``steps`` (list): Inner steps to retry.
        """
        inner = step.get("steps") or []
        if not inner:
            logger.warning("retry step: 'steps' is empty — skipping")
            return

        max_attempts: int = max(1, int(step.get("max_attempts", 3)))
        backoff_s: float = max(0.0, float(step.get("backoff_s", 1.0)))

        for attempt in range(1, max_attempts + 1):
            if not self._running:
                break
            logger.debug("retry step: attempt %d/%d", attempt, max_attempts)
            # Snapshot running state before attempt
            self._run_step_list(inner, f"retry attempt {attempt}")
            if self._running:
                logger.info("retry step: succeeded on attempt %d", attempt)
                return
            if attempt < max_attempts:
                logger.info(
                    "retry step: attempt %d failed — waiting %.1fs before retry",
                    attempt,
                    backoff_s,
                )
                self._running = True  # re-arm for next attempt
                if backoff_s > 0:
                    self._stop_event.wait(timeout=backoff_s)

        logger.warning("retry step: all %d attempts exhausted", max_attempts)

    # ------------------------------------------------------------------
    # Issue #368 — set_var / get_var steps
    # ------------------------------------------------------------------

    def _step_set_var(self, step: dict) -> None:
        """Store a value in the runtime variable store.

        Example::

            - type: set_var
              name: target_distance
              value: 1.5

        Parameters
        ----------
        step:
            ``name`` (str, required): Variable name.
            ``value`` (any): Value to store.
        """
        name: str = step.get("name", "")
        if not name:
            logger.warning("set_var step: 'name' key missing — skipping")
            return
        value = step.get("value")
        self._vars[name] = value
        logger.debug("set_var: %r = %r", name, value)

    def _step_get_var(self, step: dict) -> None:
        """Retrieve a value from the runtime variable store and inject it.

        Injects the variable value into nested steps by substituting
        ``"$var.<name>"`` string placeholders.

        Example::

            - type: get_var
              name: target_distance
              steps:
                - type: waypoint
                  distance_m: "$var.target_distance"

        Parameters
        ----------
        step:
            ``name`` (str, required): Variable name.
            ``default`` (any, default None): Value if variable not set.
            ``steps`` (list): Inner steps with ``$var.<name>`` substitution.
        """
        name: str = step.get("name", "")
        if not name:
            logger.warning("get_var step: 'name' key missing — skipping")
            return

        default = step.get("default")
        value = self._vars.get(name, default)
        inner_steps: list = step.get("steps") or []
        placeholder = f"$var.{name}"

        substituted: list = []
        for s in inner_steps:
            new_s: dict = {}
            for k, v in s.items():
                if isinstance(v, str) and v == placeholder:
                    new_s[k] = value
                else:
                    new_s[k] = v
            substituted.append(new_s)

        logger.debug("get_var: %r = %r → %d inner step(s)", name, value, len(substituted))
        self._run_step_list(substituted, f"get_var {name}")

    # ------------------------------------------------------------------
    # Issue #373 — assert step
    # ------------------------------------------------------------------

    def _step_assert(self, step: dict) -> None:
        """Halt (or warn) if a numeric condition is not met.

        Evaluates a simple condition of the form ``<lhs> <op> <rhs>`` where
        *lhs* may reference a runtime variable via ``"$var.<name>"``.

        Supported operators: ``>``, ``>=``, ``<``, ``<=``, ``==``, ``!=``.

        Example::

            - type: assert
              condition: "$var.battery_pct > 20"
              on_fail: stop

        Parameters
        ----------
        step:
            ``condition`` (str, required): Condition expression.
            ``on_fail`` (str, default ``"stop"``): ``"stop"`` halts the
                behavior; ``"warn"`` logs a warning but continues.
        """
        condition: str = step.get("condition", "")
        on_fail: str = step.get("on_fail", "stop")

        if not condition:
            logger.warning("assert step: 'condition' key missing — skipping")
            return

        try:
            result = self._eval_expr(condition)
        except Exception as exc:
            logger.warning("assert step: condition evaluation error: %s", exc)
            result = False

        if result:
            logger.debug("assert step: condition %r passed", condition)
            return

        msg = f"assert step: condition {condition!r} FAILED"
        if on_fail == "warn":
            logger.warning(msg)
        else:
            logger.error(msg)
            self._running = False

    def _eval_expr(self, condition: str) -> bool:
        """Evaluate a simple ``lhs op rhs`` condition string.

        Substitutes ``$var.<name>`` tokens from ``self._vars`` before
        evaluating.  Only numeric comparisons are supported (no exec/eval).
        """
        import re as _re

        # Substitute $var.<name> placeholders
        def _sub(m: _re.Match) -> str:
            var_name = m.group(1)
            val = self._vars.get(var_name)
            return str(val) if val is not None else "None"

        expr = _re.sub(r"\$var\.(\w+)", _sub, condition.strip())

        # Parse: <lhs> <op> <rhs>
        _OPS = {
            ">=": lambda a, b: a >= b,
            "<=": lambda a, b: a <= b,
            "!=": lambda a, b: a != b,
            "==": lambda a, b: a == b,
            ">": lambda a, b: a > b,
            "<": lambda a, b: a < b,
        }
        for op_str, op_fn in _OPS.items():
            if op_str in expr:
                parts = expr.split(op_str, 1)
                if len(parts) == 2:
                    lhs_s = parts[0].strip()
                    rhs_s = parts[1].strip()
                    try:
                        lhs = float(lhs_s)
                        rhs = float(rhs_s)
                        return op_fn(lhs, rhs)
                    except (ValueError, TypeError) as exc:
                        raise ValueError(
                            f"Cannot evaluate condition {condition!r}: lhs={lhs_s!r}, rhs={rhs_s!r}"
                        ) from exc
        raise ValueError(f"No supported operator found in condition {condition!r}")

    # ------------------------------------------------------------------
    # Issue #379 — event_trigger step + set_event / clear_event
    # ------------------------------------------------------------------

    def set_event(self, name: str) -> None:
        """Set the named event, unblocking any waiting event_trigger step.

        Creates the event if it does not yet exist.

        Args:
            name: Event name string (must match the ``event:`` key in the step).
        """
        import threading as _th

        with self._events_lock:
            if name not in self._events:
                self._events[name] = _th.Event()
            self._events[name].set()
        logger.debug("BehaviorRunner.set_event: %r", name)

    def clear_event(self, name: str) -> None:
        """Clear the named event.

        Args:
            name: Event name string.
        """
        with self._events_lock:
            if name in self._events:
                self._events[name].clear()
        logger.debug("BehaviorRunner.clear_event: %r", name)

    def _step_event_trigger(self, step: dict) -> None:
        """Block until a named event is set (or timeout elapses).

        Example::

            - type: event_trigger
              event: obstacle_cleared
              timeout_s: 30.0
              on_timeout: warn   # or stop (default)

        Parameters
        ----------
        step:
            ``event`` (str, required): Event name to wait for.
            ``timeout_s`` (float, default 30.0): Max wait time in seconds.
            ``on_timeout`` (str, default ``"stop"``): ``"stop"`` sets
                ``_running=False``; ``"warn"`` logs a warning and continues.
        """
        import threading as _th

        event_name: str = step.get("event", "")
        if not event_name:
            logger.warning("event_trigger step: 'event' key missing — skipping")
            return

        timeout_s: float = float(step.get("timeout_s", 30.0))
        on_timeout: str = step.get("on_timeout", "stop")

        with self._events_lock:
            if event_name not in self._events:
                self._events[event_name] = _th.Event()
            ev = self._events[event_name]

        logger.debug("event_trigger: waiting for %r (timeout_s=%.1f)", event_name, timeout_s)
        fired = ev.wait(timeout=timeout_s if timeout_s > 0 else None)

        if fired:
            logger.debug("event_trigger: event %r fired", event_name)
        else:
            msg = f"event_trigger: timeout waiting for event {event_name!r}"
            if on_timeout == "warn":
                logger.warning(msg)
            else:
                logger.error(msg)
                self._running = False

    # ------------------------------------------------------------------
    # Issue #383 — log_step: append structured JSON log entry to file
    # ------------------------------------------------------------------

    def _step_log_step(self, step: dict) -> None:
        """Append a structured JSON record to a log file.

        Example::

            - type: log_step
              path: /tmp/behavior.log
              data:
                event: patrol_started
                robot: my-robot

        ``data`` values that equal ``$var.<name>`` are substituted from
        the runtime variable store before writing.

        Parameters
        ----------
        step:
            ``path`` (str, required): File path to append to.
            ``data`` (dict, optional): Key-value pairs to include in the record.
                Values matching ``"$var.<name>"`` are substituted from ``_vars``.
        """
        import json as _json
        import time as _time

        path: str = step.get("path", "")
        if not path:
            logger.warning("log_step: 'path' key missing — skipping")
            return

        raw_data: dict = step.get("data") or {}

        # Substitute $var.<name> placeholders in values
        substituted: dict = {}
        for k, v in raw_data.items():
            if isinstance(v, str) and v.startswith("$var."):
                var_name = v[5:]
                substituted[k] = self._vars.get(var_name, v)
            else:
                substituted[k] = v

        record = {"ts": _time.time(), "step": step.get("label", "log_step"), "data": substituted}

        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(_json.dumps(record) + "\n")
            logger.debug("log_step: wrote record to %r", path)
        except Exception as exc:
            logger.warning("log_step: could not write to %r: %s", path, exc)

    # ------------------------------------------------------------------
    # Issue #400 — unless step (inverse condition)
    # ------------------------------------------------------------------

    def _step_unless(self, step: dict) -> None:
        """Run *inner_steps* only when *condition* evaluates to False (Issue #400).

        This is the inverse of the ``condition`` step — it skips execution when
        the condition is truthy and runs the steps when it is falsy.

        Example step::

            - type: unless
              condition: "battery.soc_pct > 20"
              inner_steps:
                - type: speak
                  text: "Battery low!"

        Parameters
        ----------
        step:
            ``condition`` (str, required): Expression evaluated by :meth:`_eval_condition`.
            ``inner_steps`` (list, required): Steps to run when condition is False.
        """
        condition = step.get("condition", "")
        inner_steps: list = step.get("inner_steps") or []

        if not condition:
            logger.warning("unless step: 'condition' is missing — skipping")
            return
        if not inner_steps:
            logger.warning("unless step: 'inner_steps' is empty — skipping")
            return

        try:
            result = self._eval_expr(condition)
        except Exception as exc:
            logger.warning("unless step: condition eval error: %s — skipping", exc)
            return

        if result:
            logger.debug("unless step: condition %r is True — skipping inner steps", condition)
            return

        logger.debug("unless step: condition %r is False — running inner steps", condition)
        self._run_step_list(inner_steps, "unless")

    # ------------------------------------------------------------------
    # Issue #402 — break_on step (sentinel exception for loop exit)
    # ------------------------------------------------------------------

    def _step_break_on(self, step: dict) -> None:
        """Raise _BreakLoop if *condition* is True, exiting the enclosing loop (Issue #402).

        Example step::

            - type: break_on
              condition: "imu.steps > 100"

        Parameters
        ----------
        step:
            ``condition`` (str, required): Expression evaluated by :meth:`_eval_condition`.
                If omitted, always breaks.
        """
        condition = step.get("condition", "")

        if not condition:
            # No condition — always break
            logger.debug("break_on step: no condition — breaking loop")
            raise _BreakLoop("break_on: unconditional break")

        try:
            result = self._eval_expr(condition)
        except Exception as exc:
            logger.warning("break_on step: condition eval error: %s — not breaking", exc)
            return

        if result:
            logger.debug("break_on step: condition %r is True — breaking loop", condition)
            raise _BreakLoop(f"break_on: condition={condition!r}")
        else:
            logger.debug("break_on step: condition %r is False — continuing", condition)

    # ------------------------------------------------------------------
    # Issue #411 — parallel_race step
    # ------------------------------------------------------------------

    def _step_parallel_race(self, step: dict) -> None:
        """Run inner steps concurrently; return as soon as the first one finishes (Issue #411).

        Unlike ``parallel`` (which waits for all), ``parallel_race`` cancels
        remaining threads as soon as any inner step completes without raising.

        Example step::

            - type: parallel_race
              inner_steps:
                - type: wait
                  duration_s: 5
                - type: speak
                  text: "hello"
              timeout_s: 30   # optional overall timeout

        Parameters
        ----------
        step:
            ``inner_steps`` (list, required): Steps to run concurrently.
            ``timeout_s`` (float, default 0): Overall timeout. 0 = none.
        """
        if not self._running:
            return

        inner_steps: list = step.get("inner_steps", [])
        if not inner_steps:
            logger.warning("parallel_race step: 'inner_steps' is missing or empty — skipping")
            return

        timeout_s: float = float(step.get("timeout_s", 0))

        logger.info(
            "parallel_race step: launching %d inner step(s) in race (timeout_s=%.1f)",
            len(inner_steps),
            timeout_s,
        )

        # Use threading.Event to signal first completion
        first_done = threading.Event()

        def _race_run(inner_step: dict) -> None:
            try:
                self._run_step(inner_step)
            finally:
                first_done.set()  # signal regardless of success/failure

        threads = []
        for inner_step in inner_steps:
            t = threading.Thread(target=_race_run, args=(inner_step,), daemon=True)
            threads.append(t)
            t.start()

        # Wait for the first to finish (or timeout)
        wait_time = timeout_s if timeout_s > 0 else None
        first_done.wait(timeout=wait_time)

        # Count how many finished
        finished = sum(1 for t in threads if not t.is_alive())
        logger.info(
            "parallel_race step: race finished — %d/%d thread(s) completed",
            finished,
            len(inner_steps),
        )

    # ------------------------------------------------------------------
    # Issue #419 — wait_for_event step (named event version)
    # ------------------------------------------------------------------

    def _step_wait_for_event(self, step: dict) -> None:
        """Block until a named event is set via set_event() (Issue #419).

        Uses the runner's internal _events dict (threading.Event objects).
        Creates the event if it doesn't exist yet.

        Example step::

            - type: wait_for_event
              event: "my_event_name"
              timeout_s: 30     # optional
              clear_after: true  # optional: clear event after waiting

        Parameters
        ----------
        step:
            ``event`` (str, required): Name of the event to wait for.
            ``timeout_s`` (float, default 30.0): Max wait time. 0 = no timeout.
            ``clear_after`` (bool, default True): Clear the event after it fires.
        """
        event_name: str = step.get("event", "")
        timeout_s: float = float(step.get("timeout_s", 30.0))
        clear_after: bool = bool(step.get("clear_after", True))

        if not event_name:
            logger.warning("wait_for_event step: 'event' key missing — skipping")
            return

        # Ensure event exists
        with self._events_lock:
            if event_name not in self._events:
                self._events[event_name] = threading.Event()
            ev = self._events[event_name]

        logger.info(
            "wait_for_event step: waiting for event %r (timeout_s=%.1f)",
            event_name,
            timeout_s,
        )

        wait_time = timeout_s if timeout_s > 0 else None
        fired = ev.wait(timeout=wait_time)

        if fired:
            logger.info("wait_for_event step: event %r fired", event_name)
            if clear_after:
                with self._events_lock:
                    if event_name in self._events:
                        self._events[event_name].clear()
        else:
            logger.info(
                "wait_for_event step: event %r timed out after %.1fs",
                event_name,
                timeout_s,
            )
