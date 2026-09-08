# The RC car, carbot, and the ten-minute clock

Review date: 2026-09-08
Scope: what `~/projects/carbot` is worth taking into OpenCastor, and what the real
"open the box and drive" path costs today.
Method: read-only. No service was started, stopped or restarted; no hardware moved.
Every claim below cites a file and line, a live PyPI/GitHub response, or a
read-only shell query run during the review.

Sources read: `/home/craigm26/projects/carbot` (all 8 source files),
`/home/craigm26/projects/opencastor-runtime` (castor, docs, scripts/image,
config/presets, community-recipes, website),
`/home/craigm26/projects/opencastor-ios` (CastorKit + app target),
`/home/craigm26/projects/RobotRegistryFoundation/rc-car-actuator`,
`/home/craigm26/rover` (runbooks and live units), and the six memory notes named
in the brief. Quoted source lines have their dashes normalised to commas to keep
this document free of em-dashes; nothing else in a quote is changed.

---

## Executive summary

The ten-minute goal is met for one thing and one thing only: getting a Pi to
show a pairing QR. It is not met, and is not close, for the thing the goal
actually promises, which is a robot that moves.

Three independent walls sit between a new owner and a moving RC car:

1. **The published install path does not work.** No wheel on PyPI contains
   `castor up`. `pip install opencastor` resolves to the April CalVer build.
2. **Every path that does work ends on simulated wheels by design.** Turning on
   real PWM is five uncommented lines in a file the documented path never tells
   the user to open, plus a sudo reboot to enable I2C that the flashable image
   is forbidden from doing.
3. **The phone cannot find the robot on its own.** Three separate mismatches
   between what `castor up` publishes and what the app looks for, so the QR is
   the only pairing path that works, and "find it on your network" never will.

Underneath all three is a structural fact: there are four separate bring-up
programs in this repository (`castor up`, `castor wizard`, `castor setup`,
`castor init`), they share almost no code, and only one of them knows what an RC
car is.

Current honest wall clock, box to wheels actually turning: **3 to 8 hours for a
newcomer**; about **45 to 60 minutes** for someone who already knows the five
undocumented edits. Box to a *paired* robot on the image path: roughly **15 to
25 minutes**, of which the ten-minute stopwatch in `docs/IMAGE.md` measures only
the last third.

carbot is worth mining. It is not a better architecture than OpenCastor's signed
rail, and nothing in it should replace the deadman, the envelope or the receipt.
What it has is a set of small, measured, physical-world fixes that OpenCastor
does not have: the phone as a pushed camera, a USB power budget the software
respects, an idle neutral re-assert, a carpet kick, and a system prompt written
by watching a model fail.

---

# Question 1: what in carbot is worth re-using

## Summary table

| # | carbot piece | Where it lives | OpenCastor's nearest thing | Verdict | Effort |
|---|---|---|---|---|---|
| 1 | Phone pushes JPEG frames to the robot | `carbot/drive.py:518-526`, `carbot/web/head.html:95-99` | `castor/camera.py:129` (pulls), `castor/console/eval_eyes.py:10` (same contract, eval-only, uncommitted) | Merge | 6 h |
| 2 | USB power budget: camera idle-close and release-before-audio | `carbot/drive.py:228-237`, `:336-362`, `carbot/brain.py:83-95` | none | Port | 4 h |
| 3 | Re-assert neutral every tick while idle; demote the handle on a write failure | `carbot/drive.py:153-161` | `rc_car_actuator/deadman.py` (better lease, no idle re-assert) | Port | 3 h |
| 4 | Re-probe the missing PWM chip every 5 s, no restart | `carbot/drive.py:89-97` | partial (three-valued `hardware_reachable`); contradicted by template | Merge and reconcile | 2 h |
| 5 | Carpet kick from rest | `carbot/drive.py:42-43`, `:124-126` | `TrimApplication.swift`, `DriveGovernor` creep threshold | Port as a profile field | 3 h |
| 6 | The drive system prompt | `carbot/brain.py:47-60` | `DrivePlan.swift`, `ProposalValidator.swift` | Port the prompt clauses only | 1 h |
| 7 | Wake-word aliases learned from real STT errors | `carbot/voice.py:26-66` | `CastorKit/.../WakeWord.swift` (better design) | Merge as test fixtures | 1 h |
| 8 | A stop that never 404s, whichever verb | `carbot/drive.py:420-421`, `carbot/rover:74` | `castor/api.py` stop route | Port the rule | 0.5 h |
| 9 | Hand-run CLI sources the service's own env file | `carbot/rover:19-30` | none (this is why `smoke.sh` had to add a source) | Port | 1 h |
| 10 | One LLM turn at a time across surfaces, via flock | `carbot/brain.py:158-161` | console `anthropic-sub` provider | Port | 2 h |
| 11 | ALSA card picked by description at call time | `carbot/brain.py:18-36` | none | Port | 1 h |
| 12 | Self-signed HTTPS on a second port for phone mic and camera | `carbot/drive.py:547-557` | none | Port, optional | 3 h |
| 13 | Browser energy VAD, PCM push | `carbot/web/head.html:71-92`, `carbot/drive.py:489-517` | `VoiceSession.swift` (native, better) | Drop for iOS, keep as the no-app on-ramp | n/a |
| 14 | `EVAL.md` as a bring-up rubric | `carbot/EVAL.md:65-90` | none | Port into docs | 1 h |
| 15 | Piper TTS, text to a playable WAV | `carbot/brain.py:71-76`, `:97-111` | **nothing.** `castor/tools.py:15,181` declares a "Speaks text aloud via TTS" tool with no implementation; no piper, espeak or coqui anywhere in the repo | Port | 3 h |
| 16 | `EnvironmentFile=-` on the units | `carbot/systemd/carbot-drive.service:7` | rover units make it mandatory | Drop, it is the anti-pattern | n/a |

Total for everything marked Port or Merge: about **31 hours**.

Item 15 is worth calling out separately. OpenCastor has four STT engines
(`castor/voice.py:329`) and a wake word (`castor/hotword.py:38`), and advertises
a speak tool that cannot speak. carbot's `tts_wav` plus `say` is thirty lines,
already handles the markdown stripping a spoken reply needs
(`carbot/brain.py:72`), and already releases the camera before drawing speaker
current. It closes a declared-but-absent capability.

## The five that matter

### 1. The phone as a pushed camera (merge, 6 h)

carbot's page grabs the back camera, draws it to a canvas, and POSTs a JPEG
about twice a second (`web/head.html:95-99`). The robot holds exactly one frame
in memory (`drive.py:189-201`) and `rover look` returns it with the depth fields
nulled, which the prompt then reads as "you have no obstacle guard, take 0.3 s
steps" (`brain.py:59-60`). This exists because the OAK-D and the USB speaker
cannot both be powered on a Pi 5 at `usb_max_current_enable=0`, which is still
this machine's state (verified read-only during this review).

OpenCastor's `castor/camera.py:129` `_HttpCameraSource` runs the other way: the
robot polls a URL. It also returns `True` from `open()` even when the endpoint
is unreachable (`castor/camera.py:170`, `return True  # non-fatal if not
reachable yet`), so a mistyped camera URL produces a camera that reports open.

The interesting fact is that this port has already started and is sitting
uncommitted. `castor/console/eval_eyes.py` (untracked in `git status`) declares
`POST /eval/frame` and says so explicitly at line 27:

> **THE BODY IS RAW JPEG, and that is deliberate.** It is byte-for-byte the
> contract carbot's phone head already speaks (`POST /phone-frame`,
> `Content-Type: image/jpeg`, the JPEG as the body)

The recommendation is to finish that thought rather than start a third one:
promote the push route out of the eval namespace into a camera source the
`CameraManager` can register, so the drive loop and `castor look` can use the
phone as the eye, not just an eval scorer. The iOS side already has the client
half in `DriveWatch.swift` (about one frame per second, only inside an open
envelope), so the app needs no new capture code, only a new destination.

### 2. The USB power budget (port, 4 h)

carbot treats USB current as a first-class resource. The OAK-D closes six
seconds after its last use (`drive.py:228-237`), every spoken reply releases the
camera first (`brain.py:83-95` calling `POST /camera/release`), and the release
path stops the car because the depth guard is about to disappear
(`drive.py:529`, `car.stop("camera released")`). The `stop()` call on the
pipeline is bounded to twelve seconds because a device that is mid-reset hangs
it (`drive.py:355`).

OpenCastor has none of this, and the failure it prevents is invisible from
software: the kernel logs `over-current change`, every USB device resets
together, and the symptom presented to the user is a camera that stopped and a
microphone that stopped, at the same time, for no reason. Port the idle janitor
and the release-before-audio rule, and add the corresponding check to
`castor doctor` (see Question 2, fix 7).

### 3. Idle neutral and write-failure demotion (port, 3 h)

`rc_car_actuator/deadman.py` is the better lease design and nothing here
displaces it. But carbot's deadman does one thing the actuator does not: while
the car is stopped it re-writes neutral on **every** 50 ms tick, and if that
write raises it drops the hardware handle and records the error
(`drive.py:153-161`):

```
self.hw_error = str(e)
self.maestro = None
log("maestro write failed, dropped:", e)
```

That is what makes the phone's hardware badge go red within a second of a wire
coming loose, instead of at the next command. The actuator currently learns
about a dead bus only when someone asks it to move. Given
`pca9685-holds-last-value-on-process-death`, a periodic write that can fail
loudly is cheap insurance.

### 4. The system prompt (port the clauses, 1 h)

`brain.py:47-60` is the only artifact in either repository written by watching a
model drive a real car into a real room. Four clauses are worth lifting verbatim
into the `drive.plan` drafting prompt:

- "look before you move and after you move"
- "Move in short steps and stop if anything is closer than half a metre"
- "If the drive service reports the hardware is unavailable, say so plainly and
  do not pretend to move"
- the no-depth branch: "your eyes are the phone camera mounted on top of you:
  there is no obstacle guard, so judge distance from the picture yourself, move
  in very short steps (0.3 s) and look after every move"

The third one is the load-bearing one. It is the clause that produced the
2026-09-04 result recorded in memory: Claude looked, saw a child in front of the
car, and refused to move.

### 5. The carpet kick (port as a profile field, 3 h)

`drive.py:124-126` applies a brief higher throttle when starting from rest,
because a gentle command is inside the ESC deadband on carpet. The measured
numbers are in `carbot.env:10-12` (kick 0.35 for 0.30 s, cruise 0.22) and the
README records the consequence: `rover forward` at 0.6 s travels about a metre.

`duckbench-keyframe-servo-cannot-saturate` and the rover note both record the
same class of problem from the other side: `creepThreshold=0.06` is probably
inside the deadband. OpenCastor already has the right home for this in
`VehicleProfileStore` and `TrimApplication.swift`; it is missing the two fields
and the "from rest" trigger.

## What to leave behind

`EnvironmentFile=-` on the carbot units (`systemd/carbot-drive.service:7`) is
exactly the trap named in the brief. The leading dash makes the file optional,
so deleting `carbot.env` leaves a service that starts, binds :8100 and :8443,
reports `active (running)`, and drives with the wrong throttle constants. Every
`rover-*` unit makes its `EnvironmentFile` mandatory and says why in a comment.
Keep the rover's posture; do not copy carbot's.

Also note carbot's four newest commits are unpushed (`git log
origin/main..HEAD` shows `b9cf256`, `65d81be`, `3300b6a`, `e1a84f8`), so all of
the phone-as-camera and `/head` work exists only on this Pi.

---

# Question 2: the real "open the box" path

## What a new owner actually has

A Pi 5, an RC car with an ESC and a steering servo, a PCA9685 breakout, an
iPhone with the App Store build, and no prior knowledge.

Before the routes, the shape of the problem: there are **four separate bring-up
programs in this repository and they share almost no code**.

| Entry | Implementation | Produces | Reads `config/presets/`? |
|---|---|---|---|
| `castor up` | `castor/up.py` | robot home, four systemd user units, pairing QR | No, uses its own packaged templates |
| `castor wizard` | `castor/wizard.py` (12 steps, preset picker at `:2326`) | `~/.config/opencastor/<name>.rcan.yaml` | Yes |
| `castor setup` | `castor/cli.py:3571` (4 steps) | rcan.yaml plus a Firebase bridge token | No |
| `castor init` | `castor/init_wizard.py` (7 prompts) | a `ROBOT.md` | No |

`castor init` asks seven questions and every default describes an arm, not a car
(`init_wizard.py:100-110`: name `bob`, model `so-arm101`, device id `bob-001`).
It performs no hardware detection and offers no preset. A newcomer with a car
who types the most obvious command gets a manifest for somebody else's robot.

Three of the four routes are documented as the way in. They do not agree with
each other, and only one can reach a paired robot at all.

## Route A: `pip install opencastor` (what the website's hero shows)

`website/src/pages/index.astro:218-219` prints exactly two lines, and
`:224-226` says `castor up` "takes a bare host to a paired robot printing its
pairing QR."

**This route is dead today.** Verified against live PyPI during this review:

| Check | Result |
|---|---|
| `pip install opencastor` resolves to | `2026.4.23.0`, uploaded 2026-04-23 |
| Does that wheel contain `castor/up.py`? | No |
| Does that wheel contain `castor/pairing.py`? | No |
| Does the newest semver wheel `3.0.3` (2026-08-01) contain `castor/up.py`? | No |
| Is `rc-car-actuator` on PyPI? | No, HTTP 404 |

`castor up` landed on 2026-08-14 (memory: `opencastor-ten-minute-goal`), two
weeks after `3.0.3` was uploaded. And pip picks the April build regardless,
because under PEP 440 the CalVer line `2026.4.23.0` sorts above `3.0.3`. The
repository already knows this: `docs/setup/pairing.md:113-114` warns that "a
bare `pip install opencastor` can resolve a stale CalVer release" and pins
`opencastor==3.*` instead. That pin gets a wheel without `castor up` either.

So the newcomer spends five to twelve minutes installing 250 packages into a
1.2 GB venv (measured: `~/venvs/castor` is 1.2 GB with 250 site-packages
entries; the metadata lists 23 hard dependencies including `streamlit`,
`opencv-python-headless` and `pygame`) and then types `castor up` and is told
there is no such command.

**Time to a driving car on this route: never.**

## Route B: the flashable Pi image (`docs/IMAGE.md`)

This is the intended answer and it is a good design.
`docs/IMAGE.md:14-24` states the whole interface as four steps and adds:

> That is the whole interface. There is no step where they read a filesystem
> path out of a terminal, because there is no terminal.

The image exists. GitHub release `image-v0.1.0`, published 2026-08-17, carries
`opencastor-pi.img.xz.part-00` (1.68 GB), `part-01` (1.64 GB) and a `.sha256`.
All three assets show **download_count 0**, so nobody has yet walked this path,
including the operator.

### Timed walk

| Step | What the user does | Source | Time |
|---|---|---|---|
| 1 | Find the release, download 3.3 GB in two parts | release body | 4.5 min at 100 Mbit/s, 18 min at 25 Mbit/s |
| 2 | `cat opencastor-pi.img.xz.part-* > opencastor-pi.img.xz` | release body step 2 | ~1 min |
| 3 | `sha256sum -c opencastor-pi.img.xz.sha256` | release body step 3 | ~0.5 min |
| 4 | Imager: write plus verify 7.8 GiB | `docs/IMAGE.md:213-220` | 3.5 min (USB3 plus A2 card), 13 min (USB2 plus class 10) |
| 5 | Two boots, `castor up` runs once | `docs/IMAGE.md:175-181`, `:227` | 2 to 3 min |
| 6 | Open `http://<hostname>.local/`, scan the QR | `docs/IMAGE.md:20-23` | ~1 min |
| 7 | Name the robot, approve a 5 s / 10 % envelope | `FirstDrive.swift:103-107` | ~1 min |
| | **Subtotal: paired robot** | | **13.5 to 38 min** |
| 8 | Press the stick. Nothing moves. | see below | |

**The ten-minute stopwatch in `docs/IMAGE.md:195-203` starts the clock at
"clicking Write"**, which excludes steps 1 to 3 entirely. Those three steps are
also the only ones that require a terminal, which contradicts the document's own
"there is no terminal" claim. On the doc's own arithmetic
(`docs/IMAGE.md:217-218`) a USB2 reader with an ordinary class 10 card "blows
the budget on step 1 alone".

### Why step 8 does not move

Four separate mechanisms all point the same way, and each one alone is
sufficient.

**8a. `castor up` deliberately ships simulated wheels.** `castor/up.py:415-416`:

```
# config is the SIMULATED-wheels default -- real PWM is a deliberate later
# flip in gateway-policy.env, never a setup default.
```

`pick_archetype` (`castor/up.py:94-105`) does detect the chip and does choose
the `rc-car` archetype, and says in the same breath that this "selects the SHAPE
of the robot, never whether it can move: an rc-car archetype still starts on
simulated wheels."

**8b. Every drive variable in the shipped template is commented out.**
`castor/templates/rc_car/gateway-policy.env.tmpl:45-49`:

```
#OPENCASTOR_DRIVE=pca9685
#OPENCASTOR_DRIVE_I2C_BUS=1
#OPENCASTOR_DRIVE_I2C_ADDRESS=0x40
#OPENCASTOR_DRIVE_THROTTLE_CHANNEL=0
#OPENCASTOR_DRIVE_STEERING_CHANNEL=1
```

Nothing in the four-step image runbook tells the user this file exists, where it
is, or that it must be edited. There is no in-app affordance for it either.

**8c. The template's channel defaults are the wrong way round for both known
vehicles.** The template says throttle 0, steering 1. The live rover's
`gateway-policy.env:48-49` says throttle 1, steering 0, and there is a backup
file literally named `gateway-policy.env.bak-channelswap`. carbot agrees with
the rover (`drive.py:31-32`, ESC ch1, steering ch0). Memory
`rc-car-pca9685-bringup` records what a cross-plugged harness costs: a week of
register-level bench tests never caught it, because "steering command produces a
pulse on the steering channel" is true no matter what is plugged into that pin.
A newcomer who does find and uncomment the template gets the failure the project
already paid for once.

**8d. The image cannot enable I2C, by rule.** `scripts/image/lib/chroot-stage.sh:113`
adds the user to the `i2c` group, but `scripts/image/selftest.sh:136` asserts
that **no script may write `config.txt`**:

```
assert "no script writes to cmdline.txt/config.txt/firstrun.sh/userconf.txt"
```

Raspberry Pi OS ships `dtparam=i2c_arm=on` commented out, so `/dev/i2c-1` does
not exist on a freshly flashed card. `_pca9685_from_env` then raises with the
right message (`rc-car-actuator/src/rc_car_actuator/backend.py:335-340`, "Is
I2C enabled (raspi-config, Interface Options, I2C)") but only if step 8b was
done first, and the fix needs sudo and a reboot the "no terminal" path does not
have.

### And then the phone cannot find the robot

Even with the QR scanned, the "find it on your network" affordance
(`FleetView.swift:87-104`) is broken for a `castor up` robot in two independent
ways.

| Layer | What the robot does | What the app looks for | Match |
|---|---|---|---|
| mDNS service type | `castor/rcan/mdns.py:38` advertises `_rcan._tcp.local.` | `RobotDiscovery.swift:64` browses `_opencastor._tcp` | No |
| mDNS enabled | `castor/templates/rc_car/robot.rcan.yaml.tmpl:35` sets `enable_mdns: false` | | No |
| mDNS at all | `castor up` writes four units (`castor/up.py:140,156,183,199`: gateway, castor, console, rrf-stub) and **no advertiser** | | No |
| LAN sweep port | `castor up` base_port 8080, so runtime is **8081** (`castor/up.py:54-56`, `:76-86`) | `RobotDiscovery.swift:100` sweeps `[8001, 8003, 8002, 8000]` | No |

The `enable_mdns: false` is not an oversight, and the reason matters for the
fix. `robot.rcan.yaml.tmpl:31-34` states it:

> castor advertises the RUNTIME port while the gateway is on another, and the
> TXT record carries no manifest_path/rrn, so a Bonjour-found robot could never
> invoke.

So the advertiser that exists is genuinely not fit to be turned on, and flipping
the flag would produce a discoverable robot the app still cannot drive. The fix
is to make the record carry gateway port, rrn and manifest path, and to publish
it on the type the app browses, which is what `~/bob/advertise.py` already does
by hand.

This is the 2026-08-14 fault from `opencastor-robot-discovery-fix` still open in
the shipping product. That fix was applied to hand-written files
(`~/bob/advertise.py`, run by `rover-discovery.service`); it was never brought
into the `castor` package, so nothing `castor up` produces advertises anything.
`avahi-browse` will not show it either way, per that same note.

One more thing `castor up` does not do: `loginctl enable-linger`. Systemd user
units do not run on a headless boot without it. The image path sets it out of
band (`scripts/image/firstboot/firstboot.sh:169`) and falls back to
`castor up --no-start` with a caveat if the user bus never appears (`:178-183`),
so a Route C user who logs out has a robot that stops existing.

## Route C: `git clone` plus `pip install -e` (`CLAUDE.md:15-22`)

This one works, because GitHub `main` does have `castor/up.py` (verified: raw
URL returns 200). The clone plus editable install of 250 dependencies is 6 to 15
minutes on a Pi, then `castor up` runs in about half a minute to running
services (memory: 31 s, measured 2026-08-14). It lands on exactly the same
simulated-wheels wall as Route B, plus the same discovery failure.

## The traps a newcomer hits, ranked by how silently they fail

| # | Trap | Evidence | What the user sees |
|---|---|---|---|
| 1 | `pip install opencastor` has no `castor up` | live PyPI, wheel contents | "No such command" at minute six |
| 2 | Wheels are simulated by default | `castor/up.py:415-416`, template `:45-49` | Stick moves, receipts sign, badge green, car still |
| 3 | mDNS never advertised, wrong type, wrong port | `castor/up.py:140-199`, `mdns.py:7`, `RobotDiscovery.swift:64,100` | "No robots found" on a robot that is answering |
| 4 | I2C off, and the image is forbidden from fixing it | `selftest.sh:136`, `chroot-stage.sh:113` | Gateway refuses to start once 8b is done |
| 5 | Second PCA9685 driver silently mocks | `castor/drivers/pca9685.py:33,146` | "Falling back to mock mode" in a log nobody reads |
| 6 | Template channels reversed vs both real cars | template `:48-49` vs `rover/gateway-policy.env:48-49` | Stick right spins the wheels |
| 7 | The manifest teaches a model to send the stopping value | `ROBOT.md.tmpl:90-92` vs `:176` | An LLM drafts `duration_s: 0` and the car does not move |
| 8 | A 422 is indistinguishable from a dead link in the app | `GatewayClient.swift:200-225`, `DriveStreamer.swift:421` | "The robot didn't answer." |
| 9 | `castor doctor` probes a port nothing runs on | `castor/doctor.py:196` (`_check_gateway(port=18789)`) vs `castor/up.py:54` (gateway 8080) | "Gateway not reachable" on a healthy robot, plus a fix line that is the wrong command |
| 10 | `castor hub install` writes nothing and says it did | `castor/hub.py:354-357,371`, `cli.py:6212` | "Installed to ./config.rcan.yaml" for a file that was never written |
| 11 | The bring-up checklist the template tells you to follow does not exist | `gateway-policy.env.tmpl:33` refers to "the PCA9685 bring-up checklist"; no such file is in the repo | A dead reference at the exact moment the user needs it |
| 12 | `castor doctor` covers none of 1 to 8 | `castor/doctor.py` (zero I2C checks, no `OPENCASTOR_DRIVE` check, no mdns check, never reads `~/robot`) | A clean bill of health on a car that cannot move |

Trap 7 is sharper than the memory note records it. The generated manifest, which
is the document a drafting model reads, contains two disagreeing defaults for the
same field. The contract schema says `duration_s: {kind: float, default: 0}`
(`castor/templates/rc_car/ROBOT.md.tmpl:90-92`) while the prose fifty lines
later says "A command that states no duration gets 400 ms"
(`ROBOT.md.tmpl:176`). Zero is a zero-length lease, which the actuator treats as
a stop (`rc_car_actuator/actuator.py:215-220`), and the invoke path defaults an
absent field to the same zero (`actuator.py:473`,
`duration_s=tool_args.get("duration_s", 0.0)`). A model that fills in the schema
default writes a stop and reports a drive.

Credit where due: the iOS app is clean on this. `DriveControl.swift:158` always
emits `duration_s`, there is exactly one producer of `drive.set` args, the reason
is documented at `:147-153`, and `ProposalValidator.swift:116` rejects a drafted
plan that omits it. The trap is live for any other client and for any model
reading the manifest directly.

On trap 11, the checklist does exist, but only as `~/rover/PCA9685-BRINGUP.md`
on this one machine. It is ten ordered steps, it is good, and it is the single
most valuable un-shipped document in the project.

On trap 12, `castor gaps` is the health check that actually understands an
`up`-provisioned robot (`castor/gaps.py:68-82` even emits the exact
`pip install rc-car-actuator` line). `castor doctor` never calls it, and neither
calls the other.

On trap 9, all seven `community-recipes/*/recipe.json` name a
`config.rcan.yaml` that does not exist in the repository, including
`picar-home-patrol-e7f3a1`, which is the closest thing to an RC-car onramp the
hub offers.

Two more, lower severity but worth recording:

- `castor/drivers/pca9685.py` offers `pca9685_i2c` and `pca9685_rc` as a second,
  competing configuration surface for the same chip, selected by RCAN YAML
  rather than by `OPENCASTOR_DRIVE`. `docs/hardware-guide.md:174-181` teaches
  that one. It is not the surface that drives the car, and unlike
  `rc-car-actuator` it falls back to mock on any failure
  (`castor/drivers/pca9685.py:146`). Two halves of the same product disagree on
  the single most expensive decision in the codebase.
- `RCANEnvelope.swift:42` declares `actuatorName` and `:85-87` serialises it,
  but no app-target code ever populates it. The multi-actuator flip described in
  `opencastor-host-config-rail` would still take every command offline with a
  422 at the parser.

## Ranked fixes to get under ten minutes

Ordered by minutes removed per hour spent.

| # | Fix | File to change | Effort | Removes |
|---|---|---|---|---|
| 1 | Publish a release containing `castor up`, and make pip pick it. Yank or post-release the `2026.*` CalVer line so `3.x` wins, then update the two-line hero. | `pyproject.toml`, `website/src/pages/index.astro:218`, `docs/setup/pairing.md:13` | 2 h plus the PyPI token | Route A goes from impossible to viable |
| 2 | Publish `rc-car-actuator` to PyPI and promote it out of the optional extra. It is already built and twine-checked. | `RobotRegistryFoundation/rc-car-actuator` upload, `pyproject.toml:86` | 1 h plus the token | `resolve_actuator()` stops falling back to `noop` |
| 3 | Make `castor up` write the drive variables **uncommented** when it detects 0x40, behind one explicit prompt or `--real-wheels`, and print the wheels-off-the-ground warning. Fix the channel defaults to throttle 1 / steering 0. | `castor/up.py:415-431`, `castor/templates/rc_car/gateway-policy.env.tmpl:45-49` | 4 h | The whole of trap 2 and trap 6, the largest single block |
| 4 | Fix the mDNS record, then ship the advertiser as a fifth unit from `castor up`. The record must carry gateway port, rrn and manifest path (which is why it is off today), and must publish on `_opencastor._tcp`. `~/bob/advertise.py` already does all of this by hand. | new `castor/discovery.py`, `castor/up.py:136-211`, `castor/rcan/mdns.py:38`, `robot.rcan.yaml.tmpl:35` | 6 h | Trap 3, and the "cannot find the robot" report closes for good |
| 5 | Make the ports agree. Either add 8081 and 8082 to the app sweep or move `castor up`'s base_port into the swept set. | `opencastor-ios/.../RobotDiscovery.swift:100` or `castor/up.py:54-56` | 1 h | The second half of trap 3 |
| 6 | Let the image enable I2C. The `config.txt` prohibition protects the Imager's own customisation, not the dtparam block; add a narrow, asserted exception, or have firstboot call `raspi-config nointeractive do_i2c 0` and reboot a third time. | `scripts/image/firstboot/firstboot.sh`, `scripts/image/selftest.sh:136` | 3 h | Trap 4, the only remaining sudo step |
| 7 | Fix the manifest's own contract so a model cannot draft a stop. Make the schema default match the prose, or drop the default and make the field required. | `castor/templates/rc_car/ROBOT.md.tmpl:90-92` | 0.5 h | Trap 7, at the cheapest price on this list |
| 8 | Ship `PCA9685-BRINGUP.md` into `docs/hardware/`, since the generated policy file already tells the user to follow it. | new `docs/hardware/pca9685-bringup.md` from `~/rover/PCA9685-BRINGUP.md`, `gateway-policy.env.tmpl:33` | 1 h | Trap 11, and it is a copy |
| 9 | Point `castor doctor` at the robot it provisioned: read `<home>/gateway-policy.env`, probe the real gateway port instead of the hard-coded 18789, call `castor gaps`, and add the physical checks (`/dev/i2c-1` present, `OPENCASTOR_DRIVE` set when 0x40 answers, something answering on the mDNS type, `usb_max_current_enable` versus a streaming USB camera). | `castor/doctor.py:196`, `:101-117`, `:379-415` | 4 h | Traps 2, 3, 4, 9, 12 and the carbot USB lesson, all from silent to printed |
| 10 | Delete or gate the mock fallback in the second PCA9685 driver, and point `docs/hardware-guide.md` at the surface that actually drives. | `castor/drivers/pca9685.py:132-146`, `docs/hardware-guide.md:174-181` | 2 h | Trap 5 |
| 11 | Host the image as one file so no terminal is needed, and restate the stopwatch to start at "click the download link". | release process, `docs/IMAGE.md:195-229` | 2 h | 1.5 min and the contradiction in the doc's central claim |
| 12 | Surface the gateway HTTP status in the app so a 422 does not read as "didn't answer". | `opencastor-ios/.../GatewayClient.swift:200-225`, `DriveStreamer.swift:421` | 2 h | Trap 8 |
| 13 | Fix `castor hub install` to fail loudly, and add the seven missing `config.rcan.yaml` files. | `castor/hub.py:354-357`, `community-recipes/*/` | 1 h | Trap 10 |
| 14 | Populate `actuatorName` from the paired robot so the multi-actuator flip is survivable. | `opencastor-ios/.../RCANEnvelope.swift:42` | 2 h | The latent 422 |
| 15 | Reconcile the install and port documentation, and give `castor init` car-shaped defaults or a preset question. Four install commands and four gateway ports are currently documented; none matches the live machine. | `README.md:44`, `CLAUDE.md:15,207,303`, `docs/setup/pairing.md:29`, `docs/robot-md-claude-code.md:7`, `castor/init_wizard.py:100-110` | 3 h | The confusion tax on every other step |

Fixes 1 through 8 total **17.5 hours** and are the ones that change the clock.
Fixes 7 and 8 are ninety minutes between them and remove two of the twelve traps.
With them, the image path becomes: download one file, flash, boot twice, scan,
approve, drive, with the wheels live on first power and I2C already on. That is
credibly inside ten minutes from "click Write" and inside fifteen from a cold
start.

## One caution on fix 3

Turning wheels on by default inverts a rule the codebase argues for carefully in
at least four places (`backend.py:10-21`, the template header at `:28-38`,
`bearers.yaml:10-13` on the live rover, `up.py:415-416`). The rule is right: a
driver built by accident must not move a real vehicle. The proposal is not to
delete the rule but to move the deliberate act from "find and edit a file
nobody mentioned" to "answer one question in the tool that just detected your
chip", and to keep the wheels-off-the-ground warning attached to it. The
envelope, the deadman, the tier gates and the 5 s / 10 % first approval
(`FirstDrive.swift:103-107`) all still stand between that flip and motion.

## Two memory notes are now stale

- `opencastor-ten-minute-goal` says "Console service NOT yet in `up`". It is.
  `castor/up.py:183` writes `{name}-console.service` and `:443` mints its token.
  The next gap on the ten-minute path is not the console, it is the three walls
  in the summary above.
- `opencastor-rover-rail` records the rover as running real wheels. It is back on
  `simulated` today (`~/rover/gateway-policy.env:45`), with a backup taken the
  same minute carbot took the bus (`gateway-policy.env.bak-carbot-20260904-1126`).
  The rover's own `smoke.sh` derives its hardware assertion from
  `OPENCASTOR_DRIVE` rather than pinning it, so it passes correctly either way.

## Also worth checking before shipping

- Ten `config/presets/*.yaml` pin `claude-opus-4-6`. Confirm that identifier
  still resolves before a newcomer's first chat turn depends on it.
- `config/presets/sunfounder_picar.rcan.yaml` declares `physics.type:
  differential` for a car with a steering servo; `sunfounder_picar_x` is the
  only preset that says `ackermann`.
- There is no `pca9685_rc` preset in `config/presets/` at all. Every PCA9685
  preset there is differential drive with four wheel channels
  (`sunfounder_picar.rcan.yaml:28-42`), not a servo plus an ESC. The wizard's
  catalog names an `rpi_rc_car` preset (`castor/setup_catalog.py:541`, "RPi RC
  Car + PCA9685 + CSI Camera") that has **no backing YAML file**, and
  `hardware_detect.suggest_preset()` can return it (`:1218-1222`). The RC-car
  shape exists only in `castor/templates/rc_car/`, reachable only through
  `castor up`.
- In `suggest_preset` the PCA9685 check is twelfth in a first-match-wins ladder
  (`castor/hardware_detect.py:1131-1236`). A car with an OAK-D, a Hailo, or an
  Arduino anywhere on it is classified as something else before the drive chip
  is ever considered.
- Preset RRNs are hardcoded and shared, so two owners of the same kit collide on
  identity.
- The `RRF_STUB_PORT = 8090` is hard-coded rather than derived from
  `--base-port` (`castor/up.py:56`), so a second robot on one host collides and
  is handled by a fallback that looks for `~/bob/keys/rrf`, a bench path
  (`castor/up.py:497-507`).
