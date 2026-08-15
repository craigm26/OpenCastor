# The flashable Pi image

**Blank SD card to a robot serving its own pairing QR. No SSH, no terminal, no
typed command, ever.**

This is the operator's runbook for `scripts/image/`. It covers the one command
that bakes the image, how to flash it, the stopwatch procedure that decides
whether the ten-minute goal is actually met, and what to look at on first boot.

Issue: [#931](https://github.com/craigm26/OpenCastor/issues/931).

---

## What the person flashing this card actually does

1. Open Raspberry Pi Imager, choose `opencastor-pi.img.xz`, fill in the
   hostname and Wi-Fi in the Imager's own customization dialog, write.
2. Put the card in the Pi. Power it on. Wait.
3. Open `http://<the-hostname-they-typed>.local/` on their phone.
4. Point the OpenCastor app's camera at the QR on that page.

That is the whole interface. There is no step where they read a filesystem path
out of a terminal, because there is no terminal.

**The robot takes its name from the hostname typed into the Imager.** This is
the only name on the machine a human chose, and it is what the pairing page is
headed with.

---

## What is inside the image

| | |
|---|---|
| Base | Raspberry Pi OS Lite arm64 (trixie, Python 3.13), unmodified except as below |
| Runtime | `opencastor` in a venv at `/opt/opencastor`, built from the working tree |
| Actuator | `rc-car-actuator` — **not on PyPI**, carried as a wheel so the image never waits on a publish |
| Gateway | `robot-md-gateway`, pinned to the build the bench runs |
| Brain | ollama + `qwen3.5:2b` (2.6 GiB) already in the store, so the robot has a local model before it has a network |
| Provisioner | `opencastor-firstboot.service` — runs `castor up` once, then never again |
| Interface | `opencastor-qr.service` — the pairing page on port 80 |
| Provenance | `/etc/opencastor-image.json` — the commit, whether the tree was dirty, and the wheelhouse manifest hash |

The NVIDIA CUDA libraries in the official ollama tarball (2.1 GiB of its
2.2 GiB) are dropped. A Pi has no NVIDIA anything.

---

## Before you start

Four inputs must be staged on the build host, which **must be an arm64
machine** — the build uses a native chroot and there is no qemu in it:

```
~/image-build/raspios-lite-arm64.img.xz     Raspberry Pi OS Lite arm64
~/image-build/ollama-linux-arm64.tar.zst    the official ollama release tarball
~/.ollama/models                            with qwen3.5:2b pulled
~/projects/RobotRegistryFoundation/rc-car-actuator/dist/   the built wheel
```

Every path is overridable — `./scripts/image/build.sh --help` lists the
`OPENCASTOR_*` environment variables.

The build host's Python must be **3.13**, the same minor version as the base
image. Compiled wheels are tagged `cp313` and do not cross a minor version;
both `build-wheelhouse.sh` and `build.sh` refuse to continue otherwise.

---

## Build it

### 1. The wheelhouse — no sudo

```bash
./scripts/image/build-wheelhouse.sh
```

Resolves every wheel the image needs (98 of them, ~300 MiB), builds
`opencastor` from the working tree, copies `rc-car-actuator` out of its `dist/`,
and then **proves the set is complete** by building a scratch venv with
`--no-index` and running `castor up --help` out of it. If that venv builds with
the index refused, so will the image's.

About 90 seconds. Re-run it whenever the working tree changes.

### 2. The image — the one sudo

```bash
sudo ./scripts/image/build.sh
```

Run `sudo ./scripts/image/build.sh --dry-run` first — it validates every input,
prints the whole plan, and touches nothing. It also works without sudo.

Useful flags:

| Flag | Why |
|---|---|
| `--dry-run` | validate and print the plan; no root needed, nothing created |
| `--shrink` | shrink the rootfs back to its contents + 1 GiB before emitting. The Pi re-expands it on first boot. **This is the single biggest lever on the ten-minute clock** — see below |
| `--no-compress` | stop at the `.img`; skip the 10–20 minute xz while iterating |
| `--reuse-img` | keep the already-unpacked work image; every stage is idempotent |
| `--xz-preset N` | 0–9, optionally with xz's `e` suffix. Validated when the flag is parsed, not when `xz` finally runs 25 minutes later |

Output lands in `~/image-build/work/`:

```
opencastor-pi.img.xz
opencastor-pi.img.xz.sha256
opencastor-image.json          <- what was built
```

### Which build is this?

Every build records what it was made of, in two places: `/etc/opencastor-image.json`
inside the image, and `opencastor-image.json` next to the artifacts (because an
`.img.xz` gets handed around far more often than a booted robot does).

```json
{
  "built_at": "2026-08-15T09:12:44Z",
  "git_head": "151c5a4d63677bb61577ee126c1d8ae299ca8153",
  "git_branch": "feat/pair-attest-pub",
  "git_dirty": false,
  "wheelhouse_manifest_sha256": "…",
  "wheelhouse_wheels": 98,
  "base_image": "raspios-lite-arm64.img.xz",
  "model": "qwen3.5:2b"
}
```

`git_dirty` is the one that matters. `build-wheelhouse.sh` builds the
`opencastor` wheel **from the working tree**, so a build made over uncommitted
edits ships code that exists in no commit and `git_head` describes nothing.
`build.sh` prints a loud warning in that case. Commit first, re-run
`build-wheelhouse.sh`, then build — or accept an image nobody can reproduce.

### Expected wall clock (Raspberry Pi 5, USB-3 SSD as the work disk)

| Stage | Time |
|---|---|
| preflight (reads the ollama tarball to measure it) | ~20 s |
| unpack the base image (2.77 GiB) | 1–2 min |
| grow: truncate, `parted resizepart`, `resize2fs` | ~1 min |
| chroot: accounts, venv, ~300 MiB of wheels installed | 2–4 min |
| ollama + the 2.6 GiB model copied in | 2–3 min |
| verify, unmount, fsck | ~1 min |
| `xz -T0 -6` of a 7.8 GiB image | **10–20 min** |

**About 25 minutes total**, most of it the final compression. Use
`--no-compress` while iterating.

---

## Flash it

Raspberry Pi Imager → **Use custom** → `opencastor-pi.img.xz`.

Then click the gear / **Edit settings** and set:

- **hostname** — this becomes the robot's name and the address of its pairing
  page. Pick something the household will recognise.
- **Wi-Fi SSID, password, country**
- everything else is optional; SSH is not needed and is not used by anything
  in this image

**The build never writes to the boot partition.** It mounts it read-only and
fingerprints it on the way in and on the way out, and fails the build if a
single byte moved. The fingerprint is a recursive sha256 over every file's path
**and its contents** — the first version hashed names and sizes only, which
would not have noticed `sed -i s/x/y/` on `cmdline.txt`, i.e. exactly the file
the claim is about. Hostname, Wi-Fi, locale and SSH are the Imager's job, and
the image stays out of the way of that entirely.

### The Pi boots twice, and that is normal

With Imager customization, `cmdline.txt` carries
`systemd.run=/boot/firstrun.sh … systemd.run_success_action=reboot`. The first
boot runs only the Imager's own script — hostname, Wi-Fi, the user account —
and then reboots. **OpenCastor provisions on the second boot.** If the page is
not there yet, the Pi is probably still on pass one.

---

## The measured ten minutes

The goal is a robot up in under ten minutes on common hardware, starting from a
blank card. That clock includes the flash, and **the flash is the largest slice
of it.** Do not accept the image on the strength of a fast build; accept it on
the strength of this stopwatch.

Run it with somebody who has not seen the robot before. Watch them; do not
help. Record the hardware, because the answer depends on it.

| # | Step | Start the clock at | Record |
|---|---|---|---|
| 1 | Imager: select the image, set hostname + Wi-Fi | clicking **Write** | ⏱ write + verify |
| 2 | Card into the Pi, power on | card seated | ⏱ |
| 3 | First boot (Imager's `firstrun.sh`, then reboot) | power on | ⏱ to reboot |
| 4 | Second boot to `http://<hostname>.local/` answering | reboot | ⏱ to first page |
| 5 | Page reaches **ready to pair** | first page | ⏱ to QR |
| 6 | Scan with the app; robot appears paired | QR visible | ⏱ to paired |
| | **Total** | | **must be < 10:00** |

Also record:

- SD card model and speed class, and whether the reader is USB 2 or USB 3
- whether Imager's **verify** pass was left on (leave it on)
- the image's uncompressed size (`--shrink` or not)
- what the page listed under *Working, with these caveats*
- `castor gaps` output — see below

### The arithmetic to expect, honestly

Without `--shrink` the image is about **7.8 GiB uncompressed**. Imager writes
every byte of that and then reads it all back to verify:

| Reader + card | Write + verify |
|---|---|
| USB 3 + UHS-I A2 (~80 MB/s) | ~3.5 min |
| USB 2 + ordinary class 10 (~20 MB/s) | ~13 min — **blows the budget on step 1 alone** |

With `--shrink` the image is roughly **5.8 GiB**, which is about 2 GiB less to
write and 2 GiB less to verify.

Steps 2–6 should total around 2–3 minutes: two boots plus a `castor up` that
has nothing to download. So the ten minutes is comfortably met on a fast reader
and *not* met on a slow one. If a run misses, the number to report is step 1's,
not the total — the fix is image size and card choice, not the robot.

---

## First boot: what to check

Everything below is visible from the phone. None of it needs a shell.

1. **`http://<hostname>.local/`** answers, even while provisioning. If it says
   *setting up*, that is correct; it refreshes itself every four seconds.
2. It reaches **ready to pair** and shows a QR.
3. It says **"Everything this Pi has hardware for has a driver and a brain. No
   gaps."** — that line is `castor gaps` coming back empty. On this image it
   should: the two gaps the bench used to ship with (no actuator package, no
   local model) are closed by construction.
4. Anything listed under *Working, with these caveats* is a real finding. Each
   caveat names itself and says what to do.
5. `http://<hostname>.local/status.json` is the same information as machine
   -readable JSON, if you want to script the acceptance run.

If you do have a shell and want the detail:

```bash
systemctl status opencastor-firstboot opencastor-qr ollama
cat /var/lib/opencastor/status.json
less /var/lib/opencastor/firstboot.log
sudo -u opencastor XDG_RUNTIME_DIR=/run/user/$(id -u opencastor) \
     systemctl --user status            # the gateway/runtime/console castor up wrote
```

---

## What the page says when something degraded

`castor up` reports honestly, so first boot can succeed partially — and when it
does, the page must still come up and say which half. Every caveat is tagged:

| Tag | What happened | What to do |
|---|---|---|
| `brain` | ollama did not answer on `:11434` in 90 s | The robot pairs and drives anyway; chat has no local model. `systemctl status ollama` |
| `user-manager` | systemd started no session for the `opencastor` account, so `castor up` ran `--no-start`: the services are configured but not running | Reboot once. If it persists: `sudo loginctl enable-linger opencastor` |
| `gaps` | `castor gaps` found something real — a chip on the bus with no driver | Not a failure. It is a skill nobody has written yet; see [SKILL-GAPS.md](SKILL-GAPS.md). A gap never closes itself |
| `no-qr` | `pair-qr.png` was not written | **No stamp was written, so the next reboot retries.** In the meantime `pair-payload.json` in the robot home is the documented fallback; the app takes it pasted |
| `castor-up` | `castor up` exited non-zero | **No stamp was written, so the next reboot retries.** Identity — keys, tokens, RRN — is reused, not regenerated |
| `no-castor` / `no-user` | the venv or the service account is missing | The image build did not finish. Rebuild it |
| `aborted` | first boot was stopped by a signal — the unit's `TimeoutStartSec`, a shutdown, or a Ctrl-C. The caveat names the signal and the phase | Reboot; it retries from where it can |

**A failed first boot leaves no `/var/lib/opencastor/.provisioned` stamp on
purpose.** The stamp is what the unit's `ConditionPathExists=!` reads, so no
stamp means the next boot tries again. A stamp written on failure would be a
robot that is permanently broken and permanently certain it is finished.

Two ways that used to break, both now closed and both rehearsed in the
self-test:

- **`no-qr` used to stamp anyway.** It degraded, said the right words, and then
  wrote the stamp — so a robot whose pairing code never got written was
  unpairable forever *and* certain it was finished, with rebooting (the
  operator's only tool) taken away from it. It now exits non-zero and stamps
  nothing.
- **A timed-out first boot used to report success.** `TimeoutStartSec` expiring
  sends SIGTERM, and with only an `EXIT` trap the script read `$?` as 0 — the
  status of the `while` loop whose `sleep` had just been killed — and wrote
  `"ok": true` with an empty `degraded` list. SIGTERM, SIGINT and SIGHUP are now
  trapped by name; the handler records the `aborted` caveat, writes the status
  file *before* logging (systemd signals the whole cgroup, `tee` included), and
  exits 143.

---

## Security: what the pairing page exposes

The QR encodes an **actuate-tier bearer**, and the page hands it to anyone on
the LAN who asks for it. That is deliberate: at minute three the operator has no
credential, so the kiosk cannot ask for one. The trust model is a QR sticker on
the robot's chassis — presence on the home network.

Two things follow, and both are already true in the code:

- `pair-payload.json` is **not** served. The bytes are the same, but a
  curl-able token is a materially worse exposure than a picture something has
  to decode, and there is no reason to offer both.
- The page says so, in its own footer: *anyone on this network can see this
  code until you pair.*

**After pairing, turn the page off:**

```bash
sudo systemctl disable --now opencastor-qr
```

Re-enable it any time you need to re-pair a phone. Nothing else depends on it.

---

## When it goes wrong

**The build failed and left a loop device behind.** It should not have — the
teardown trap unmounts in reverse order and detaches on every exit path. If it
did anyway: `losetup -a`, then `sudo losetup -d /dev/loopN`.

**You pressed Ctrl-C and the build kept going.** Fixed, and it was worse than
it looked. `trap cleanup EXIT INT TERM` ran the teardown on the signal and then
*returned*, which in bash means execution resumes at the line after the
interrupted command — so the stages after the Ctrl-C ran against unmounted
paths, writing into `$WORK/mnt` on the host's own disk instead of into the
image. `INT`, `TERM` and `HUP` now have their own handler that tears down and
then exits `128+n`; only the `EXIT` trap is allowed to return.

**`pip` tried to reach the network inside the chroot.** It cannot: every
`chroot` in `build.sh` runs under `unshare -n`, so the stage lives in a network
namespace holding one down loopback interface — no addresses, no routes,
nowhere to go. What you will see instead is `chroot-stage.sh` failing on
`--no-index` with an unresolvable requirement. That is the wheelhouse being
incomplete, and it is a bug in `build-wheelhouse.sh`'s closure, not something to
work around by adding a network.

> **This used to be documented as a guarantee and was not one.** The earlier
> claim was that the chroot had no network because `/etc/resolv.conf` was not
> bind-mounted in. That guaranteed nothing. The base image ships an
> `/etc/resolv.conf` of its own (`nameserver 8.8.8.8` — read straight out of
> the base image's ext4), and a chroot shares the host's network stack whole,
> so an accidental `pip install` would have succeeded and the image's contents
> would have depended on the day it was built. `unshare -n` is the mechanism;
> `chroot-stage.sh` re-reads `/proc/net/dev` and refuses to install anything if
> it can see an interface other than `lo`.

**`python3-venv` is missing from the base image.** Handled: the wheelhouse
builder pre-fetches the `.deb`s rootless with `apt-get download`, and the chroot
`dpkg -i`s them offline.

**The page never appears.** Check the Pi is on pass two (see above), then that
the hostname resolves — `.local` needs mDNS, and some networks eat it. The IP
works just as well.

---

## What is verified, and what is not

`./scripts/image/selftest.sh` runs everything provable **without root** — 145
checks, all green as of this writing:

- `bash -n` and `shellcheck -S warning` on every script
- the wheelhouse's completeness, by building a scratch venv with `--no-index`
  and running `castor up --help` out of it
- `systemd-analyze verify` on all three units, against stubs at the exact
  absolute paths the units name
- that the unit's `ExecStart` and `build.sh`'s install destination agree
- that no script writes to `cmdline.txt`, `config.txt`, `firstrun.sh` or
  `userconf.txt`
- that the no-network invariant is still a mechanism: no bare `chroot` call
  survives in `build.sh`, the wrapper is `unshare -n`, `unshare` is a preflight
  requirement, and `chroot-stage.sh` still refuses a namespace with interfaces
  in it
- `build.sh --dry-run`, including that it creates nothing, that it names the
  commit it would bake, and that it warns when the tree is dirty
- `--xz-preset` argument validation, including the missing-value case
- the firstboot degradation path, run for real: a partial boot must still leave
  parseable JSON that names every cause, and no stamp — plus both ends of the
  stamp decision, a boot with a QR (stamps) and one without (must not)
- **signals**, which nothing used to exercise: `firstboot.sh` SIGTERM'd inside a
  foreground `sleep` must exit 143, write `ok: false`, carry exactly one
  `aborted` caveat naming the signal and the phase, and leave no stamp; and
  `build.sh`'s teardown traps — extracted from `build.sh` itself between
  markers and sourced, so it is the real code — must exit `128+n` on INT and
  TERM with **nothing** running after the teardown
- the guards, run in isolation: the boot fingerprint (including a probe showing
  the old name+size digest missing a same-length edit), the `--reuse-img` grow
  guard on both branches, and the provenance writer
- the pairing page, served by plain `/usr/bin/python3` and curled: the QR
  bytes, the content type, the instructions, the robot name, the caveats, the
  404 on `pair-payload.json`, the "still setting up" state with no status file
  at all, and a `status.json` holding `[]` / `null` / `17` / a bare string —
  all valid JSON, none of them objects, each of which used to take the page
  down with a 500

**Still unverified.** Nothing that needs root has been executed. Until somebody
runs a real `sudo ./scripts/image/build.sh` on hardware, these are code review
and nothing more:

- `losetup -P`, `mount`, `umount` on a real failure. The teardown *trap logic*
  is now proven rootless (see above); the mount operations it drives are not
- `truncate` + `parted resizepart` + `resize2fs` against a real partition table
- `--shrink`: `resize2fs -P`, the `sfdisk -N` rewrite, and the truncate
- the native aarch64 chroot under `unshare -n`: `useradd`, `dpkg -i` of the
  venv `.deb`s, `pip`, and `chroot-stage.sh`'s own empty-namespace assertion
- the boot-partition fingerprint against a real mounted FAT partition. The hash
  function is proven rootless; the read-only mount and the compare are not
- `/etc/opencastor-image.json` actually landing in the image
- **and then, on hardware: flash, boot twice, and run the stopwatch above.**
  The ten minutes is a measurement, not a claim.
