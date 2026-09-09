# The Microduck, and the ten-minute clock

Review date: 2026-09-08
Scope: what it actually costs to get a Pollen Robotics Microduck walking under an
LLM through OpenCastor, and whether the Microduck can be the reference robot for
the ten-minute goal.
Method: read-only. No service was started, stopped or restarted on any robot; no
hardware was moved; no app was built. Two processes were run in the scratchpad
(duck-studio's mock robotd, and an OpenCastor driver against it) and both were
killed. Every claim below cites a file and line, a live PyPI/GitHub response, or
a read-only command run during this review.

Sources read: `/home/craigm26/projects/opencastor-runtime` (castor, docs,
website, config/presets, CHANGELOG), `/home/craigm26/projects/duck-studio`
(bridge, StudioKit, DuckStudio, GATES.md, PLAN.md, README),
`/home/craigm26/projects/duckkit`, `/home/craigm26/projects/duckbench`,
`/home/craigm26/projects/opencastor-ios`, and shallow clones of
`pollen-robotics/microduck` (rev `5620aa2`) and `pollen-robotics/microduck_rl`
(rev `2b581c6`), both **public and cloneable today**, both Apache-2.0, with no
`gh api` needed. Quoted source lines have their dashes normalised to commas to
keep this document free of em-dashes; nothing else in a quote is changed. Pollen
paths are relative to the `microduck` repository root.

---

## Executive summary

The Microduck is the best ten-minute candidate OpenCastor has, and OpenCastor is
currently the slowest way to reach it, on a code path that has never met one.

Everything the RC-car review spent seventeen hours of proposed fixes on is
already solved on this robot, and solved by Pollen rather than by us. The duck
has its own compute, its own 50 Hz control loop, its own trained policies, its
own safety envelope, its own fall detection and its own battery cutoff. There is
no I2C to enable, no PWM channel to uncomment, no chip to detect, no
simulated-wheels flag. A Microduck out of its box is a robot that already walks.
That is a category difference from an RC car, and it is why the duck should be
the reference robot.

Five walls sit between a new owner and a duck walking **under an LLM via
OpenCastor**. Only the last belongs to Pollen.

1. **`castor duck` reads four wire keys that do not exist.** Against a real duck,
   `get_policies()` reads `result["networks"]` from `robot.subscribe`
   (`castor/drivers/microduck_driver.py:233`); upstream's `SubscribeResult` has
   `accepted, walk, stand, unavailable, sitstand, ground_pick, skills` and **no
   `networks`** (`duck-ipc-proto/src/lib.rs:2519-2546`, handler at
   `robotd/src/main.rs:4282-4300`). `health_check()` reads `res["loop"]`
   (`microduck_driver.py:557`); the wire key is `control_loop`
   (`duck-ipc-proto/src/lib.rs:3140`, no serde rename), and its fields are
   `target_hz, achieved_hz, ticks, missed, last_tick_age_ms`
   (`:3152-3166`), not `hz`. `get_battery()` reads `state["battery"]`
   (`microduck_driver.py:780`); `RobotState` carries no battery at all
   (`:3317-3365`; `docs/robot/cheatsheet.md:51-53` says so explicitly, "because
   none of it is on the state stream"). So `castor duck`'s own success screen
   prints `loop ? Hz` and `none loaded` on a healthy duck, and the
   choreographer's documented battery abort cannot fire.
2. **`castor duck` cannot find a stock duck.** Its hostname ladder is
   `duck.local, duck-01.local, microduck.local, duckling.local`
   (`castor/microduck.py:73-78`). A stock board's hostname is `radxa-zero3`
   (`docs/design/webrtc-console.md:27`: "the hostname on **every** board flashed
   from one image"), and `configd`'s default robot name is `duck-<4 hex>` from
   the SoC serial (`configd/src/identity.rs:84`). The duck publishes **no mDNS at
   all** (no Avahi or zeroconf registration anywhere in the Pollen repo;
   `scripts/dev-push.sh:80` and `scripts/provision-board.sh:8` both say mDNS on
   this image is unreliable and route around it). So three of `castor duck`'s
   four discovery methods cannot work, and the fourth, `duckctl ip`, is a Rust
   binary you build from a clone.
3. **`castor duck` ends by printing a command that cannot run.** It writes
   `~/.config/opencastor/<name>.rcan.yaml` (`castor/microduck.py:624`) and tells
   the user to run `castor run --config <that file>` (`castor/cli.py:4129`,
   `:4136`). `cmd_run` rejects any path ending in `.rcan.yaml` at
   `castor/cli.py:207` via the guard at `:57-67` and exits 1. Verified by running
   the guard during this review. `castor duck --start` takes the same path
   (`castor/cli.py:4142-4147`) and dies the same way. The documented escape,
   `castor migrate ... -o ROBOT.md`, was run during this review on a real
   generated duck config and produced a 53-line manifest with **no `drivers`
   block**; `ComponentRegistry.get_driver` then returns `None`
   (`castor/registry.py:201-202`), silently converting a configured duck into no
   robot.
4. **The duck's LLM tools are behind a flag no shipped profile sets.**
   `docs/hardware/microduck.md:227-229` says `duck_vocabulary` and `duck_perform`
   are registered "automatically whenever a Microduck is the attached robot".
   They are registered at `castor/api.py:713`, inside a block gated on
   `agent.harness.enabled` (`castor/api.py:707-709`), which is opt-in and which
   neither `castor/profiles/pollen/microduck.yaml` nor
   `config/presets/pollen_microduck.rcan.yaml` sets. The one LLM path that does
   work, `castor duck do`, calls `get_provider({})` (`castor/cli.py:4192`), which
   defaults to `google` (`castor/registry.py:176`), ignoring both
   `castor duck --brain` and the profile's own `agent.provider: anthropic`.
5. **The duck arrives without Wi-Fi and the only tool that fixes that is a Rust
   build.** There is no hotspot or AP mode anywhere in the Pollen repo. A duck
   that has never seen a network is reachable over BLE only, through `duckctl`,
   which you run as `cargo run -q -p duckctl` or install with
   `cargo install --path duckctl` (`docs/robot/duckctl.md:15-33`). Pollen calls
   `duckctl` "the phone app's stand-in" (`:3-4`), and
   `docs/design/app-path-design.md:19-20` records that as of 2026-08-04
   "`net.connect` has not been driven over BLE, and nothing has been tested with
   a phone rather than a laptop."

Underneath all five is the fact this review exists to report:
**Pollen already ships a network transport a phone can open, and OpenCastor uses
neither of them.** `mediad` runs a WebRTC signalling server on `0.0.0.0:8443` and
serves a full drive console over HTTP on `0.0.0.0:8080`
(`mediad/src/main.rs:31-38`, `:69-76`; the axum server is `mediad/src/web.rs:79`,
one route, `GET /`), enabled on every install and every update
(`scripts/install.sh:783-784`, `hooks/postinstall:161`), carrying move, head,
look, pose, mouth, do, sound, enable, init, relax, stop, subscribe, policy load
and the ToF stream over the datachannel (`mediad/src/route.rs:63-146`).
OpenCastor instead opens an `ssh -L 7788:/run/robotd.sock` forward
(`castor/drivers/microduck_driver.py:242-286`), which costs an SSH key, a `robot`
group edit and a reboot before anything moves.

Current honest wall clock, box to a duck walking under an LLM through OpenCastor:
**never on `castor run`**, and about **25 to 60 minutes** on `castor duck do`
once a Google key is in the environment and the owner has typed `--host <ip>`,
of which OpenCastor owns maybe four minutes and Wi-Fi onboarding owns most of the
rest. Box to a duck walking under a human, using only what Pollen ships:
**about two minutes with a gamepad**, and under a minute in a browser if the duck
is already on Wi-Fi.

---

# What a new owner actually has

## In the box

A Microduck: about 25 cm, about 800 g, fifteen Dynamixel-protocol servos on
`/dev/ttyS2` at 1 Mbps (`robotd-params/src/lib.rs:1628-1629`), a camera, a ToF
depth sensor, two IMUs, an articulated beak, and a removable NP-F550 pack good
for about an hour (`docs/hardware/microduck.md:44-52`). Its compute is its own: a
Rockchip RK3566, quad Cortex-A55, 1 GB RAM, 32 GB storage, running Armbian 26.2.x
on a Radxa Zero 3 / 3W with a Debian 13 userland
(`robotd/systemd/robotd.service:17-18`, `scripts/install.sh:7-8`,
`docs/design/app-path-design.md:12`).

This is what makes the duck the right reference robot. **There is no "where does
the brain go" question at unboxing.** The robot boots into a working robot.
Compare the RC car, where the box contains a chassis, an ESC, a servo, a breakout
board and a Pi that has never met any of them.

Joint order is fixed and public: fifteen names, mouth at index 9
(`duck-ipc-proto/src/lib.rs:396-412`). The policy contract is 61 observations to
14 actions at 50 Hz (`duck-ipc-proto/src/lib.rs:313`, `:317`, `:321`), and
`castor/profiles/pollen/microduck.yaml` `physics.dof: 14` matches.

## What the duck does on its own, before anything is installed

Seven units ship, all `WantedBy=multi-user.target`, and `hooks/postinstall:161`
runs `systemctl enable --now` on every unit that has an `[Install]` section, on
every update:

- **`robotd`** owns the motor bus and the 50 Hz loop, listening on
  `/run/robotd.sock` at mode `0o660` with `Group=robot`
  (`robotd/src/main.rs:60`, `:3232`, `robotd/systemd/robotd.service:26`, `:49`).
  `Restart=always`, `RestartSec=2s` (`:60-61`), and deliberately no network
  dependency (`:20-22`).
- **`mediad`** streams the head camera and runs the WebRTC signalling server, and
  serves the console (ports below).
- **`padd`** reads a paired gamepad at 50 Hz.
- **`btd`** answers over BLE, which is how a duck that has never seen a network is
  reachable at all.
- **`configd`** owns Wi-Fi and identity; **`updaterd`** owns updates and the
  Hugging Face account; **`tofd`** owns the depth sensor.

It walks, rolls on wheels, picks things up off the floor, gets back up when
knocked over, sits, kicks with either leg, does a forward roll, and quacks in a
voice derived from its own SoC serial (`README.md:35-66`,
`hooks/postinstall:69-72` in the Pollen repo). None of that needs training, an
LLM, or anything installed on a second machine. **The duck's out-of-box
capability is what the ten-minute clock should be measured against, not something
the clock has to deliver.**

Two caveats a review has to state.

**The policies are not in the box in the software sense.** They are nine ONNX
files fetched from Hugging Face at install and at every update
(`scripts/seed-policies.sh:47-49`, `:61`:
`alpha_walking, alpha_stand, alpha_sitstand, alpha_ground_pick, ball_kick_left,
ball_kick_right, roller, roller_crouch, roulade`), landing in
`/opt/robot/policies/current` (`robotd-params/src/lib.rs:40`). A board that
cannot reach the Hub gets **no gait**, and that is the accepted shape rather than
an oversight (`scripts/seed-policies.sh:25-31`); the robot holds its pose and
reports degraded. `robot.init` still works with no policy at all, and the proto
says why: "'stand up' is a reasonable thing to ask of a robot with no walking
network" (`duck-ipc-proto/src/lib.rs:487-491`).

**The console depends on GStreamer plugins that are not in Debian.**
`mpph264enc` and `webrtcsink` are built in CI from pinned sources at
`pollen-robotics/microduck-gst-plugins` (`Cargo.toml:59-69`) and installed by a
separate step, `sudo /usr/local/sbin/robot-setup-gstreamer`
(`scripts/install.sh:787`). A retail duck presumably ships with them; a board you
provision yourself may not, and the symptom is `mediad` failing to start and
taking the WebRTC control surface down with the video, because the control
datachannel is bundled with the video track
(`mediad/systemd/mediad.service`, the "consequence of the camera being on by
default" comment).

## Where the Pi or laptop comes in, and where the phone comes in

Later than you would expect, and for less than you would expect.

The duck does not need a second computer to walk. It needs one to be *told* what
to walk toward by a model, which is OpenCastor's whole contribution, and the docs
are right that it should be off-board: 1 GB of RAM and a 50 Hz loop on four A55
cores is not where a Python brain belongs
(`castor/drivers/microduck_driver.py:17-22`, `docs/hardware/microduck.md:56-60`).

The phone comes in through a browser today and through no OpenCastor app at all.
`mediad` serves a page at `http://<robot>:8080/`, compiled into the binary
(`mediad/src/web.rs:56`, `const PAGE: &str = include_str!("../webclient/index.html")`),
with the camera, two on-screen pads plus `W`/`A`/`S`/`D` and `Q`/`E` to drive at
0.3 m/s and 1.5 rad/s, a drag on the picture to look at a point,
enable/init/relax/stop/shutdown, the voice bank as a menu, the skills as a menu
the robot fills from `robot.policies`, and the state stream at 2 Hz beside
`robot.health` (`docs/robot/duckctl.md:108-131`). Nothing to install and nothing
to serve.

The OpenCastor iOS app has no Microduck path: the only duck-shaped files in it
are `DuckSoccerView.swift`, `GhostDuckView.swift` and `DuckLabView.swift`, which
are games and a policy lab, and `RobotDiscovery.swift` mentions no duck. Microduck
Studio is the duck phone app, and it cannot drive a duck either (Route B).

## The out-of-box gap, which is the real ten-minute problem

**A duck fresh from the box is not on your Wi-Fi, and every network route starts
there.** Wi-Fi is NetworkManager over D-Bus, driven by `configd`
(`configd/src/nm.rs:1-2`), and the board has to be migrated off Armbian's
netplan/wpa_supplicant stack first by `scripts/migrate-network.sh`
(`docs/design/app-path-design.md:48-60`). There are exactly three ways in, and
**none of them is a hotspot**: greps for `hotspot`, `AP mode` and `access point`
across `configd/`, `btd/` and `scripts/` return only NetworkManager's own bit
constants.

1. **Serial console or ssh, then `robotctl net connect`**
   (`docs/robot/cheatsheet.md:731-751`). Needs a cable or a network you already
   have.
2. **BLE from a laptop, `duckctl wifi connect`**
   (`docs/robot/duckctl.md:203-222`; up to 45 seconds to answer). This is the
   real answer for a duck that has never seen a network, and `duckctl` is not a
   download: it is `cargo run -q -p duckctl -- --name <robot> info` or
   `cargo install --path duckctl` (`:15-33`). On a laptop with no Rust toolchain
   that is a `rustup` install plus a cold `cargo build` of a twenty-crate
   workspace: **ten to twenty-five minutes, and it is the single largest block on
   every route in this document.**
3. **A phone app, which does not exist in either repository.** It is named as
   future work throughout: `docs/robot/duckctl.md:3-4` calls `duckctl` "the phone
   app's stand-in", `padd/src/main.rs:7` calls the IPC "the path the app, the SDK
   and any remote client will use", and `docs/design/app-path-design.md:19-20`
   is blunt: "`net.connect` has not been driven over BLE, and nothing has been
   tested with a phone rather than a laptop."

A shipped retail duck may arrive with a better answer. This review cannot verify
that from a repository and says so rather than guessing. Everything below reports
Wi-Fi onboarding as a separate, excluded interval with its own number.

The BLE pairing PIN, for the record, is a shared `000000`
(`configd/src/store.rs:36`), and the PIN check lives at the application layer
rather than the link layer because BLE cannot express a fixed printed passkey
(`duck-ipc-proto/src/lib.rs:930-937`).

---

# Route A: `pip install "opencastor>=3.1"` then `castor duck`

This is the route the hardware guide and the live website both show. The install
half now works, which is a real improvement over the RC car's story. The run half
does not, and the discovery and identity halves have never met a duck.

## The install resolves correctly today

Verified against live PyPI during this review:

| Check | Result |
|---|---|
| `pip download --no-deps opencastor` resolves to | `1!3.1.0` |
| `castor/microduck.py` in that wheel? | **Yes** |
| `castor/drivers/microduck_driver.py`? | **Yes** |
| `castor/microduck_choreography.py`? | **Yes** |
| `castor/profiles/pollen/microduck.yaml`? | **Yes** |
| `config/presets/pollen_microduck.rcan.yaml`? | No, and that is fine |
| Core dependency count | 26, including `zeroconf`, `rich`, `streamlit`, `opencv-python-headless`, `pygame` |

The missing preset does not matter: `microduck.profile_path()` tries the repo
preset first and the packaged profile second (`castor/microduck.py:512-521`), and
the packaged one ships. The epoch `1!` is what makes a bare `pip install
opencastor` pick 3.1.0 over the `2026.*` CalVer line; the RC-car review's fix 1
is confirmed working from the outside.

One documentation lag: `docs/hardware/microduck.md:6` still says
`pip install opencastor` where `README.md:45` says `pip install "opencastor>=3.1"`
and explains at `:48` why the pin is load-bearing. The live guide at
`opencastor.com/docs/hardware/microduck` (200 OK, served from
`docs.opencastor.com`, mkdocs, not from `website/`) also shows the bare form.
Both work now. Both stop working the day anyone yanks the epoch.

## Timed walk

Laptop, decent connection, duck unboxed and charged.

| Step | What the owner does | Source | Time |
|---|---|---|---|
| 1 | Power on, wait for boot | BLE "does not exist for the first ~73s of a boot" (`mediad/src/route.rs:58-60`) | 1.5 min |
| 2 | Install Rust, clone `microduck`, `cargo install --path duckctl` | `docs/robot/duckctl.md:15-27` | **10 to 25 min** |
| 3 | `duckctl wifi connect <ssid> --psk ...` | `docs/robot/duckctl.md:218-222` | 1 min |
| 4 | `pip install "opencastor>=3.1"` | `README.md:45` | 2 to 5 min on a laptop, 5 to 12 on a Pi |
| 5 | `castor duck` discovery. **Finds nothing**, because the hostnames do not match and the duck publishes no mDNS. | `castor/microduck.py:73-78`, `:231-271` | **5.3 s measured**, then a dead end |
| 5b | Read the printed hint, run `duckctl ip`, retry as `castor duck --host <ip>` | `castor/cli.py:4030-4035` | 1 min |
| 6 | `ssh-copy-id radxa@<ip>`, which prompts for a password nothing in OpenCastor's docs states | `castor/cli.py:4043-4049`, `castor/microduck.py:451-454` | 0.5 min, or blocked |
| 7 | `sudo usermod -aG robot $USER` **and a reboot** | `castor/microduck.py:457-460` emits `ssh ... sudo reboot` | 2 min, of which 1.5 is the second boot |
| 8 | `castor duck --host <ip>` again. Writes the config. Prints `loop ? Hz` and `none loaded` (see below). | `castor/cli.py:4066-4103` | 0.5 min |
| 9 | `castor duck test`: stand up, walk 1.5 s at 0.06 m/s, stop. **No brain needed.** | `castor/cli.py:3981-4018` | 0.5 min |
| | **Subtotal: a duck walking, no LLM** | | **19 to 38 min** |
| 10 | `castor run --config ~/.config/opencastor/duck.rcan.yaml` | `castor/cli.py:4136` | **exits 1** |
| 11 | `castor migrate ... -o ROBOT.md`, as the error says | `castor/cli.py:63-64` | 0.2 min, and the driver is gone |
| 12 | `castor duck do "walk forward and quack"`, with `GEMINI_API_KEY` set | `castor/cli.py:3919-3978` | 0.5 min if the key exists |
| | **Subtotal: first LLM turn that moves the duck** | | **20 to 39 min, and only via `duck do`** |

Steps 2 and 7 are 12 to 27 of those minutes and neither is OpenCastor's code.
Steps 5, 8 and 10 are OpenCastor's, are cheap to fix, and are the difference
between a product and a demo.

## The four wire keys, which are the real story

The Microduck driver is a good piece of work: correct NDJSON framing, correct
notification-versus-request split, correct last-writer-wins intent slots, a real
driver-side deadman, and a wrapper for nearly the whole `robot.*` surface. It has
also, on the evidence, never been run against a real `robotd`. Four reads do not
match the wire, and all four fail silently.

| OpenCastor reads | Upstream wire key | Evidence | What a real duck produces |
|---|---|---|---|
| `result["networks"]` from `robot.subscribe` (`microduck_driver.py:233`) | none. `SubscribeResult` is `accepted, walk, stand, unavailable, sitstand, ground_pick, skills` | `duck-ipc-proto/src/lib.rs:2519-2546`; handler `robotd/src/main.rs:4282-4300` | `get_policies()` always `[]`; `castor duck` prints "none loaded" |
| `result["status"]` in the connect log (`microduck_driver.py:236`) | `accepted` | same | `status=None` in the log line |
| `res["loop"]` from `robot.health` (`microduck_driver.py:557`) | `control_loop`, no serde rename | `duck-ipc-proto/src/lib.rs:3140` | `castor duck health` prints `loop ? Hz (0 missed)` |
| `loop["hz"]` (`castor/cli.py:3907`) | `LoopHealth` is `target_hz, achieved_hz, ticks, missed, last_tick_age_ms` | `duck-ipc-proto/src/lib.rs:3152-3166` | `?` even if the outer key were right |
| `state["battery"]` from `robot.state` (`microduck_driver.py:780`) | `RobotState` has **no** battery | `:3317-3365`; `docs/robot/cheatsheet.md:51-53` | `get_battery()` always `{}` |

The last one is the one with a safety consequence. `docs/hardware/microduck.md:235-237`
promises "The duck can end the performance. A fall, a limp, or a battery under
12% stops the run between steps and stops the duck." The fall and limp guards
work: `state.safety.fallen` and `.limp` are real keys
(`duck-ipc-proto/src/lib.rs:3491-3494`) and
`castor/microduck_choreography.py:622-625` reads them correctly. **The battery
guard cannot fire**, because `castor/microduck_choreography.py:628-631` reads
`state["battery"]["percent"]` from a stream that has no battery in it. The number
is available, on `robot.health`'s `battery.percent`
(`duck-ipc-proto/src/lib.rs:3263-3266`), which the driver already calls; it is
simply read from the wrong place.

The likely cause is a conflation that is easy to make and easy to fix:
`RobotState` genuinely has a `loop` with `hz` and `missed`
(`duck-ipc-proto/src/lib.rs:3512-3516`). Those field names were read off the
state stream and applied to the health reply.

Two smaller mismatches, recorded for completeness: `get_odometry()`'s docstring
says `{"position": [x, y], "yaw": θ}` (`microduck_driver.py:783`) where
`OdomState.position` is `[f64; 3]` (`duck-ipc-proto/src/lib.rs:3472-3476`); and
`docs/hardware/microduck.md:21` shows example output reading
`healthy · loop 49.8 Hz · battery 64% · walk.onnx, sit.onnx`, which the code as
written cannot produce.

## Why step 10 exits 1

`castor duck` writes YAML:

```
castor/microduck.py:624   target = Path(path) if path else config_dir() / f"{robot_name}.rcan.yaml"
```

and prints `castor run --config {path}` at `castor/cli.py:4129` and `:4136`.
`cmd_run`'s first act is:

```
castor/cli.py:205-208
    # v3.0 hard-cut: reject legacy .rcan.yaml input with migration guidance
    _target = getattr(args, "manifest", None) or getattr(args, "config", None)
    if _legacy_rcan_yaml_guard(_target):
        raise SystemExit(1)
```

and the guard rejects on the suffix alone (`castor/cli.py:61-66`). Run during
this review against the exact path `castor duck` prints, it returned `True` and
wrote "castor: legacy .rcan.yaml input is no longer supported in v3.0+". The two
halves of the product disagree about the config format and the half that
generates is on the losing side.

`castor gateway` does **not** call the guard (`castor/cli.py:265-278`), so
`castor gateway --config <duck>.rcan.yaml` is a live workaround no document
mentions.

## Why step 11 does not rescue it

`castor migrate` is documented as "Deprecated at ship (opencastor 3.0.0).
Scheduled for removal in 3.1.0" (`castor/cli.py:2078`), and we are on 3.1.0.
Executed during this review on a config generated by
`microduck.build_config(host=..., user=..., transport="ssh")`, it emitted a
53-line `ROBOT.md` with `metadata`, `network`, `agent` and `safety`, and **no
`drivers` key**. The duck's `protocol: microduck`, `transport: ssh`, `ssh_host`
and the whole velocity envelope were dropped. Then:

```
castor/registry.py:201-202
        if not config.get("drivers"):
            return None
```

The migration turns a configured Microduck into a brain with no body, and nothing
in the path says so.

## The LLM story on this route

**`castor duck do`** is the one that works, and its design is good: it resolves a
request three ways, cheapest first, a routine name, a literal JSON plan, or
English handed to a model (`castor/cli.py:4151-4208`), and the first two need no
model at all, stated as such at `:4156-4157`. The third calls
`get_provider({})` at `:4192`. An empty dict means
`config.get("provider", "google")` (`castor/registry.py:176`), so the model is
Gemini regardless of `castor duck --brain ollama` or the profile's
`agent.provider: anthropic`, and the setup command's own closing advice to run
`castor login` (`castor/cli.py:4131-4136`) buys nothing here.

**The choreography tools** are the better mechanism and are effectively dark.
`register_duck_tools` registers `duck_vocabulary` and `duck_perform`
(`castor/microduck_choreography.py:641-680`), and
`castor/api.py:439-459` correctly detects a `MicroduckDriver` by class name. Its
only call site is `castor/api.py:713`, inside:

```
castor/api.py:707-709
    _harness_enabled = _harness_cfg.get("enabled", False)  # opt-in
    if _harness_enabled:
```

No shipped duck config sets `agent.harness.enabled`. The guide's claim that the
tools register automatically is true of the function and false of the product.

---

# Route B: Microduck Studio plus the bridge

**This route cannot drive a duck today, at any number of minutes.** The app has
never sent a `robot.move` to a robot.

## What exists, and it is a lot

`bridge/microduck-bridge.py` is a complete, tested relay: Python 3.9 stdlib only,
socket `/run/robotd.sock`, port **7788**, deadman **700 ms**
(`/home/craigm26/projects/duck-studio/bridge/microduck-bridge.py:49-53`). The
first line must be `{"microduck":"v1","token":"..."}`, compared with
`hmac.compare_digest` (`:83-101`); the token file is refused if group or other
readable (`:72-73`); the robotd socket is not opened until the hello passes
(`:380-381`). The deadman sends one `robot.stop` with `id: "bridge-deadman"`
after silence and re-arms on **any** byte from the client (`:292-297`,
`:327-328`). `bridge/install.sh` mints a token, installs a systemd **user** unit
needing no root (`:37-44`), and registers an Avahi `_robotd._tcp` record when
`/etc/avahi/services` is writable (`:51-57`). `bridge/test_bridge.py` is 17 tests
against a recording mock, stdlib only, no duck. (`bridge/README.md:55` and `:58`
still say "9 tests"; stale by eight since the `policy.install` work landed.)

The kit half is done. `DuckTransportKind.bridge`'s reach already permits
`hello, move, head, look, stop, enable, initPose, relax, state, installPolicy`
and refuses `pairingPin, setPairingPin, update`
(`StudioKit/Sources/StudioKit/DuckPeer.swift:211-217`).
`BridgeHandshake.swift:26` writes the exact bytes the Python expects, and `:95`
pins `_robotd._tcp`, matched against the Avahi record by
`StudioKitTests/BridgeHandshakeTests.swift:76-77`.

## Why it still cannot drive

The Control tab's peer is concretely a bench:

```
DuckStudio/Sources/DriveView.swift:2841-2846
    @MainActor private func requirePeer() throws -> BenchPeer {
        if let peer { return peer }
        guard let made = try benches.makePeer() else { throw DuckBench.Refusal.empty }
        peer = made
        return made
    }
```

with `@State private var peer: BenchPeer?` at `:262`. The only `robot.move` in the
app target is `DriveView.swift:3198`, `try await peer.notify(.move(go.command))`,
and that peer posts HTTP to `duckbench.mjs`. `BridgeClient` has one consumer and
makes one peer call: `RobotBridgeView.swift:200`,
`client.peer.call(.installPolicy(request))`. The app can put a policy file onto
the duck's disk and nothing else.

The app says so twice. `RobotBridgeView.swift:6-16`: the bridge "has relayed
robotd's socket to TCP since build 46, and nothing in the app ever dialled it."
And on screen, `StudioKit/Sources/StudioKit/DriveVenue.swift:171-176`: "No stick
here yet, and the reason is the link."

The blocker is one bench-only member, not the transport. `await peer.live`
(`DriveView.swift:3199`, `:3289`) is `BenchPeer.live: DuckDrive.Live?`
(`StudioKit/Sources/StudioKit/BenchPeer.swift:92`) and has no representation in
`DuckPeer` or `DuckState`. Re-typing `requirePeer()` to `any DuckPeer` is about
eight lines in DriveView plus `BenchStore.makePeer`'s return type
(`BenchStore.swift:291`); deciding what replaces `live` is the real work. One
focused day.

There is also no Bonjour browse: `NSBonjourServices` is declared at
`DuckStudio/project.yml:225-230` and nothing in either target browses, so the app
must be pointed at a typed host (`RobotBridgeView.swift:48`).

## Timed walk

| Step | What the owner does | Source | Time |
|---|---|---|---|
| 1-3 | Same boot and Wi-Fi as Route A | | 12 to 27 min |
| 4 | Get the app (TestFlight build 65) | memory `duck-studio-app` | 2 min |
| 5 | SSH to the duck, run `bridge/install.sh` | `bridge/README.md:9-11` | 1 min, after Route A steps 6 and 7 |
| 6 | Type the duck's address and token into Robot > Bridge | `RobotBridgeView.swift:48`, `:179` | 1 min |
| 7 | Install a policy file | `RobotBridgeView.swift:200` | 0.5 min |
| 8 | Drive it | | **not possible** |

**Time to a duck walking under this app: unbounded.**

---

# Route C: what Pollen already ships

The fastest route to a moving duck by a wide margin, involving no OpenCastor and
no LLM.

## C1: the gamepad

`padd` runs at 50 Hz reading a paired gamepad, `--max-linear` 0.3 m/s,
`--max-linear-backward` 0.3, `--max-angular` 1.5 rad/s, `--deadzone` 0.1,
`--max-head` 2.5 rad (`padd/src/main.rs:139-163`), with a 100 ms heartbeat that
keeps robotd's deadman fed (`:781`). Pairing is `sudo robotctl pad pair`, once per
pad (`padd/src/main.rs:51-56`, `docs/robot/pair-a-gamepad.md`). No network at all.
From a charged duck and a paired pad this is **under two minutes**, and it is
what the product's own README opens with: "It walks. Pick up a gamepad and drive."

## C2: the browser console

Once the duck is on Wi-Fi, `http://<duck>:8080/` is a full drive console served by
`mediad` itself, embedded in the binary, with nothing to install on the client
(`mediad/src/main.rs:69-76`, `mediad/src/web.rs:3-5`, `:56`,
`docs/robot/duckctl.md:108-131`). It works from an iPhone. `duckctl open` finds
the robot and opens it (`docs/robot/duckctl.md:113`).

The datachannel behind it permits, exhaustively and by design
(`mediad/src/route.rs:63-146`): `RobotMove`, `RobotHead`, `RobotLook`,
`RobotPose`, `RobotMouth`, `RobotDo`, `RobotSound`, `RobotTheremin`,
`RobotHealth`, `RobotMode`, `RobotSubscribe`, `RobotEnable`, `RobotInit`,
`RobotRelax`, `RobotStop`, `RobotShutdown`, `RobotLoadPolicy`,
`RobotReloadPolicies`, `RobotPolicies`, `RobotModel`, `RobotSkills`,
`RobotSetSkill`, `PadBind`, plus `tof.stream` and `head_imu.stream`. It refuses
`RobotSetMode` (a claim about hardware only someone in the room can make), the
chorale, the pairing PIN, and the update mutations.

**This is the transport OpenCastor should be using and is not.**

Two things a review must state plainly:

- **It does not authenticate.** `mediad/src/main.rs:9-13`: "anyone who reaches
  the signalling port can drive the robot and see its camera. That is a decision,
  not an omission", because the pairing PIN is a shared `000000` and "a gate would
  add a step to every connection and prove nothing". Both ports bind `0.0.0.0`
  (`mediad/src/main.rs:31-33`). Any OpenCastor documentation that tells an owner
  to put a duck on a home LAN inherits that fact and should say so.
- **It is off if the camera or the plugins are.** The control datachannel is
  bundled with the video track, so a duck with no working camera fails to start
  `mediad` and loses its control surface with the video; `camera = false` in
  `[media]` streams a test pattern so the pipeline starts.

## C3: off-LAN

`mediad` also registers with a rendezvous service so a signed-in duck is reachable
from outside its LAN, with TURN credentials from a Hugging Face proxy
(`mediad/src/main.rs:41-59`, `mediad/src/relay.rs`, `mediad/src/turn.rs`).
Sign-in is `sudo robotctl account login`, a device-code flow
(`docs/robot/cheatsheet.md:760-780`), or `duckctl account login --no-open`, which
`docs/robot/duckctl.md:355` calls "the one thing that works on a robot that has
never seen a network".

## Timed walk

| Step | What the owner does | Time |
|---|---|---|
| 1 | Power on, wait for boot | 1.5 min |
| 2a | Pair a gamepad and drive | 0.5 min → **2 min total** |
| 2b | Get on Wi-Fi (the `duckctl` build tax, or whatever ships in the box) | 1 to 25 min |
| 3b | Open `http://<duck>:8080/` on a phone and drive | 0.5 min |
| | **Duck walking under a human** | **2 min (pad), or 3 to 27 min (browser)** |

---

# The traps, ranked by how silently they fail

| # | Trap | Evidence | What the owner sees |
|---|---|---|---|
| 1 | `get_battery()` reads a key `robot.state` does not have, so the choreographer's battery abort never fires | `microduck_driver.py:780`, `microduck_choreography.py:628-631` vs `duck-ipc-proto/src/lib.rs:3317-3365` | A documented safety guard that has never triggered and never will |
| 2 | `castor migrate` drops the `drivers` block | run during this review; `castor/registry.py:201-202` returns `None` | A brain that answers and a duck that never moves, no error anywhere |
| 3 | `get_policies()` reads `networks`, which is not a `SubscribeResult` key | `microduck_driver.py:233` vs `duck-ipc-proto/src/lib.rs:2519-2546` | "none loaded" on a duck with nine policies loaded |
| 4 | `health_check()` reads `loop`, the wire says `control_loop`, and the inner field is `target_hz` not `hz` | `microduck_driver.py:557`, `cli.py:3907` vs `duck-ipc-proto/src/lib.rs:3140`, `:3152-3166` | `loop ? Hz (0 missed)` on a duck running at 50 Hz |
| 5 | The duck's LLM tools are behind `agent.harness.enabled`, which no shipped profile sets | `castor/api.py:707-713` vs `castor/profiles/pollen/microduck.yaml` | The model can chat about the duck and cannot sequence it, and the docs say it can |
| 6 | `castor duck do` uses Gemini whatever brain you chose | `castor/cli.py:4192`, `castor/registry.py:176` | "The brain could not answer" after `castor login` said you were signed in |
| 7 | `MicroduckDriver` degrades to mock on any connect failure and keeps answering | `microduck_driver.py:208-213`, `:463-465`, `:535-536` | Every command succeeds; `health_check()` returns `ok: True` in mock mode |
| 8 | Discovery cannot find a stock duck: wrong hostnames, no mDNS on the robot, and the one working method needs a Rust build | `castor/microduck.py:73-78`, `:231-271`, `:184-191` vs `docs/design/webrtc-console.md:27`, `configd/src/identity.rs:84`, no Avahi in the Pollen repo | "nothing found", on a duck two metres away |
| 9 | `castor duck test` walks at `vx=0.06 m/s`, a fifth of the pad's speed | `driver.move(0.3, 0.0)` at `cli.py:4012` times `max_vx` 0.2 (`microduck_driver.py:89`); **measured on the wire during this review** | "it walks" prints, the duck barely moves, and the owner compares it to the gamepad |
| 10 | `castor duck` writes a config `castor run` refuses | `microduck.py:624` vs `cli.py:57-67`, `:207` | The refusal is printed by the tool that just wrote the file |
| 11 | The fix for the `robot` group is a **reboot**, mid-setup | `castor/microduck.py:457-460` | Ninety seconds the owner did not know they were spending |
| 12 | `castor doctor` and `castor gaps` know nothing about ducks | zero matches for `duck` or `robotd` in `castor/doctor.py` and `castor/gaps.py` | A clean bill of health on a duck OpenCastor cannot reach |
| 13 | `mediad` is unauthenticated on `0.0.0.0` and OpenCastor never says so | `mediad/src/main.rs:9-13`, `:31-33` | Nothing, until a housemate drives the duck |
| 14 | `castor duck` prints the health failure as a warning and writes the config anyway | `castor/cli.py:4076-4081` | "Duck ready." on a duck that did not answer |
| 15 | A duck that could not reach Hugging Face at install has no gait, non-fatally | `scripts/seed-policies.sh:25-31` | A duck that stands, holds its pose, and will not walk |
| 16 | The hardware guide's install line lacks the `>=3.1` pin the README calls load-bearing | `docs/hardware/microduck.md:6` vs `README.md:45-48` | Nothing today; a broken install the day the epoch is yanked |
| 17 | The app's Bonjour key is declared and nothing browses | `DuckStudio/project.yml:225-230`, no `NWBrowser` anywhere | An address to type, in an app whose plist promises discovery |
| 18 | `GATES.md:126-128` still says the app "currently has none, deliberately" of a bridge | it has had one since build 46 | A decision document that no longer describes the product |

Traps 1, 3 and 4 are one bug with three faces, and together they are the finding
of this review: **`castor duck` has never been run against a real `robotd`.** The
evidence is not an accusation, it is arithmetic. Four reads, four keys, none of
which exists on the wire, and every one of them on the exact path
`docs/hardware/microduck.md:21` prints a filled-in example of. A single session
with a duck, or with `scripts/duck-sim`, would have caught all four in the first
minute. That is also why the benchmark in the second half of this document is
worth building: it is a machine that has that session on every push.

Trap 9 deserves a paragraph, because it is the one that loses a demo.
`castor duck test` is the command the setup flow offers as proof
(`castor/cli.py:4137`). It calls `driver.move(0.3, 0.0)` (`cli.py:4012`), and
`_move` scales that by `max_vx = 0.2` m/s (`microduck_driver.py:457`, `:89`).
During this review, with an OpenCastor driver talking to duck-studio's
`bridge/mock-robotd.py`, the wire carried exactly
`{"vx": 0.06, "vy": 0.0, "vyaw": 0.0}` at 20 Hz. `padd` drives the same duck at up
to 0.3 m/s. OpenCastor's "make it walk" is five times slower than picking up the
controller, and nothing tells the owner the number is a deliberate envelope
rather than a robot that is struggling.

Trap 7 is the RC car's simulated-wheels trap in a new costume. There the driver
was real and the config was simulated; here the driver silently becomes a mock on
any connect failure and reports `{"ok": True, "mode": "mock"}` from
`health_check()`. `castor duck health` guards against this by checking
`driver._mode != "hardware"` first (`castor/microduck.py:492-493`), which is
right. Nothing else in the codebase does.

---

# Ranked fixes

Ordered by minutes removed per hour spent.

| # | Fix | File to change | Effort | Removes |
|---|---|---|---|---|
| 1 | **Fix the four wire keys.** `robot.subscribe` → read `walk`, `stand`, `sitstand`, `ground_pick`, `skills`, `unavailable`; `robot.health` → read `control_loop.achieved_hz` (falling back to `target_hz`) and `.missed`; battery from `robot.health`, not `robot.state`. Pin each against a fixture captured from `scripts/duck-sim`. | `castor/drivers/microduck_driver.py:233-238`, `:552-561`, `:778-784`, `castor/microduck_choreography.py:628-631`, `castor/cli.py:3904-3912`, `:4069-4075` | 4 h | Traps 1, 3 and 4, and the reason the whole path looks broken on first contact |
| 2 | Make `castor duck` write and print a manifest `castor run` accepts. Either emit `ROBOT.md` directly, or let `cmd_run` accept the `.rcan.yaml` a first-party generator just wrote. | `castor/microduck.py:613-627`, `castor/cli.py:4102-4147`, `:57-67` | 3 h | Trap 10, and Route A stops being a dead end |
| 3 | Teach `castor migrate` to carry the `drivers` block, and to fail loudly when it would drop one. | `castor/migrate.py`, `castor/cli.py:2075-2087` | 2 h | Trap 2, for every robot |
| 4 | Fix discovery for a real duck: add `radxa-zero3.local` and a `duck-*` prefix match, stop advertising mDNS as a method the duck supports, and make `--host` the headline in the not-found message. | `castor/microduck.py:73-78`, `:231-271`, `castor/cli.py:4030-4035`, `docs/hardware/microduck.md:82-96` | 2 h | Trap 8's surprise |
| 5 | Set `agent.harness.enabled: true` in the microduck profile and preset, or register the duck tools outside the harness gate, with a test that a Microduck config yields a registry containing `duck_perform`. | `castor/profiles/pollen/microduck.yaml`, `config/presets/pollen_microduck.rcan.yaml`, `castor/api.py:707-713` | 1 h | Trap 5, the difference between chat and choreography |
| 6 | Make `castor duck do` use the duck's own configured brain: load the written config and pass its `agent` block to `get_provider`. | `castor/cli.py:4174-4198` | 1 h | Trap 6, cheapest on the list |
| 7 | **Add a `microduck` archetype to `castor up`.** `ARCHETYPES = ("rc-car", "sim")` at `castor/up.py:52` is the whole list. A duck archetype writes the same five units plus a sixth for the relay, so the console, the gateway, discovery and the pairing QR exist for a duck the way they do for a car. | `castor/up.py:52`, `:137-148`, `:305-410`, new `castor/templates/microduck/` | 12 h | The four-bring-up-programs problem, for this robot |
| 8 | **Ship the bridge as that sixth unit.** Take `bridge/microduck-bridge.py` into `castor/microduck_bridge.py`, have `castor duck --bridge` and the archetype install it as `{name}-duckbridge.service`, and mint one token that both OpenCastor's `transport: tcp` and the app read. The driver's `local_port` default is already **7788**, the port the bridge binds (`microduck_driver.py:127-129` vs `bridge/microduck-bridge.py:51`), so `transport: tcp` reaches it the moment the driver learns to send the hello line. | new `castor/microduck_bridge.py`, `castor/up.py`, `castor/drivers/microduck_driver.py:200-204` | 8 h | The two-relays problem: one deadman, one token, one unit, both clients |
| 9 | Teach `castor doctor` about ducks: is `robotd` answering, is the login in the `robot` group, is `mediad` up, does `robot.health` report a live loop, which policies are in which slots (`robot.policies`), is the battery above 12 %. | `castor/doctor.py` | 4 h | Trap 12, and traps 7, 11, 14, 15 go from silent to printed |
| 10 | **Add a `webrtc` transport to `MicroduckDriver`**, speaking `mediad`'s signalling on 8443 and the `control` datachannel. It removes the SSH key, the group edit and the reboot: Route A steps 6 and 7, about 2.5 minutes and two failure modes. The contract is public and in the tree this review cloned: `duck-ipc-proto/src/lib.rs` (5929 lines, `API_VERSION = 25` at `:304`), `docs/design/remote-webrtc.md` (586 lines), `docs/design/webrtc-console.md`. | `castor/drivers/microduck_driver.py:186-213`, new transport | 20 h | The largest OpenCastor-owned block on Route A, and the app's transport problem at once |
| 11 | Refuse to write a config for a duck that did not answer `robot.health`, or write it and say "not verified" rather than "Duck ready." | `castor/cli.py:4066-4139` | 0.5 h | Trap 14 |
| 12 | Print the envelope next to the walk: "walking at 0.06 m/s of a 0.20 m/s envelope; the gamepad's own limit is 0.30", and add `--speed`. | `castor/cli.py:3989-4016` | 1 h | Trap 9 |
| 13 | Say in the hardware guide that `mediad` binds `0.0.0.0` unauthenticated, and what turning it off costs. Replace the invented example output at `:21` with a real capture. | `docs/hardware/microduck.md` | 1 h | Traps 13 and 16 |
| 14 | Re-type `DriveView.requirePeer()` to `any DuckPeer`, decide what replaces `BenchPeer.live`, then wire `peer.notify(.move(...))` to a `.bridge` peer. | `DuckStudio/Sources/DriveView.swift:262`, `:2841-2846`, `:3199`, `:3289`, `BenchStore.swift:291`, `StudioKit/.../DuckPeer.swift` | 8 h | Route B stops being unbounded |
| 15 | Fix the stale counts and claims: `bridge/README.md:55`, `:58` (17 tests, not 9), duck-studio `README.md:459` (it does declare Bluetooth and Bonjour), `GATES.md:126-128` (the bridge exists), `docs/hardware/microduck.md:227-229` (the tools are gated). | those files | 1 h | The confusion tax |

Fixes 1 through 6 total **13 hours** and turn Route A from "never, and wrong when
it answers" into "works". Fixes 7, 8 and 10 total **40 hours** and are what would
make the Microduck the reference robot rather than a supported one.

## One caution on fix 10

Moving OpenCastor onto WebRTC inherits `mediad`'s decision that there is no
authorisation on the robot. Today an OpenCastor duck is protected by SSH keys and
group membership, which is real security the owner paid 2.5 minutes for. A WebRTC
transport removes the cost and the protection together, on a LAN where `mediad`
is already exposed anyway. That is defensible, and it must be a stated choice with
the sentence from `mediad/src/main.rs:9-13` reproduced next to it, not a side
effect of making setup faster.

---

# What the stopwatch honestly starts and ends at

The RC car's `docs/IMAGE.md` starts its clock at "clicking Write", which excludes
the download. The duck has no equivalent to hide behind, and the honest framing is
harder because the duck is already a working robot when the clock starts.

**The clock should start when the owner first types a command**, not when the box
opens and not when the duck is on Wi-Fi. Unboxing and charging are not software.
Wi-Fi onboarding belongs to Pollen and to whatever ships in the retail box, and
cannot be measured from a repository; it must be reported as an excluded interval
with its own number, never folded in silently.

**The clock should end at the first `robot.move` that OpenCastor originated from
a model's answer, accepted by robotd with the deadman armed**, not at "config
written", not at "the brain replied", and not at `castor duck test`, which needs
no brain and is therefore not the thing being measured.

Against that definition, today:

| Route | Clock starts | Clock ends | Honest wall clock |
|---|---|---|---|
| A, via `castor run` | `pip install` | never | **never** |
| A, via `castor duck do` | `pip install` | first Gemini-planned move | **8 to 20 min**, excluding Wi-Fi, with `--host` typed and `GEMINI_API_KEY` set |
| B, Microduck Studio | app install | never | **never** |
| C, Pollen's own | power on | first pad or browser move | **2 min** (pad), **1 min** (browser, duck already on Wi-Fi), no LLM |

With fixes 1 through 6 (13 hours), Route A's honest clock becomes: `pip install`
2 to 5 min, `castor duck` 1 min, `castor run` starts, first LLM turn moves the
duck. **Four to seven minutes, inside the goal**, for an owner whose duck is
already on Wi-Fi and who has a brain signed in. That would be the first time any
robot in this project credibly met the ten-minute goal end to end, and the reason
is that the duck brought most of the ten minutes with it.

---

# Two memory notes are now stale

**`microduck-real-interfaces` says there is no network endpoint an iPhone can
open.** Lines 21 to 24: "`robotd` speaks JSON-RPC over a Unix socket
(`/run/robotd.sock`), not HTTP. WebRTC is named in `duck-ipc-proto/src/lib.rs` as
where continuous intents travel *later*. **There is no network endpoint an iPhone
can open.** Do not invent one."

True of `robotd`, and `robotd` genuinely has no TCP listener: the only
`TcpListener::bind` outside tests in the whole Pollen repo is
`mediad/src/web.rs:79`. But it is not true of the duck. `mediad` binds a WebRTC
signalling server on `0.0.0.0:8443` and an HTTP console on `0.0.0.0:8080`
(`mediad/src/main.rs:31-38`, `:69-76`), the unit is enabled on every install and
every update (`scripts/install.sh:783-784`, `hooks/postinstall:161`), and the
datachannel carries the whole control surface including `robot.move`
(`mediad/src/route.rs:63-146`). `docs/design/remote-webrtc.md:11-21` records it as
"Status: working on hardware", proven end to end 2026-08-25. An iPhone opens
`http://<duck>:8080/` and drives. The note should keep its warning about robotd's
socket and replace the iPhone sentence with the two ports and the "it does not
authenticate" line.

**`microduck-bridge` says WebRTC is blocked on a document we do not have.**
Line 18: "`duck-ipc-proto` is not on this box (searched ~/projects) ... Do not
write a client against a guessed contract; get the proto first."

The proto is public and is a workspace member of the repository we can clone.
`git clone --depth 1 https://github.com/pollen-robotics/microduck` succeeded
during this review with no authentication; `Cargo.toml:7` lists `duck-ipc-proto`
in `members`, `duck-ipc-proto/Cargo.toml` shows it depends only on `serde`,
`libc` and `serde_json`, and `duck-ipc-proto/src/lib.rs` is 5929 lines with
`pub const API_VERSION: u32 = 25` at `:304`. Alongside it,
`docs/design/remote-webrtc.md` (586 lines) is the signalling design the note says
is missing, and `docs/design/webrtc-console.md` is the client. The instruction was
right and the premise expired. Nothing needs to be guessed.

A third correction, smaller and worth folding into
`microduck-real-interfaces`: the discrete request list in that note
(`robot.stop, robot.enable, robot.look, robot.init, robot.relax`) is a small
subset. robotd answers roughly thirty methods
(`duck-ipc-proto/src/lib.rs:416-758`, dispatch at `robotd/src/main.rs:3421-4401`),
including `robot.subscribe`, `robot.health`, `robot.do`, `robot.sound`,
`robot.mouth`, `robot.pose`, `robot.theremin`, `robot.policies`, `robot.model`,
`robot.skills`, `robot.loadPolicy`, `robot.mode`, `robot.setMode`,
`robot.safeToRestart`, `robot.shutdown` and `hello`; `updaterd` owns
`update.*`, `policy.*` and `account.*`; `configd` owns `net.*`, `system.*` and
`pad.bind*`; `padd` owns `pad.input`; `tofd` owns `tof.stream`. And the note's
"expiring, a real deadman" should carry the number: robotd's deadman is
**500 ms**, `deadman_ms: 500` at `robotd-params/src/lib.rs:1608` and
`duck-control/src/safety.rs:74`, applied at `safety.rs:226-231`, reported on the
wire as the string `"deadman"` (`robotd/src/main.rs:3102`), and it zeroes the
twist and nothing else (`safety.rs:561-564`).

---

# The ten-minute benchmark, as a thing you can run

The ten-minute goal has been a sentence in a memory note since 2026-08-14. It
should be a command that emits a JSON file. This section specifies that command.

The case for writing it against the Microduck first is the section above. Four
wire keys were wrong for weeks in shipped code, on the exact path a hardware
guide prints filled-in example output for, and nothing caught it, because nothing
in this project ever runs OpenCastor against a robotd. A benchmark whose C3
checkpoint reads identity off the wire would have failed on the first push.

The duck is also the only robot in the project where every checkpoint can be
answered by the robot itself rather than by our own logs. `robot.health` reports
the real loop and battery; `robot.policies` reports the real slots; `robot.state`
reports the real odometry and fall state. A benchmark that reads its own writes is
not a measurement, and on the RC car that is nearly all we could have done.

## The checkpoints

Seven, each a single timestamp, in order. Each has a defined evidence source and
a defined failure.

| # | Checkpoint | How it is timestamped | Evidence it records | Fails when |
|---|---|---|---|---|
| **T0** | **Clock start** | The moment the runner writes the first command to its own transcript | the argv | never; T0 is the origin |
| **C1** | **Package installed** | `importlib.metadata.version("opencastor")` returns, in a venv created after T0 | version, wheel URL, `sys.executable`, venv path | the resolved wheel has no `castor/microduck.py` |
| **C2** | **Duck reachable** | The transport is open and one JSON-RPC round trip has completed | transport (`unix`/`ssh`/`tcp`/`webrtc`), target, the raw `hello` reply | `MicroduckDriver._mode != "hardware"` (`microduck_driver.py:217`). **Mock mode is a fail, never a pass** |
| **C3** | **Identity read** | `hello`, `robot.health` and `robot.policies` have all answered | `api_version`, `daemon_version`, `revision`; `healthy`, `control_loop.achieved_hz`, `control_loop.missed`, `battery.percent`, `battery.volts`, `bus`, `imu`; and the policy slots with their `origin` | `healthy` is false, no walking policy is in a slot, or battery is below the choreographer's 12 % floor (`microduck_choreography.py:373`) |
| **C4** | **First LLM turn accepted** | The provider returned a plan that parsed and that `DuckChoreographer.expand()` accepted | provider, model, prompt bytes, response bytes, the expanded step list, latency | the plan is refused whole (`ChoreographyError`), which is a **correct** refusal and a failed checkpoint |
| **C5** | **First `robot.move` accepted, deadman armed** | The first `robot.move` notification is on the wire **and** the intent loop is observed re-sending | `{vx, vy, vyaw}`, `intent_hz`, `command_ttl_s`, and the wall time of the second re-send | no second re-send within `2 / intent_hz`; a deadman not observed to re-arm was never armed |
| **C6** | **`robot.stop` observed after silence** | The runner stops sending and waits; the stop is seen on the wire | which layer sent it, and the measured silence-to-stop interval | no stop within `command_ttl_s + 1 / intent_hz + 250 ms` |
| **C7** | *(optional, hardware or sim only)* **First step measured by the duck** | `robot.state.odom.position` moves more than 30 mm from the C5 position | the odometry samples, start and end position and yaw | the duck did not move, or `state.safety.fallen` went true |

C3 is written the way it is because of this review's central finding. It must read
identity **from the keys the wire actually uses**, and it must fail when a key is
absent rather than defaulting to `"?"`. Note that the robot's *name* and *serial*
are on a different daemon: `system.info` on `/run/configd.sock`
(`duck-ipc-proto/src/lib.rs:3650-3663`, serial from
`/proc/device-tree/serial-number` per `configd/src/identity.rs:35`). Reading it is
optional; claiming it without reading it is not allowed.

C6 is the checkpoint that distinguishes this from a smoke test. Three independent
deadmen exist on this stack and the record must name **which one fired**: the
driver's `command_ttl_s` of 1.5 s (`microduck_driver.py:82`), the bridge's 700 ms
(`duck-studio/bridge/microduck-bridge.py:52`), and robotd's own **500 ms**
(`robotd-params/src/lib.rs:1608`). A run where the driver's fires is healthy. A
run where only robotd's fires means our client stopped feeding it and did not
notice, which is exactly the failure the layered design exists to survive and
exactly the thing a benchmark should name.

C7 is optional because it needs a floor and a charged duck, or the simulator. It
is the only checkpoint that proves motion rather than acceptance, and a run
without it must say `"stepped": null`, never `"stepped": true`.

## The pass rule

**All six mandatory checkpoints (C1 through C6), in order, with
`C6.t - T0 < 600 s`.** Not the sum of the steps: wall clock from the first
command, including every retry, prompt and wait.

Three rules that keep it honest:

- **A skipped checkpoint is a fail, not an omission.** A run that could not read
  identity fails; it does not pass with a smaller set.
- **Mock transports fail C2.** The mock is for CI wiring. A CI run reports
  `"verdict": "ci-pass"` and is never counted as a pass of the ten-minute goal.
- **Wi-Fi onboarding is excluded and reported.** The record carries
  `excluded.wifi_onboarding_s` with a value or `null` and a one-line reason. A
  benchmark that hides the largest interval is the `docs/IMAGE.md` stopwatch
  again.

## What gets recorded

One JSON object per run. Every field is measured or explicitly null.

```json
{
  "benchmark": "opencastor/ten-minutes",
  "schema_version": 1,
  "robot": "microduck",
  "verdict": "pass",
  "started_at": "2026-09-08T19:00:00.000Z",
  "elapsed_s": 412.7,
  "budget_s": 600,
  "environment": {
    "host": {"platform": "linux", "machine": "aarch64", "python": "3.11.2"},
    "opencastor": {"version": "1!3.1.0", "git_sha": "...", "wheel": "..."},
    "repo_shas": {"opencastor-runtime": "...", "duck-studio": "...",
                  "pollen/microduck": "5620aa2"},
    "transport": {"kind": "tcp", "target": "192.168.1.42:7788",
                  "bridge": "microduck-bridge/1", "bridge_deadman_ms": 700}
  },
  "duck": {
    "hello": {"api_version": 25, "daemon_version": "...", "revision": "..."},
    "name": "duck-c51b", "serial": "...", "name_source": "configd system.info",
    "health": {"healthy": true, "degraded": false,
               "control_loop": {"target_hz": 50.0, "achieved_hz": 49.8, "missed": 0},
               "battery": {"volts": 7.9, "percent": 64}},
    "policies": {"walk": "alpha_walking.onnx", "stand": "alpha_stand.onnx",
                 "skills": ["ground_pick", "kick_left", "kick_right",
                            "sit_toggle", "roulade"],
                 "unavailable": null},
    "obs_len": 61, "action_len": 14
  },
  "brain": {"provider": "anthropic", "model": "claude-sonnet-4-5",
            "tools": ["duck_vocabulary", "duck_perform"], "turns": 1},
  "checkpoints": [
    {"id": "C1", "t": 168.2, "ok": true, "evidence": {"version": "1!3.1.0"}},
    {"id": "C2", "t": 174.9, "ok": true, "evidence": {"mode": "hardware"}},
    {"id": "C3", "t": 175.4, "ok": true, "evidence": {"healthy": true, "achieved_hz": 49.8}},
    {"id": "C4", "t": 402.1, "ok": true, "evidence": {"steps": ["walk", "say"]}},
    {"id": "C5", "t": 403.0, "ok": true,
     "evidence": {"params": {"vx": 0.06, "vy": 0.0, "vyaw": 0.0},
                  "intent_hz": 20, "command_ttl_s": 1.5, "resend_seen_at": 403.05}},
    {"id": "C6", "t": 412.7, "ok": true,
     "evidence": {"fired_by": "driver_ttl", "silence_to_stop_ms": 1548}},
    {"id": "C7", "t": null, "ok": null, "evidence": {"reason": "no floor run"}}
  ],
  "excluded": {"wifi_onboarding_s": null,
               "reason": "duck was already on the network at T0"},
  "transcript": [
    {"t": 0.0,   "typed": "python3 -m venv /tmp/bench-venv"},
    {"t": 2.1,   "typed": "/tmp/bench-venv/bin/pip install 'opencastor>=3.1'"},
    {"t": 170.0, "typed": "castor duck --host 192.168.1.42 --yes"},
    {"t": 390.0, "typed": "castor duck do 'walk forward a little, then quack'"}
  ],
  "wire": [{"t": 403.0, "dir": "out",
            "line": "{\"jsonrpc\":\"2.0\",\"method\":\"robot.move\",\"params\":{...}}"}]
}
```

`transcript` is the load-bearing field and the one most likely to be dropped.
**Every command the operator typed, with its timestamp**, is what makes a claimed
number auditable by someone who was not there. A run that reports 412 s and lists
four commands is a different artifact from one that reports 412 s and lists
twenty-two.

`wire` should carry, at minimum, the C5 move, its first re-send, and the C6 stop.
It is the only part of the record that can prove the driver spoke the protocol
rather than logged that it did, which is the exact class of bug traps 1, 3 and 4
belong to.

## The proposed CLI

```
castor bench ten-minutes --robot microduck [options]

  --transport unix|ssh|tcp|webrtc|mock   default: auto-detect; mock refused unless --ci
  --host HOST                            skip discovery
  --brain PROVIDER[:MODEL]               the brain under test; default: the robot's own config
  --ci                                   run against a mock robotd; verdict is "ci-pass", never "pass"
  --mock-cmd CMD                         default: python3 -u bridge/mock-robotd.py --socket <tmp>
  --sim                                  run against Pollen's scripts/duck-sim (real robotd, MuJoCo body)
  --floor                                enable C7; asks once before the duck moves
  --budget-s N                           default 600
  --exclude-wifi                         record wifi onboarding as excluded, with a reason
  --out PATH                             default: ./ten-minutes-<robot>-<iso>.json
  --evallog PATH                         also emit an inspect-robots EvalLog v1
```

Three targets, all of which already exist.

**The mock, for CI.** `duck-studio/bridge/mock-robotd.py` is 49 lines of stdlib
and answers every method (`:40-43`). **Proven during this review**:
`MicroduckDriver({"transport": "unix", "socket": "<scratch>/mock.sock"})`
connected in 1 ms, sent `robot.subscribe`, `robot.health`, eight
`robot.move {"vx": 0.06, ...}` notifications at 20 Hz, then one zeroing move and
`robot.stop`. That is C2, C5 and C6 exercised end to end with no duck and no
network. To also serve C3 it needs three replies shaped like the real ones:
`robot.health` returning
`{"healthy": true, "control_loop": {"target_hz": 50.0, "achieved_hz": 49.8, "missed": 0}, "battery": {"volts": 7.9, "percent": 64}, "bus": {...}}`,
`robot.subscribe` returning
`{"accepted": true, "walk": "alpha_walking.onnx", "stand": "alpha_stand.onnx", "skills": [...]}`,
and `hello` returning `{"api_version": 25, ...}`. Today it returns
`{"ok": true}` for everything, and the driver correctly reads that as unhealthy
(measured: `{'ok': False, ..., 'error': 'robotd reports unhealthy'}`). **Those
replies are the fixture that would have caught traps 1, 3 and 4**, and they must
be transcribed from `duck-ipc-proto/src/lib.rs`, never invented, with the source
line recorded beside each.

**The simulator, for honesty without hardware.** Pollen's `scripts/duck-sim`
(893 lines) runs **the real `robotd` binary** with
`duck_control::sim::RemoteIo` in place of the servo bus, against a MuJoCo body
from `microduck_rl` (`docs/robot/simulation.md:9-20`). Everything above the servo
seam is the code a robot runs: the control loop, the policy, safety, fall
detection, kinematics, odometry and the whole IPC surface. It answers C3 with real
numbers and it is the only target short of hardware that can honestly answer C7.
It costs a checkout of `microduck_rl` with its venv and, for the container form,
`systemd-nspawn`. It cannot tell you anything about a driver, and the doc says so.

**A real duck, for the number that counts.** Everything above, plus a floor.

The mock target belongs in CI on every push; the sim target belongs in a nightly;
the hardware target belongs in a recorded bench run whose JSON is committed.

## Exporting into EvalLog v1

Microduck Studio already wrote the hard part. `EvalLog.swift:30` pins
`schemaVersion = 1`, `EvalLogWriter.swift:67` is the single builder, and
`scripts/check_evallog_parity.sh` proves the output is read by the real
`inspect-robots==0.58.0` (`:56`), re-dumped byte-identically, and rendered by
`inspect-robots view` (`scripts/evallog_parity.py:4-11`, which imports upstream's
own `_sanitize` and `reduce_scores` rather than reimplementing them, `:19-24`).

A ten-minute run maps onto that schema cleanly, and fixing the mapping now stops
two projects inventing two formats:

| EvalLog v1 key | The ten-minute run |
|---|---|
| `eval.task` | `"opencastor/ten-minutes"` |
| `eval.embodiment` | `"microduck"` |
| `eval.embodiment_info` | `hello` (api/daemon/revision), name, serial, policy slots, `obs_len`, `action_len`, achieved loop Hz |
| `eval.policy` / `policy_config` | the brain: provider, model, tool list, and the transport |
| `eval.git_commit` | the `opencastor-runtime` sha |
| `eval.inspect_robots_version` | the same honest string the app uses: written by OpenCastor, no inspect-robots ran |
| `eval.seed` | `null`, with a `seed_note`, exactly as the app does |
| `samples[].scene_id` | the route: `"A-castor-duck"`, `"B-studio-bridge"`, `"C-pollen-console"` |
| `samples[].trial_metadata` | one entry per checkpoint: id, `t`, `ok`, evidence |
| `samples[].termination_reasons` | which deadman fired at C6 |
| `stats.started_at` / `completed_at` / `duration_s` | T0, C6, elapsed |
| `stats.total_steps` | traced control ticks, or `0` when C7 was not run |
| `results.metrics` | `{"elapsed_s": ..., "checkpoints_passed": 6}` |
| `status` | `"success"` on a pass, `"error"` when a checkpoint failed |

One refusal to carry over from the app's own list: **no success scorer without a
motion-evidence scorer.** A run that passes C1 through C6 and skipped C7 has
proved that commands were accepted, not that a robot moved, and the exported log
must not let a viewer read it as the latter. The app already enforces exactly this
shape of rule (memory note `duck-studio-evaluations-inspect-robots`, refusal L1),
and `castor bench` should inherit it rather than re-argue it.

## What this benchmark would say if run today

Honestly, against a real duck on a floor, with the code as it stands:

- Route A via `castor run`: **fail at C4**, because the process exits at step 10.
- Route A via `castor duck do` with a Google key: **fail at C3**, because
  `control_loop` and the policy slots are read from keys that do not exist, so no
  identity can be proved. It would pass C5 and C6, which is the sharpest possible
  statement of the problem: OpenCastor can drive this duck and cannot describe it.
- Route B: **fail at C5**. The app has never sent a move.
- Route C: not measurable by this benchmark, because there is no LLM turn to
  timestamp at C4. That is the correct answer, and it is the sentence that says
  what OpenCastor is for.

With fixes 1 through 6 landed, Route A passes C1 through C6 in four to seven
minutes on a duck that is already on Wi-Fi, and the number becomes something the
project can publish because a machine produced it.
