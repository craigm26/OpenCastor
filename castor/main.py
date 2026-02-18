"""
OpenCastor Runtime - The main entry point.
Ties Brain (Provider), Body (Driver), Eyes (Camera), Voice (TTS),
Law (RCAN Config), and the Virtual Filesystem together.
"""

import argparse
import io
import logging
import os
import threading
import time

import yaml

from castor.fs import CastorFS
from castor.providers import get_provider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("OpenCastor")


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------
def load_config(path: str) -> dict:
    """Loads and validates the RCAN configuration."""
    try:
        with open(path) as f:
            config = yaml.safe_load(f)
            logger.info(f"Loaded Configuration: {config['metadata']['robot_name']}")
            return config
    except FileNotFoundError as exc:
        logger.error(f"Config file not found: {path}")
        raise SystemExit(1) from exc


# ---------------------------------------------------------------------------
# Driver factory
# ---------------------------------------------------------------------------
def get_driver(config: dict):
    """Initialize the appropriate driver based on config."""
    if not config.get("drivers"):
        return None

    driver_config = config["drivers"][0]
    protocol = driver_config.get("protocol", "")

    if protocol == "pca9685_rc":
        from castor.drivers.pca9685 import PCA9685RCDriver

        return PCA9685RCDriver(driver_config)
    elif "pca9685" in protocol:
        from castor.drivers.pca9685 import PCA9685Driver

        return PCA9685Driver(driver_config)
    elif "dynamixel" in protocol:
        from castor.drivers.dynamixel import DynamixelDriver

        return DynamixelDriver(driver_config)
    else:
        logger.warning(f"Unknown driver protocol: {protocol}. Running without hardware.")
        return None


# ---------------------------------------------------------------------------
# Camera abstraction (CSI via picamera2, USB via OpenCV, or blank)
# ---------------------------------------------------------------------------
class Camera:
    """Unified camera interface with three operating modes:

      1. CSI mode (picamera2) -- Raspberry Pi ribbon-cable camera.
      2. USB mode (OpenCV) -- standard USB webcams.
      3. Blank mode (returns a fixed-size, zero-filled placeholder frame when no
         camera is available).

    Config (``config["camera"]``):
      - ``type`` (str): ``"auto"`` (default), ``"csi"``, or ``"usb"``.
      - ``resolution`` (list[int, int]): Target frame size, default ``[640, 480]``.

    In normal operation, :meth:`capture_jpeg` returns a JPEG-encoded frame.
    When no camera is successfully initialized, :meth:`capture_jpeg` instead
    returns a 1024-byte zero-filled placeholder buffer (not a valid JPEG), and
    :meth:`close` is a safe no-op.
    """

    def __init__(self, config: dict):
        self._picam = None
        self._cv_cap = None

        cam_cfg = config.get("camera", {})
        cam_type = cam_cfg.get("type", "auto")
        res = cam_cfg.get("resolution", [640, 480])

        # --- Try picamera2 (CSI ribbon cable camera) ---
        if cam_type in ("csi", "auto"):
            try:
                from picamera2 import Picamera2

                self._picam = Picamera2()
                cam_config = self._picam.create_still_configuration(
                    main={"size": (res[0], res[1]), "format": "RGB888"}
                )
                self._picam.configure(cam_config)
                self._picam.start()
                logger.info(f"CSI camera online ({res[0]}x{res[1]})")
                return
            except Exception as exc:
                if cam_type == "csi":
                    logger.error(f"CSI camera requested but failed: {exc}")
                else:
                    logger.debug(f"picamera2 not available: {exc}")
                self._picam = None

        # --- Fall back to OpenCV (USB cameras) ---
        if cam_type in ("usb", "auto"):
            try:
                import cv2

                idx = int(os.getenv("CAMERA_INDEX", "0"))
                self._cv_cap = cv2.VideoCapture(idx)
                if self._cv_cap.isOpened():
                    self._cv_cap.set(cv2.CAP_PROP_FRAME_WIDTH, res[0])
                    self._cv_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, res[1])
                    logger.info(f"USB camera online (index {idx})")
                    return
                else:
                    self._cv_cap.release()
                    self._cv_cap = None
            except ImportError:
                pass

        logger.warning("No camera detected. Using blank frames.")

    def is_available(self) -> bool:
        """Return True if a real camera (CSI or USB) is online."""
        return self._picam is not None or self._cv_cap is not None

    def capture_jpeg(self) -> bytes:
        """Return a JPEG-encoded frame as bytes."""
        if self._picam is not None:
            try:
                import cv2

                frame = self._picam.capture_array()
                _, buf = cv2.imencode(".jpg", frame)
                return buf.tobytes()
            except Exception:
                return b"\x00" * 1024

        if self._cv_cap is not None:
            import cv2

            ret, frame = self._cv_cap.read()
            if ret:
                _, buf = cv2.imencode(".jpg", frame)
                return buf.tobytes()

        return b"\x00" * 1024

    def close(self):
        if self._picam is not None:
            try:
                self._picam.stop()
            except Exception:
                pass
        if self._cv_cap is not None:
            self._cv_cap.release()


# ---------------------------------------------------------------------------
# TTS (text-to-speech via USB speaker)
# ---------------------------------------------------------------------------
class Speaker:
    """Speaks the robot's thoughts aloud using gTTS + pygame."""

    def __init__(self, config: dict):
        audio_cfg = config.get("audio", {})
        self.enabled = audio_cfg.get("tts_enabled", False)
        self.language = audio_cfg.get("language", "en")
        self._lock = threading.Lock()
        self._mixer_ready = False

        if not self.enabled:
            return

        try:
            import pygame
            from gtts import gTTS  # noqa: F401

            pygame.mixer.init()
            self._mixer_ready = True
            logger.info("TTS speaker online (gTTS + pygame)")
        except ImportError as exc:
            logger.warning(f"TTS disabled -- missing dependency: {exc}")
            self.enabled = False
        except Exception as exc:
            logger.warning(f"TTS disabled -- audio init failed: {exc}")
            self.enabled = False

    def say(self, text: str):
        """Speak text asynchronously (non-blocking)."""
        if not self.enabled or not text:
            return
        threading.Thread(target=self._speak, args=(text,), daemon=True).start()

    def _speak(self, text: str):
        with self._lock:
            try:
                import pygame
                from gtts import gTTS

                if not self._mixer_ready:
                    try:
                        pygame.mixer.init()
                        self._mixer_ready = True
                    except Exception as exc:
                        logger.warning(f"TTS disabled -- audio init failed during playback: {exc}")
                        self.enabled = False
                        return

                buf = io.BytesIO()
                tts = gTTS(text=text[:200], lang=self.language)
                tts.write_to_fp(buf)
                buf.seek(0)
                pygame.mixer.music.load(buf, "mp3")
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    time.sleep(0.1)
            except Exception as exc:
                logger.debug(f"TTS error: {exc}")

    def close(self):
        """Stop playback and release the audio mixer."""
        self.enabled = False
        try:
            import pygame

            if self._mixer_ready:
                pygame.mixer.music.stop()
                pygame.mixer.quit()
                self._mixer_ready = False
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Shared globals for gateway access (thread-safe)
# ---------------------------------------------------------------------------
_shared_lock = threading.Lock()
_shared_camera: Camera = None
_shared_speaker: Speaker = None
_shared_fs: CastorFS = None


def get_shared_camera() -> Camera:
    with _shared_lock:
        return _shared_camera


def set_shared_camera(camera: Camera):
    global _shared_camera
    with _shared_lock:
        _shared_camera = camera


def get_shared_speaker() -> Speaker:
    with _shared_lock:
        return _shared_speaker


def set_shared_speaker(speaker: Speaker):
    global _shared_speaker
    with _shared_lock:
        _shared_speaker = speaker


def get_shared_fs() -> CastorFS:
    with _shared_lock:
        return _shared_fs


def set_shared_fs(fs: CastorFS):
    global _shared_fs
    with _shared_lock:
        _shared_fs = fs


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="OpenCastor Runtime")
    parser.add_argument(
        "--config",
        type=str,
        default="robot.rcan.yaml",
        help="Path to RCAN config file",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run without physical hardware",
    )
    parser.add_argument(
        "--memory-dir",
        type=str,
        default=None,
        help="Directory for persistent memory (default: none)",
    )
    args = parser.parse_args()

    # 0. CRASH RECOVERY CHECK
    try:
        from castor.crash import handle_crash_on_startup

        if not handle_crash_on_startup():
            return
    except Exception:
        pass  # crash module is optional

    # 1. BOOT SEQUENCE
    logger.info("Booting OpenCastor Runtime...")
    config = load_config(args.config)

    # 1a. STARTUP HEALTH CHECK
    try:
        from castor.healthcheck import print_health_report, run_startup_checks

        health = run_startup_checks(config, simulate=args.simulate)
        print_health_report(health)
        if health["status"] == "critical":
            logger.critical("Health check CRITICAL — resolve issues before continuing")
            # Don't block, but warn loudly
    except Exception as e:
        logger.debug(f"Health check skipped: {e}")

    # 1b. INITIALIZE VIRTUAL FILESYSTEM
    fs = CastorFS(persist_dir=args.memory_dir)
    fs.boot(config)
    set_shared_fs(fs)
    logger.info("Virtual Filesystem Online")

    # 1c. CONSTRUCT RURI
    try:
        from castor.rcan.ruri import RURI

        ruri = RURI.from_config(config)
        fs.proc.set_ruri(str(ruri))
        logger.info(f"RURI: {ruri}")
    except Exception as e:
        logger.debug(f"RURI construction skipped: {e}")

    # 2. INITIALIZE BRAIN
    try:
        brain = get_provider(config["agent"])
        logger.info(f"Brain Online: {config['agent'].get('model', 'unknown')}")
        fs.proc.set_driver("none")
    except Exception as e:
        logger.critical(f"Failed to initialize Brain: {e}")
        raise SystemExit(1) from e

    # 3. INITIALIZE BODY (Drivers)
    driver = None
    if not args.simulate:
        try:
            driver = get_driver(config)
            if driver:
                logger.info("Hardware Online")
                protocol = config.get("drivers", [{}])[0].get("protocol", "unknown")
                fs.proc.set_driver(protocol)
        except Exception as e:
            logger.error(f"Hardware Init Failed: {e}. Switching to Simulation.")
            args.simulate = True

    # 4. INITIALIZE EYES (Camera -- CSI first, then USB, then blank)
    camera = Camera(config)
    set_shared_camera(camera)
    fs.proc.set_camera("online" if camera.is_available() else "offline")

    # 5. INITIALIZE VOICE (TTS via USB speaker)
    speaker = Speaker(config)
    set_shared_speaker(speaker)
    fs.proc.set_speaker("online" if speaker.enabled else "offline")

    # 6. mDNS BROADCAST (opt-in)
    mdns_broadcaster = None
    rcan_proto = config["rcan_protocol"]
    if rcan_proto.get("enable_mdns"):
        try:
            from castor.rcan.mdns import RCANServiceBroadcaster

            ruri_str = fs.ns.read("/proc/ruri") or "rcan://opencastor.unknown.00000000"
            mdns_broadcaster = RCANServiceBroadcaster(
                ruri=ruri_str,
                robot_name=config.get("metadata", {}).get("robot_name", "OpenCastor"),
                port=int(rcan_proto.get("port", 8000)),
                capabilities=rcan_proto.get("capabilities", []),
                model=config.get("metadata", {}).get("model", "unknown"),
                status_fn=lambda: fs.ns.read("/proc/status") or "active",
            )
            mdns_broadcaster.start()
        except Exception as e:
            logger.debug(f"mDNS broadcast skipped: {e}")

    # 6b. PRIVACY POLICY (default-deny for sensors)
    try:
        from castor.privacy import PrivacyPolicy

        PrivacyPolicy(config)
    except Exception as e:
        logger.debug(f"Privacy policy skipped: {e}")

    # 6c. APPROVAL GATE (opt-in for dangerous commands)
    approval_gate = None
    try:
        from castor.approvals import ApprovalGate

        approval_gate = ApprovalGate(config)
        if approval_gate.require_approval:
            logger.info("Approval gate active -- dangerous commands will be queued")
    except Exception as e:
        logger.debug(f"Approval gate skipped: {e}")

    # 6d. BATTERY MONITOR (opt-in)
    battery_monitor = None
    try:
        from castor.battery import BatteryMonitor

        def _on_battery_critical(voltage):
            logger.critical(f"Battery critical ({voltage}V) -- stopping motors!")
            if driver:
                driver.stop()

        battery_monitor = BatteryMonitor(
            config,
            on_warn=lambda v: logger.warning(f"Battery low: {v}V"),
            on_critical=_on_battery_critical,
        )
        if battery_monitor.enabled:
            battery_monitor.start()
            logger.info(f"Battery monitor online (warn={battery_monitor.warn_voltage}V)")
    except Exception as e:
        logger.debug(f"Battery monitor skipped: {e}")

    # 6e. WATCHDOG (auto-stop motors if brain unresponsive)
    watchdog = None
    try:
        from castor.watchdog import BrainWatchdog

        stop_fn = driver.stop if driver else None
        watchdog = BrainWatchdog(config, stop_fn=stop_fn)
        watchdog.start()
    except Exception as e:
        logger.debug(f"Watchdog skipped: {e}")

    # 6f. GEOFENCE (limit operating radius)
    geofence = None
    try:
        from castor.geofence import Geofence

        geofence = Geofence(config)
    except Exception as e:
        logger.debug(f"Geofence skipped: {e}")

    # 6g. AUDIT LOG
    audit = None
    try:
        from castor.audit import get_audit

        audit = get_audit()
        audit.log_startup(args.config)
    except Exception as e:
        logger.debug(f"Audit log skipped: {e}")

    # 7. THE CONTROL LOOP
    latency_budget = config.get("agent", {}).get("latency_budget_ms", 3000)
    logger.info("Entering Perception-Action Loop. Press Ctrl+C to stop.")

    # Latency tracking for sustained-overrun warnings
    _latency_overrun_count = 0
    _LATENCY_WARN_THRESHOLD = 5  # consecutive overruns before suggesting action

    try:
        while True:
            loop_start = time.time()

            # Check emergency stop
            if fs.is_estopped:
                logger.warning("E-STOP active. Waiting...")
                time.sleep(1.0)
                continue

            # --- PHASE 1: OBSERVE ---
            frame_bytes = camera.capture_jpeg()
            fs.ns.write("/dev/camera", {"t": time.time(), "size": len(frame_bytes)})

            # --- PHASE 2: ORIENT & DECIDE ---
            # Build instruction with memory context
            memory_ctx = fs.memory.build_context_summary()
            context_ctx = fs.context.build_prompt_context()
            instruction = "Scan the area and report what you see."
            if memory_ctx:
                instruction = f"{instruction}\n\n{memory_ctx}"
            if context_ctx:
                instruction = f"{instruction}\n\n{context_ctx}"

            thought = brain.think(frame_bytes, instruction)
            fs.proc.record_thought(thought.raw_text, thought.action)

            # Watchdog heartbeat (brain responded successfully)
            if watchdog:
                watchdog.heartbeat()

            # --- PHASE 3: ACT ---
            if thought.action:
                logger.info(f"Action: {thought.action}")

                # Approval gate: queue dangerous actions for human review
                action_to_execute = thought.action
                if approval_gate:
                    gate_result = approval_gate.check(thought.action)
                    if isinstance(gate_result, dict) and gate_result.get("status") == "pending":
                        logger.warning(
                            f"Action queued for approval (ID={gate_result['approval_id']})"
                        )
                        action_to_execute = None  # Skip execution
                    else:
                        action_to_execute = gate_result

                # Geofence check
                if action_to_execute and geofence:
                    action_to_execute = geofence.check_action(action_to_execute)

                if action_to_execute:
                    # Write action through the safety layer (clamping + rate limiting)
                    fs.write("/dev/motor", action_to_execute, principal="brain")

                    if driver and not args.simulate:
                        # Read back the clamped values from the safety layer
                        clamped_action = fs.read("/dev/motor", principal="brain")
                        safe_action = clamped_action if clamped_action else action_to_execute
                        action_type = safe_action.get("type", "")
                        if action_type == "move":
                            linear = safe_action.get("linear", 0.0)
                            angular = safe_action.get("angular", 0.0)
                            driver.move(linear, angular)
                            if audit:
                                audit.log_motor_command(safe_action)
                        elif action_type == "stop":
                            driver.stop()

                # Record episode in memory
                fs.memory.record_episode(
                    observation=instruction[:100],
                    action=thought.action,
                    outcome=thought.raw_text[:100],
                )

                # Push to context window
                fs.context.push("brain", thought.raw_text[:200], metadata=thought.action)

                # Speak the raw reasoning (truncated)
                speaker.say(thought.raw_text[:120])
            else:
                logger.warning("Brain produced no valid action.")

            # --- PHASE 4: TELEMETRY & LATENCY CHECK ---
            latency = (time.time() - loop_start) * 1000
            fs.proc.record_loop_iteration(latency)
            if latency > latency_budget:
                _latency_overrun_count += 1
                logger.warning(f"Loop Lag: {latency:.2f}ms (Budget: {latency_budget}ms)")
                # Sustained overrun warning with suggestions
                if _latency_overrun_count == _LATENCY_WARN_THRESHOLD:
                    model = config.get("agent", {}).get("model", "unknown")
                    logger.warning(
                        f"Sustained latency overrun ({_latency_overrun_count} consecutive). "
                        f"Suggestions: "
                        f"(1) Switch to a faster model (current: {model}), "
                        f"(2) Reduce camera resolution, "
                        f"(3) Increase latency_budget_ms in your RCAN config"
                    )
            else:
                _latency_overrun_count = 0

            # Sleep to prevent API rate limiting
            time.sleep(1.0)

    except KeyboardInterrupt:
        logger.info("User Interrupt. Shutting down...")
        if audit:
            audit.log_shutdown("user_interrupt")
    except Exception as exc:
        logger.critical(f"Runtime crash: {exc}")
        if audit:
            audit.log_error(str(exc), source="runtime")
            audit.log_shutdown("crash")
        # Save crash report for next startup
        try:
            import traceback

            from castor.crash import save_crash_report

            last_thought = None
            last_action = None
            try:
                last_thought = fs.ns.read("/proc/last_thought")
                last_action = fs.ns.read("/proc/last_action")
            except Exception:
                pass
            uptime = time.time() - loop_start if "loop_start" in dir() else 0
            loop_count = fs.proc._loop_count if hasattr(fs.proc, "_loop_count") else 0
            save_crash_report(
                config_path=args.config,
                error=traceback.format_exc(),
                last_thought=str(last_thought) if last_thought else None,
                last_action=last_action,
                loop_count=loop_count,
                uptime_seconds=uptime,
            )
        except Exception:
            pass
        raise
    finally:
        # Clear shared references first so in-flight gateway requests
        # cannot grab a closing/closed device.
        set_shared_camera(None)
        set_shared_speaker(None)

        if watchdog:
            watchdog.stop()

        if battery_monitor:
            battery_monitor.stop()

        if mdns_broadcaster:
            mdns_broadcaster.stop()

        if driver and not args.simulate:
            logger.info("Parking hardware...")
            driver.close()
        speaker.close()
        camera.close()

        # Flush memory and shut down the virtual filesystem
        fs.shutdown()
        set_shared_fs(None)
        logger.info("OpenCastor Offline.")


if __name__ == "__main__":
    main()
