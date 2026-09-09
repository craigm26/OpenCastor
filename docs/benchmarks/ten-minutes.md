# The ten-minute benchmark

```
castor bench ten-minutes --robot microduck [options]
```

The ten-minute goal has been a sentence in a memory note since 2026-08-14. This
is the command that turns it into a JSON file.

It exists because of what
[the Microduck review](../reviews/microduck-ten-minutes-2026-09-08.md) found:
four wire keys were wrong for weeks in shipped code, on the exact path a
hardware guide prints filled-in example output for, and nothing caught it,
because nothing in this project ever ran OpenCastor against a `robotd`. A
benchmark whose C3 checkpoint reads identity off the wire would have failed on
the first push. **This is the machine that has that session on every push.**

---

## What the clock starts and ends at

**The clock starts when the runner writes its first command** to its own
transcript. Not when the box opens, and not when the robot is on Wi-Fi.
Unboxing and charging are not software.

**The clock ends at the first `robot.move` OpenCastor originated from a model's
answer, accepted by robotd with the deadman armed.** Not at "config written",
not at "the brain replied", and not at `castor duck test`, which needs no brain
and is therefore not the thing being measured.

**Wi-Fi onboarding is excluded and reported.** The record carries
`excluded.wifi_onboarding_s` with a value or `null` and a one-line reason. A
benchmark that hides its largest interval is a stopwatch that starts after the
download.

---

## The checkpoints

Seven, each a single timestamp, in order. Each has a defined evidence source
and a defined failure. Six are mandatory.

| # | Checkpoint | Timestamped when | Fails when |
|---|---|---|---|
| **T0** | Clock start | The runner writes its first command to its transcript | never; T0 is the origin |
| **C1** | Package installed | `importlib.metadata.version("opencastor")` returns | the resolved distribution has no `castor/microduck.py` |
| **C2** | Duck reachable | The transport is open and one JSON-RPC round trip (`hello`) has completed | `MicroduckDriver._mode != "hardware"`. **Mock mode is a fail, never a pass** |
| **C3** | Identity read | `hello`, `robot.health` and `robot.policies` have all answered | `healthy` is false, no walking policy is in a slot, the battery is below the choreographer's 12 % floor, **or any required key is absent** |
| **C4** | First LLM turn accepted | The provider returned a plan that parsed and that `DuckChoreographer.expand()` accepted | the plan is refused whole, which is a **correct** refusal and a failed checkpoint |
| **C5** | First `robot.move` accepted, deadman armed | The first `robot.move` is on the wire **and** the intent loop is observed re-sending | no second re-send within `2 / intent_hz`; a deadman not observed to re-arm was never armed |
| **C6** | `robot.stop` observed after silence | The runner stops sending, and the stop is seen on the wire | no stop within `command_ttl_s + 1 / intent_hz + 250 ms` |
| **C7** | *(optional)* First step measured by the robot | `robot.state.odom.position` moved more than 30 mm | the robot did not move, or `state.safety.fallen` went true |

### Why C3 is written that way

C3 reads identity **from the keys the wire actually uses** and **fails when a
key is absent rather than defaulting to `"?"`**. Every key it reads is a
constant in [`castor/bench/wire.py`](../../castor/bench/wire.py), transcribed
from `pollen-robotics/microduck` at rev `5620aa2` with its `duck-ipc-proto`
line beside it. Nothing there is invented; if a key is not in that file, the
benchmark does not read it.

The robot's *name* and *serial* are on a different daemon — `system.info` on
`/run/configd.sock` — so the record reports them as `null` with a
`name_source` sentence saying they were not read. Reading them is optional;
claiming them without reading them is not allowed.

### Why C6 is the checkpoint that matters

Three independent deadmen exist on this stack, and the record names **which one
fired**: the driver's `command_ttl_s` of 1.5 s, duck-studio's bridge at 700 ms,
and robotd's own 500 ms. A run where the driver's fires is healthy. A run where
only robotd's fires means our client stopped feeding it and did not notice,
which is exactly the failure the layered design exists to survive and exactly
the thing a benchmark should name — so it is recorded as a **failure**, not a
pass.

### Why C7 is optional and never assumed

C7 needs a floor and a charged robot, or the simulator. It is the only
checkpoint that proves motion rather than acceptance. A run without it says
`"stepped": null`, never `"stepped": true`.

---

## The pass rule

**All six mandatory checkpoints (C1 through C6), in order, with
`C6.t - T0 < 600 s`.** Not the sum of the steps: wall clock from the first
command, including every retry, prompt and wait.

Three rules keep it honest.

- **A skipped checkpoint is a fail, not an omission.** A run that could not read
  identity fails; it does not pass with a smaller set.
- **Mock targets never pass.** A run against a mock robotd, or with a scripted
  brain, reports `ci-pass` and is never counted as a pass of the ten-minute
  goal. `verdict_reason` says which.
- **Wi-Fi onboarding is excluded and reported**, always with a reason.

### Verdicts

| verdict | meaning | exit code |
|---|---|---|
| `pass` | a real robot or the simulator, a real brain, every mandatory checkpoint in order inside the budget | 0 |
| `fail` | the same run, and something did not hold | 1 |
| `ci-pass` | every checkpoint held, but the target was a mock or the brain was scripted | 0 |
| `ci-fail` | the same, and something did not hold | 1 |

Read `target` beside the verdict: `mock`, `sim` or `real`. **A sim pass is a
pass of this benchmark, not of a duck on a floor.**

---

## The three targets

### The mock, for CI on every push

```sh
castor bench ten-minutes --robot microduck --ci
```

`castor/bench/mock_robotd.py` is a robotd-shaped Unix socket that answers
`hello`, `robot.health`, `robot.subscribe` and `robot.policies` with the real
field names, transcribed from `duck-ipc-proto` with the source line beside each,
and `{"ok": true}` for everything else. **Those four replies are the fixture
that would have caught the review's traps 1, 3 and 4.** Nothing in it has
physics, so C7 is `null` with a reason, and the verdict can only be `ci-pass`.

duck-studio carries the same four fixtures at `bridge/mock-robotd.py`. To run
against that copy instead:

```sh
castor bench ten-minutes --robot microduck --ci \
  --mock-cmd "python3 -u ~/projects/duck-studio/bridge/mock-robotd.py --socket <tmp>"
```

`<tmp>` is replaced with the socket path the benchmark chose.

To prove the benchmark can fail, point the driver at nothing:

```sh
castor bench ten-minutes --robot microduck --ci --transport mock   # ci-fail at C2
```

`--transport mock` is refused without `--ci`, because a run that cannot pass
should say so before it starts.

### The simulator, for honesty without hardware

```sh
castor bench ten-minutes --robot microduck --sim \
  --sim-repo ~/Pollen/microduck --sim-rl ~/Pollen/microduck_rl --floor
```

Pollen's `scripts/duck-sim` runs **the real `robotd` binary** with
`duck_control::sim::RemoteIo` in place of the servo bus, against a MuJoCo body
from `microduck_rl`. Everything above the servo seam is the code a robot runs:
the control loop, the policy, safety, fall detection, kinematics, odometry and
the whole IPC surface. It answers C3 with real numbers and it is the only target
short of hardware that can honestly answer C7. It tells you nothing about a
driver's transport, and Pollen's own doc says so.

It needs, and the benchmark checks each before it starts anything:

1. a `microduck` checkout (`--sim-repo` or `$DUCK_SIM_REPO`);
2. `target/debug/robotd` and `target/debug/robotctl` in it —
   `cargo build -p robotd -p robotctl`, a cold build of a twenty-crate
   workspace;
3. a `microduck_rl` checkout with a `.venv` (`uv sync`) — `duck-sim` reads
   `libonnxruntime` out of it and MuJoCo is the body
   (`scripts/duck-sim:193-200`).

A machine that cannot meet one of those gets a single line naming it, and the
run refuses. It never reports a pass because the thing it measures was absent.

### A real duck, for the number that counts

```sh
castor bench ten-minutes --robot microduck --host 192.168.1.42 --brain anthropic \
  --exclude-wifi 930 --exclude-wifi-reason "cold cargo build of duckctl" --floor
```

`--transport` picks `ssh` (the default when `--host` is given), `tcp` (a bridge
or an existing forward; the port defaults to 7788) or `unix` (OpenCastor running
on the robot itself). `webrtc` is accepted by the parser and refused by the
target with the reason: `MicroduckDriver` has no such transport yet, and
`mediad`'s signalling server on 8443 is the contract to write it against.

Discovery is deliberately not attempted. Three of `castor duck`'s four discovery
methods cannot find a stock duck, and spending the clock on a search that cannot
succeed would be a measurement of the wrong thing. Pass `--host`.

`--floor` asks once before the robot moves, unless `--yes` is given.

---

## The record

One JSON object per run. **Every field is measured or explicitly null.**

```json
{
  "benchmark": "opencastor/ten-minutes",
  "schema_version": 1,
  "robot": "microduck",
  "target": "mock",
  "verdict": "ci-pass",
  "verdict_reason": "all of C1..C6 in order in 1.7 s of 600 s; the target was a mock robotd …",
  "started_at": "2026-09-08T20:11:04.812Z",
  "elapsed_s": 1.677,
  "budget_s": 600.0,
  "environment": {
    "host":       {"platform": "linux", "machine": "aarch64", "python": "3.13.7"},
    "opencastor": {"version": "1!3.1.0", "git_sha": "…", "path": "…", "wheel": null},
    "repo_shas":  {"opencastor-runtime": "…", "pollen/microduck": "5620aa2"},
    "transport":  {"kind": "unix", "target": "/tmp/…/robotd.sock", "mock": true}
  },
  "duck": {
    "hello":    {"api_version": 25, "daemon_version": "…", "revision": null},
    "health":   {"healthy": true, "control_loop": {…}, "battery": {…}, "bus": {…}},
    "policies": {"mode": "walk", "slots": [{"slot": "walk", "path": "…", "origin": "official"}]},
    "obs_len": 61, "action_len": 14,
    "name": null, "serial": null,
    "name_source": "not read; the robot's name and serial are on configd's own socket …"
  },
  "brain": {"provider": "…", "model": "…", "scripted": false, "turns": 1, "latency_s": …},
  "checkpoints": [
    {"id": "C5", "t": 1.55, "ok": true,
     "evidence": {"params": {"vx": 0.12, "vy": 0.0, "vyaw": 0.0},
                  "intent_hz": 20.0, "command_ttl_s": 1.5, "resend_seen_at": 1.59}},
    {"id": "C6", "t": 3.04, "ok": true,
     "evidence": {"fired_by": "driver_ttl", "silence_to_stop_ms": 1489.0}},
    {"id": "C7", "t": null, "ok": null,
     "evidence": {"stepped": null, "reason": "the mock target has no physics …"}}
  ],
  "excluded": {"wifi_onboarding_s": null, "reason": "…"},
  "transcript": [{"t": 0.0, "typed": "…"}],
  "wire": [{"t": 1.55, "dir": "out", "line": "{\"jsonrpc\":\"2.0\",\"method\":\"robot.move\",…}"}],
  "notes": ["…"]
}
```

Two fields carry more weight than the rest.

**`transcript`** is the load-bearing field and the one most likely to be
dropped. Every command the runner or the operator typed, with its timestamp, is
what makes a claimed number auditable by someone who was not there. A run that
reports 412 s and lists four commands is a different artifact from one that
reports 412 s and lists twenty-two.

**`wire`** carries at minimum the C5 move, its first re-send and the C6 stop. It
is the only part of the record that can prove the driver spoke the protocol
rather than logged that it did, which is the exact class of bug the review's
traps 1, 3 and 4 belong to.

**`notes`** is where the benchmark says out loud what it noticed. A run against
today's driver prints one: the wire reported filled policy slots and
`MicroduckDriver.get_policies()` returned `[]`, because `microduck_driver.py:233`
reads `result["networks"]` and `SubscribeResult` has no such key.

---

## Exporting into EvalLog v1

```sh
castor bench ten-minutes --robot microduck --ci --evallog run.evallog.json
```

Microduck Studio already wrote the hard part, and fixing the mapping here stops
two projects inventing two formats.

| EvalLog v1 key | The ten-minute run |
|---|---|
| `eval.task` | `"opencastor/ten-minutes"` |
| `eval.embodiment` | the robot |
| `eval.embodiment_info` | `hello`, name, serial, policy slots, `obs_len`, `action_len`, achieved loop Hz, battery |
| `eval.policy` / `policy_config` | provider, model, tool list, transport, and the honesty notes |
| `eval.git_commit` | the `opencastor-runtime` sha |
| `eval.inspect_robots_version` | a sentence naming what wrote the file; **no inspect-robots ran** |
| `eval.seed` | `null`, with a `seed_note` |
| `samples[].scene_id` | the route: `A-castor-duck`, `B-studio-bridge`, `C-pollen-console` (`--scene`) |
| `samples[].trial_metadata` | one entry per checkpoint: id, `t`, `ok`, evidence |
| `samples[].termination_reasons` | which deadman fired at C6 |
| `stats.started_at` / `completed_at` / `duration_s` | T0, C6, elapsed |
| `stats.total_steps` | `0` when C7 was not run; never seconds times a tick rate |
| `results.metrics` | `{"elapsed_s": …, "checkpoints_passed": 6}` |
| `status` | `"success"` on a pass or ci-pass, `"error"` when a checkpoint failed |

**One refusal is carried over from the app's own list: no success scorer
without a motion-evidence scorer.** A run that passed C1 through C6 and skipped
C7 has proved that commands were accepted, not that a robot moved, so
`success_at_end` is written **only** when C7 actually measured motion, and
`motion_evidence` is written beside it.

The file is written the way upstream re-dumps one — `json.dumps(indent=2,
sort_keys=True)`, no trailing newline — so a parity check can compare bytes.
Verified against the real `inspect-robots==0.58.0`: `read_eval_log` opens it,
their own `json.dumps(_sanitize(log.to_dict()))` reproduces it byte for byte,
and `inspect-robots view` renders it.

---

## Other robots

`--robot rc-car` is a stub. It prints the checkpoint list it would use and
refuses, because every checkpoint above C2 would be answered by our own logs
rather than by the robot: the car has no health reply, no policy slots and no
odometry on the wire, so C3 and C7 have no honest source yet. The duck was
written first because it can answer all seven itself.

Adding a robot is a `RobotSpec` in `castor/bench/ten_minutes.py` plus a target;
the record, the pass rule and the EvalLog export are robot-agnostic already.

---

## Where to run each target

| Target | Where it belongs |
|---|---|
| mock | CI, on every push |
| duck-sim | a nightly |
| a real duck | a recorded bench run whose JSON is committed |

## Proving it

```sh
pytest tests/test_bench_ten_minutes.py
```

The suite runs the whole benchmark against a mock robotd **in this process** —
the real `run()`, the real `MicroduckDriver`, the real `DuckChoreographer`, the
real intent loop, a real Unix socket. The only thing faked is the duck. Every
test that asserts a pass has a sibling asserting the same machinery produces a
fail, because a checkpoint that cannot fail is not a checkpoint: mock mode fails
C2, a deleted `control_loop` fails C3, an `{"ok": true}` reply fails C3, a flat
battery fails C3, a missing walk slot fails C3, a refused plan fails C4, a plan
that never moves fails C5, and a 1 ms budget fails a run whose checkpoints are
all green.
