"""The Microduck wire keys, transcribed from ``duck-ipc-proto``, with sources.

WHY THIS FILE EXISTS. The review this benchmark comes from
(``docs/reviews/microduck-ten-minutes-2026-09-08.md``) found four reads in
``castor/drivers/microduck_driver.py`` that name keys ``robotd`` does not send.
All four failed silently, for weeks, on the exact path a hardware guide prints
filled-in example output for. Nothing caught them because nothing in this
project ever ran OpenCastor against a ``robotd``.

So the benchmark does not ask the driver what the duck said. It reads the raw
JSON-RPC replies and holds them to the keys below, and **a missing key is a
failed checkpoint, never a "?"**. Every constant here carries the file and line
in ``pollen-robotics/microduck`` it was transcribed from, at revision
``5620aa2``, so a reader can check it against upstream rather than against us.

Nothing here is invented. If a key is not in this file, the benchmark does not
read it.
"""

from __future__ import annotations

#: Where every constant below came from. Repository ``pollen-robotics/microduck``
#: at rev ``5620aa2``; paths are relative to that repository root.
PROTO_REPO = "pollen-robotics/microduck"
PROTO_REV = "5620aa2"
PROTO_FILE = "duck-ipc-proto/src/lib.rs"

# ── constants ────────────────────────────────────────────────────────────────

#: ``pub const API_VERSION: u32 = 25`` — duck-ipc-proto/src/lib.rs:304
API_VERSION = 25
#: ``pub const POLICY_OBS_LEN: usize = 61`` — duck-ipc-proto/src/lib.rs:313
POLICY_OBS_LEN = 61
#: ``pub const POLICY_ACTION_LEN: usize = 14`` — duck-ipc-proto/src/lib.rs:317
POLICY_ACTION_LEN = 14
#: ``pub const ROBOT_MODEL: &str = "microduck"`` — duck-ipc-proto/src/lib.rs:321
ROBOT_MODEL = "microduck"

#: robotd's own deadman, in milliseconds — robotd-params/src/lib.rs:1608,
#: duck-control/src/safety.rs:74. Informational: a run in which only this one
#: fires is a run whose client stopped feeding the robot and did not notice.
ROBOTD_DEADMAN_MS = 500
#: duck-studio's bridge deadman — duck-studio/bridge/microduck-bridge.py:52.
BRIDGE_DEADMAN_MS = 700

# ── methods ──────────────────────────────────────────────────────────────────

#: ``pub const HELLO: &str = "hello"`` — duck-ipc-proto/src/lib.rs:417
M_HELLO = "hello"
#: duck-ipc-proto/src/lib.rs:442
M_HEALTH = "robot.health"
#: duck-ipc-proto/src/lib.rs:640
M_SUBSCRIBE = "robot.subscribe"
#: duck-ipc-proto/src/lib.rs:582
M_POLICIES = "robot.policies"
#: duck-ipc-proto/src/lib.rs:649 — a notification robotd pushes, not a request.
M_STATE = "robot.state"
#: duck-ipc-proto/src/lib.rs:465
M_MOVE = "robot.move"
#: duck-ipc-proto/src/lib.rs:472
M_STOP = "robot.stop"

# ── HelloResult — duck-ipc-proto/src/lib.rs:2800-2807 ────────────────────────

HELLO_API_VERSION = "api_version"  # :2801, u32, always present
HELLO_DAEMON_VERSION = "daemon_version"  # :2802, Option<semver::Version>
HELLO_REVISION = "revision"  # :2806, Option<String>, always serialised

#: The one hello key whose absence is a protocol failure rather than an old
#: daemon: it is not an ``Option`` and carries no ``skip_serializing_if``.
HELLO_REQUIRED = (HELLO_API_VERSION,)

# ── HealthResult — duck-ipc-proto/src/lib.rs:3089-3147 ───────────────────────

HEALTH_HEALTHY = "healthy"  # :3090, bool, always present
HEALTH_DEGRADED = "degraded"  # :3103, bool, skipped when false
HEALTH_REASON = "reason"  # :3105, Option<String>
HEALTH_BATTERY = "battery"  # :3118, Option<Battery>
HEALTH_MOTORS = "motors"  # :3122, Option<MotorThermal>
HEALTH_CPU_TEMP_C = "cpu_temp_c"  # :3132, Option<f64>
HEALTH_CONTROL_LOOP = "control_loop"  # :3140, Option<LoopHealth>, NO serde rename
HEALTH_BUS = "bus"  # :3144, BusHealth, present on every answer
HEALTH_IMU = "imu"  # :3147, Option<ImuHealth>

#: What C3 will not proceed without. ``control_loop`` and ``battery`` are
#: ``Option`` on the wire and are still required here, because a benchmark that
#: cannot read the loop rate or the battery cannot describe the robot — which is
#: precisely the failure this benchmark exists to catch.
HEALTH_REQUIRED = (HEALTH_HEALTHY, HEALTH_CONTROL_LOOP, HEALTH_BATTERY)

# ── LoopHealth — duck-ipc-proto/src/lib.rs:3152-3167 ─────────────────────────

LOOP_TARGET_HZ = "target_hz"  # :3155, f64
LOOP_ACHIEVED_HZ = "achieved_hz"  # :3159, Option<f64> — None until the first window closes
LOOP_TICKS = "ticks"  # :3160
LOOP_MISSED = "missed"  # :3164, u64
LOOP_LAST_TICK_AGE_MS = "last_tick_age_ms"  # :3166, u64

#: ``achieved_hz`` is deliberately absent from this tuple: upstream documents it
#: as ``None`` until the first window closes, and *unknown* is not *missing*.
LOOP_REQUIRED = (LOOP_TARGET_HZ, LOOP_TICKS, LOOP_MISSED, LOOP_LAST_TICK_AGE_MS)

# ── Battery — duck-ipc-proto/src/lib.rs:3263-3265 ────────────────────────────

BATTERY_VOLTS = "volts"  # :3264, f64
BATTERY_PERCENT = "percent"  # :3265, f64
BATTERY_REQUIRED = (BATTERY_VOLTS, BATTERY_PERCENT)

#: The choreographer's floor — castor/microduck_choreography.py:373.
MIN_BATTERY_PERCENT = 12.0

# ── BusHealth — duck-ipc-proto/src/lib.rs:3174-3184 ──────────────────────────

BUS_CONSECUTIVE_ERRORS = "consecutive_errors"  # :3179
BUS_STARTUP_FAILURES = "startup_failures"  # :3183

# ── SubscribeResult — duck-ipc-proto/src/lib.rs:2519-2546 ────────────────────
#
# THERE IS NO ``networks`` KEY. ``castor/drivers/microduck_driver.py:233`` reads
# one; this is the list it should have read.

SUB_ACCEPTED = "accepted"  # :2520, bool
SUB_WALK = "walk"  # :2525, Option<String>, a file name
SUB_STAND = "stand"  # :2529, Option<String>
SUB_UNAVAILABLE = "unavailable"  # :2535, Option<String> — why nothing is driving
SUB_SITSTAND = "sitstand"  # :2539, Option<String>
SUB_GROUND_PICK = "ground_pick"  # :2541, Option<String>
SUB_SKILLS = "skills"  # :2546, Vec<String>

SUBSCRIBE_REQUIRED = (SUB_ACCEPTED,)
#: Every policy-slot key a ``SubscribeResult`` may carry, in wire order.
SUBSCRIBE_SLOTS = (SUB_WALK, SUB_STAND, SUB_SITSTAND, SUB_GROUND_PICK)

# ── PoliciesResult / PolicySlot — duck-ipc-proto/src/lib.rs:2218-2269 ────────

POL_MODE = "mode"  # :2220, String
POL_ENABLED = "enabled"  # :2224, bool
POL_SLOTS = "slots"  # :2226, Vec<PolicySlot>
POL_SKILLS = "skills"  # :2235, Vec<String>
POL_CHANGE_ERROR = "change_error"  # :2247, Option<String>

SLOT_SLOT = "slot"  # :2255, String
SLOT_PATH = "path"  # :2258, Option<String>
SLOT_ORIGIN = "origin"  # :2263, Option<String>
SLOT_OVERRIDDEN = "overridden"  # :2265, bool
SLOT_ERROR = "error"  # :2268, Option<String>

POLICIES_REQUIRED = (POL_SLOTS,)

#: The slot name a walking duck must have filled. ``robot.init`` works without
#: one — "'stand up' is a reasonable thing to ask of a robot with no walking
#: network", duck-ipc-proto/src/lib.rs:487-491 — but this benchmark ends at a
#: move, so a walk slot is mandatory.
WALK_SLOT = "walk"

# ── RobotState — duck-ipc-proto/src/lib.rs:3317-3365 ─────────────────────────
#
# ``RobotState`` carries NO battery. ``microduck_driver.py:780`` reads one, and
# ``microduck_choreography.py:628-631`` gates the documented battery abort on
# it, which is why that abort has never fired.

STATE_T = "t"  # :3320
STATE_MOVE = "move"  # :3321-3322, serde rename of `movement`
STATE_POLICY = "policy"  # :3325
STATE_SAFETY = "safety"  # :3326
STATE_LOOP = "loop"  # :3327-3328, serde rename of `control_loop` — LoopState, not LoopHealth
STATE_ODOM = "odom"  # :3337

#: LoopState — duck-ipc-proto/src/lib.rs:3512-3517. ``hz`` and ``missed`` live
#: HERE, on the state stream, and nowhere on ``robot.health``. Conflating the
#: two is the review's stated root cause of traps 1, 3 and 4.
STATE_LOOP_HZ = "hz"  # :3514
STATE_LOOP_MISSED = "missed"  # :3516

#: OdomState — duck-ipc-proto/src/lib.rs:3472-3477.
ODOM_POSITION = "position"  # :3474, [f64; 3] — THREE, not two
ODOM_YAW = "yaw"  # :3476, f64

#: SafetyState — duck-ipc-proto/src/lib.rs:3491-3509.
SAFETY_FALLEN = "fallen"  # :3492
SAFETY_LIMP = "limp"  # :3494

# ── MoveParams — duck-ipc-proto/src/lib.rs:1933-1940 ─────────────────────────

MOVE_VX = "vx"  # :1935
MOVE_VY = "vy"  # :1937
MOVE_VYAW = "vyaw"  # :1939
MOVE_KEYS = (MOVE_VX, MOVE_VY, MOVE_VYAW)


#: Line references for every group above, so a record can say where it read a
#: key from without a reader opening this file.
SOURCES: dict[str, str] = {
    "API_VERSION": f"{PROTO_FILE}:304",
    "POLICY_OBS_LEN": f"{PROTO_FILE}:313",
    "POLICY_ACTION_LEN": f"{PROTO_FILE}:317",
    "HelloResult": f"{PROTO_FILE}:2800-2807",
    "HealthResult": f"{PROTO_FILE}:3089-3147",
    "LoopHealth": f"{PROTO_FILE}:3152-3167",
    "Battery": f"{PROTO_FILE}:3263-3265",
    "BusHealth": f"{PROTO_FILE}:3174-3184",
    "SubscribeResult": f"{PROTO_FILE}:2519-2546",
    "PoliciesResult": f"{PROTO_FILE}:2218-2248",
    "PolicySlot": f"{PROTO_FILE}:2253-2269",
    "RobotState": f"{PROTO_FILE}:3317-3365",
    "LoopState": f"{PROTO_FILE}:3512-3517",
    "OdomState": f"{PROTO_FILE}:3472-3477",
    "SafetyState": f"{PROTO_FILE}:3491-3509",
    "MoveParams": f"{PROTO_FILE}:1933-1940",
    "robotd deadman": "robotd-params/src/lib.rs:1608",
}


def missing_keys(payload: object, required: "tuple[str, ...]") -> list[str]:
    """Which of ``required`` are absent from ``payload``.

    A non-dict payload is missing all of them: a reply that is not an object is
    not a reply this benchmark can read a key out of.

    Args:
        payload: The raw decoded JSON-RPC ``result``.
        required: Keys that must be present.

    Returns:
        The absent keys, in the order given.
    """
    if not isinstance(payload, dict):
        return list(required)
    return [key for key in required if key not in payload]
