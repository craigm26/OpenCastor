# Pollen Robotics Microduck

The easiest setup in OpenCastor. One command:

```bash
pip install opencastor
castor duck
```

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

## Where to run OpenCastor

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

## Wire protocol

The driver speaks robotd's contract directly: **JSON-RPC 2.0, one object per line
(NDJSON)**, over `/run/robotd.sock`. This is the same contract `robotctl`, the
gamepad daemon and the phone app use, so OpenCastor is a first-class client.

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

`init()` and `relax()` are maintenance calls that Pollen deliberately keeps off
remote transports. They work over `unix` and over an SSH forward (both trusted
paths), not through the WebRTC/rendezvous bridge.

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

The same two tools (`duck_vocabulary`, `duck_perform`) are registered with the
brain automatically whenever a Microduck is the attached robot, so the model
can discover the verbs mid-conversation and use them.

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
| `transport` | `unix` | `unix`, `ssh` or `tcp` |
| `socket` | `/run/robotd.sock` | robotd socket on the robot |
| `ssh_host` / `ssh_user` / `ssh_port` | — | SSH forward target |
| `local_port` | `7788` | Local end of the SSH forward |
| `host` / `port` | — | Target for `transport: tcp` |
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
- Setup: `castor/microduck.py`
- Upstream: [pollen-robotics/microduck](https://github.com/pollen-robotics/microduck)
