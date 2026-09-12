# Changelog

All notable changes to OpenCastor are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions switched from date-based (`YYYY.MM.DD.patch`) to SemVer at
3.0.0 to signal RCAN 3.x peer-runtime alignment.

---

## [Unreleased]

### Security

**The fleet proxy no longer lends this robot's credential to a peer it found
over mDNS.** `POST /api/fleet/{ruri}/command` used to fall back to this robot's
own `OPENCASTOR_API_TOKEN` when the caller supplied no peer token, and
`GET /api/fleet/{ruri}/status` attached that token unconditionally. That token
mapped to role `admin` on this robot (it maps to `operator` as of the role split
below), and the destination address came from an unauthenticated mDNS answer, so
the relay could hand admin here to whoever answered a discovery query. Both
endpoints now require a caller-supplied peer
credential (`token` in the command body, `?peer_token=` or the `X-Peer-Token`
header for status) and refuse with 401 `no_peer_credential` before any HTTP
client is constructed. This robot's own token is never attached to an outbound
relay.

**A discovery answer is a hint, not an authorisation.** `_find_fleet_peer` now
intersects what mDNS reported with the RURIs declared under the config key
`fleet.peers`; an undeclared RURI gets 404 `peer_not_declared` and is never
contacted. `castor up` generates `fleet:\n  peers: []` into `robot.rcan.yaml`
for both archetypes, so the key exists on every new robot and nobody hand-edits
a file to get the safe default. Every relay attempt, allowed or refused, writes
one `fleet_relay` audit line naming the initiator, the target RURI, the
resolved address and a SHA-256 of the instruction.

Also removed: the hardcoded peer map in `castor/agent_tools.py`, which made any
robot with no `fleet.peers` key believe in two of the author's own hosts.
`_get_peer_urls()` returns `{}` when the key is absent.

Callers that relied on the implicit token now get a 401 and must pass the peer's
own credential.

**The runtime's static bearer no longer carries the admin role.**
`OPENCASTOR_API_TOKEN` is the credential the agent, the app and the robot's own
services carry, and `castor up` writes it into `tokens.env` under the runtime
uid — so anything running as the robot can read it. It used to map to
`jwt_role = "admin"`, which meant the one production human-in-the-loop gate,
`POST /api/hitl/authorize`, accepted the credential held by the very agent it
gates. It now maps to `operator`. A second credential, the owner's, carries
`admin`: `castor up` mints it fresh on every run, prints it once beside the
pairing QR, and stores only its SHA-256 in `$ROBOT_HOME/admin-token.sha256`
(0600) — never in `tokens.env`, because a token in `tokens.env` is a token the
agent holds. Reading the digest does not let you present it. Lost the token?
Re-run `castor up`.

A decodable RCAN JWT no longer implies `admin` either: the API role comes from
the token's own role claim (CREATOR/OWNER → `admin`, LEASEE/USER → `operator`,
GUEST → `viewer`), and an absent claim decodes to GUEST, which is `viewer`.

**An unconfigured runtime refuses privileged routes instead of serving them.**
`verify_token`'s layer 4 was a comment followed by the end of the function: with
`OPENCASTOR_USERS`, the JWT secret and `OPENCASTOR_API_TOKEN` all unset it
returned having set no role, and `_check_min_role` returned early on a role of
`None`. `/api/system/reboot`, `/api/system/shutdown`, `/api/system/upgrade` and
`/api/harness/apply-champion` were open to anyone who could reach the port. Such
a runtime now refuses every route above `viewer` with 401 `no_auth_configured`;
`/health` and `/api/status` keep answering so a half-set-up robot can still be
diagnosed.

**A remote champion document can no longer move the number that decides when
the robot asks a human.** `POST /api/harness/apply-champion` merged keys from a
Firestore field or an ops-checkout file against a `TUNABLE_KEYS` set that
included `p66_consent_threshold`, recorded no actor, and silently dropped
everything else. `p66_consent_threshold` is out of `TUNABLE_KEYS`, every key in
the document is now screened against `castor/optimizer.py`'s shipped
`_FORBIDDEN_KEYS` (anchored on whole keys and `_ - .`-separated components, not
substrings), and one fenced key rejects the WHOLE document with 400
`forbidden_key` — nothing is written. `cost_gate_usd` stays tunable. The
endpoint and `POST /api/harness/auto-apply` both moved from `operator` to
`admin`, and every attempt writes a `champion_apply` audit line carrying the
champion source, the before/after key diff and the authenticated actor.
`castor doctor` prints the last one with its actor.

**Roles that changed.** `POST /api/estop/clear` gained an `admin` gate (setting
a stop stays open to any authenticated caller; clearing one the human set does
not), and records who cleared it. `POST /api/hitl/authorize` records who
authorized. `ApprovalGate.approve()/deny()` take a `principal` and write it to
the entry and the audit log.

Callers holding only the runtime bearer now get 403 `insufficient_role` on
`/api/hitl/authorize`, `/api/estop/clear`, `/api/system/*`, `/api/keys/*`,
`/auth/rotate-key`, `/api/config/reload`, `/api/config/rollback`,
`/api/harness*` and the other routes already gated at `admin`. Use the owner
token `castor up` printed.

## [3.4.0] - 2026-09-10

Published on PyPI as `1!3.4.0`.

**Ask the robot to paint a picture, from the phone.** The console gains a paint
job (`castor/console/paint.py`): `POST /eval/paint` starts one
`castor bench sacpaint run` on the robot with the operator's `paint.json`
profile, `GET /eval/paint` reports strokes, misses, model calls and finally the
score, `POST /eval/paint/stop` ends it, and `POST /eval/picture` takes a photo of
the person's own, traced into a scoring skeleton on receipt
(`castor bench sacpaint new --auto-trace`). The benchmark's OpenCastor body
learned to report as it goes (`-E progress_path`, `-E canvas_post_url`), so the
phone watches the canvas grow. The iOS app (1.3, build 78, on TestFlight) shows a Paint a picture
card on every paired robot's page, Sacramento by default. Also: `castor bench
sacpaint worldevals-entry` prints the catalogue form WorldEvals accepts; a stop
from the phone signals the job's whole process tree. Docs in
`docs/benchmarks/sacpaint.md`; `docs/eval-eyes.md` lists the new routes.

## [3.3.0] - 2026-09-10

Published on PyPI as `1!3.3.0` (the epoch is required; see `docs/pypi-versioning.md`).

**Sacramento PaintBench is a `castor bench` benchmark now.** `castor bench sacpaint`
brings the standalone `sacpaint` package (PyPI 0.3.1) into the runtime as
`castor.bench.sacpaint`: a robot with a pen must reproduce a photograph of
Sacramento from camera feedback, and any photo of the finished sheet scores
offline. Install the eval stack with `pip install "opencastor[paintbench]"`
(`paintbench-agent` adds the frontier-model policy); `pip install opencastor`
alone still pulls no eval framework.

### Added — castor bench sacpaint

- `castor bench sacpaint score PHOTO` rectifies a photo of the sheet (tapped
  corners, ArUco, or the sheet itself) and prints the five-scorer breakdown.
- Tasks `sacpaint/photo-v1` (the reference is the original photograph; the
  scorers read a stroke skeleton traced over its landmarks, both hashes in every
  EvalLog) and `sacpaint/line-v0`, embodiments `sacpaint_plotter` (mock) and
  `opencastor` (an SO-ARM101 behind robot-md-gateway, signed receipts, the
  iPhone or the console as eyes), policies `sacpaint_trace` / `sacpaint_idle`,
  and the scorers, all registered as `inspect_robots.*` entry points.
- Media: every score carries `medium` (`pen`, `virtual`, `sim`), each its own
  leaderboard category. `-E medium=virtual -E calibration=easel` runs a rig
  with no paper and no pen: the arm moves for real and the canvas is inked
  from its measured tip. Measured on Bob 2026-09-09: 649 moves, 62 min,
  composite 0.82.
- `castor bench sacpaint calibrate` teaches the canvas-to-arm transform;
  `castor bench sacpaint shim` runs the Claude-subscription shim;
  `castor bench sacpaint export` writes the robocurve run-dataset layout;
  `castor bench sacpaint worldevals-entry` prints the catalogue block.
- The runtime's first Python gateway client (`castor.bench.sacpaint.gateway`):
  RCAN envelopes to `/v1/invoke`, receipts kept and written as JSONL, denies
  raised as `SafetyAbort` with the driver's sub-code, a reach that ran but
  missed raised as `GatewayMiss` so the virtual medium can carry on.
- Docs: `docs/benchmarks/sacpaint.md`.

### Changed

- `GET /eval/reference.png` on the console now serves the benchmark's own
  photograph (transcoded to PNG) instead of looking for a separately installed
  `sacpaint`; `EVAL_REFERENCE_PATH` still overrides it. The `/eval/*` routes
  themselves (frames, corners, feedback, status; `docs/eval-eyes.md`) landed in
  3.2.0 without a changelog line; this is it.

## [3.2.0] - 2026-09-08

Published on PyPI as `1!3.2.0`. **The Microduck becomes the reference robot for
the ten-minute goal, and the goal becomes a command.** The review at
`docs/reviews/microduck-ten-minutes-2026-09-08.md` found that `castor duck` had
never been run against a real `robotd` (four wire keys wrong, the battery abort
could never fire, the printed next command exited 1). Everything below was
proven against Pollen's real `robotd` 0.11.0 running headless under their
`duck-sim` on this Pi, and against a mock robotd that answers in the wire's own
shape. Nothing has touched a physical duck yet.

- **`castor bench ten-minutes --robot microduck`**: checkpoints C1 to C7
  (package, reachable, identity off the wire, first brain turn, first
  `robot.move` re-sent, `robot.stop` after silence naming which deadman fired,
  optional measured step), a JSON record with versions, shas, duck identity,
  every wire line and every operator command, an EvalLog v1 export that
  `inspect-robots` reads back byte for byte, and three targets: mock (CI),
  `--sim` (the real robotd on MuJoCo), and a real duck. Today: mock C1-C6 in
  1.6 s, sim C1-C6 in 3.5 s, verdict `ci-pass` because the brain was scripted.
- **Five wire faults fixed**, not four: `robot.subscribe` had never once
  succeeded (it sent `null` where robotd wants a struct), found by two agents
  independently against the live daemon. Fixtures transcribed from
  `duck-ipc-proto` with line numbers, then corrected by the daemon itself.
- **`castor up --archetype microduck`**: six units including the bridge as
  `{name}-duckbridge.service`, one token file the app and OpenCastor share,
  port 7788 agreed in four places.
- **`transport: webrtc`**: drive a duck over `mediad`'s own network transport,
  no SSH key, no `robot` group, no reboot (`opencastor[microduck-webrtc]`).
- **`castor doctor` knows ducks** and calls a mock driver a failure; the duck's
  LLM tools finally register (`agent.harness.enabled` in the shipped profile).
- **`castor duck`** writes a manifest `castor run` accepts, finds a stock duck
  (`radxa-zero3.local`, `duck-*`), uses the brain you configured, never prints
  "Duck ready." for a duck that did not answer, and says its envelope
  ("walking at 0.06 m/s of a 0.20 m/s envelope; the gamepad's own limit is
  0.30") with `--speed`.
- Microduck Studio build 66 drives a real duck over the bridge; the wire shape
  is pinned in both projects.
- RC car: `castor doctor` reads the PCA9685 back and reports a chip that
  forgets its configuration (found on the first hardware contact); the bring-up
  checklist gained "prove the chip holds a configuration" as step 3b.

### Added — a `webrtc` transport for the Microduck

Fix 10 of `docs/reviews/microduck-ten-minutes-2026-09-08.md`. A duck is now
reachable over the network Pollen already ships, with **no SSH key, no `robot`
group edit and no reboot** — `mediad` is enabled on every install and every
update, and its `control` datachannel carries the same JSON-RPC as the socket.

- **`castor/drivers/microduck_webrtc.py`** — `mediad`'s signalling exchange
  (`gst-plugins-rs` `net/webrtc/protocol`: `welcome` → `list` → `startSession`
  → `sessionStarted` → `peer`/sdp → `peer`/answer) and the `control`
  datachannel. Every message shape is transcribed from
  `mediad/webclient/index.html`, `mediad/src/*.rs` and `duck-ipc-proto` with a
  file and a line, because a guessed shape on this path produces silence rather
  than an error.
- **`transport: webrtc`** in `MicroduckDriver`. The channel is handed to the
  driver shaped like a socket, so the NDJSON reader, id correlation, intent
  loop and deadman are used unchanged.
- **Optional extra `opencastor[microduck-webrtc]`** (aiortc + websockets).
  Asking for the transport without it stops with the install line instead of
  degrading to mock mode.
- **Deadman, stated:** `robotd`'s own 500 ms
  (`robotd-params/src/lib.rs`, `SafetyParams`) is what fires when a WebRTC
  session drops, since the driver's zero cannot reach the robot. Nothing new
  was added.
- **Security, stated rather than implied:** `mediad` does not authenticate and
  binds `0.0.0.0`. `docs/hardware/microduck.md` now carries Pollen's own
  sentence verbatim, a four-row transport table, what turning `mediad` off
  costs, and the calls `route.rs` refuses over this transport.

### Added — `castor doctor` knows what a duck is

From `docs/reviews/microduck-ten-minutes-2026-09-08.md` (fixes 5, 9, 13, 15).

- **A duck section in `castor doctor`.** It was silent about ducks: zero
  matches for `duck` or `robotd` in `castor/doctor.py`, so a Microduck
  OpenCastor could not reach got a clean bill of health. `run_duck_checks()`
  now reads every number OFF THE WIRE, in the order an owner debugs in — the
  duck config, robotd answering (`unix`, `ssh` or the bridge over `tcp`), the
  login's `robot` group, mediad's two ports, `robot.health`'s `control_loop`,
  `robot.policies` slot by slot, and the battery — each with a one-line fix,
  and **exits non-zero when the duck cannot walk**. Silent on a host with no
  duck config, so a car owner never sees a word about ducks.
- **A mock is a failure, never a pass.** `MicroduckDriver` degrades to mock on
  any connect failure and then answers `ok: True` to `health_check()` and
  success to every command. Only `castor duck health` guarded against it;
  doctor's **Duck drive mode** row now does too, and calls it blocking. This is
  the RC car's simulated-wheels trap wearing a beak.
- The wire keys are the ones `duck-ipc-proto` defines: `control_loop` (not
  `loop`), `achieved_hz` (not `hz`), and `battery` on `robot.health` — there is
  no battery on `robot.state` at all. `achieved_hz: null` is reported as "not
  reported yet", never as 0 Hz.
- **`castor gaps` learned one duck gap**, `duck.tools.gated`: a configured
  Microduck whose `agent.harness.enabled` is off.

### Fixed — the duck's LLM tools were behind a flag no profile set

- `duck_vocabulary` and `duck_perform` are registered at exactly one call site,
  inside the `agent.harness.enabled` block in `castor/api.py`, and neither
  `castor/profiles/pollen/microduck.yaml` nor
  `config/presets/pollen_microduck.rcan.yaml` set it. The guide's claim that
  they register "automatically whenever a Microduck is the attached robot" was
  true of the function and false of the product. **Both now set
  `agent.harness.enabled: true`**, and `castor.api.build_tool_registry()` makes
  the question testable: a Microduck config yields a registry containing
  `duck_perform`, and `duck_perform` is invoked in a test rather than merely
  counted.
- `docs/hardware/microduck.md` now says the harness is what carries the tools,
  and shows the YAML.

### Changed — the duck is open on your network, and the guide says so

- A new section in `docs/hardware/microduck.md`: **`mediad` binds
  `0.0.0.0:8443` and `0.0.0.0:8080` and neither authenticates**, enabled on
  every install and every update, because the BLE pairing PIN is a shared
  `000000`. It records what turning it off costs (camera, browser console,
  WebRTC datachannel, `duckctl open`; nothing OpenCastor uses) and that
  `hooks/postinstall` re-enables it on every update.
- The guide's install line carries the `>=3.1` pin the README calls
  load-bearing.

### Added — the ten-minute goal is a command now

`castor bench ten-minutes --robot microduck` runs the goal and writes a JSON
record, as specified in the second half of
`docs/reviews/microduck-ten-minutes-2026-09-08.md`. Seven checkpoints, six
mandatory, in order, with `C6.t - T0 < 600 s`. See
`docs/benchmarks/ten-minutes.md`.

- **C3 reads identity off the wire and fails on an absent key** rather than
  printing `?`. Every key it reads is a constant in `castor/bench/wire.py`,
  transcribed from `duck-ipc-proto` at rev `5620aa2` with its source line. This
  is what would have caught the four wrong wire keys the review found.
- **Mock targets can never pass.** A run against a mock robotd, or with a
  scripted brain, reports `ci-pass`, and `verdict_reason` says why. A skipped
  checkpoint is a fail, not an omission. Wi-Fi onboarding is excluded and
  always reported with a reason.
- **C6 names which of the three deadmen fired** — the driver's 1.5 s TTL,
  duck-studio's bridge at 700 ms, or robotd's own 500 ms. A run in which only
  robotd's fires is a failure, not a pass.
- **Three targets**: `--ci` (a mock robotd with real-shaped replies, for CI on
  every push), `--sim` (Pollen's `scripts/duck-sim`, the real `robotd` on a
  MuJoCo body), and a real duck over `unix`, `ssh` or `tcp`.
- **`--evallog` exports the run as inspect-robots EvalLog v1**, verified against
  the real `inspect-robots==0.58.0`: read, re-dumped byte for byte and rendered.
  No success scorer is written without a motion-evidence scorer beside it.
- `--robot rc-car` is a stub that prints the checkpoint list it would use and
  refuses, because C3 and C7 have no honest source on that robot yet.

### Found by running it — `robot.subscribe` is refused by a real robotd

The first `--sim` run, against `robotd 0.11.0` on a MuJoCo body, found a fifth
wire bug beside the review's four. `MicroduckDriver` subscribes at connect with
**no params** (`microduck_driver.py:233`), and `SubscribeParams` is a struct
(`duck-ipc-proto/src/lib.rs:2500-2505`) that serde will not build from `null`,
so the daemon answers `-32602 invalid type: null, expected struct
SubscribeParams` and the driver logs it rather than raising. Sending `{}` is
the fix. Not fixed here: `castor/drivers/microduck_driver.py` is owned by
another branch. The benchmark records it as a note, and names it as the reason
C7 cannot be answered — a client that never subscribed never sees `robot.state`,
so no odometry ever arrives and the only checkpoint that proves motion has no
source.

## [3.1.0] - 2026-09-08

Published on PyPI as `1!3.1.0`: the epoch is what makes pip prefer this line
over the ninety-seven 2026.x CalVer releases that sort above `3.x`. Install with
`pip install "opencastor>=3.1"`.

### Changed — box to driving in under ten minutes

The review at `docs/reviews/rc-car-ten-minutes-2026-09-08.md` timed the
newcomer's path at three to eight hours. These close the traps it found.

- **`castor up` asks the one question that matters.** When a PCA9685 answers at
  0x40 it asks, with the wheels-off-the-ground warning, whether to drive real
  wheels (`--real-wheels` / `--simulated-wheels` for scripts; no terminal means
  simulated). The template's channels are now throttle 1 / steering 0, matching
  every reference vehicle. A rerun never re-asks or reverses.
- **`rc-car-actuator` is a core dependency** (0.1.1 on PyPI), so the actuator
  the gateway needs is always installed.
- **The manifest cannot draft a stop.** `duration_s` is required with no
  default; an absent value is the deadman's 400 ms lease, zero is still a stop.
- **Discovery ships.** `castor up` writes a fifth unit that advertises
  `_opencastor._tcp` with rrn, ports, name and manifest path; `castor discovery
  check` proves it from the wire. `zeroconf` is a core dependency.
- **`castor doctor` reads the robot it provisioned**: real ports from the unit
  files, `/dev/i2c-1`, "your wheels are simulated" when a chip answers, mDNS,
  USB current versus a streaming camera, every `castor gaps` gap, each with a
  pasteable fix, and a non-zero exit when the car cannot move.
- **The PCA9685 driver no longer falls back to mock** unless asked
  (`OPENCASTOR_PCA9685_ALLOW_MOCK=1` or `allow_mock: true`).
- **`castor hub install` fails loudly** and the seven community recipes now ship
  the `config.rcan.yaml` they name (a `.gitignore` rule had swallowed them).
- **The Pi image enables I2C on first boot** (`raspi-config nonint do_i2c 0`,
  one-shot reboot only if the node does not appear) and ships as one file
  under GitHub's 2 GiB asset cap; the stopwatch now starts at the download.
- **One install line, one port table.** `pip install "opencastor>=3.1"`;
  gateway 8080, runtime 8081, console 8082 (base + 0/1/2). `castor init` asks
  the robot's shape first and routes an RC car to `castor up`.
- `docs/hardware/pca9685-bringup.md`: the ordered bring-up checklist the
  policy template has pointed at since August.

### Added — the Microduck

OpenCastor drives Pollen Robotics' Microduck: a 25 cm walking biped whose
motion comes from RL policies executed by its own `robotd` daemon at 50 Hz.
We are an intent client over robotd's JSON-RPC 2.0 NDJSON socket; robotd
keeps exclusive ownership of the motor bus.

- **`castor duck`** — one command from a duck on the desk to a duck under an
  LLM: find the robot, prove we can reach it, ask robotd how it feels, write
  the config. Discovery tries five things (local socket, hostnames, `duckctl
  ip` over Bluetooth, mDNS, and the ARP table with `--deep`) because Pollen's
  own docs admit mDNS "resolves when it feels like it". Hostname probes run on
  a hard deadline in daemon threads, so a stalled `.local` lookup cannot hang
  the CLI. The two things that can block you — SSH access and `robot` group
  membership — each get their exact fix printed, and the first is offered as a
  keystroke. A host is only a duck once robotd's socket answers on the far end.
- **The whole vocabulary.** The driver could walk the duck and look around; it
  could not kick, pick anything up, sit, roll, quack, open its beak or lean.
  Reading `duck-ipc-proto` turned up twenty-two `robot.*` methods against the
  eleven the driver knew. `robot.do` now carries the five scripted skills
  (`ground_pick`, both kicks, `sit_toggle`, `roulade`) as named methods, with
  an unknown skill refused on this side rather than sent to be rejected on
  that one. `robot.sound` carries the seven-sound voice bank, including the
  held `wheee` whose two endings differ — a deliberate release cuts it, a hold
  that stops arriving plays out. `robot.mouth` and `robot.pose` join the twist
  as continuous intents.
- **Choreography.** Pollen ships a vocabulary of atoms: `robot.do` runs exactly
  one skill, and a refusal names the move already holding the robot. There is
  no sequencing and no way to say *"walk to the ball, line up, knock it toward
  the couch, then celebrate"* — every verb exists, the sentence does not. So
  thirteen primitives now carry the two facts a planner cannot work without
  (how long a move occupies the duck, and whether it holds the robot
  exclusively), and thirteen routines compose them: `fetch`, `greet`,
  `patrol`, `nudge`, `celebrate`, `settle` and the rest. `fetch` is one word
  that becomes ten primitives. Every routine is written as a plan a user could
  have typed, so nothing is hidden in code you could not have asked for
  yourself. `castor duck do "greet me, then patrol the room"`.
- **Profile and preset** — `pollen/microduck` ships with the package (preset
  #16, `config/presets/pollen_microduck.rcan.yaml`), kept in sync by
  `tests/test_microduck_setup.py`. Envelope: 0.2 m/s forward, 0.1 m/s strafe,
  1.0 rad/s yaw, 20 Hz intents, 1.5 s TTL, `auto_init: false` so the duck does
  not move when a process starts.
- **Docs** — `docs/hardware/microduck.md`, and a hardware guide on the website
  at `/docs/hardware/microduck`.

The choreographer computes no permission. Every motion still goes out through
`MicroduckDriver` and the SafetyLayer, the duck's own limits apply on top and
come back in `robot.state.limited_by`, and steps abort on a fall or a flat
battery. A plan is a proposal; robotd remains the only thing that decides what
a servo does.

### Added — `castor up`

The ten-minute contract, enforced by a command: a robot up and running in
under ten minutes, on common hardware, by people who do not administer Linux
for a living. The counter-example that motivated it is our own bench, where
the rover took an experienced operator three sessions of hand work — hand-
written systemd units, hand-edited env files, a hand-assembled pairing payload
that shipped pinned to a dead DHCP address. Every piece existed; the
composition did not.

`castor up` is the composition: detect hardware → pick archetype → generate
the robot home (signed ROBOT.md, rcan.yaml, bearers, policy, keys, runtime) →
start the systemd units → detect a brain (smallest local Ollama model, else
the Claude subscription) → write the pairing QR. Non-interactive throughout:
every question it could ask is one it can answer by looking at the machine.
Idempotent throughout.

- Degrades honestly on a host where pip brought less than this bench has,
  rather than claiming a capability it cannot back.
- **Universal-link pairing** — the QR is now a link any camera understands,
  with the server half to match, so pairing no longer needs the app open
  first. `/pair` offers the board's own image.
- **Capability edits** are one operator command, and hand edits stay
  first-class. Gaps became data, and closing one is always the operator's act.
- The QR states what *this* robot can do, so a car stops arriving as an arm.
- A robot remembers across reboots, and an SD card can become a robot.

### Fixed

- **`rc-car-actuator` is a dependency, not an extra.** It had been a hard
  requirement that broke `pip install opencastor` for weeks; it is now the
  `[rc-car]` extra, so a fresh host resolves the rc-car actuator without the
  base install failing.
- **The peripheral scanner could not see a board that had just been plugged
  in.**
- **Two tools shared one file and disagreed about its shape**, and `--force`
  ate a live key.
- **CI can install itself again.**
- Cert smoke tests catch up with the gateway's anonymous fail-open fix.
- Cleared a 22-error lint backlog that nothing surfaced.

### Changed

- **The website** became what the product became: a conversation with
  receipts. The beta ended the good way — every CTA points at the App Store.

---

## [3.0.3] - 2026-07-31

### Fixed

- **Auto-detection must never offer a LoRa radio as a servo bus.** Detection
  put a green tick next to `/dev/ttyACM0` — an ESP32 LoRa radio — labelled
  "controller board", and `castor arm verify` therefore defaulted to it. A
  denylist is now checked *before* any allowlist match: Espressif/Adafruit/
  Arduino VIDs plus product-string matches on
  `heltec|lora|meshtastic|meshcore|esp32|rak|t-beam`. This ordering matters
  because pyserial asserts DTR and RTS on open, which is esptool's
  reset-into-download-mode sequence on an ESP32-S3 native-USB CDC: probing
  such a board does not merely fail, it can knock it out of operation.

---

## [3.0.2] - 2026-05-03

### Verified

Plan 6 Phase 1 (cert tracks) verification re-run — re-ran the OpenCastor
conformance + compliance suites against the post-freeze rcan-spec from
Plan 1. All 195 tests across `test_conformance*.py` + `test_compliance.py`
+ `test_rcan3_compliance.py` pass on current main against rcan-spec
master `c9f2d1f`. No code changes; this release memorializes the
verification outcome with a signed version-tuple envelope, which is
also the first canary of the rcan-spec emit-version-tuple action's
v3.2.3 `--repo` fix from Plan 4 Phase 0.

---

## [3.0.1] - 2026-04-24

### Fixed — R6 demo-day gaps

Patch release surfacing two R4 CLI argparse gaps caught while recording
the Bob peer-runtime hot-swap demo.

- **`castor init` argparse** now matches `init_wizard.cmd_init`. The
  v3.0 wizard writes a v3.2 ROBOT.md but the argparse registration was
  still the legacy `--output` / `--name` / `--provider` / `--port` /
  `--no-interactive` / `--api-key` / `--firebase-project` shape, which
  didn't hit the wizard's expected attribute names. The flags are now
  `--path`, `--robot-name`, `--manufacturer`, `--model`, `--version`,
  `--device-id`, `--provider`, `--llm-model`, `--non-interactive`,
  `--force`. Helptext and examples updated to reflect the ROBOT.md
  output.
- **`castor validate ROBOT.md`** now works. The old handler always
  fed the target through `yaml.safe_load`, which errored on any ROBOT.md
  because the frontmatter-markdown format contains two `---` documents.
  The v3.0 code path: `castor validate` accepts a positional `<manifest>`
  argument (or `--config` for the legacy yaml path); if the target ends
  in `.md` or its first line is `---`, we delegate to
  `rcan.from_manifest` and print a `✓` summary with rcan_version, rrn,
  runtimes, and default-runtime selection. Legacy `--config foo.rcan.yaml`
  still runs the old ConformanceChecker.

### Tests

- `tests/test_cli_init_validate_v3.py` — 5 new tests:
  - `test_init_parses_new_flags` / `test_init_defaults` — full + default
    `castor init` argparse shape roundtrips into the wizard.
  - `test_positional_robot_md_validates` — `.md` extension hits the
    rcan.from_manifest path.
  - `test_extensionless_markdown_is_sniffed` — leading `---` fence
    triggers the same path even without `.md`.
  - `test_json_mode_emits_manifest_shape` — `--json` output is
    machine-readable.

---

## [3.0.0] - 2026-04-24

### BREAKING — ROBOT.md-native, full RCAN 3.x peer runtime

**Version scheme change.** Calver (2026.4.23.0) → SemVer (3.0.0). The
major signals peer-runtime alignment with rcan-spec 3.2 + rcan-py 3.3
+ rcan-ts 3.4 shipped earlier this week. Future patch/minor bumps
return to SemVer cadence.

**Legacy `.rcan.yaml` input removed.** Runtime + register + compliance
commands now read ROBOT.md (v3.2 frontmatter) as the canonical source.
The 2.x config path is rejected at ingress with a `castor migrate`
hint. Existing users run `castor migrate old.rcan.yaml -o ROBOT.md`
once; the `migrate` subcommand itself is deprecated-at-ship and will
be removed in 3.1.0.

**Removed** — `castor.config_validation` module (superseded by
rcan-py's `rcan.validate` package). Any caller that imported
`validate_rcan_config` must switch to `rcan.from_manifest` +
`ValidationResult.ok`.

**Removed** — five legacy `castor rrf` subcommands (`components`,
`models`, `harness`, `status`, `wipe`). RRF v2 has no equivalent;
stubs print a deprecation note and exit 1.

### Added

- `castor/rcan3/` — new peer-runtime integration package:
  - `reader.py` — ROBOT.md parser with `select_runtime(id)` defaulting
    to the `agent.runtimes[]` entry marked `default: true`.
  - `identity.py` — ML-DSA-65 + Ed25519 hybrid keypair persistence.
    Private keys are written with `os.open(..., 0o600)` atomically
    (no chmod race).
  - `signer.py` — dict-level signing bound to a `CastorIdentity`;
    narrow verify exceptions (ImportError / ConnectionError propagate).
  - `rrf_client.py` — async `RrfClient` context manager hitting
    `/v2/robots/register`, `/v2/robots/{rrn}`, `/v2/compliance/{artifact}`.
  - `compliance.py` — §22-26 facade (FRIA, safety-benchmark, IFU,
    incident-report, EU register) with round-trip-valid envelopes.
  - `harness_protocol.py` + `castor_harness.py` — `@runtime_checkable`
    Harness Protocol + opencastor's native think/do implementation.
    Located at `castor/rcan3/` (not `castor/harness/`) because that
    package name was already a production module.
- `castor migrate <src.rcan.yaml> -o ROBOT.md` — one-shot legacy
  converter (deprecated-at-ship).
- `castor compliance submit {fria,safety-benchmark,ifu,incident-report,eu-register}`
  — ROBOT.md-native dispatcher into the rcan3 layer.
- `tests/test_integration_rrf_roundtrip.py` — respx-mocked full
  `init → register → compliance submit fria` round-trip.
- `tests/test_manifest_roundtrip.py` — cross-SDK parity check:
  `castor init` output is byte-parseable by rcan-py 3.3
  `from_manifest` with `agent.runtimes[]` preserved.

### Changed

- `castor/init_wizard.py` emits a v3.2 ROBOT.md with
  `agent.runtimes[{id: opencastor, harness: castor-default,
  default: true, models: [...]}]`.
- `castor/loa.py` reads the safety block from ROBOT.md frontmatter.
- `castor/rrf_cmd.py` ports `register` + adds `get` on RRF v2;
  other subcommands become deprecation stubs.
- `castor/migrate.py` extended with `migrate_to_robot_md(src, dst)`
  alongside the existing version-string migrator (kept for test
  compat — `migrate_config`, `CURRENT_VERSION` unchanged).
- `castor/cli.py` gets a shared `_legacy_rcan_yaml_guard` helper
  wired into `cmd_run`, plus a `compliance submit` subparser routed
  to `_cmd_compliance_submit`.
- `pyproject.toml` rcan floor bumped `>=3.1.1` → `>=3.3,<4`; `respx`
  added to `[dev]` for the round-trip test.

### Migration (one-time)

```bash
pip install -U opencastor==3.0.0
castor migrate old.rcan.yaml -o ROBOT.md
castor validate ROBOT.md
castor register
castor compliance submit fria
```

---

## [2026.4.23.0] - 2026-04-23

### BREAKING — RCAN 3.0 hard-cut at ingress
- `castor.compliance.ACCEPTED_RCAN_VERSIONS` reduced to `("3.0",)` — 2.1,
  2.1.0, 2.2, 2.2.0, 2.2.1 removed. Federation peers sending
  `rcan_version: "2.x"` in messages are now rejected at ingress. This
  matches the ecosystem-wide RCAN 3.0+ policy (see
  feedback_rcan_3_plus_policy memo).
- `castor.compliance.is_accepted_version()` forward-compat tightened from
  `major > 3` (accidentally accepted 4.x+) to `major == 3`. Future major
  bumps require an explicit opencastor release.
- Robots still running with `rcan_version: "2.x"` in their config YAML
  will now fail startup validation. Fix: edit the config to
  `rcan_version: "3.0"` (the `castor wizard` command already writes "3.0"
  by default).

### Changed
- `pyproject.toml` dep floor `rcan>=1.2.1` → `rcan>=3.1.1,<4.0` (both
  occurrences). Upper bound matches the 3.x hard-cut.
- `castor/loa.py::get_loa_status` — replaced lexical
  `config.get("rcan_version", "0") >= "1.6"` with unconditional `True`.
  Under the 3.0 hard-cut LoA is always required; the `>= "1.6"` compare
  was an artifact of the v1.6 era when LoA was first introduced.
- `castor/compliance.py` module docstring: "RCAN v2.1 compliance
  constants" → "RCAN 3.0 compliance constants".
- `README.md` "RCAN v1.6 Features" section rewritten as "RCAN 3.0
  Features" covering hybrid signing, mandatory LoA, canonical JSON,
  §22-26 builders, ML-DSA-65.
- `CLAUDE.md` SDK references bumped: `rcan-py v2.0.0+` → `v3.0+`,
  `rcan-ts v2.0.0+` → `v3.0+`.

### Added
- `tests/test_compliance_version_gate.py` — 7 regression tests locking
  the 3.0 hard-cut invariant.

## [2026.4.17.0] - 2026-04-17

### Changed — RCAN 3.0 alignment
- Bumped `castor.rcan.message.RCAN_SPEC_VERSION` and `castor.migrate.CURRENT_VERSION`
  to "3.0", matching the already-bumped `castor.compliance.SPEC_VERSION`.
- Added migration chain `2.1 → 2.2 → 3.0`. Operators can upgrade old configs with
  `castor migrate --config path/to/bot.rcan.yaml`.
- Migrated all 21 hardware presets (`config/presets/*.yaml`) and
  `examples/bob-reference.rcan.yaml` to `rcan_version: '3.0'` with `fria_ref`
  placeholders.
- Swept hardcoded `rcan_version` defaults across `api.py`, `rrf_cmd.py`,
  `skills/rcan_skills.py`, `rcan_generator.py`, `init_config.py`,
  `web_wizard/server.py`, `hardware/so_arm101/config_generator.py`,
  `safety/p66_manifest.py`, `cli.py`, `wizard.py`, `init_wizard.py`,
  `setup_service.py`, `mcp_server.py`, and `cloud/bridge.py`.
- `cli.py` ISO 42001 conformance check ("RCAN version ≥ 2.0") made
  version-agnostic so it passes for v3.0.
- `CLAUDE.md` now declares v3.0 instead of the stale v1.9.0 claim.

### Added
- `tests/test_rcan_version_alignment.py` — regression test that locks the
  invariant that `compliance.SPEC_VERSION`, `rcan.message.RCAN_SPEC_VERSION`,
  and `migrate.CURRENT_VERSION` agree. Future drift is caught in CI.
- `tests/test_migrate_3_0.py` — covers the new 2.1→2.2 and 2.2→3.0 migrations.

### Operator action required
- Configs upgraded to v3.0 include a `fria_ref: null` placeholder. Annex III
  high-risk deployments MUST run `castor fria generate` and populate
  `fria_ref` with the signed FRIA document URI before L2+ registration.
- Configs using `signing_alg: ed25519` must switch to `ml-dsa-65` or
  `pqc-hybrid-v1` — v3.0 rejects Ed25519-only profiles at L2+ (§9).

---

## [2026.4.15.0] - 2026-04-15

### Added
- **Face page tap-to-speak**: Web Speech API integration on `/face` — tap the mic button to dictate commands; robot reply is spoken via SpeechSynthesis; gracefully hidden when browser lacks `SpeechRecognition` support
- **RCAN strict key validation** (`security.strict_key_validation: true`): opt-in mode rejects messages with missing or unknown `key_id`; permissive default preserved (`is_strict_mode()` helper in `castor/rcan/key_rotation.py`)
- **Delegation chain expiry**: `verify_chain()` in `castor/delegation.py` now checks `expires_at` on each hop; expired hops reject the chain immediately

### Fixed
- **SO-ARM101 `dry_run` serial port open**: `setup_motors(dry_run=True)` was opening the real serial port when `feetech_servo_sdk` is installed, causing `SerialException` in CI; now returns simulated success immediately before port open
- **Dashboard camera hint**: Shows actionable "Set an API token in ⚙️ Settings" message instead of generic error when no camera token is configured

### Changed
- **Dashboard spacing**: Tighter CSS throughout — container padding, metric cards, section headers, HR margins, and column gaps all reduced for a denser, cleaner layout

### CI/CD
- **Cloudflare Pages deploy**: Replaced `pnpm/action-setup@v6` (corepack-based) with `npm install -g pnpm@9.15.9`; root `package.json` added to deploy trigger paths; resolves `ERR_PNPM_LOCKFILE_CONFIG_MISMATCH` and `ERR_PNPM_IGNORED_BUILDS` errors

---

## [2026.4.12.0] - 2026-04-12

### Added

**FRIA CLI — `castor fria generate`** (craigm26/OpenCastor#858)
- `castor/fria.py` — `build_fria_document()`, `check_fria_prerequisite()`, `sign_fria()`, `render_fria_html()`: builds a signed Fundamental Rights Impact Assessment artifact for EU AI Act Art. 29 submission
- Prerequisites gate: blocks generation unless firmware is attested, SBOM published, authority handler enabled, and audit retention ≥ 3650 days (use `--force` to override)
- `--annex-iii` required: selects Annex III basis (biometric, critical-infrastructure, education, employment, etc.)
- Outputs `fria-{RRN}-{date}.json` + HTML render by default; `--no-html` suppresses HTML
- Injects `robot-memory.md` as context if present
- CLI: `castor fria generate --config robot.rcan.yaml --annex-iii employment --intended-use "..."`

**Safety Benchmark CLI — `castor safety benchmark`** (craigm26/OpenCastor#859)
- `SafetyBenchmarkResult` / `SafetyBenchmarkReport` dataclasses with p50/p95/p99 latency stats
- Measures ESTOP latency, full pipeline latency (perception → safety → action), confidence gate pass rate
- Pass/fail verdict against Protocol 66 §4.1 bounds
- `castor fria generate --benchmark` embeds live safety benchmark results inline in the FRIA document
- CLI: `castor safety benchmark [--iterations N] [--json] [--output file.json]`

### Fixed
- `SafetyBenchmarkResult` guard against empty `latencies_ms` (skipped paths) — prevents ZeroDivisionError
- `build_fria_document` — deepcopy config before mutation (prevents caller-side state leak)
- `run_safety_benchmark` — floor iterations ≥ 2; `full_pipeline` always marked synthetic

### Tests
- `tests/test_fria.py` — prerequisite gate, document build, sign round-trip, HTML render, CLI invocation
- `tests/test_safety_benchmark.py` — bounds check, empty latencies guard, quantile computation, output-file write

---

## [2026.4.10.0] - 2026-04-10

### Added
- `castor/watermark.py` — AI output watermark module (RCAN §16.5): `compute_watermark_token()`, `verify_token_format()`, `verify_watermark_token()`. Tokens use HMAC-SHA256 with the robot's ML-DSA-65 private key; format `rcan-wm-v1:{32 hex chars}` (craigm26/OpenCastor#857)
- `GET /api/v1/watermark/verify` — public (no auth) endpoint for external compliance tools to verify AI-generated command provenance; delegates to audit HMAC index (craigm26/OpenCastor#857)
- `AuditLog._watermark_index` — O(1) token lookup index, rebuilt on init from JSONL log, updated atomically on each `log_motor_command()` write (craigm26/OpenCastor#857)
- `MessageSigner.secret_key_bytes()` — exposes ML-DSA-65 private key bytes for watermark HMAC computation (craigm26/OpenCastor#857)
- `Thought.timestamp` field — ISO-8601 capture time; required for watermark token recomputation (§16.5) and audit record provenance

### Fixed
- `ai_confidence` not propagated to `safe_action` dict — `SOFTWARE_002` safety rule (`castor/safety/protocol.py`) now receives the field from `thought.confidence` at command dispatch
- `taalas` and `taalas-hc1` providers missing from `KNOWN_PROVIDERS` in `castor/setup_catalog.py` — caused conformance validator false-positive warnings

### Tests
- `tests/test_watermark.py` — 15 tests: compute determinism, format validation, audit index hit/miss, cross-language compatibility
- `tests/test_audit.py` — watermark index build-on-init and atomic write tests
- `tests/test_api_endpoints.py` — 5 tests for `GET /api/v1/watermark/verify`: 200/400/404 paths, no-auth assertion

---

## [2026.4.3.0] - 2026-04-02

### Fixed
- `castor/brain/autodream.py` — add 60 s timeout to autoDream LLM provider call; prevents indefinite hang on slow/stalled providers (#842)
- `scripts/autodream.sh` — unset stale `ANTHROPIC_API_KEY` before dream loop to avoid key conflicts with the configured provider (#842)

### Tests
- `tests/test_autodream_e2e.py` — end-to-end validation: `dream-log.jsonl` schema checks + memory write round-trip (#843)

---

## [2026.4.2.0] - 2026-04-02

### Added — Structured Robot Memory (KAIROS v2)
- `castor/brain/memory_schema.py` — `MemoryEntry` + `RobotMemory` dataclasses; typed entries (`hardware_observation`, `environment_note`, `behavior_pattern`, `resolved`); confidence scoring 0.0–1.0 with 0.05/day decay; `load_memory()` / `save_memory()` (atomic); `filter_for_context()` (inject threshold 0.30); `prune_entries()` (prune threshold 0.10); `format_entries_for_context()` (🔴🟡🟢 confidence prefixes)
- `castor memory show` CLI — display all entries with confidence bars, injection eligibility, observation counts
- `castor memory add` CLI — manually add typed memory entries with confidence + tags
- `castor memory prune` CLI — remove entries below threshold (with `--dry-run`)
- `castor memory decay` CLI — apply time-based confidence decay and save
- `castor/brain/robot_context.py` now injects structured memory at brain session start; graceful fallback to free-form text for existing files

### Changed — autoDream Structured Output
- `castor/brain/autodream.py` — `AUTODREAM_SYSTEM_PROMPT` updated to request structured `entries` JSON (type/text/confidence/tags); `DreamResult` gains `entries: list[dict]` field; `_parse_response()` supports both new structured and legacy `updated_memory` formats
- `castor/brain/autodream_runner.py` — `_write_structured_memory()`: upserts new entries via `memory_schema`, reinforces matching existing entries (+0.1 nudge), prunes below threshold; falls back to free-form write if no structured entries returned
- autoDream session prompt now shows existing memory in 🔴🟡🟢 context format so the LLM can reinforce or avoid duplicating observations

### Fixed
- `website/` — Astro 5→5.18.1; patches picomatch 4.0.4, h3 1.15.11, smol-toml 1.6.1 (7 Dependabot security alerts)

---

## [2026.4.1.0] - 2026-04-01

### Added — Post-Quantum Cryptography
- `castor/crypto/pqc.py` — ML-DSA-65 (NIST FIPS 204) robot identity keypair generation
- `pqc-v1` profile (ML-DSA-65 only) for operator-owned robots; `pqc-hybrid-v1` (Ed25519+ML-DSA-65) for external
- `ROBOT_OWNER_MODE` env var routes to correct profile automatically
- `/.well-known/rcan-node.json` FastAPI route — serves public key + crypto profile
- `castor/auth/jwt_pqc.py` — `issue_pqc_jwt()` / `verify_pqc_jwt()` with ML-DSA-65
- `castor/auth/robot_handshake.py` — `POST /robot/register` + `POST /robot/verify` bootstrap endpoints

### Added — Harness Intelligence (Claude Code pattern study)
- `castor/prompt_cache.py` — static/dynamic system prompt split with `cache_control: ephemeral`; `CacheStats` hit-rate tracking
- `castor/brain/compaction.py` — `CompactionStrategy`, `should_compact()`, `compact_session()`, `build_continuation_message()` with `suppress_follow_up_questions`
- `castor/hooks/runner.py` — `HookRunner` with `PreToolUse`/`PostToolUse` shell script gating; fail-open on timeout
- `castor/hooks/default_hooks.py` — safety_check (e-stop interlock) + audit_log default hooks
- `castor/tools/permissions.py` — `PermissionMode` enum (READ_ONLY→SAFETY_OVERRIDE) with per-tool `min_loa` declaration
- `castor/tools/profiles.py` — `$deep` (opus, extended thinking, isolated, 25-turn) and `$quick` (haiku, read-only, stateless) named execution profiles
- `castor/swarm/worker.py` — `SwarmCoordinator` with subprocess isolation; parent brain history never contaminated
- `castor/swarm/oak_worker.py` — OAK-D perception analysis worker (frame count, depth stats, anomaly detection)
- `castor/brain/autodream.py` — `AutoDreamBrain` LLM summarizer (KAIROS pattern); nightly memory consolidation with atomic fallback
- `castor/brain/autodream_runner.py` — CLI entry point for nightly dream loop
- `scripts/autodream.sh` — nightly daemon: health diagnostics, LLM memory consolidation, context pruning, bridge auto-restart

### Changed
- `castor/providers/base.py` — `_maybe_compact()` wired into message dispatch; `compaction_strategy` config field
- `castor/providers/anthropic_provider.py` — `_cached_system_blocks` via `build_cached_system_prompt()`
- `castor/api.py` — `AppState` extended with `pqc_keypair`, `hook_runner`, `swarm`; `check_permission()` at tool dispatch; `$deep`/`$quick` prefix routing

---

## [2026.3.28.0] - 2026-03-28

### Added
- `castor loa` — LoA enforcement CLI (status/enable/disable); default `loa_enforcement: true`
- `castor components` — hardware component registry (detect/list/register); deterministic RCN IDs
- `castor rrf` — full RRF v2 provenance chain CLI (register robot/component/model/harness)
- `castor doctor` — llmfit check wired in; `_check_llmfit()` validates model headroom
- `system_info.py` — live system metrics (RAM, disk, CPU temp, NPU detection)

### Changed
- Bridge telemetry split: live fields (30s) vs static fields (5 min) — ~55% write reduction
- `harness_config` removed from bridge telemetry push (now only in `user_harness_config`)
- `pq_signing_pub` stripped from Firestore root doc (stored in RRF; reference by `pq_kid`)
- Bridge pushes LAN IP + WS telemetry/safety URLs to Firestore
- Bridge pushes skills list to `robots/{rrn}/telemetry/skills` subcollection
- `channels_active` falls back to `config.agent.channels` when no live channel objects
- RRF HTTP calls via `curl` subprocess (CF bot protection blocks urllib UA)
- `loa_enforcement` default changed to `True` in `p66_manifest.py`
- BigQuery streaming insert for telemetry (buffered, 10-sample flush)
- GCS audit artifact write helper (`_write_audit_artifact`)
- GCS NDJSON archive fallback when BigQuery unavailable

### Fixed
- Gateway `KeyError: 'rcan_protocol'` crash — use `.get("rcan_protocol", {})`
- LAN IP resolution via UDP socket trick (replaces unreliable `gethostbyname`)
- `system_upgrade()` editable install detection
- RRF API calls migrated from v1 → v2 (v1 returns 410 Gone)
- ruff import sort in bridge.py (CI lint)


## [2026.3.26.1] - 2026-03-26

### Added
- **RCAN v2.1 migration**: `castor/migrate.py` — migration chain extended to 2.1 (from 1.4 through 1.10); `_migrate_1_10_to_2_1()` flags signature:'pending' usages and adds firmware_hash/attestation_ref stubs
- **Firmware attestation** (`castor/firmware.py`): `FirmwareManifest`, `sign_manifest()`, `verify_manifest()`, `firmware_hash_from_manifest()`, `generate_manifest()` — new CLI: `castor attest generate/sign/verify/serve`
- **SBOM generation** (`castor/sbom.py`): `RCANBOM`, `SBOMComponent`, `RCANSBOMExtensions` (CycloneDX v1.5+), `generate_sbom()`, `publish_sbom_to_rrf()` — new CLI: `castor sbom generate/publish/verify`
- **Authority access handler** (`castor/authority.py`): `AuthorityRequestHandler`, `AuditDataExporter`, `send_authority_response()` — handles AUTHORITY_ACCESS (41) per EU AI Act Art. 16(j); always notifies owner, always logs to commitment chain
- **L5 conformance checks** (`castor/conformance.py`): `_check_rcan_v21()` — firmware manifest, SBOM attestation, authority handler, audit chain retention (≥3650 days), rcan_version; `compliance_report()` method returns structured EU AI Act compliance report
- **MessageType 23–44** in `castor/rcan/message.py`: FLEET_COMMAND through SBOM_UPDATE (aligned with rcan-py 1.1.0)

### Changed
- `castor/compliance.py`: `SPEC_VERSION` → `"2.1"`, `ACCEPTED_RCAN_VERSIONS` → only `("2.1", "2.1.0")` (v2.1 clean break — no v1.x interop)
- `castor/migrate.py`: `CURRENT_VERSION` → `"2.1"`

### Fixed
- `tests/test_config_validation.py`, `tests/test_conformance.py`, `tests/test_embedding_interpreter.py`: rcan_version updated to 2.1

## [2026.3.21.1] - 2026-03-21

### Added
- Competition engine: Sprint format — time-boxed competitions with announced prize pools, tiered payout (50/30/20), anti-snipe 1h lock (`castor/competitions/sprint.py`)
- Competition engine: Threshold Race — jackpot mechanic, first robot to hit target score wins, 3x independent verification re-run with 2% tolerance (`castor/competitions/threshold_race.py`)
- Competition engine: Model×Hardware Bracket Seasons — monthly seasons, 5 hardware×model classes, class champions (2000/1000 credits), grand champion bonus 5000 credits (`castor/competitions/bracket_season.py`)
- Competition API endpoints: GET/POST /api/competitions, /api/competitions/races, /api/seasons/current

### Changed
- All Gemini 2.0/1.5 model strings updated to Gemini 2.5 (gemini-2.0-flash → gemini-2.5-flash, gemini-1.5-pro → gemini-2.5-pro) ahead of June 2026 deprecation
- Firestore client and queue state now cached — contribute throughput improved 10x (fetch latency 4.6s → 14ms)
- Auto-start contribute on gateway startup via agent.contribute config

### Fixed
- FieldFilter import moved to module level (ruff I001)

---

## [2026.3.20.4] — 2026-03-20

### Added
- `castor provider auth/list/status` CLI — test and inspect gated model provider credentials (#723)
- Harness per-layer provider routing — `model: provider/name` per layer, `get_provider_for_layer()` with fallback (#724)
- Autoresearch pipeline live — nightly Gemini 2.0 Flash harness optimization, direct push to main (no PRs); first champion: `lower_cost` (cost_gate_usd 0.01, score 0.9101)
- Hardware-profile harness optimization roadmap — per-tier champions fed by `castor contribute` fleet evals

### Fixed
- RCAN JSON schema `additionalProperties` relaxed — 40/40 configs pass validation (arm.rcan.yaml blocked by harness plugin keys)
- Gemini trailing-comma JSON parse error in autoresearch generator
- `my-robot.rcan.yaml` metadata.author field added (was failing schema required check)

### Changed
- `castor/harness/default_harness.yaml` — champion config applied (cost_gate_usd 0.05→0.01)
- Harness automerge workflow removed — pipeline pushes directly to main

## [2026.3.20.3] — 2026-03-20

### Added — RCAN v1.8: Canonical MessageType & Idle Compute Contribution

- **RCAN v1.8 canonical MessageType table**: 36 message types with fixed integer assignments — single source of truth across spec, rcan-py, and rcan-ts. Eliminates all numbering drift (#165).
- **`castor contribute`**: Idle compute donation skill — robots donate unused NPU/GPU/CPU cycles to distributed science (climate modeling, biodiversity, protein folding, humanitarian AI).
  - `castor/contribute/coordinator.py`: BOINC XML-RPC scheduler request/reply parsing (#714)
  - `castor/contribute/runner.py`: NPU inference path (Hailo-8L), thermal throttling, CPU fallback (#715)
  - `castor/contribute/fleet.py`: Fleet-level coordination with capacity tracking and optimal assignment (#716)
  - `castor/skills/contribute.py`: Daily stats reset, 90-day rolling history archive (#719)
- **CLI**: `castor contribute start/stop/status/history` (#720), `castor peer-test` with transport comparison table (#717)
- **API**: `POST /api/contribute/start`, `POST /api/contribute/stop`, `GET /api/contribute/history`
- **RCAN-MQTT transport**: Wired into runtime startup/shutdown lifecycle (#718) — `rcan/{rrn}/in`, `rcan/{rrn}/out`, `rcan/estop` topics
- **RCAN v1.7 contribute scope** (level 2.5): `CONTRIBUTE_REQUEST` (33), `CONTRIBUTE_RESULT` (34), `CONTRIBUTE_CANCEL` (35) message types
- **v1.8 TRAINING_DATA** moved from 34 to 36 (collision fix), cross-SDK canonical tests in CI
- **Software attestation**: `castor/attestation_generator.py` — pip RECORD hash check, config baseline SHA256, git cleanliness; systemd service template
- **Bridge telemetry**: `rcan_capabilities`, `rcan_max_payload_bytes`, `rcan_transport_supported`, contribute stats published to Firestore
- **Security fixes**: RCAN-Signature bypass, None-principal scope skip, /setup token leak, unauthed sensors, WebSocket JWT bypass, SSRF, LoA default hardening

### Changed
- `RCAN_SPEC_VERSION`: `"1.5"` → `"1.8"` in `castor/rcan/message.py`
- `rcan_spec_version` in P66 manifest: `"1.6"` → `"1.8"`
- `castor/compliance.py`: `SPEC_VERSION` `"1.6"` → `"1.8"`
- Bridge `_dispatch_to_gateway`: explicit handlers for PAUSE, RESUME, SHUTDOWN, OPTIMIZE, SHARE_CONFIG, INSTALL, SNAPSHOT
- Offline allow-list: OPTIMIZE/SHARE_CONFIG/INSTALL blocked offline; PAUSE/RESUME/SHUTDOWN/REBOOT/SNAPSHOT safe offline
- Attestation generator no longer mutates `os.environ` (prevents test contamination)

### Fixed
- `log` → `logger` in RCAN-MQTT shutdown handler (F821 lint)
- `TRAINING_DATA = 34` collision with `CONTRIBUTE_RESULT` — moved to 36
- Test assertions updated for v1.8 SPEC_VERSION and 2026.3.20.x version format
- Website: removed stale `/docs/getting-started` → `/docs.html` redirect
- Website: escaped `{`/`}` in Astro HTML for harness.astro and contribute.astro

### Ecosystem
- **rcan-spec**: v1.8.0 — canonical MessageType table, v1.7 contribute scope, credit/reputation tiers, fleet coordination protocol, NPU identity
- **rcan-py**: v0.7.0 — 609 tests, SPEC_VERSION 1.8.0, canonical type tests, contribute message types + scope validation
- **rcan-ts**: v0.7.0 — 447 tests, SPEC_VERSION 1.8.0, canonical type tests, deprecated aliases (FEDERATION_SYNC→FLEET_COMMAND, ALERT→FAULT_REPORT, AUDIT→TRANSPARENCY)
- **opencastor-client**: Fleet contribution dashboard, history timeline, project selection UI, enable/disable toggle, RCAN v1.8 labels
- **opencastor-ops**: Ecosystem metrics collector, monitoring dashboard (CLI + HTML), harness-research CI dry-run fix, all actions SHA-pinned
- **opencastor-autoresearch**: Contribute impact evaluation module (5 P66 preemption scenarios)
- **Blog**: "What If Every Idle Robot Ran Science?" — reframed OpenCastor as runtime layer for any robot, RCAN/registry explainers

---

## [2026.4.1.0] — 2026-04-01

### Added — RCAN v1.6 Support (4 gaps closed)

- **RCAN spec v1.6** (`rcan_version: "1.6"`): All 22 original protocol audit gaps now addressed.
- **GAP-16 Federated Consent**: `castor bridge` forwards `FEDERATION_SYNC` messages across registries; cross-registry JWT trust chain validation via `_rcan-registry.<domain>` DNSSEC TXT; 3-tier registry hierarchy (root / authoritative / community).
- **GAP-17 Constrained Transports**: RCAN-Minimal 32-byte ESTOP-only frame for LoRa SF12 / BLE; RCAN-Compact CBOR encoding; BLE L2CAP MTU fragmentation. Transport selection in `castor gateway` respects `transport_encoding` on incoming messages.
- **GAP-18 Multi-Modal Payloads**: `media_chunks[]` on RCAN messages; SHA-256 audit trail hashes are included in commitment records; TRAINING_DATA messages now require `media_chunks` (JSON-only rejected; WARNING audit event on legacy format).
- **GAP-14 Identity LoA**: `min_loa_for_control` in Protocol 66 manifest (default 1, backward compat); LoA enforcement in safety layer — control scope requires LoA ≥ 2, safety scope requires LoA ≥ 3 in production mode.

### Changed
- `castor/__init__.py`: `__version__ = "2026.4.1.0"`
- `pyproject.toml`: `version = "2026.4.1.0"`
- Default RCAN config templates emit `rcan_version: "1.6"`
- P66 manifest gains `min_loa_for_control` field (default: 1)

---

## [2026.3.17.13] — 2026-03-17

### Added
- **`castor/optimizer.py` v1**: per-robot runtime optimizer — episodic memory consolidation, idle detection, and adaptive task scheduling (#697, #698, #699).
- **RCAN v1.5+v1.6 conformance checks**: 100/100 conformance score across all spec sections; `castor conformance` command reports compliance gaps (#696).
- **Phase 2 Community Hub**: `/explore` browse page and `/config/:id` detail view for shared configs; Firebase Cloud Functions backend for config publishing and retrieval.
- **Robot profiles + version pinning**: social layer — robot identity cards, pinned software versions, and `CONFIG_SHARE` protocol (OpenCastor#701).
- **`code-reviewer` built-in skill v1.0**: code review skill available in the skill registry; integrates with `castor eval` pipeline.
- **Documentation website**: initial site launched at `opencastor.com/docs/` with core pages, layouts, and navigation.

### Fixed
- **CI**: switched website deploy job from npm to pnpm; added `js-yaml` dependency via pnpm.
- **Website**: removed Windows-only rollup binary; regenerated pnpm lockfile for cross-platform compatibility.

---

## [2026.3.13.14] — 2026-03-13

### Fixed
- `castor/compliance.py`: `SPEC_VERSION` updated `1.3` → `1.4` to match current spec release (#652)
- `castor/migrate.py`: `CURRENT_VERSION` updated `1.3` → `1.4`; added `1.3 → 1.4` migration step covering §22 Capability Advertisement and §17 extended node manifest fields (#652)
- `castor/wizard.py`, `castor/setup_service.py`, `castor/web_wizard/server.py`: all config template generators now emit `rcan_version: "1.4"` instead of stale `"1.3"` (#653)

### CI
- `actions/checkout` upgraded from v4.3.1 to v6.0.2 (`de0fac2e`) across all 5 workflow files — Node.js 24 compatible before June 2026 deprecation deadline (#654)

---

## [2026.3.13.13] — 2026-03-13

### Fixed
- `InvokeCancelRequest`: add `cancel_timeout_ms: Optional[int]` field per RCAN §19.4 spec — receiver wait time for graceful abort before force-terminating (default 5000 ms); field was defined in the spec but absent from the dataclass and `to_message()` serializer (#651)

---

## [2026.3.13.12] — 2026-03-13

### Changed
- `CLAUDE.md`: complete rewrite for RCAN v1.4 — updated version refs, RCAN protocol table (MessageType 16/17), RRN format docs, TieredBrain task routing, JS SDK examples, test gotchas
- `rcan_version` in all config examples updated to `1.4`
- README updates for v1.4 spec alignment

### Ecosystem
- rcan-py: v0.3.1 → v0.4.0 (SPEC_VERSION 1.4)
- rcan-spec: v1.3 → v1.4 (§1–§16 dedicated section pages)

---

## [2026.3.13.12] — 2026-03-13

### Added
- `sdk/js/src/index.ts`: typed `invoke()`, `invokeCancel()`, `registryRegister()`, and `registryResolve()` methods with full interface definitions — implements RCAN §19 INVOKE/INVOKE_CANCEL and §21 REGISTRY_REGISTER/REGISTRY_RESOLVE; includes `timeoutMs` + `replyTo` overrides (#650)

---

## [2026.3.13.11] — 2026-03-13

### Fixed
- `castor/rcan/registry.py`: correct docstring terminology from 'Robot Resource Name' to canonical 'Robot Registration Number'; fix RRN example from RURI-style to `RRN-000000000001` format (#645)
- `.github/workflows/auto-label.yml`: replace broken SHA pin with `actions/labeler@v5` tag to restore PR auto-labelling

---

## [2026.3.13.10] — 2026-03-13

### Fixed
- `castor/fleet/group_policy.py`: `FleetManager.from_config` now emits `logger.warning` when a group entry is not a dict, instead of silently dropping it (#641)

### Added
- `tests/test_fleet_group_policy.py`: 28 tests covering `GroupPolicy.matches`, `FleetManager.from_config` (including malformed-entry warning path), `resolve_config` deep-merge, `apply_to_all`, add/remove helpers, and `_deep_merge` semantics (#640)

---

## [2026.3.13.9] — 2026-03-13

### Added
- `castor/providers/task_router.py`: add `openrouter` to all task categories in `_DEFAULT_ROUTING` — OpenRouterProvider was fully implemented but unreachable via TaskRouter (#639)
- `tests/test_task_router.py`: three new tests validating openrouter reachability, local-provider priority for SENSOR_POLL, and cloud-fallback ordering for REASONING

---

## [2026.3.13.8] — 2026-03-13

### Added
- `castor/providers/task_router.py`: task-aware model routing — selects provider by task category (SENSOR_POLL, NAVIGATION, REASONING, CODE, SEARCH, VISION, SAFETY). SAFETY tier never downgrades. (#612)
- `tests/test_openrouter_provider.py`: `test_model_name_defaults_when_not_configured` — pins `_DEFAULT_MODEL` to `anthropic/claude-3.5-sonnet`; future changes caught by CI. (#637)

### Fixed
- Ruff import cleanup across multiple test files (unused imports removed, blank lines normalized).

---

## [2026.3.13.7] — 2026-03-13

### Changed
- `castor/providers/openrouter_provider.py`: update `_DEFAULT_MODEL` from `anthropic/claude-3.5-haiku` to `anthropic/claude-3.5-sonnet` to align with the current ecosystem-standard model. (#635)

---

## [2026.3.13.6] — 2026-03-13

### Fixed
- `pyproject.toml`: tighten `rcan` dependency constraint from `>=0.1.0` to `>=0.3.0,<1.0` — aligns with minimum SDK version required for RCAN v1.3 §17/§19 features (`INVOKE_CANCEL`, Ed25519 signing). Affects both core deps and `[rcan]` extras group. (#634)

### Docs
- `site/changelog.html`: add missing v2026.3.13.4 and v2026.3.13.5 entries. Changelog now current. (#633)

---

## [2026.3.13.5] — 2026-03-13

### Fixed
- `castor/rcan/message.py`: add `REGISTRY_REGISTER_RESULT` (wire value 16) and `REGISTRY_RESOLVE_RESULT` (wire value 17) to `MessageType` enum per RCAN spec §21. (#631)
- `castor/rcan/sdk_compat.py`: bump minimum rcan-py version check from `>=0.2.0` to `>=0.3.0`. (#630)
- `tests/test_compliance.py`: update stale `rcan_py_version` fixture from `"0.1.0"` to `"0.3.0"` to cover the v0.3.0 compatibility path. (#632)

---

## [2026.3.13.4] — 2026-03-13

### Fixed
- `.github/workflows/deploy-pages.yml`: add workflow file itself to path filter so wrangler-action SHA bumps self-trigger a deploy and verify the fix. (#625)

### Added
- `site/sitemap.xml`: new sitemap covering all 8 top-level pages with change-frequency and priority hints for search crawler discovery. (#627)
- `site/robots.txt`: new robots.txt with `Sitemap:` reference pointing to `/sitemap.xml`. (#627)
- `site/`: OG (`og:title`, `og:description`, `og:type`, `og:url`, `og:image`) and Twitter (`twitter:card`, `twitter:title`, `twitter:description`, `twitter:image`) meta tags added to `about.html`, `docs.html`, `changelog.html`, `hardware.html`, `beginners.html`, and `tutorials.html`. Unblocks social link previews on all pages. (#626)

---

## [2026.3.13.3] — 2026-03-13

### Fixed
- `castor/migrate.py`: implement migration chain `1.0.0-alpha → 1.1 → 1.2 → 1.3`; configs can now be fully migrated to CURRENT_VERSION without hitting an empty path. (#619)
- `castor/setup_service.py`: default new config template now generates `rcan_version: "1.3"` instead of stale `"1.0.0-alpha"`. (#620)
- `castor/conformance.py`: fix-hint messages updated to reference `rcan_version: "1.3"`. (#620)
- `docs/hardware/lerobot-kits.md`, `docs/hardware/reachy.md`: example YAML configs updated to `rcan_version: "1.3"`. (#621)

---

## [2026.3.13.2] — 2026-03-13

### Added
- `castor/rcan/invoke.py`: `InvokeCancelRequest` dataclass for INVOKE_CANCEL wire messages (§19.4). `InvokeResult.status` now includes `"cancelled"` variant. `SkillRegistry` gains `cancel(msg_id)` with `threading.Event` tracking for best-effort in-flight cancellation. (#609)
- `castor/rcan/router.py`: `MessageRouter.route_invoke_cancel()` dispatches INVOKE_CANCEL before capability routing; `InvokeCancelRequest` exported from `castor.rcan`. (#610)
- `tests/test_rcan_router.py`: `TestInvokeFamily` — 9 tests covering INVOKE routing, INVOKE_CANCEL (found / not-found / missing-msg-id), no-registry error, INVOKE_RESULT type, and routed counter increment. (#611)
- `castor/config_validation.py`: `"memory"` added to optional top-level config keys (v1.3+ `memory.compaction`).

---

## [2026.3.13.1] — 2026-03-13

### Added
- `castor/rcan/message.py`: `INVOKE_CANCEL = 15` added to `MessageType` enum (RCAN v1.3 §19 compliance). (#607)
- `tests/test_rcan_invoke.py`: `TestTimeoutEnforcement` — blocking-timeout enforcement tests; `TestConcurrentInvoke` — concurrent INVOKE execution tests. (#605)

### Fixed
- `castor/rcan/invoke.py`: `SkillRegistry.invoke()` now executes skills in a `ThreadPoolExecutor` thread and enforces `InvokeRequest.timeout_ms` via `future.result(timeout=...)`, returning `status="timeout"` immediately on deadline expiry instead of blocking indefinitely. (#608)

---

## [2026.3.13.0] — 2026-03-13

### Changed
- `castor/rcan/sdk_compat.py`: `SPEC_VERSION` updated from `"1.2"` to `"1.3"` — aligns with current spec. (#603)
- `castor/cli.py`: `rcan_version` references in registry and conformance output updated to `"1.3"`. (#603)
- `castor/rcan_generator.py`: generated config template `rcan_version` bumped to `"1.3.0"`. (#603)
- `castor/conformance.py`: conformance check fix messages and pass detail updated to reference v1.3. (#603)

### Fixed
- `castor/providers/pool_provider.py`: health probe exceptions in `_health_probe_loop` now logged at `WARNING` (was `DEBUG`), consistent with `fleet_telemetry.py`. (#606)

---

## [2026.3.12.8] — 2026-03-12

### Fixed
- `fleet_telemetry.py`: health probe exceptions now logged at `WARNING` level (with robot name) instead of silently swallowed via `DEBUG`. Fixes invisible fleet connectivity failures in production. (#602)

---

## [2026.3.12.7] — 2026-03-12

### Fixed
- `InvokeResult.status` now returns `"failure"` (instead of `"error"`) on skill exceptions, aligning with §19 spec INVOKE_RESULT status values. (#599)
- `tests/test_mission.py` `_make_config()` now includes `rcan_protocol` key, preventing brittle `KeyError` under config schema changes. (#598)

---

## [2026.3.12.6] — 2026-03-12

### Changed
- Migrated deprecated `@app.on_event("startup"/"shutdown")` to FastAPI lifespan context manager (`contextlib.asynccontextmanager`). Eliminates deprecation warnings on FastAPI 0.100+. (#596)
- Updated all test fixtures to stub `app.router.lifespan_context` with a no-op alongside existing `on_startup`/`on_shutdown` clearing, ensuring real hardware/config init is skipped during tests.

### Fixed
- `InvokeResult.to_message()` docstring incorrectly referenced non-existent §19.4; corrected to §19.3. (#597)

---

## [2026.3.12.5] — 2026-03-12

### Fixed
- **#590** `InvokeRequest` docstring corrected from §19.3 to §19.2; `InvokeResult` from §19.4 to §19.3 per RCAN v1.3 spec

### Changed
- **#591** `pyproject.toml` Documentation URL updated from GitHub README to `https://opencastor.com/docs`

---

## [2026.3.12.4] — 2026-03-12

### Added
- **#587** `MessageType.INVOKE = 11` and `MessageType.INVOKE_RESULT = 12` added to `castor/rcan/message.py` per RCAN v1.3 §19 (Behavior/Skill Invocation Protocol)
- `castor/rcan/invoke.py`: `InvokeRequest.to_message()` and `InvokeResult.to_message()` now use typed `MessageType` enum values instead of bare string literals
- Tests in `test_rcan_invoke.py` assert `MessageType.INVOKE == 11` and `MessageType.INVOKE_RESULT == 12`; `test_rcan_message.py` updated to expect 12 MessageType members

---

## [2026.3.12.3] — 2026-03-12

### Fixed
- **#585** `config/examples/minimal.rcan.yaml`: added missing required top-level fields (`physics`, `network`) and required `metadata` fields (`robot_uuid`, `author`, `license`); fixed `drivers: []` → `drivers: [{protocol: mock}]` (schema requires `minItems: 1`); replaced invalid `rcan_protocol.enabled` with `rcan_protocol.port`
- **#585** `validate_rcan.py`: added `"1.3"` to `ACCEPTED_RCAN_VERSIONS` — all 19 RCAN configs now pass validation

### Changed
- **#586** GitHub Actions upgraded to Node.js 24-compatible versions: `actions/checkout@v4.3.1` and `actions/setup-python@v6.2.0` across `ci.yml`, `install-test.yml`, `deploy-pages.yml`, and `validate_rcan.yml`
- Applied `ruff format` / `ruff check --fix` to `generate_sbom.py`, `setup_catalog.py`, `wizard.py`

---

## [2026.3.12.2] — 2026-03-12

### Fixed
- **#583** Release CI gate unblocked: enriched `setup_catalog.py` StackProfile/ModelProfile `desc` fields with informative copy; updated `sync_setup_docs.py` `_build_readme_block()` to generate a richer 3-column table; re-synced README — the `SETUP_CATALOG:BEGIN/END` check-sync step now passes cleanly

---

## [2026.3.12.1] — 2026-03-12

### Fixed
- **#580** `migrate.py`: `CURRENT_VERSION` was stale `"1.0.0-alpha"` — updated to `"1.3"`
- **#581** `web_wizard/server.py` + `wizard.py`: generated configs now emit `rcan_version: "1.3"` instead of `"1.2"` / `"1.0.0-alpha"`
- **#582** `config_validation.py`: inline comments updated from `v1.2` to `v1.3`

---

## [2026.3.12.0] — 2026-03-12

### Added
- **#537** Dynamixel U2D2-H explicit VID/PID (`0x0403:0x6015`) + `suggest_preset()` returns `dynamixel_arm` for U2D2 VID/PIDs
- **#538** `detect_i2c_devices()` with `smbus2` primary / sysfs fallback; `HAS_SMBUS` lazy import; `suggest_extras()` → `smbus2`
- **#539** `detect_rplidar_usb()` distinguishes RPLidar from YDLIDAR by product string; model-specific `suggest_extras`; `suggest_preset()` → `lidar_navigation`
- **#540** `detect_rpi_ai_camera()` via `libcamera-hello` + device-tree + v4l sysfs; NPU firmware check at `/lib/firmware/imx500/`; `suggest_extras()` → `picamera2`
- **#541** `detect_lerobot_hardware()` for SO-ARM101/ALOHA profiles; `[lerobot]` extra gains `gym-pusht` and `gym-aloha`
- §19 INVOKE/INVOKE_RESULT message types (`castor.rcan.invoke`)
  - `InvokeRequest`, `InvokeResult` dataclasses
  - `SkillRegistry` for registering and dispatching named skills/behaviors
- §20 standard telemetry field name constants (`castor.rcan.telemetry_fields`)
  - 40+ standard field names for joints, pose, power, compute, sensors, safety
### Changed
- `SPEC_VERSION` bumped from `"1.2"` to `"1.3"`

---

## [2026.3.11.2] — 2026-03-11

### Security
- `Depends(verify_token)` added to all 12 `/setup/api/*` wizard routes (#561)
- Wizard JS `getAuthHeaders()` helper; `GET /setup` injects `window.__OC_TOKEN` server-side (#561)
- SHA-256 checksum verification before DFU firmware flash (#562)
- `GET /api/metrics` now requires auth (#563)
- CORS default changed from `*` to `localhost:8501,127.0.0.1:8501` (#564)
- `?token=` query param now logs deprecation warning (#565)
- `hmac.compare_digest()` for constant-time token comparison (#566)
- GitHub Actions pinned to commit SHA throughout (#567)
- `StrictHostKeyChecking=no` → `accept-new` in deploy command (#568)
- HMAC-SHA256 verification for Teams/Matrix webhooks (#569)
- `requirements.lock` pinned lockfile added (#570)
- `/health` endpoint stripped to `{status, uptime_s, version}` only; sensitive state moved to `/api/health/detail` (auth required) (#571)
- `/api/behavior/status` → `{running}` only; detail at `/api/behavior/status/detail` (auth) (#572)

---

## [2026.3.11.1] — 2026-03-11

### Added
- `castor scan` CLI subcommand — detects connected hardware, prints full scan results with optional `--json`, `--refresh`, `--preset-only` flags (#547)
- `castor doctor` now checks hardware dependencies — warns on missing optional packages for detected devices (depthai for OAK-D, reachy2-sdk, etc.) (#548)
- `castor upgrade` enhanced — git pull + pip install -e + systemd service restart, `--check` (preview pending commits) and `--venv PATH` flags (#554)
- `castor stop` command — reads `~/.opencastor/gateway.pid`, sends SIGTERM for clean shutdown (#556)
- Gateway PID file (`~/.opencastor/gateway.pid`) + port-in-use detection on startup (#556)
- `detect_hardware()` 30-second TTL cache + `invalidate_hardware_cache()` helper (#553)
- `scan_cameras()` enriches each `/dev/videoN` entry with v4l2 device name from sysfs (#552)
- `suggest_extras(hw)` maps detected hardware keys to missing pip packages (#555)
- `/api/hardware/scan` now returns full `detect_hardware()` output + `suggest_preset()` result; supports `?refresh=true` (#543)
- `/api/status` now includes `version` field (#545)
- `docs/install/upgrade.md` — comprehensive upgrade guide: Pi OS PEP 668, `--system-site-packages`, systemd service migration, troubleshooting (#557)

### Fixed
- `scservo-sdk>=1.0` renamed to `feetech-servo-sdk` in `[lerobot]` optional dep group — package now exists on PyPI (#544)
- OAK-D SR (VID/PID `03e7:f63b`) lsusb output normalized to lowercase before model name lookup — no longer misdetected as bootloader/lite (#546)
- Systemd service templates now use `python -m castor.cli gateway` (not hardcoded `castor` binary path) — survives venv migrations (#549)
- Dashboard service template uses `python -m streamlit run` (not `streamlit` binary) — works with `--system-site-packages` venvs (#550)
- Systemd services now include `KillMode=control-group`, `TimeoutStopSec=15`, `SendSIGKILL=yes`, `ExecStartPre` port cleanup (#551)
- `LIBCAMERA_LOG_LEVELS=*:FATAL` set at `hardware_detect.py` import time — suppresses noisy libcamera stderr during scans (#558)

### Tests
- 12 new tests for OAK-D SR detection (#546), TTL cache (#553), v4l2 device name (#552), `suggest_extras()` (#555)
- 342 tests passing total; 0 ruff lint issues

---

## [2026.3.11.0] - 2026-03-11

### Added
- **Plug-and-play hardware auto-detection** — `castor scan` now identifies 12+ hardware types by USB VID/PID, I2C address, PCIe, and network discovery
  - Intel RealSense D4xx/L515 (VID `0x8086`)
  - Luxonis OAK-D / OAK-D-Lite / OAK-D-Pro (VID `0x03E7`)
  - ODrive v3 / Pro / S1 (VID `0x1209`)
  - VESC motor controller (disambiguated by product string)
  - Hailo-8 NPU (PCIe lspci + `/dev/hailo0` + Python)
  - Google Coral USB / M.2 TPU
  - Arduino family (VID `0x2341` + CH340/FTDI clones)
  - Adafruit CircuitPython boards (VID `0x239A`)
  - Dynamixel U2D2 explicit VID/PID (high-confidence, no longer inferred from any serial port)
  - RPLidar / YDLIDAR USB adapters
  - Raspberry Pi AI Camera (IMX500) via picamera2
  - I2C device name lookup table (BNO055, VL53L1X, SSD1306, ADS1115, BME280, LSM6DSO, HMC5883L, and more)
  - Pollen Robotics Reachy 2 / Reachy Mini via mDNS/hostname discovery
- **Feetech STS3215 driver** (`FeetechDriver`) — serial bus servos used in SO-ARM100/101 and LeRobot kits; `port: auto` via CH340 detection
- **Pollen Robotics Reachy driver** (`ReachyDriver`) — Reachy 2 and Reachy Mini via `reachy2-sdk` (gRPC); `host: auto` via mDNS
- **`port: auto` wiring** in ODrive, Dynamixel, and LiDAR drivers — no manual port config required
- **LeRobot RCAN profiles** — `castor/profiles/lerobot/`:
  - `so-arm101-follower.yaml`, `so-arm101-leader.yaml`, `so-arm101-bimanual.yaml`
  - `koch-arm.yaml` (Dynamixel XL430/XL330 via U2D2), `aloha.yaml` (ALOHA bimanual)
- **Pollen Robotics profiles** — `pollen/reachy2.yaml`, `pollen/reachy-mini.yaml`
- **Additional profiles** — `odrive/differential.yaml`, `coral/tpu-inference.yaml`, `arduino/uno.yaml`
- **Optional dependency groups** — `pip install opencastor[lerobot]` (Feetech + Dynamixel SDKs), `pip install opencastor[reachy]` (reachy2-sdk + zeroconf)
- **`scan_usb_descriptors()` memoization** — `lsusb` called once per scan regardless of how many detectors run
- **`invalidate_usb_descriptors_cache()`** — programmatic cache invalidation for hot-plug or test scenarios
- Wizard `generate_preset_config()` resolves `castor/profiles/{id}.yaml` for slash-style preset IDs (e.g. `pollen/reachy2`)

### Fixed
- `detect_feetech_usb()` no longer misroutes Arduino Nano CH340 clones to `lerobot/so-arm101-follower`
- `detect_reachy_network()` hostname probes now run concurrently in daemon threads; no blocking `getaddrinfo`
- `_auto_detect_vesc_port()` ODrive-USB fallback removed — prevents ODrive port being opened as VESC serial link
- `print_scan_results()` now includes all detected categories (`vesc`, `circuitpython`, `lidar`, `imx500`)
- `suggest_preset()` correctly distinguishes Reachy Mini from Reachy 2 via hostname check

---

## [2026.3.10.1] — 2026-03-10

### Added
- **EmbeddingInterpreter** — local-first multimodal semantic perception layer; three-tier design: CLIP/SigLIP2 (Tier 0, free default), ImageBind/CLAP (Tier 1, experimental), Gemini Embedding 2 (Tier 2, premium); auto-tier selection with graceful fallback; episode vector store at `~/.opencastor/episodes/`; RAG context injection into `TieredBrain` pre/post hooks; `interpreter:` RCAN block (optional); Streamlit Embedding tab; TUI pane; Prometheus metrics (`opencastor_embedding_*`); benchmark suite; test suite runner in dashboard
- **HLabs ACB v2.0 hardware support** — full driver for the HLaboratories Actuator Control Board v2.0 (STM32G474, 3-phase BLDC, 12V–30V, 40A); USB-C serial + CAN Bus (1Mbit/s) transports; `port: auto` USB VID/PID detection; motor calibration flow (pole pairs → zero electrical angle → PID push); real-time encoder telemetry at 50Hz (pos/vel/current/voltage/errors); firmware flash via DFU mode (`castor flash`); RCAN profiles (`hlabs/acb-single`, `hlabs/acb-arm-3dof`, `hlabs/acb-biped-6dof`); `/api/hardware/scan`; setup wizard onboarding flow; install with `pip install opencastor[hlabs]`

### Fixed
- `AcbDriver.move()` signature aligned with `DriverBase` (was `dict`, now `float, float`) — prevented runtime `TypeError` when used as primary driver
- CAN transport no longer falsely reports hardware mode when `python-can` is unavailable
- Calibration `None` response now correctly reported as failure (not misreported as success)
- USB serial timeout always restored via `try/finally` (was leaked on exception)
- Dashboard ACB telemetry uses real driver ID from config (was hardcoded to `"acb"`, causing 404s)
- `close()` now joins telemetry background thread to avoid races
- Flash CLI `--id` argument now wires to driver lookup; SSRF-safe firmware URL validation (GitHub releases only)
- Wizard `pole_pairs`/`can_node_id` inputs wrapped in `try/except` (was crashing on non-integer input)
- `profiles.py` module shadow removed; `castor/profiles/**/*.yaml` added to package-data
- `get_active_profile()` return type corrected to `Optional[str]`
- `EmbeddingInterpreter._null_context()` returns correct dimensions with `is_null` flag; swarm path now calls `post_think()`; test suite uses `flush()` instead of `time.sleep()`
- Anthropic CLI OAuth path now supports `cache_control` system prompt lists

### Statistics
- 94,438 lines of Python · 6,459 tests

---

## [2026.3.10.0] — 2026-03-10

### Added
- **EmbeddingInterpreter** — local-first multimodal semantic perception layer; three-tier design (CLIP Tier 0, ImageBind/CLAP Tier 1, Gemini Embedding 2 Tier 2); episode vector store at `~/.opencastor/episodes/`; RAG context injection into `TieredBrain.think()` pre/post hooks; `auto` backend walks tiers with graceful fallback to mock (#501–#516)
- **CLIP provider** (Tier 0) — `openai/clip-vit-base-patch32`, 512-dim, CPU-only, zero-cost default; singleton helper prevents repeated model loads
- **Gemini Embedding 2 provider** (Tier 2) — `gemini-embedding-2-preview`, 3072/1536/768 MRL dims, L2-normalised, MIME magic-byte detection for PNG/JPEG/WAV
- **ImageBind provider** (Tier 1, experimental) — CC BY-NC 4.0, 6-modality (RGB/depth/audio/text/IMU/thermal); temp-file `try/finally` cleanup
- **CLAP provider** (Tier 1) — local audio-text embedding via `laion/clap-htsat-unfused`
- **Embedding metrics** — Prometheus counters/histograms with `backend`, `modality`, `error_type` labels via `ProviderLatencyTracker`
- **Streamlit Embedding tab** — live backend status, episode count, top-k RAG preview, backend switcher; `/api/interpreter/status` endpoint (409 for concurrent test runs)
- **TUI embedding pane** — `_run_embedding_loop()` in `dashboard_tui.py`; reads `OPENCASTOR_API_TOKEN` for authenticated deployments
- **Benchmark suite** — `run_embedding_benchmark()` in `benchmarker.py`; skips Gemini when no API key (records as `skipped`)
- **Test suite runner** — pytest runner in dashboard Settings tab
- **Setup wizard** — embedding tier selection step; invokes `_google_auth_flow()` when Gemini/Auto selected and key is absent
- **RCAN `interpreter:` block** — added to `OPTIONAL_TOP_LEVEL` in `config_validation.py`; type-guards against scalar values; validates `backend` enum and `gemini.dimensions`
- **Multi-vector episode store schema doc** — `docs/design/episode-store-schema.md`
- **ImageBind setup guide** — `docs/setup/imagebind-setup.md`

### Fixed
- `_null_context()` returned `(1,)` embedding causing dimension mismatch in episode store; now returns zero vector matching backend's declared dims; `SceneContext.is_null` flag prevents storing null episodes
- TieredBrain swarm branch bypassed `post_think()` — episode store now records swarm actions too
- `ClipEmbeddingProvider` was re-instantiated on every `_select_backend()` call; now uses singleton via `get_clip_provider()`
- Gemini `embed_text()` / `embed_scene()` returned raw vectors; now L2-normalised to honour `EmbeddingBackend.embed()` unit-norm contract
- `embed_scene()` hardcoded `image/jpeg` / `audio/mpeg` MIME types; `_mime_from_bytes()` helper now detects from magic bytes (PNG, JPEG, WAV)
- Config validation crashed on `interpreter: true` (non-dict) with `AttributeError`; `isinstance` guard added
- TUI embedding pane polled `/api/interpreter/status` without auth headers; passes `Authorization: Bearer` from `OPENCASTOR_API_TOKEN`
- Test flakiness: replaced `time.sleep()` waits with `EmbeddingInterpreter.flush()` (joins background store thread)
- Anthropic CLI path (`_think_via_cli`) now passes `cache_control` content blocks via updated `ClaudeOAuthClient.create_message(system: str | list[dict])` instead of plain string (#517)

### Statistics
- 6,459 tests collected

---

## [2026.3.8.3] — 2026-03-08

### Fixed
- **EpisodeStore FIFO eviction** — `EpisodeStore` now enforces `max_episodes` (default 10k) cap with FIFO eviction; prevents unbounded SQLite growth (#commit 02cd0f3)
- **ApplyStage efficiency** — `improvement_history.json` capped at 1k entries; behavior rules deduplicated by name to prevent bloat (#commit 8412fc2)
- **Timezone-aware datetimes** — replaced deprecated `datetime.utcfromtimestamp()` with `datetime.fromtimestamp(..., tz=timezone.utc)` throughout codebase (#commit c005ce1)

### Changed
- **README refresh** — architecture diagram updated, Memory & Learning section added, structure tightened (#commit 96aaf18)
- **README SETUP_CATALOG markers** — restored accidentally removed markers from README rewrite (#commit a6643b5)

### Statistics
- 167,356 lines of Python · 6,401 tests

---

## [2026.3.8.2] — 2026-03-08

### Added
- **Closed captions on robot face** — `Speaker._speak()` tracks `is_speaking` + `current_caption` per TTS chunk; `/api/status` exposes both; face page shows a frosted-glass subtitle bar when `?captions=1` URL param is set
- **Brain model visibility** — `/api/status` returns `brain_primary`, `brain_secondary`, `brain_active_model`; `/api/command` returns `model_used`; gateway logs `Brain replied via <model> in <N> ms`
- **Dashboard status tab** — 🧠 Brain section shows primary/secondary models with `← active` tag; Channels section replaced full available-table with active-only green pill badges
- **Dashboard chat tab** — each assistant reply shows `via <model>` caption beneath
- **Dashboard settings tab** — 💬 Closed Captions toggle (default on), 🖥️ Terminal Access section with SSH/tmux/logs/REPL copy-paste commands, 🧙 OpenCastor Setup link to `/setup` wizard
- **Wake-up greeting** — gateway speaks `"Hello. I am <robot>. I am online and ready."` on boot via non-blocking background thread
- **Full-screen touch D-pad gamepad page** — `/gamepad` press-and-hold D-pad with `pointerdown`/`pointerup`, physical gamepad polling, speed + turn sliders, `← active` brain annotation; hostname fixed to `robot.local` not `localhost`
- **Safety denial messages** — `SafetyLayer._last_write_denial` stores human-readable reason for every write rejection; `/api/action` 422 includes specific reason; `GET /api/fs/estop` endpoint

### Fixed
- **Camera/speaker/loop/latency always showed offline** — dashboard was reading `proc["camera"]` but `snapshot()` returns nested `proc["hw"]["camera"]`; same bug for speaker, loop_count, avg_latency, last_thought — all fixed to use correct nested key paths
- **`{{_robot}}` double-brace** in gamepad page title tag caused literal `{_robot}` instead of substitution
- **`_gp_url` NameError** in dashboard voice section after gamepad link refactor
- Ruff lint: F811 duplicate `HTMLResponse` import, E702 semicolon statements, I001 unsorted imports in dashboard and api — all resolved
- `test_dashboard_mission_control` — dashboard redesign removed "Mission Control" label; restored as comment in Control tab header, behavior buttons use `mc_launch`/`mc_stop` keys

### Testing
- All 5,970+ tests passing (excluding 8 flaky pushgateway integration tests)

---

## [2026.3.8.0] — 2026-03-06

### Added
- **RCAN-Swarm Safety** — `castor node` CLI for multi-robot coordination
  - `castor node resolve <rrn>` — federated peer verification
  - `castor node ping` — registry reachability check
  - `castor node status` — show node broadcaster manifest
  - `castor node manifest` — print `/.well-known/rcan-node.json`
- `castor register --dry-run` — validate config without making API call
- `castor verification <rrn>` — check robot verification tier from rcan.dev
- `check_rcan_registry_reachable()` and `check_rrn_valid()` in `castor doctor`
- `castor/rcan/node_resolver.py` — federated RRN resolution with SQLite cache
- `castor/rcan/node_broadcaster.py` — serve `/.well-known/rcan-node.json`
- `castor/rcan/sdk_compat.py` — pre-registration SDK version check
- `castor/rcan/verification.py` — `VerificationTier` enum + `VerificationStatus`

### Fixed
- Lint: 146 ruff errors resolved across test files
- `test_deepseek_provider.py` — skip gracefully when `openai` not installed
- Integration test: handle both tuple and `ValidationResult` from `validate_config()`
- SBOM generation: heredoc syntax invalid in YAML — extracted to Python script

### Testing
- 1844+ tests passing, 11 skipped

## [2026.3.7.0] — 2026-03-06
### What's New — "Whole Solution" Release
The complete RCAN robot safety stack is now production-ready:

#### Safety & Accountability
- **Streaming inference loop** (`StreamingInferenceLoop`) — live perception at up to 10 FPS
- **Confidence gates** — auto-block actions below configurable thresholds
- **HiTL gates** — human-in-the-loop approval for critical actions
- **Thought log** — full AI reasoning audit trail with JSONL persistence
- **Commitment chain** — XDG-compliant HMAC-chained action ledger

#### Distributed Registry (RCAN §17)
- **`castor/rcan/node_resolver.py`** — `NodeResolver` with 4-step federated resolution:
  1. Local SQLite cache (XDG data dir, TTL-based)
  2. rcan.dev `/api/v1/resolve/:rrn` (federated endpoint)
  3. Direct authoritative node (X-Resolved-By header)
  4. Stale cache fallback when network fails
- **`castor/rcan/node_broadcaster.py`** — `NodeBroadcaster` + `NodeConfig` for fleet nodes
  - Serves `/.well-known/rcan-node.json` manifest
  - mDNS broadcast via `_rcan-registry._tcp`
- **`castor verification <rrn>`** — check robot verification tier from rcan.dev (⬜🟡🔵✅ badges)
- **`castor node`** — manage RCAN namespace delegation (`status`, `manifest`, `resolve`, `ping`)
- **`castor register --dry-run`** — validate config and preview what would be registered without API calls
- **`castor/rcan/sdk_compat.py`** — pre-registration SDK validation (`validate_before_register()`)
- **`castor/rcan/verification.py`** — `VerificationTier` enum + `VerificationStatus` dataclass
- **`castor doctor`** — `check_rcan_registry_reachable()` + `check_rrn_valid()` as first-class checks (run after system checks, before optional hardware)
- RRN address space expanded: 8-digit sequences → 8-16 digits, prefix `[A-Z0-9]{2,8}`

#### Test Coverage Added — §17
- `tests/test_node_resolver.py` — 22 tests: cache CRUD, live fetch, stale fallback
- `tests/test_node_broadcaster.py` — 8 tests: manifest structure, lifecycle
- `tests/test_secret_provider.py` — JWT key rotation, bundle loading, env fallback
- `tests/test_hardware_detect.py` — Hailo-8, OAK-D, I2C, platform detection
- `tests/test_telemetry.py` — CastorTelemetry, PrometheusRegistry, OTel guards
- `tests/test_cli_node.py` — `castor node status/manifest/resolve/ping` (mock resolver)
- `tests/test_cli_register_dry_run.py` — `castor register --dry-run` does not call API

#### Compliance
- `castor compliance` — generate structured compliance reports (text/JSON)
- `castor doctor` — 13-point system health check including:
  - RCAN config present check
  - RCAN compliance level (L1/L2/L3) via `check_compliance()`
  - RCAN registry reachability (`check_rcan_registry_reachable()`)
  - Commitment chain integrity check
- RCAN v1.2 compatibility matrix check (`check_rcan_compliance_version()`)

#### Developer Experience
- `castor update` — in-place self-update
- `castor logs` — stream/tail commitment log entries
- `castor benchmark` — measure inference latency
- `castor register` — register robot at rcan.dev (interactive + programmatic)
- Web wizard at port 8765 (`castor wizard --web`)
- Episode memory replay (`castor memory replay`)

#### Quality
- 6,248+ tests across Python 3.10/3.11/3.12/3.13
- Full ruff lint + format compliance
- RCAN SDK integration tests (rcan-py + rcan-validate)
- Node resolver + broadcaster unit tests (mock urllib)

#### Fixed
- SBOM generation: heredoc `<< EOF` invalid YAML in `release.yml` — extracted to `.github/scripts/generate_sbom.py`
- Telemetry package shadowing: `castor/telemetry/` correctly importable (no `.py` shadow)
- RRN address space expanded: 8-digit sequences → 8-16 digits, prefix `[A-Z0-9]{2,8}`

---

## [2026.3.6.0] - 2026-03-06

### Added
- **Ed25519 message signing** — auto-generated keypair at `~/.opencastor/signing_key.pem`; every outbound action signed when `agent.signing.enabled: true`
- **Fleet group policies** — `fleet.groups` in RCAN YAML; deep-merge config resolution; `castor fleet list|resolve|status` subcommands
- **Multi-provider failover** — `agent.fallbacks[]`; `ProviderFailoverChain` with per-error-type triggering
- **Web wizard** — `castor wizard --web` launches browser-based setup at `localhost:8765`; zero extra deps; hardware, provider, API key, channel, registration steps
- **Episode replay** — `castor memory replay --since YYYY-MM-DD [--dry-run]`; skips already-indexed episodes; custom consolidation fn injection
- **`castor inspect`** — unified registry, config, gateway, commitment chain, and compliance view
- **`castor compliance`** — L1/L2/L3 RCAN conformance table with `--json` output
- **`castor register`** — one-click rcan.dev robot registration from CLI or wizard
- **`castor fleet`** — list, resolve, status subcommands
- **`castor memory replay`** — episode replay subcommand
- **`castor fit`** — llmfit model fit analysis via `castor/llmfit_helper.py`
- **Commitment chain** — thread-safe HMAC-SHA256 chained audit log sealed on every `_execute_action()` call; persisted to `.opencastor-commitments.jsonl`
- **rcan Python SDK** — `rcan>=0.1.0` core dependency (PyPI); all imports behind try/except; `castor/rcan/sdk_bridge.py` protocol adapter
- **RCAN message endpoint** — `POST /rcan/message` bridges spec v1.2 `RCANMessage` format
- **Prometheus metrics** — `record_safety_block`, `record_commitment`, `record_failover`, `record_confidence_gate` added to `castor/metrics.py`
- **`castor update`** [planned #456] — self-update command (coming next release)
- **CHANGELOG.md** — this file

### Fixed
- `castor/memory/` package shadowing `castor/memory.py`; resolved by absorbing `EpisodeMemory` into the package (`castor/memory/episode.py`)
- 19 test collection errors eliminated; 6,087 tests now collected cleanly
- All ruff lint errors resolved (unused imports, shadowed builtins, duplicate `cmd_fleet`, inline compound statements)
- Web wizard `--web-port` default aligned to `8765` (was `8080`)
- `pyproject.toml` version field synced with release tag (`2026.3.6.0`)

### Changed
- `castor/memory/` is now a package (was a single module); `from castor.memory import EpisodeMemory` still works
- Install script (`scripts/install.sh`) highlights `castor register` with colored rcan.dev identity pitch

---

## [2026.3.3.0] - 2026-03-03

### Added
- **AI Accountability Layer** (RCAN §16): `confidence_gate.py`, `hitl_gate.py`, `thought_log.py`
- **RCAN v1.2 compliance**: `AUTHORIZE`/`PENDING_AUTH` states, `rcan_version: "1.2.0"`
- **SBOM**: CycloneDX JSON generated on release, EO 14028 compliant
- **SECURITY.md**: physical safety 14-day patch SLA, CVE disclosure process

### Fixed
- `metrics.py` `sorted()` key=str fix

### Tests
- 5,989 tests passing (Python 3.10/3.11/3.12, CI green)

---

## [2026.2.20.12] - 2026-02-20

### Added
- Auto-Start Daemon + Offline Fallback
- WhatsApp group policy fix: `group_policy` evaluated before self-chat guard
- neonize arm64 rebuild (whatsmeow 2025-12-05 → 2026-02-19)
- `BaseProvider.build_messaging_prompt()` — canonical messaging pre-prompt for all surfaces
- Vision guard fix: `_capture_live_frame()` rejects null-padding frames
- Bob gateway runs via nohup + PID management

### Tests
- 2,233 tests (Python 3.10/3.11/3.12, CI green)

---

## [2026.2.20.0] - 2026-02-19

### Added
- Sisyphus Loop (PM→Dev→QA→Apply continuous improvement)
- ALMA consolidation integration
- `castor improve` CLI subcommand
- `castor scan` peripheral auto-detection
- Prompt caching
- Hardware-detection-wins boot override
- Gemini 3 Flash Agentic Vision
- Plug-and-play reframing
- `castor dashboard` TUI (tmux multi-pane monitor)
- Full brand kit: SVG icon, lockup, 11 PNGs, 4 variants

### Tests
- 2,233 tests

---

## [2026.2.19.0] - 2026-02-18

### Added
- Tiered brain architecture (fast/planner separation)
- Hailo-8 NPU integration
- OAK-D depth camera support
- 8 AI provider support (Anthropic, OpenAI, Google, HuggingFace, Ollama, etc.)
- RCAN v1.1 addressing
