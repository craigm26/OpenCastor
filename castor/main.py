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
# Env file loader — runs before EVERYTHING else
# ---------------------------------------------------------------------------
def _load_env_file(path: str | None = None) -> int:
    """Load KEY=VALUE pairs from ~/.opencastor/env into os.environ.

    Rules:
      - Existing env vars are NEVER overwritten (shell export wins over file)
      - Blank lines and # comments are skipped
      - Returns number of variables loaded
    """
    path = path or os.path.expanduser("~/.opencastor/env")
    if not os.path.exists(path):
        return 0
    loaded = 0
    try:
        with open(path) as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:]
                if "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
                    loaded += 1
        if loaded:
            logger.debug(f"Loaded {loaded} env var(s) from {path}")
    except Exception as e:
        logger.debug(f"Could not load env file {path}: {e}")
    return loaded


# ---------------------------------------------------------------------------
# Hardware-detection-wins override
# ---------------------------------------------------------------------------
# Camera type priority: depth cameras first, then usb, then csi
_CAMERA_TYPE_PRIORITY = ["oakd", "realsense", "usb", "csi"]

_USB_CAMERA_IDS = {
    "03e7:2485",
    "03e7:f63b",  # OAK-D family
    "8086:0b3a",
    "8086:0b07",  # Intel RealSense
    "046d:082d",
    "046d:085e",  # Logitech webcams
    "045e:097d",
    "0c45:636b",  # Microsoft / Microdia
}


def _lsusb_ids() -> set:
    """Return set of VID:PID strings from lsusb, or empty set on failure."""
    import subprocess

    try:
        out = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=3)
        ids = set()
        for line in out.stdout.splitlines():
            parts = line.split()
            for p in parts:
                if len(p) == 9 and p[4] == ":":
                    ids.add(p.lower())
        return ids
    except Exception:
        return set()


def apply_hardware_overrides(config: dict) -> dict:
    """Scan connected hardware at startup and override stale RCAN config.

    Real hardware ALWAYS wins over the wizard/config file. The wizard ran
    when certain hardware was plugged in; at boot time, reality is ground truth.

    Overrides applied:
      - camera.type: if configured type not detected, pick best available
      - drivers[].address: if PCA9685 not at configured I2C addr, find actual addr

    Each override logs a warning so the user knows to update the config file.
    """
    import glob

    cam_cfg = config.setdefault("camera", {})
    configured_type = cam_cfg.get("type", "auto")

    # Try castor.peripherals (available in newer installs)
    scan_results = []
    try:
        from castor.peripherals import scan_all

        scan_results = scan_all()
    except Exception:
        pass

    # --- Camera override ---
    if configured_type != "auto":
        available_types: set = set()

        if scan_results:
            for p in scan_results:
                if p.category in ("camera", "depth"):
                    available_types.add(p.driver_hint)
        else:
            # Fallback: direct USB + device checks
            usb_ids = _lsusb_ids()
            if "03e7:2485" in usb_ids or "03e7:f63b" in usb_ids:
                available_types.add("oakd")
            if "8086:0b3a" in usb_ids or "8086:0b07" in usb_ids:
                available_types.add("realsense")
            if usb_ids & _USB_CAMERA_IDS:
                available_types.add("usb")
            if glob.glob("/dev/video*"):
                available_types.add("usb")
            try:
                import subprocess

                out = subprocess.run(
                    ["libcamera-hello", "--list-cameras"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if "0 :" in out.stdout:
                    available_types.add("csi")
            except Exception:
                pass

        if available_types and configured_type not in available_types:
            best_type = next((t for t in _CAMERA_TYPE_PRIORITY if t in available_types), None)
            if best_type:
                logger.warning(
                    "⚡ Hardware override: camera.type '%s' not detected — "
                    "switching to '%s' (detected: %s). "
                    "Update your RCAN config to silence this warning.",
                    configured_type,
                    best_type,
                    ", ".join(sorted(available_types)),
                )
                cam_cfg["type"] = best_type

    # --- PCA9685 I2C address override ---
    for driver in config.get("drivers", []):
        if "pca9685" not in driver.get("protocol", ""):
            continue
        try:
            configured_addr = int(str(driver.get("address", "0x40")), 16)
        except ValueError:
            continue

        actual_addr = None
        if scan_results:
            for p in scan_results:
                if p.driver_hint == "pca9685" and p.i2c_address is not None:
                    actual_addr = p.i2c_address
                    break
        else:
            try:
                import smbus2

                bus_num = int(driver.get("port", "/dev/i2c-1").replace("/dev/i2c-", ""))
                with smbus2.SMBus(bus_num) as bus:
                    for addr in [0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47]:
                        try:
                            bus.read_byte(addr)
                            actual_addr = addr
                            break
                        except Exception:
                            pass
            except Exception:
                pass

        if actual_addr is not None and actual_addr != configured_addr:
            logger.warning(
                "⚡ Hardware override: PCA9685 not at %s — found at %s. "
                "Update your RCAN config to silence this warning.",
                hex(configured_addr),
                hex(actual_addr),
            )
            driver["address"] = hex(actual_addr)

    return config


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
        self._oakd_pipeline = None
        self._oakd_rgb_q = None
        self._oakd_depth_q = None
        self.last_depth = None  # Expose depth for reactive layer

        cam_cfg = config.get("camera", {})
        cam_type = cam_cfg.get("type", "auto")
        res = cam_cfg.get("resolution", [640, 480])
        depth_enabled = cam_cfg.get("depth_enabled", False)

        # --- Try OAK-D (DepthAI USB camera with depth) ---
        if cam_type in ("oakd", "auto"):
            try:
                import depthai as dai

                pipeline = dai.Pipeline()

                cam = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)
                rgb_out = cam.requestOutput((res[0], res[1]), type=dai.ImgFrame.Type.BGR888p)
                self._oakd_rgb_q = rgb_out.createOutputQueue()

                if depth_enabled:
                    left_cam = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_B)
                    right_cam = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_C)
                    stereo = pipeline.create(dai.node.StereoDepth)
                    left_cam.requestOutput((640, 480), type=dai.ImgFrame.Type.GRAY8).link(
                        stereo.left
                    )
                    right_cam.requestOutput((640, 480), type=dai.ImgFrame.Type.GRAY8).link(
                        stereo.right
                    )
                    self._oakd_depth_q = stereo.depth.createOutputQueue()

                pipeline.start()
                self._oakd_pipeline = pipeline
                depth_str = " + depth" if depth_enabled else ""
                logger.info(f"OAK-D camera online ({res[0]}x{res[1]}{depth_str})")
                return
            except Exception as exc:
                if cam_type == "oakd":
                    logger.error(f"OAK-D camera requested but failed: {exc}")
                else:
                    logger.debug(f"OAK-D not available: {exc}")
                self._oakd_pipeline = None

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
        """Return True if a real camera (CSI, USB, or OAK-D) is online."""
        return (
            self._picam is not None or self._cv_cap is not None or self._oakd_pipeline is not None
        )

    def capture_jpeg(self) -> bytes:
        """Return a JPEG-encoded frame as bytes."""
        if self._oakd_pipeline is not None:
            try:
                import cv2

                rgb_frame = self._oakd_rgb_q.get()
                frame = rgb_frame.getCvFrame()

                # Also grab depth if available
                if self._oakd_depth_q is not None:
                    try:
                        depth_frame = self._oakd_depth_q.tryGet()
                        if depth_frame is not None:
                            self.last_depth = depth_frame.getFrame()
                    except Exception:
                        pass

                _, buf = cv2.imencode(".jpg", frame)
                return buf.tobytes()
            except Exception:
                return b"\x00" * 1024

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
        if self._oakd_pipeline is not None:
            try:
                self._oakd_pipeline.stop()
                logger.debug("OAK-D pipeline stopped")
            except Exception:
                pass
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

    # 0.5. LOAD ENV FILE — before any provider reads os.environ
    # ~/.opencastor/env contains HF_TOKEN, GOOGLE_API_KEY, etc.
    # Never overwrites vars already exported in the shell environment.
    _load_env_file()

    # Reset runtime stats for fresh session
    try:
        from castor.runtime_stats import reset as _reset_stats

        _reset_stats()
    except Exception:
        pass

    # 1. BOOT SEQUENCE
    logger.info("Booting OpenCastor Runtime...")
    config = load_config(args.config)

    # 1b-pre. HARDWARE DETECTION WINS
    # Real hardware at boot time overrides anything the wizard wrote to the config.
    # Detected OAK-D but config says CSI? Switches to OAK-D automatically.
    # Found PCA9685 at 0x41 but config says 0x40? Uses the real address.
    config = apply_hardware_overrides(config)

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

    # 2b. TIERED BRAIN (optional: primary = fast brain, secondary[0] = planner)
    tiered = None
    secondary_models = config.get("agent", {}).get("secondary_models", [])
    tiered_cfg = config.get("tiered_brain", {})
    if secondary_models and tiered_cfg:
        try:
            from castor.tiered_brain import TieredBrain

            # Primary provider = fast brain (runs every tick)
            # First secondary = planner (runs periodically / on escalation)
            planner_config = secondary_models[0]
            planner_brain = get_provider(planner_config)
            logger.info(
                f"Planner Brain Online: {planner_config.get('provider', '?')}"
                f"/{planner_config.get('model', '?')}"
            )
            tiered = TieredBrain(
                fast_provider=brain,  # Primary = fast (Gemini Flash)
                planner_provider=planner_brain,  # Secondary = planner (Claude)
                config=config,
            )
            logger.info(
                "Tiered Brain: reactive → fast(%s) → planner(%s)",
                config["agent"].get("model", "?"),
                planner_config.get("model", "?"),
            )
        except Exception as e:
            logger.warning(f"Tiered brain unavailable ({e}), using single brain")
            tiered = None

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

    # 7. SIGNAL HANDLING (graceful shutdown on SIGTERM/SIGINT)
    import signal

    _shutdown_requested = False

    def _graceful_shutdown(signum, frame):
        nonlocal _shutdown_requested
        sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else signum
        if _shutdown_requested:
            logger.warning(f"Received {sig_name} again — forcing exit.")
            raise SystemExit(1)
        _shutdown_requested = True
        logger.info(f"Received {sig_name}. Shutting down gracefully...")

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    # 7b. AGENT ROSTER (Phase 2-3)
    _agent_registry = None
    _agent_shared_state = None
    _agent_observer = None
    _agent_navigator = None
    roster_cfg = config.get("agent_roster", [])
    if roster_cfg:
        try:
            from castor.agents import AgentRegistry, SharedState
            from castor.agents.navigator import NavigatorAgent
            from castor.agents.observer import ObserverAgent

            _agent_shared_state = SharedState()
            _agent_registry = AgentRegistry()
            _agent_registry.register(ObserverAgent)
            _agent_registry.register(NavigatorAgent)

            for entry in roster_cfg:
                if not entry.get("enabled", True):
                    continue
                agent_name = entry.get("name", "")
                agent_config = entry.get("config", {})
                try:
                    if agent_name in ("observer", "navigator"):
                        agent = _agent_registry.spawn(
                            agent_name,
                            config=agent_config,
                            shared_state=_agent_shared_state,
                        )
                    else:
                        agent = _agent_registry.spawn(agent_name, config=agent_config)
                    logger.info(f"Agent '{agent_name}' registered from roster")
                    if agent_name == "observer":
                        _agent_observer = agent
                    elif agent_name == "navigator":
                        _agent_navigator = agent
                except Exception as e:
                    logger.warning(f"Could not spawn agent '{agent_name}': {e}")

            logger.info(f"Agent roster: {len(_agent_registry.list_agents())} agent(s) registered")
        except ImportError as e:
            logger.debug(f"Agent roster skipped: {e}")

    # 7c. SWARM CONFIG snapshot (injected into SisyphusLoop after learner section)
    swarm_cfg = config.get("swarm", {})

    # 8. THE CONTROL LOOP
    latency_budget = config.get("agent", {}).get("latency_budget_ms", 3000)
    logger.info("Entering Perception-Action Loop. Press Ctrl+C to stop.")

    # Latency tracking for sustained-overrun warnings
    _latency_overrun_count = 0
    _LATENCY_WARN_THRESHOLD = 5  # consecutive overruns before suggesting action

    # Episode recording for self-improving loop
    _episode_actions = []
    _episode_sensors = []
    _episode_start = time.time()
    _episode_store = None
    learner_cfg = config.get("learner", {})
    if learner_cfg.get("enabled", False):
        try:
            from castor.learner import EpisodeStore

            _episode_store = EpisodeStore()
            logger.info("Learner: episode recording enabled")
        except ImportError:
            logger.debug("Learner module not available")

    # Wire swarm config into SisyphusLoop's ApplyStage (if learner enabled)
    _sisyphus_loop = None
    if learner_cfg.get("enabled", False) and swarm_cfg:
        try:
            from castor.learner import SisyphusLoop

            _sisyphus_loop = SisyphusLoop(config=learner_cfg)
            robot_uuid = config.get("metadata", {}).get("robot_uuid", "unknown")
            _sisyphus_loop.apply_stage.set_swarm_config({**swarm_cfg, "robot_id": robot_uuid})
            logger.info("SisyphusLoop: swarm config injected into ApplyStage")
        except ImportError as e:
            logger.debug(f"SisyphusLoop init skipped: {e}")

    try:
        while not _shutdown_requested:
            loop_start = time.time()

            # Check emergency stop
            if fs.is_estopped:
                logger.warning("E-STOP active. Waiting...")
                time.sleep(1.0)
                continue

            # --- PHASE 1: OBSERVE ---
            frame_bytes = camera.capture_jpeg()
            fs.ns.write("/dev/camera", {"t": time.time(), "size": len(frame_bytes)})

            # Feed frame to ObserverAgent if running
            if _agent_observer is not None:
                try:
                    import asyncio

                    hailo_dets = []
                    if tiered is not None and hasattr(tiered, "reactive"):
                        for d in getattr(tiered.reactive, "last_detections", []):
                            hailo_dets.append(
                                {
                                    "label": getattr(d, "class_name", str(d)),
                                    "confidence": getattr(d, "confidence", 0.0),
                                    "bbox": list(getattr(d, "bbox", [0.0, 0.0, 0.0, 0.0])),
                                }
                            )
                    depth_map = getattr(camera, "last_depth", None)
                    sensor_pkg = {
                        "hailo_detections": hailo_dets,
                        "depth_map": depth_map,
                        "frame_shape": (480, 640),
                    }
                    asyncio.run(_agent_observer.observe(sensor_pkg))
                except Exception as e:
                    logger.debug(f"ObserverAgent observe error: {e}")

            # --- PHASE 2: ORIENT & DECIDE ---
            # Build instruction with memory context
            memory_ctx = fs.memory.build_context_summary()
            context_ctx = fs.context.build_prompt_context()
            instruction = "Scan the area and report what you see."
            if memory_ctx:
                instruction = f"{instruction}\n\n{memory_ctx}"
            if context_ctx:
                instruction = f"{instruction}\n\n{context_ctx}"

            if tiered:
                # Build sensor data from depth camera if available
                sensor_data = None
                if hasattr(camera, "last_depth") and camera.last_depth is not None:
                    import numpy as np

                    depth = camera.last_depth
                    # Get min distance in center region (front obstacle)
                    h, w = depth.shape
                    center = depth[h // 3 : 2 * h // 3, w // 4 : 3 * w // 4]
                    valid = center[center > 0]
                    if len(valid) > 0:
                        front_dist_mm = float(np.percentile(valid, 5))
                        sensor_data = {"front_distance_m": front_dist_mm / 1000.0}

                # Blend NavigatorAgent suggestion into sensor context
                if _agent_navigator is not None and _agent_shared_state is not None:
                    try:
                        import asyncio

                        nav_action = asyncio.run(_agent_navigator.act({}))
                        nav_dir = nav_action.get("direction", "forward")
                        nav_speed = nav_action.get("speed", 0.5)
                        logger.debug(f"NavigatorAgent suggests: {nav_dir} @ {nav_speed:.2f}")
                        if sensor_data is None:
                            sensor_data = {}
                        sensor_data["nav_direction"] = nav_dir
                        sensor_data["nav_speed"] = nav_speed
                    except Exception as e:
                        logger.debug(f"NavigatorAgent act error: {e}")

                thought = tiered.think(frame_bytes, instruction, sensor_data=sensor_data)
            else:
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

                # Record for self-improving loop
                if _episode_store is not None:
                    _episode_actions.append(
                        {
                            "type": thought.action.get("type", "unknown"),
                            "params": thought.action,
                            "timestamp": time.time(),
                            "result": "ok",
                        }
                    )
                    if sensor_data:
                        _episode_sensors.append(
                            {
                                **sensor_data,
                                "timestamp": time.time(),
                            }
                        )

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

            # Record runtime stats for dashboard status bar
            try:
                from castor.runtime_stats import record_tick

                _loop_tick = fs.proc._loop_count if hasattr(fs.proc, "_loop_count") else 0
                _last_act = thought.action.get("type", "—") if thought and thought.action else "—"
                record_tick(_loop_tick, _last_act)
            except Exception:
                pass

            # Sleep to prevent API rate limiting
            time.sleep(1.0)

    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
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
        logger.info("🛑 Shutdown sequence starting...")

        # Phase 0: Stop all agents
        if _agent_registry is not None:
            try:
                import asyncio

                asyncio.run(_agent_registry.stop_all())
                logger.info("  ✓ All agents stopped")
            except Exception as e:
                logger.debug(f"Agent shutdown error: {e}")

        # Phase 1: Stop motors immediately (safety first)
        if driver and not args.simulate:
            try:
                driver.stop()
                logger.info("  ✓ Motors stopped")
            except Exception as e:
                logger.warning(f"  ✗ Motor stop failed: {e}")

        # Phase 2: Clear shared references so in-flight requests
        # cannot grab a closing/closed device.
        set_shared_camera(None)
        set_shared_speaker(None)

        # Phase 3: Stop background services
        if watchdog:
            try:
                watchdog.stop()
                logger.info("  ✓ Watchdog stopped")
            except Exception:
                pass

        if battery_monitor:
            try:
                battery_monitor.stop()
                logger.info("  ✓ Battery monitor stopped")
            except Exception:
                pass

        if mdns_broadcaster:
            try:
                mdns_broadcaster.stop()
                logger.info("  ✓ mDNS stopped")
            except Exception:
                pass

        # Phase 4: Close hardware
        if driver and not args.simulate:
            try:
                driver.close()
                logger.info("  ✓ Hardware parked")
            except Exception as e:
                logger.warning(f"  ✗ Hardware close failed: {e}")

        try:
            speaker.close()
            logger.info("  ✓ Speaker closed")
        except Exception:
            pass

        try:
            camera.close()
            logger.info("  ✓ Camera closed")
        except Exception:
            pass

        # Phase 5: Flush memory and shut down filesystem
        try:
            fs.shutdown()
            logger.info("  ✓ Filesystem flushed")
        except Exception:
            pass
        set_shared_fs(None)

        if audit:
            try:
                audit.log_shutdown("graceful")
            except Exception:
                pass

        # Save episode for self-improving loop
        if _episode_store is not None and _episode_actions:
            try:
                from castor.learner import Episode

                ep = Episode(
                    goal=config.get("metadata", {}).get("robot_name", "session"),
                    actions=_episode_actions,
                    sensor_readings=_episode_sensors,
                    success=not _shutdown_requested,  # graceful = success
                    duration_s=time.time() - _episode_start,
                )
                _episode_store.save(ep)
                logger.info(
                    f"Episode saved: {ep.id[:8]} ({len(ep.actions)} actions, {ep.duration_s:.0f}s)"
                )
            except Exception as exc:
                logger.debug(f"Episode save failed: {exc}")

        logger.info("🤖 OpenCastor Offline. Goodbye.")


if __name__ == "__main__":
    main()
