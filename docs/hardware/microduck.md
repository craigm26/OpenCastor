# Pollen Robotics Microduck

The easiest setup in OpenCastor. One command:

```bash
pip install "opencastor>=3.1"
castor duck
```

The `>=3.1` is load-bearing and [README.md](../../README.md) explains why: a
bare `pip install opencastor` can resolve the old CalVer wheel, which has no
`castor duck` in it.

That's the whole thing. `castor duck` finds the duck, checks it can reach it, asks
robotd how it's feeling, and writes a working RCAN config.

```
  🦆 OpenCastor · Microduck

  1/4  Finding your duck
        found radxa@duck-01.local (via hostname)
  2/4  Checking access
        ssh ok as radxa · robot group ok
  3/4  Talking to robotd
        healthy · loop 49.8 Hz · battery 64% · walk.onnx, sit.onnx
  4/4  Writing config
        ~/.config/opencastor/duck-01.rcan.yaml

  Ready.
    castor run --config ~/.config/opencastor/duck-01.rcan.yaml
    castor duck test     make it walk
    castor duck health   check on it
```

If the brain has no credentials yet, the last step says so and points at
`castor login` instead of pretending you're done. Pick a different brain at
setup time with `--brain`:

```bash
castor duck --brain ollama                     # local model, no API key
castor duck --brain anthropic:claude-sonnet-4-5
```

## Hardware

| | |
|---|---|
| **Compute** | Rockchip RK3566 — quad Cortex-A55, 1 GB RAM, 32 GB storage, NPU |
| **OS** | Armbian (Radxa Zero 3 profile), ordinary systemd + sudo |
| **Actuation** | 15 servos, Dynamixel v2 protocol on `/dev/ttyS2` @ 1 Mbps |
| **Sensing** | Camera, ToF depth, 2 × IMU |
| **Power** | NP-F550 removable, ~1 h; robotd sits down and powers off at 6.6 V |
| **Size** | ~25 cm, ~800 g |

Motion comes from RL policies (PPO in MuJoCo → ONNX) executed by the `robotd`
daemon at 50 Hz. OpenCastor does not replace that — it sends *intents* to it.

## Transports

Four ways to reach `robotd`. They all carry the same JSON-RPC — the difference
is what the owner has to set up first, and what protects the robot afterwards.

| `transport` | Reaches | Setup cost on the duck | Who may drive |
|---|---|---|---|
| `unix` | `/run/robotd.sock` on the duck itself | membership in the `robot` group (and the logout or reboot that makes it take effect) | anyone with a login on the duck |
| `ssh` | `/run/robotd.sock` through `ssh -L 7788:...` | an SSH key **and** the `robot` group edit **and** the reboot | anyone holding the private key |
| `tcp` | a relay already listening on `host:port` (default `7788`) | install and run the relay, plus whatever token it wants | anyone who can reach that port |
| `webrtc` | `mediad`'s signalling server on `host:8443`, then its `control` datachannel | **nothing** — `mediad` is enabled on every install and every update | **anyone who can reach the duck's port 8443** |

`webrtc` is the only one that costs the owner no setup at all, and the reason it
is not simply the default is the last column. Read
[Security: `mediad` is unauthenticated](#security-mediad-is-unauthenticated-on-0000)
before choosing it.

### Where to run OpenCastor

**Off-board (recommended).** The duck has 1 GB of RAM and a 50 Hz control loop to
protect. Run OpenCastor on a laptop, a Pi 5, or a NAS, and let the driver open an
`ssh -L 7788:/run/robotd.sock` forward. Nothing extra is installed on the robot —
OpenSSH forwards to Unix sockets natively.

```yaml
drivers:
- id: duck
  protocol: microduck
  transport: ssh
  ssh_host: 192.168.1.42
  ssh_user: radxa
```

**On-board.** Set `transport: unix`. Works, but expect contention with `robotd`,
`mediad` and the policy on four Cortex-A55 cores. Anything heavy — a Node-based
agent CLI, a large Python brain — will not fit in 1 GB alongside all that.

```yaml
drivers:
- id: duck
  protocol: microduck
  transport: unix
  socket: /run/robotd.sock
```

**Off-board with no setup at all.** `transport: webrtc` speaks to `mediad`, the
daemon that already runs on every duck and already serves the browser console.
No SSH key, no `robot` group edit, no reboot, nothing installed on the robot.
It needs one optional extra on *your* machine:

```bash
pip install 'opencastor[microduck-webrtc]'
```

```yaml
drivers:
- id: duck
  protocol: microduck
  transport: webrtc
  host: radxa-zero3.local    # or the address `duckctl ip` prints
  # port: 8443               # mediad --port default
  # webrtc_video: false      # negotiate control only (default)
```

The extra is `aiortc` plus `websockets`. It is an extra rather than a core
dependency because it pulls a media stack (`av`, `pylibsrtp`, `cffi`) that a
duck driven over a Unix socket has no use for; it installs from wheels on a
Pi 5 (aarch64, Python 3.13, aiortc 1.15.0). Ask for `transport: webrtc`
without it and the driver stops with the install line rather than quietly
pretending to be a duck.

### Security: `mediad` is unauthenticated on 0.0.0.0

**This is the price of `transport: webrtc`, and it is a choice, not a
side effect.** Verbatim from `mediad/src/main.rs:9-13` in Pollen's own tree:

> **It does not authenticate.** Anyone who reaches the signalling port can
> drive the robot and see its camera. That is a decision, not an omission —
> §4 has the reasoning, and the short version is that the pairing PIN is a
> shared `000000`, so a gate would add a step to every connection and prove
> nothing. The bridge that makes a robot reachable from outside the LAN
> authenticates on both sides before a session arrives.

`mediad` binds all interfaces by default (`--host 0.0.0.0`, `main.rs:28-36`)
and its unit is enabled on every install and every update. So this is already
true of a stock duck whether or not OpenCastor ever connects: `transport: ssh`
does not protect the duck from the network, it protects *robotd's socket* from
it, and the camera and the whole `mediad` control surface were open the whole
time.

What choosing `transport: webrtc` changes is that OpenCastor is now on that
surface too. Pollen's own summary (`docs/design/remote-webrtc.md` §4) is that it
is "fine on a bench and in an office. **Not fine in a home**." Weigh it that
way.

**What turning it off costs.** `sudo systemctl disable --now mediad` on the duck
closes both ports. You lose: the browser console at `http://<duck>:8080/` (the
one-minute path from a phone to a moving duck, and the only client a duck in the
field ships with), the camera stream, the duck detector, and `transport: webrtc`
itself. You keep: walking, `robotd`, `padd`, Bluetooth, and updates — `mediad`
is deliberately not on the recovery path (`main.rs:15-17`). If you want the
console but not OpenCastor on it, that is the same switch; there is no
finer-grained gate on the robot, which is the point §4 is making.

**What `webrtc` cannot reach.** `mediad/src/route.rs` is an exhaustive
per-transport match. It refuses `robot.setMode` (a mode switch is a claim about
hardware only somebody in the room can make), `system.pairingPin` /
`system.setPairingPin` (they authorise BLE, which is the recovery path) and the
`update.*` mutations (they would drop the session that asked). Everything this
driver sends — `robot.move`, `head`, `look`, `pose`, `mouth`, `do`, `sound`,
`stop`, `enable`, `init`, `relax`, `subscribe`, `health`, `policies`, `skills`,
`model` — is permitted.

## Discovery

`castor duck` tries these in order, because no single one is reliable:

| Method | Notes |
|---|---|
| Local socket | `/run/robotd.sock` exists → OpenCastor is already on the duck |
| Hostnames | `duck.local`, `duck-01.local`, `microduck.local`, `duckling.local` |
| `duckctl ip` | Over Bluetooth — the most reliable path on the stock image |
| mDNS | Needs `zeroconf`; Pollen's docs warn it "resolves when it feels like it" |
| ARP table | `castor duck --deep` — finds ducks with unknown hostnames |

Know the address already? Skip all of it: `castor duck --host 192.168.1.42`.

## The two things that can block you

Both are one-liners, and `castor duck` prints the exact command (and offers to
run the first one for you):

```bash
ssh-copy-id radxa@duck-01.local              # install your SSH key
ssh radxa@duck-01.local 'sudo usermod -aG robot $USER'   # robotd socket access
```

The `robot` group is how robotd's socket is shared with unprivileged clients —
the same group Pollen's own setup guide creates.

`transport: webrtc` needs neither of them. That is the whole reason it exists,
and the reason it is not the default is one section up.

## The duck is open on your network

**`mediad` binds `0.0.0.0:8443` (WebRTC signalling) and `0.0.0.0:8080` (a full
drive console), and neither authenticates.** It is enabled on every install and
every update. Anyone who can reach the duck's IP can open
`http://<duck>:8080/` in a browser and drive it, look through its camera, load
a policy, and shut it down.

This is upstream's stated decision rather than an oversight. From
`mediad/src/main.rs`: "anyone who reaches the signalling port can drive the
robot and see its camera. That is a decision, not an omission" — because the
BLE pairing PIN is a shared `000000` printed on every duck, so "a gate would
add a step to every connection and prove nothing."

OpenCastor's default path does not use either port: robotd's unix socket over
an SSH forward, which costs an SSH key and the `robot` group and is real
protection. The optional `transport: webrtc` (section above) uses the 8443
signalling port on purpose, trading that protection for no key, no group edit
and no reboot. Either way, putting the duck on your Wi-Fi so OpenCastor can
reach it puts `mediad` on your Wi-Fi too, and this guide inherits that fact.

What to do about it, in the order most people should:

- **Nothing, on a home LAN you trust.** The browser console is the fastest way
  to drive a duck, and losing it costs more than it saves. `castor doctor`
  prints the ports and the words "NEITHER AUTHENTICATES" next to them, so it
  is at least never a surprise.
- **Put the duck on a guest or IoT VLAN** if the LAN is shared. This keeps the
  console for you and removes it from everyone else.
- **Turn it off**, and know the bill: `sudo systemctl disable --now mediad`
  costs you the camera stream, the browser console at `:8080`, the WebRTC
  datachannel, and `duckctl open`. It does **not** affect OpenCastor, robotd,
  the gamepad, or anything in this guide — the duck still walks. It also means
  that when robotd is unreachable you have no second way in except a gamepad
  or a serial cable, and `hooks/postinstall` re-enables every unit with an
  `[Install]` section on **every update**, so this does not survive an upgrade.

Do not put a duck on a public network, and do not port-forward `8080` or
`8443`.

## Wire protocol

The driver speaks robotd's contract directly: **JSON-RPC 2.0, one object per line
(NDJSON)**, over `/run/robotd.sock`. This is the same contract `robotctl`, the
gamepad daemon and the phone app use, so OpenCastor is a first-class client.

Over `transport: webrtc` it is the *same* JSON-RPC, with one framing
difference: the `control` datachannel carries **one JSON object per message,
with no newline** (`mediad` opens it as a string channel and trims each frame
before forwarding it to the same Unix socket). The transport translates the
framing at the boundary, so the table below is identical on every transport —
`mediad` is a dumb pipe by design, and "no per-method work in `mediad` when a
method is added" (`docs/design/remote-webrtc.md` §5).

| OpenCastor | robotd |
|---|---|
| `driver.move(linear, angular)` | `robot.move` notification `{vx, vy, vyaw}` (trunk frame) |
| `driver.strafe(lateral)` | `robot.move` with `vy` |
| `driver.head(...)` / `look_at(x,y,z)` | `robot.head` notification (radians) |
| `driver.stop()` | `robot.stop` request — stands still, **not** limp |
| `driver.init()` / `relax()` / `enable()` | `robot.init` / `robot.relax` / `robot.enable` |
| `driver.health_check()` | `robot.health` — loop Hz, battery, IMU, bus |
| `driver.get_state()` / `get_battery()` / `get_odometry()` | cached `robot.state` stream |
| `driver.get_policies()` | ONNX policies reported at `robot.subscribe` |

### Skills, voice and the beak

The duck ships more than a gait. Every scripted move `robotd` schedules has a
method here, named as the wire names it:

| OpenCastor | What happens |
|---|---|
| `driver.kick(left=False)` | `robot.do` `kick_left`/`kick_right` — half a second, and **blind**: the duck does not look for the ball, so aiming is yours |
| `driver.ground_pick()` | The beak goes down and comes up with whatever was there (~3 s) |
| `driver.sit_toggle()` | Sit if standing, stand if sitting — the daemon knows which |
| `driver.roulade()` | One forward roll (~1 s); requests during a roll chain another |
| `driver.quack()` / `sound(tag, hold=)` | The voice bank: `alarm`, `greet`, `inquire`, `peck`, `chirp`, `coo`, `wheee` |
| `driver.mouth(open)` | 0 closed → 1 open. No policy touches the mouth; this is the only thing that moves it |
| `driver.pose(z, roll, pitch)` | Lean the standing body, held inside the trained envelope |
| `driver.look_at(x, y, z)` | `robot.look` — **robotd's own IK**, not trigonometry on this end |
| `driver.theremin(True)` | The ToF sensor becomes an instrument; the beak opens with the pitch |
| `driver.shutdown()` | Sit, then power off |

`wheee` is a held ride: pass `hold=True` repeatedly to keep it going and
`hold=False` to cut it. A hold that simply stops arriving plays out through
its end segment — the two endings differ on purpose.

`mouth()` and `pose()` are continuous intents like the twist, so the driver
re-sends them until the command TTL expires. A pose that expires snaps the
body back to nominal rather than leaving the duck leaning.

### Deadman

robotd zeroes the twist if intents stop arriving (~0.5 s), but OpenCastor's
`move()` is a one-shot call. The driver runs a background intent loop that
re-sends the last twist at `intent_hz` (default 20, floored at 2 Hz so a bad
config can't under-feed robotd), expires it after `command_ttl_s` (default 1.5 s)
with one explicit zero, then goes quiet.

Two independent deadmen: **ours**, so a wedged brain can't leave the duck
walking, and **robotd's**, so a wedged driver can't either.

**Which one fires, on which failure** — worth being exact about now that a
transport can be a network:

| What went wrong | Deadman that fires | How long |
|---|---|---|
| The brain stopped asking for motion, transport healthy | **ours** (`command_ttl_s`) — one explicit zero, then quiet | 1.5 s |
| The driver, the process or the host died | **robotd's** — nothing more arrives, so it zeroes | 500 ms |
| The WebRTC session dropped, Wi-Fi went, the duck went out of range | **robotd's** — our zero cannot reach the robot | 500 ms |

robotd's is `deadman_ms: 500` in `robotd-params/src/lib.rs` (`SafetyParams`).
It is the only deadman that survives losing the transport, which is what makes
a network transport acceptable at all. `transport: webrtc` adds no third
deadman: one would be a second answer to a question robotd already answers, and
it would answer it later.

### Velocity envelope

`max_vx` / `max_vy` / `max_vyaw` scale OpenCastor's normalised `[-1, 1]` into
robotd's m/s and rad/s. robotd clamps on top of whatever you set and names the
binding limit in `robot.state.limited_by` — the authoritative envelope lives on
the robot.

### Safety

`move()` routes through OpenCastor's `SafetyLayer` before it ever reaches the
wire (the driver implements `_move()`, so this is automatic). On the far side,
robotd owns the motor bus exclusively and enforces its own limits, fall
detection, limp-fall predictor and battery cutoff. OpenCastor sends intents,
never raw motor writes — don't bypass the driver to write the servo bus while
robotd is running.

`init()`, `relax()` and `enable()` reach the duck over **every** transport this
driver has, WebRTC included. `mediad/src/route.rs` permits them deliberately,
and says why: BLE refuses them because it wants "the person doing it to be
looking at the robot rather than at a screen", and "that condition is met here
rather than waived. A peer holding this session has the camera: it is looking at
the robot." What WebRTC refuses instead is listed under
[Security](#security-mediad-is-unauthenticated-on-0000).

## Commands

```bash
castor duck                      # find, verify, configure  (start here)
castor duck --deep               # also sweep the ARP neighbour table
castor duck --host 192.168.1.42  # skip discovery
castor duck --start              # configure, then run it
castor duck find                 # list candidates and what's blocking each
castor duck health               # live loop rate, battery, policies
castor duck test                 # stand up and walk forward (asks first)
castor duck --brain ollama       # choose the LLM provider while configuring
```

Add `--json` to any of them for machine-readable output.

`castor doctor` also has a duck section now, and it is the one to run when
something is wrong rather than when you are setting up. It prints nothing about
ducks on a host with no duck config; when there is one it reads every number
off the wire — robotd answering, the login's `robot` group, mediad's two
ports, `robot.health`'s `control_loop`, `robot.policies` slot by slot, and the
battery — and **exits non-zero when the duck cannot walk**, so
`castor doctor && castor duck test` means something.

The row worth knowing about before you need it is **Duck drive mode**. A
`MicroduckDriver` that cannot reach robotd does not raise: it silently becomes
a mock, answers `ok: True` to `health_check()` and accepts every move. Doctor
calls that a blocking failure, never a pass.

## Stringing it together

The duck's own vocabulary is atomic. `robot.do` runs exactly one skill, and a
refusal names the move already holding the robot. It can kick. It cannot
*"walk to the ball, line up, knock it toward the couch, then celebrate"* —
every verb in that sentence exists, but the sentence does not.

That sentence is what OpenCastor adds:

```bash
castor duck do fetch                              # a routine by name
castor duck do '[{"move":"approach","metres":0.4},{"move":"nudge"}]'
castor duck do "greet me, then patrol the room"   # plain English
```

`fetch` is one word that becomes ten primitives — look down, walk, stop, pick,
turn, stop, walk, stop, open beak, quack. The routines are `approach`,
`back_off`, `turn_by`, `scan`, `nod`, `shake`, `greet`, `celebrate`, `nudge`,
`fetch`, `patrol`, `dance` and `settle`, and each one is written as a plan a
user could have typed — nothing is hidden in code that you could not have
asked for yourself.

Plain English goes to whatever brain the robot is configured with, along with
the vocabulary — including how long each move takes and which ones hold the
robot, because a planner that doesn't know a kick blocks for half a second
cannot sequence around one. **A routine name and a literal JSON plan need no
model at all**: a duck that can only be choreographed by an LLM is a duck that
stops working offline.

The same two tools (`duck_vocabulary`, `duck_perform`) reach the brain through
the **agent harness**, and the harness is opt-in. The shipped Microduck profile
and preset therefore set it:

```yaml
agent:
  harness:
    enabled: true      # without this the tools are registered nowhere
```

That line is load-bearing, and it was missing until 2026-09-08: the tools are
registered at exactly one call site, inside the harness block in
`castor/api.py`, so a duck whose config left the harness off had a brain that
could describe the duck and could not sequence it. `castor doctor`'s duck
section now warns when a duck config has the harness off, and `castor gaps`
reports it as `duck.tools.gated`.

If you hand-write a duck config rather than letting `castor duck` write one,
copy that block. `castor duck test` and `castor duck do` need no harness; the
choreography tools do.

Three things the performer enforces, because a plan is not a promise:

- **A bad plan is refused whole, before anything moves.** An unknown move in
  step nine means step one never runs.
- **The duck can end the performance.** A fall, a limp, or a battery under
  12% stops the run between steps and stops the duck.
- **Timing is the robot's, not a guess.** A kick waits out its half second, a
  ground pick its three, a roll its one.

Everything still goes out through the driver, so the SafetyLayer sees every
motion, and robotd's own limits apply on top and come back in `limited_by`.
A plan is a proposal. The robot still decides.

## Config reference

| Key | Default | Meaning |
|---|---|---|
| `transport` | `unix` | `unix`, `ssh`, `tcp` or `webrtc` |
| `socket` | `/run/robotd.sock` | robotd socket on the robot |
| `ssh_host` / `ssh_user` / `ssh_port` | — | SSH forward target |
| `local_port` | `7788` | Local end of the SSH forward |
| `host` / `port` | — | Target for `transport: tcp`; for `webrtc`, `port` defaults to `8443` |
| `robot` | — | `webrtc`: pick a producer by `meta.name` when a server lists several |
| `webrtc_video` | `false` | `webrtc`: accept and drain the video track instead of answering `inactive` |
| `webrtc_timeout_s` | `20` | `webrtc`: how long to wait for a control channel |
| `max_vx` / `max_vy` / `max_vyaw` | `0.2` / `0.1` / `1.0` | Envelope at full deflection |
| `intent_hz` | `20` | Intent re-send rate (floored at 2 Hz) |
| `command_ttl_s` | `1.5` | Driver-side deadman |
| `rpc_timeout_s` | `2.0` | Request/response timeout |
| `auto_init` | `false` | Call `robot.init` on connect |

`auto_init` defaults to false on purpose: the duck deliberately does not move on
process start, and OpenCastor doesn't change that.

## The same duck, in Swift

The brain also exists as a standalone Swift package —
[DuckKit](https://github.com/craigm26/duckkit) — so a phone can run the *real*
trained policy with no robot in the room. That is what makes an AR ghost duck
the trained network walking rather than an animation of walking.

It has **zero dependencies**: a hand-written ONNX reader and an ELU multilayer
perceptron in Foundation and arithmetic, which is what lets the real
`alpha_walking.onnx` run under `swift test` on a Raspberry Pi and produce the
same floats an iPhone will. The joint order, home pose, action scaling and
filter coefficients are the same ported numbers this driver uses, and the
kinematic chain is the upstream MuJoCo model vendored as a fixture — so the
tables cannot drift from upstream without a test going red. The forward pass is
proved against onnxruntime's own output to 1e-4.

A second product, `DuckEvidence`, adds swift-crypto for the things that sign:
canonical bytes, a hash-chain fold, and a match record nobody can quietly edit.

```swift
.package(url: "https://github.com/craigm26/duckkit.git", from: "1.0.0")
```

## See also

- Profile: `castor/profiles/pollen/microduck.yaml` (ships with the package)
- Preset: `config/presets/pollen_microduck.rcan.yaml`
- Driver: `castor/drivers/microduck_driver.py`
- WebRTC transport: `castor/drivers/microduck_webrtc.py` (the signalling
  exchange and the `control` datachannel, transcribed with file and line)
- Setup: `castor/microduck.py`
- Health: `castor/doctor.py` (`run_duck_checks`) — `castor doctor`'s duck section
- Upstream: [pollen-robotics/microduck](https://github.com/pollen-robotics/microduck)
