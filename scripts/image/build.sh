#!/usr/bin/env bash
# Bake opencastor-pi.img.xz — the one command between a wheelhouse and a card.
#
# THE CLOCK THIS SERVES. Ten minutes, starting at a blank SD card, for someone
# who has never used a terminal. Flash with Raspberry Pi Imager, put the card
# in, wait, and the robot's own pairing QR is on a web page. Everything that
# used to be typed at a robot's shell — the venv, `pip install`, the model
# pull, `castor up`, reading a PNG path out of scrollback — has to be done here
# instead, once, by the person building the image.
#
# WHAT IS DELIBERATELY ABSENT.
#
#   * No qemu. This host IS a Pi, the chroot runs the image's own aarch64
#     binaries natively, and the build asserts that before it touches anything.
#     binfmt emulation is roughly 10x slower and has its own failure modes;
#     paying for it on a machine that does not need it would be silly.
#   * No network in the chroot, and it is a MECHANISM rather than a claim.
#     Every chroot below runs under `unshare -n`, so the only interface in the
#     namespace is a down loopback and a stray `pip install` has nowhere to go.
#     The first draft rested this on declining to bind-mount /etc/resolv.conf,
#     which guaranteed nothing: the base image ships an /etc/resolv.conf of its
#     own (nameserver 8.8.8.8) and a plain chroot inherits the host's network
#     stack whole, so an accidental download would have worked perfectly. Every
#     Python byte comes from a wheelhouse built on this host by
#     build-wheelhouse.sh and PROVEN complete there. A build that can reach
#     PyPI is a build whose output depends on the date.
#   * No touching the boot partition. Hostname and Wi-Fi belong to the Raspberry
#     Pi Imager. This build mounts boot READ-ONLY and checksums cmdline.txt on
#     the way in and the way out, because "we didn't touch it" is a claim worth
#     being able to prove.
#   * No leftovers. Loop devices and mounts come down in a trap, in reverse
#     order, on every exit path — a failed build that strands /dev/loop0 makes
#     the NEXT build fail for a reason that has nothing to do with the change.
#
# RUN scripts/image/build-wheelhouse.sh FIRST, without sudo. This script is the
# only one that needs root, and it needs it for as few minutes as possible.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"

# sudo resets HOME to /root on Debian, so ~ here is not the ~ where the
# operator staged 3 GB of inputs. Resolve the INVOKING user's home explicitly;
# defaulting to /root would send every input path somewhere nothing exists and
# produce four "not found" errors that all look like the operator's fault.
OC_INVOKER="${SUDO_USER:-$(id -un)}"
OC_INVOKER_HOME="$(getent passwd "$OC_INVOKER" | cut -d: -f6)"
[ -n "$OC_INVOKER_HOME" ] || OC_INVOKER_HOME="$HOME"

BASE_IMG="${OPENCASTOR_BASE_IMG:-$OC_INVOKER_HOME/image-build/raspios-lite-arm64.img.xz}"
OLLAMA_TAR="${OPENCASTOR_OLLAMA_TAR:-$OC_INVOKER_HOME/image-build/ollama-linux-arm64.tar.zst}"
WHEELHOUSE="${OPENCASTOR_WHEELHOUSE:-$OC_INVOKER_HOME/image-build/wheelhouse}"
MODEL_STORE="${OPENCASTOR_MODEL_STORE:-$OC_INVOKER_HOME/.ollama/models}"
MODEL="${OPENCASTOR_MODEL:-qwen3.5:2b}"
WORK="${OPENCASTOR_IMAGE_WORK:-$OC_INVOKER_HOME/image-build/work}"
XZ_PRESET="${OPENCASTOR_XZ_PRESET:-6}"

REPO_ROOT="$(cd "$HERE/../.." && pwd)"

OC_USER=opencastor
OC_STATE=/var/lib/opencastor
VENV=/opt/opencastor
PAYLOAD_DIR=/opt/opencastor-image
MIN_GROW_MIB=5120          # the mission floor; the computed figure usually wins
SLACK_MIB=1024             # headroom left free inside the rootfs after install

DRY_RUN=0; REUSE_IMG=0; NO_COMPRESS=0; SHRINK=0
LOOPDEV=""; ROOTMNT=""; MOUNTED=0; ROOT_PART=""; ROOT_START=""

usage() {
  cat <<'EOF'
build.sh — bake opencastor-pi.img.xz. Needs root (loop devices, mount, chroot).

  --dry-run        validate every input and print the plan. Touches nothing,
                   needs no root. Run this first, always.
  --reuse-img      keep an already-unpacked work image instead of re-extracting
                   the base (fast iteration; each stage is idempotent).
  --no-compress    stop after the .img; skip the xz and the checksum.
  --shrink         after installing, shrink the rootfs back to its contents
                   plus 1 GiB and truncate the image to match. Raspberry Pi OS
                   expands the last partition to fill the card on first boot,
                   so the robot loses nothing and the operator writes ~3 GiB
                   fewer bytes. Flashing is the largest single slice of the
                   ten minutes; this is the lever that shortens it. Untested
                   at root as of this writing — see docs/IMAGE.md.
  --xz-preset N    xz compression preset, 0-9 with an optional 'e' suffix
                   (default 6; -9 buys little on a payload that is mostly an
                   already-compressed 2.7 GB model).
  -h, --help       this

Inputs (override with the matching OPENCASTOR_* environment variable):
  base image   OPENCASTOR_BASE_IMG      ~/image-build/raspios-lite-arm64.img.xz
  ollama       OPENCASTOR_OLLAMA_TAR    ~/image-build/ollama-linux-arm64.tar.zst
  wheelhouse   OPENCASTOR_WHEELHOUSE    ~/image-build/wheelhouse
  model store  OPENCASTOR_MODEL_STORE   ~/.ollama/models
  model        OPENCASTOR_MODEL         qwen3.5:2b
  workdir      OPENCASTOR_IMAGE_WORK    ~/image-build/work
EOF
}

# xz's own grammar: a single digit 0-9, optionally followed by 'e' for the
# slower "extreme" variant. Checked HERE and not at minute twenty-five, because
# the only place an invalid preset shows up otherwise is `xz: (null): Invalid
# argument` after the image is already built and the operator has waited out
# every other stage.
check_xz_preset() {
  case "$1" in
    [0-9]|[0-9]e) : ;;
    '')  die "$2 is empty — it takes 0-9, optionally followed by 'e'" ;;
    *)   die "$2 must be 0-9, optionally followed by 'e' (got '$1')" ;;
  esac
}

# The default can arrive from the environment, which no flag parser ever sees.
check_xz_preset "$XZ_PRESET" "OPENCASTOR_XZ_PRESET"

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --reuse-img) REUSE_IMG=1; shift ;;
    --no-compress) NO_COMPRESS=1; shift ;;
    --shrink) SHRINK=1; shift ;;
    # `[ $# -ge 2 ]` before "$2" and not after: under `set -u` a trailing
    # `--xz-preset` with no value dies as "$2: unbound variable", which tells
    # the operator nothing about the flag they mistyped.
    --xz-preset)
      [ $# -ge 2 ] || die "--xz-preset needs a value: 0-9, optionally followed by 'e'"
      check_xz_preset "$2" "--xz-preset"
      XZ_PRESET="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

OUT_IMG="$WORK/opencastor-pi.img"
OUT_XZ="$OUT_IMG.xz"

# --- BEGIN teardown-traps (extracted verbatim by selftest.sh) --------------
# Teardown. Registered before the first mount, runs on every exit path.
#
# WHY THE SIGNALS ARE THEIR OWN HANDLER, AND NOT `trap cleanup EXIT INT TERM`.
# That is what this used to say, and it was a bug with teeth: a bash trap
# handler that RETURNS hands control back to the line after the interrupted
# command, so Ctrl-C tore every mount down and then carried on building —
# `install` into an unmounted path, `chroot` into a directory that was no
# longer a filesystem, `losetup -d` on a device already gone. The operator
# pressed Ctrl-C and watched the build keep going, writing into $WORK/mnt on
# the host's own disk. A signal handler has to EXIT; only the EXIT trap may
# return.
CLEANED=0
cleanup() {
  set +e
  if [ "$CLEANED" -eq 1 ]; then return 0; fi   # signal path already tore down
  CLEANED=1
  if [ "$MOUNTED" -eq 1 ] && [ -n "$ROOTMNT" ]; then
    sync
    # Deepest first. `umount -R` alone is usually enough, but a chroot that
    # left a process holding /proc turns "usually" into a stranded loop device
    # and a build that fails tomorrow for today's reason.
    local m
    for m in "$ROOTMNT/mnt/wheelhouse" "$ROOTMNT/boot/firmware" "$ROOTMNT/boot" \
             "$ROOTMNT/sys" "$ROOTMNT/proc" "$ROOTMNT/dev/pts" "$ROOTMNT/dev"; do
      mountpoint -q "$m" 2>/dev/null && { umount "$m" 2>/dev/null || umount -l "$m" 2>/dev/null; }
    done
    umount -R "$ROOTMNT" 2>/dev/null
    mountpoint -q "$ROOTMNT" 2>/dev/null && umount -l "$ROOTMNT" 2>/dev/null
    MOUNTED=0
  fi
  if [ -n "$LOOPDEV" ]; then
    sync
    losetup -d "$LOOPDEV" 2>/dev/null && say "detached $LOOPDEV"
    LOOPDEV=""
  fi
  return 0
}

on_exit() {
  local rc=$?
  set +e
  cleanup
  [ "$rc" -ne 0 ] && printf '\n%sBuild failed (rc=%d). Mounts and loop devices were released.%s\n' \
      "$OC_C_RED" "$rc" "$OC_C_OFF" >&2
  return $rc
}

# Tear down, then LEAVE. `exit` here also runs on_exit, which is why cleanup
# is idempotent rather than merely careful.
on_signal() {
  local name="$1" num="$2"
  set +e
  printf '\n%sSIG%s — stopping. Tearing the mounts and the loop device down first.%s\n' \
      "$OC_C_YEL" "$name" "$OC_C_OFF" >&2
  cleanup
  exit $(( 128 + num ))
}

install_teardown_traps() {
  trap on_exit EXIT
  trap 'on_signal INT 2'  INT
  trap 'on_signal TERM 15' TERM
  trap 'on_signal HUP 1'  HUP
}
# --- END teardown-traps ----------------------------------------------------

# ===========================================================================
stage "preflight — validating every input before anything is touched"
# ===========================================================================
oc_require_aarch64
say "host arch  aarch64 (native chroot; no qemu)"

# `unshare` is load-bearing, not a nicety: it is the whole of the no-network
# guarantee for the chroot stage. A host without it would build an image that
# LOOKS identical and whose pip could have reached PyPI.
oc_require_cmds xz zstd tar losetup parted sfdisk resize2fs e2fsck dumpe2fs \
                truncate chroot unshare install python3 sha256sum du df stat mountpoint

HOST_PYVER="$(oc_pyver python3)"
[ "$HOST_PYVER" = "3.13" ] || die \
  "host python is $HOST_PYVER; the wheelhouse and the trixie base image are 3.13.
   Compiled wheels are tagged cp3XX and do not cross a minor version."
say "host python $HOST_PYVER"

oc_require_file "$BASE_IMG"   "base image"
oc_require_file "$OLLAMA_TAR" "ollama tarball"
oc_require_dir  "$WHEELHOUSE" "wheelhouse (run build-wheelhouse.sh first)"
oc_require_file "$WHEELHOUSE/requirements-image.txt" "wheelhouse requirements"
oc_require_file "$WHEELHOUSE/MANIFEST.sha256" "wheelhouse manifest"
[ -n "$(find "$WHEELHOUSE" -maxdepth 1 -name 'opencastor-*.whl' -print -quit)" ] \
  || die "no opencastor wheel in $WHEELHOUSE — run scripts/image/build-wheelhouse.sh"
[ -n "$(find "$WHEELHOUSE" -maxdepth 1 -name 'rc_car_actuator-*.whl' -print -quit)" ] \
  || die "no rc_car_actuator wheel in $WHEELHOUSE. It is not on PyPI; without it
   \`castor up\` resolves the gateway's noop actuator and the rover has no wheels."
[ -n "$(find "$WHEELHOUSE" -maxdepth 1 -name 'pip-*.whl' -print -quit)" ] \
  || die "no pip wheel in $WHEELHOUSE — the image's venv is bootstrapped from it"

# -- the model: manifest plus exactly the blobs it references ---------------
# The host store is ~29 GB across every model ever pulled. Copying it whole
# would be a 30 GB image; the manifest names the three blobs that are actually
# qwen3.5:2b, and those are the only bytes that travel.
MODEL_REPO="${MODEL%%:*}"; MODEL_TAG="${MODEL##*:}"
MODEL_MANIFEST="$MODEL_STORE/manifests/registry.ollama.ai/library/$MODEL_REPO/$MODEL_TAG"
oc_require_file "$MODEL_MANIFEST" "ollama manifest for $MODEL"

mapfile -t MODEL_DIGESTS < <(python3 - "$MODEL_MANIFEST" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
for layer in [m["config"], *m["layers"]]:
    print(layer["digest"])
PY
)
[ "${#MODEL_DIGESTS[@]}" -gt 0 ] || die "manifest for $MODEL references no blobs"
MODEL_BYTES=0
for d in "${MODEL_DIGESTS[@]}"; do
  blob="$MODEL_STORE/blobs/${d/:/-}"
  oc_require_file "$blob" "blob $d referenced by $MODEL"
  MODEL_BYTES=$(( MODEL_BYTES + $(stat -c%s "$blob") ))
done
say "model      $MODEL — ${#MODEL_DIGESTS[@]} blobs, $(oc_human "$MODEL_BYTES")"

# -- how much the image has to grow -----------------------------------------
# Every NVIDIA byte in the ollama tarball is dead weight on a Pi: cuda_v12 and
# cuda_v13 are 2.1 GB of the 2.2 GB. Measured, not guessed, because the figure
# below is what decides whether the rootfs runs out at minute fourteen.
say "measuring ollama tarball (excluding cuda_v*) — this reads 1.5 GB, ~30s"
OLLAMA_BYTES="$(zstd -dc "$OLLAMA_TAR" | tar -tv 2>/dev/null \
  | awk '$6 !~ /^lib\/ollama\/cuda_v/ {s+=$3} END{print s+0}')"
[ "$OLLAMA_BYTES" -gt 0 ] || die "ollama tarball listed as empty — is $OLLAMA_TAR intact?"
WHEEL_BYTES="$(oc_du_bytes "$WHEELHOUSE")"
# Installed trees run ~3x their wheels (unzipped, plus .dist-info and the
# bytecode pip writes). Over-estimating here costs card space; under-estimating
# costs the whole build.
VENV_EST=$(( WHEEL_BYTES * 3 ))
PAYLOAD=$(( MODEL_BYTES + OLLAMA_BYTES + VENV_EST ))
GROW_MIB=$(( PAYLOAD / 1048576 + SLACK_MIB ))
if [ "$GROW_MIB" -lt "$MIN_GROW_MIB" ]; then GROW_MIB="$MIN_GROW_MIB"; fi

say "ollama     $(oc_human "$OLLAMA_BYTES") after dropping cuda_v12/cuda_v13"
say "wheelhouse $(oc_human "$WHEEL_BYTES") of wheels -> ~$(oc_human "$VENV_EST") installed"
say "grow by    ${GROW_MIB} MiB (payload $(oc_human "$PAYLOAD") + ${SLACK_MIB} MiB free)"

# -- room on the build host --------------------------------------------------
mkdir -p "$WORK"
BASE_UNPACKED="$(xz --robot --list "$BASE_IMG" | awk '/^totals/{print $5}')"
# This number is not just a free-space estimate any more: the --reuse-img grow
# guard compares against it, so an empty or non-numeric value would silently
# become a zero and re-grow the work image on every run.
case "$BASE_UNPACKED" in
  ''|*[!0-9]*) die "could not read the uncompressed size of $BASE_IMG out of \`xz --robot --list\`" ;;
esac
[ "$BASE_UNPACKED" -gt 0 ] || die "$BASE_IMG reports an uncompressed size of zero"
NEED_BYTES=$(( BASE_UNPACKED + GROW_MIB * 1048576 + PAYLOAD ))   # .img + the .xz beside it
AVAIL_BYTES=$(( $(df --output=avail -B1 "$WORK" | tail -1) ))
say "workdir    $WORK — need ~$(oc_human "$NEED_BYTES"), have $(oc_human "$AVAIL_BYTES")"
[ "$AVAIL_BYTES" -gt "$NEED_BYTES" ] || die "not enough free space in $WORK"

# -- provenance: WHAT is being baked, recorded before anything is baked -------
# An image is a frozen copy of a working tree, and six weeks later nobody can
# tell which one. Three facts answer that: the commit, whether the tree had
# uncommitted edits on top of it, and the hash of the wheelhouse manifest —
# because the wheelhouse is where the tree actually became bytes. All three go
# into the image at /etc/opencastor-image.json and land next to the artifacts.
#
# `-c safe.directory=*`: under sudo, git refuses a repo owned by another user,
# and provenance that silently degrades to "unknown" the moment you build the
# way the docs tell you to is worse than no provenance at all.
# --- BEGIN provenance (extracted verbatim by selftest.sh) ------------------
BUILT_AT="$(date -u +%FT%TZ)"
GIT_HEAD="$(git -c safe.directory='*' -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
GIT_BRANCH="$(git -c safe.directory='*' -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
if [ -n "$(git -c safe.directory='*' -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]; then
  GIT_DIRTY=true
else
  GIT_DIRTY=false
fi
MANIFEST_SHA="$(sha256sum "$WHEELHOUSE/MANIFEST.sha256" | cut -d' ' -f1)"
WHEEL_COUNT="$(find "$WHEELHOUSE" -maxdepth 1 -name '*.whl' | wc -l)"

# Written twice on a real build: into the image, and beside the artifacts.
write_provenance() {
  OC_P_BUILT="$BUILT_AT" OC_P_HOST="$(uname -srm)" \
  OC_P_GIT="$GIT_HEAD" OC_P_BRANCH="$GIT_BRANCH" OC_P_DIRTY="$GIT_DIRTY" \
  OC_P_MAN="$MANIFEST_SHA" OC_P_WHEELS="$WHEEL_COUNT" \
  OC_P_BASE="$(basename "$BASE_IMG")" OC_P_MODEL="$MODEL" \
  python3 - "$1" <<'PY'
import json, os, sys
doc = {
    "schema": 1,
    "built_at": os.environ["OC_P_BUILT"],
    "build_host": os.environ["OC_P_HOST"],
    "git_head": os.environ["OC_P_GIT"],
    "git_branch": os.environ["OC_P_BRANCH"],
    "git_dirty": os.environ["OC_P_DIRTY"] == "true",
    "wheelhouse_manifest_sha256": os.environ["OC_P_MAN"],
    "wheelhouse_wheels": int(os.environ["OC_P_WHEELS"]),
    "base_image": os.environ["OC_P_BASE"],
    "model": os.environ["OC_P_MODEL"],
}
with open(sys.argv[1], "w") as fh:
    fh.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
PY
}
# --- END provenance --------------------------------------------------------

say "provenance ${GIT_HEAD:0:12} on $GIT_BRANCH, wheelhouse manifest ${MANIFEST_SHA:0:12} ($WHEEL_COUNT wheels)"
if [ "$GIT_DIRTY" = true ]; then
  warn "THE WORKING TREE IS DIRTY. The opencastor wheel in $WHEELHOUSE was built
       from code that is not in any commit, so ${GIT_HEAD:0:12} does NOT describe
       what this image will run. Commit, re-run build-wheelhouse.sh, then rebuild
       — or accept an image nobody can reproduce."
fi

cat <<PLAN

  ${OC_C_BOLD}plan${OC_C_OFF}
    base         $BASE_IMG
    ->           $OUT_IMG  (grown by ${GROW_MIB} MiB)
    venv         $VENV  from $WHEELHOUSE, --no-index, in a native chroot
    ollama       $OLLAMA_TAR -> /usr/local (cuda_v* dropped)
    model        $MODEL -> /usr/share/ollama/.ollama/models
    firstboot    $PAYLOAD_DIR + opencastor-firstboot.service (once, then never)
    pairing page $PAYLOAD_DIR/qr_server.py on :80 (always, even when degraded)
    boot part    mounted READ-ONLY; the Imager owns hostname and Wi-Fi
    provenance   ${GIT_HEAD:0:12} ($GIT_BRANCH)$([ "$GIT_DIRTY" = true ] && echo " + UNCOMMITTED EDITS" || echo "") -> /etc/opencastor-image.json
    shrink       $([ "$SHRINK" -eq 1 ] && echo "yes — rootfs back to contents + ${SLACK_MIB} MiB (the Pi re-expands on first boot)" || echo "no (--shrink writes a smaller card, which is time off the ten minutes)")
    output       $([ "$NO_COMPRESS" -eq 1 ] && echo "$OUT_IMG (uncompressed)" || echo "$OUT_XZ + .sha256")

PLAN

if [ "$DRY_RUN" -eq 1 ]; then
  ok "dry run: every input is present and the plan above is what would happen."
  say "nothing was created, mounted, or modified. Re-run with sudo to build."
  exit 0
fi

oc_require_root
install_teardown_traps

# ===========================================================================
stage "work image — unpacking the base"
# ===========================================================================
if [ "$REUSE_IMG" -eq 1 ] && [ -f "$OUT_IMG" ]; then
  say "--reuse-img: keeping $OUT_IMG ($(oc_human "$(stat -c%s "$OUT_IMG")"))"
else
  rm -f "$OUT_IMG"
  say "xz -dc (~2.7 GB, a couple of minutes)"
  xz -dc "$BASE_IMG" > "$OUT_IMG"
  ok "$OUT_IMG  $(oc_human "$(stat -c%s "$OUT_IMG")")"
fi

# ===========================================================================
stage "grow — image file, then partition, then filesystem"
# ===========================================================================
# The three have to move in that order and all three have to move. Growing the
# file alone leaves the partition table describing the old size; growing the
# partition alone leaves ext4 using the old block count. Either half-step
# produces an image that flashes fine and runs out of space during install.
read -r ROOT_PART ROOT_START <<<"$(python3 - "$OUT_IMG" <<'PY'
import json, subprocess, sys
t = json.loads(subprocess.check_output(["sfdisk", "-J", sys.argv[1]]))["partitiontable"]
parts = t["partitions"]
assert len(parts) == 2, f"expected boot+root, saw {len(parts)} partitions"
root = parts[-1]
print(root["node"].rsplit(sys.argv[1], 1)[-1] or len(parts), root["start"])
PY
)"
ROOT_PART="${ROOT_PART//[!0-9]/}"
# Both numbers steer destructive arithmetic later (resizepart, and under
# --shrink an sfdisk rewrite plus a truncate). An empty one would silently
# become a zero, so this refuses rather than guesses.
[ -n "$ROOT_PART" ] && [ -n "$ROOT_START" ] || die \
  "could not read the partition table out of $OUT_IMG — is it a Raspberry Pi OS image?"
say "root partition: p$ROOT_PART (starts at sector $ROOT_START)"

# --- BEGIN grow-target (extracted verbatim by selftest.sh) -----------------
# The target is measured from the size the base image UNPACKS to, never from
# the size the work image happens to be right now. The first version wrote
# `TARGET=CUR+GROW` and then asked whether `CUR >= TARGET`, which is false for
# every GROW above zero: the guard could not fire, and every --reuse-img run
# grew the same image by another 5 GiB and ran `parted resizepart` over a
# partition that was already at 100% — until the work disk filled up.
CUR_BYTES="$(stat -c%s "$OUT_IMG")"
TARGET_BYTES=$(( BASE_UNPACKED + GROW_MIB * 1048576 ))
if [ "$REUSE_IMG" -eq 1 ] && [ "$CUR_BYTES" -ge "$TARGET_BYTES" ]; then
  GROW_NEEDED=0
else
  GROW_NEEDED=1
fi
# --- END grow-target -------------------------------------------------------

if [ "$GROW_NEEDED" -eq 0 ]; then
  say "already grown; leaving the image at $(oc_human "$CUR_BYTES")"
else
  truncate -s "$TARGET_BYTES" "$OUT_IMG"
  say "image file now $(oc_human "$TARGET_BYTES")"
  parted -s "$OUT_IMG" -- resizepart "$ROOT_PART" 100%
  ok "partition $ROOT_PART extended to the end of the image"
fi

LOOPDEV="$(losetup --show -fP "$OUT_IMG")"
say "loop device: $LOOPDEV"
ROOTDEV="${LOOPDEV}p${ROOT_PART}"
BOOTDEV="${LOOPDEV}p1"
[ -b "$ROOTDEV" ] || die "$ROOTDEV did not appear — losetup -P found no partitions"

# resize2fs refuses on a filesystem it has not been told is clean. -p so the
# check is not silent for the two minutes it takes on a 9 GB filesystem.
# Exit 1 means "fixed something", 2 means "fixed something, wants a reboot" —
# both are fine for an offline image. Anything higher is a filesystem we should
# not be shipping.
e2fsck -f -p "$ROOTDEV" || [ $? -le 2 ] || die "e2fsck found errors it could not fix in $ROOTDEV"
resize2fs "$ROOTDEV"
ok "rootfs grown (size confirmed against df once it is mounted)"

# ===========================================================================
stage "mount"
# ===========================================================================
ROOTMNT="$WORK/mnt"
mkdir -p "$ROOTMNT"
mount "$ROOTDEV" "$ROOTMNT"
MOUNTED=1
say "root mounted at $ROOTMNT"

# The Imager's territory. Mounted read-only and checksummed so "we left the
# boot partition alone" is a verified claim rather than an intention: the whole
# customization promise (hostname, Wi-Fi, SSH, locale) lives in these files.
BOOTMNT="$ROOTMNT/boot/firmware"
[ -d "$BOOTMNT" ] || BOOTMNT="$ROOTMNT/boot"
mount -o ro "$BOOTDEV" "$BOOTMNT"
# Recursive, over CONTENTS. See oc_dir_fingerprint in lib/common.sh: the
# name-and-size version this replaced would not have noticed a rewritten
# cmdline.txt, which is precisely the file the claim is about.
BOOT_FINGERPRINT="$(oc_dir_fingerprint "$BOOTMNT")"
say "boot mounted READ-ONLY at $BOOTMNT (content fingerprint ${BOOT_FINGERPRINT:0:16})"

for d in dev dev/pts proc sys; do mkdir -p "$ROOTMNT/$d"; done
mount --bind /dev     "$ROOTMNT/dev"
mount --bind /dev/pts "$ROOTMNT/dev/pts"
mount -t proc  proc  "$ROOTMNT/proc"
mount -t sysfs sysfs "$ROOTMNT/sys"
# No /etc/resolv.conf bind-mount, and — the part that actually matters — no
# network namespace either: chroot_nonet below wraps every chroot in
# `unshare -n`. Omitting the bind-mount on its own proves nothing, because the
# base image ships an /etc/resolv.conf of its own (verified: nameserver
# 8.8.8.8) and a chroot inherits the host's network stack whole.
mkdir -p "$ROOTMNT/mnt/wheelhouse"
mount --bind -o ro "$WHEELHOUSE" "$ROOTMNT/mnt/wheelhouse"
ok "chroot filesystems ready"

# ===========================================================================
stage "chroot — accounts and the /opt/opencastor venv (native aarch64)"
# ===========================================================================
# THE no-network GUARANTEE, IN ONE LINE. `unshare -n` gives the chroot a fresh
# network namespace: one loopback interface, down, no routes, no addresses.
# Nothing inside can reach PyPI even if a future edit asks it to — and
# chroot-stage.sh asserts the namespace is empty before it installs anything,
# so this cannot be quietly dropped in a refactor. There is no bare `chroot`
# call anywhere in this file; selftest.sh checks that too.
chroot_nonet() { unshare -n chroot "$@"; }
install -D -m 0755 "$HERE/lib/chroot-stage.sh" "$ROOTMNT$PAYLOAD_DIR/.chroot-stage.sh"
chroot_nonet "$ROOTMNT" /usr/bin/env -i \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    DEBIAN_FRONTEND=noninteractive \
    OC_USER="$OC_USER" OC_STATE="$OC_STATE" VENV="$VENV" \
    WHEELHOUSE=/mnt/wheelhouse EXPECT_PYVER=3.13 \
    /bin/bash "$PAYLOAD_DIR/.chroot-stage.sh"
rm -f "$ROOTMNT$PAYLOAD_DIR/.chroot-stage.sh"
ok "venv installed and smoke-tested inside the image"

OC_UID="$(awk -F: -v u="$OC_USER" '$1==u{print $3}' "$ROOTMNT/etc/passwd")"
OC_GID="$(awk -F: -v u="$OC_USER" '$1==u{print $4}' "$ROOTMNT/etc/passwd")"
OL_UID="$(awk -F: '$1=="ollama"{print $3}' "$ROOTMNT/etc/passwd")"
OL_GID="$(awk -F: '$1=="ollama"{print $4}' "$ROOTMNT/etc/passwd")"
[ -n "$OC_UID" ] && [ -n "$OL_UID" ] || die "accounts missing from the image after the chroot stage"
say "uids: $OC_USER=$OC_UID  ollama=$OL_UID"

# ===========================================================================
stage "ollama — staged tarball, NVIDIA payload dropped"
# ===========================================================================
# Host-side on purpose — but NOT for the reason this comment used to give. It
# said Raspberry Pi OS Lite lacks a zstd binary and therefore could not unpack
# this tarball itself. Untrue: the trixie arm64 Lite rootfs carries
# /usr/bin/zstd (1316424 bytes, mode 0755, read straight out of the base
# image's ext4). The real reason is the one at the top of lib/chroot-stage.sh:
# this is plain file placement, and file placement is faster to do and easier
# to undo from the host, writing straight into the mount. The chroot is
# reserved for the two jobs that genuinely need the image's own userland.
if [ -x "$ROOTMNT/usr/local/bin/ollama" ] && [ "$REUSE_IMG" -eq 1 ]; then
  say "ollama already installed; --reuse-img keeps it"
else
  mkdir -p "$ROOTMNT/usr/local"
  zstd -dc "$OLLAMA_TAR" | tar -x -C "$ROOTMNT/usr/local" \
      --exclude='lib/ollama/cuda_v*' --exclude='./lib/ollama/cuda_v*'
  ok "/usr/local/bin/ollama + /usr/local/lib/ollama  ($(oc_human "$(oc_du_bytes "$ROOTMNT/usr/local/lib/ollama")"))"
fi
[ -x "$ROOTMNT/usr/local/bin/ollama" ] || die "the tarball produced no /usr/local/bin/ollama"
# An `if`, not `[ … ] && die`: as the trailing command of a block that pattern
# returns non-zero when the test is FALSE, which under `set -e` is a build that
# fails exactly when everything went right.
if [ -d "$ROOTMNT/usr/local/lib/ollama/cuda_v12" ]; then
  die "cuda_v12 survived the exclude — the image would carry 1.2 GB of NVIDIA libraries"
fi

# ===========================================================================
stage "model — $MODEL manifest and its blobs, nothing else"
# ===========================================================================
STORE="$ROOTMNT/usr/share/ollama/.ollama/models"
install -d -m 0755 "$STORE/blobs" "$STORE/manifests/registry.ollama.ai/library/$MODEL_REPO"
install -m 0644 "$MODEL_MANIFEST" \
    "$STORE/manifests/registry.ollama.ai/library/$MODEL_REPO/$MODEL_TAG"
for d in "${MODEL_DIGESTS[@]}"; do
  src="$MODEL_STORE/blobs/${d/:/-}"
  dst="$STORE/blobs/${d/:/-}"
  if [ -f "$dst" ] && [ "$(stat -c%s "$dst")" = "$(stat -c%s "$src")" ]; then
    say "blob ${d:7:12}… already staged"
  else
    say "copying blob ${d:7:12}…  $(oc_human "$(stat -c%s "$src")")"
    cp --reflink=auto "$src" "$dst.part" && mv -f "$dst.part" "$dst"
  fi
  chmod 0644 "$dst"
done
chown -R "$OL_UID:$OL_GID" "$ROOTMNT/usr/share/ollama"
ok "$MODEL staged ($(oc_human "$(oc_du_bytes "$ROOTMNT/usr/share/ollama")"))"

# ===========================================================================
stage "firstboot rail — the payload and its two units"
# ===========================================================================
install -D -m 0755 "$HERE/firstboot/firstboot.sh"  "$ROOTMNT$PAYLOAD_DIR/firstboot.sh"
install -D -m 0755 "$HERE/firstboot/qr_server.py"  "$ROOTMNT$PAYLOAD_DIR/qr_server.py"
for unit in opencastor-firstboot.service opencastor-qr.service ollama.service; do
  install -D -m 0644 "$HERE/firstboot/$unit" "$ROOTMNT/etc/systemd/system/$unit"
done
install -d -o "$OC_UID" -g "$OC_GID" -m 0755 "$ROOTMNT$OC_STATE"
# Never ship a stamp. It is the one file whose presence means "already
# provisioned", and an image that carries it is an image that never brings a
# robot up — silently, with a QR page that says it is still starting.
rm -f "$ROOTMNT$OC_STATE/.provisioned" "$ROOTMNT$OC_STATE/status.json" \
      "$ROOTMNT$OC_STATE/firstboot.log"

# --root, not a chroot: `systemctl enable` offline is only symlink work, and
# systemd's own --root does it with systemd's rules rather than ours.
systemctl --root="$ROOTMNT" enable \
    opencastor-firstboot.service opencastor-qr.service ollama.service >/dev/null
for unit in opencastor-firstboot opencastor-qr ollama; do
  [ -L "$ROOTMNT/etc/systemd/system/multi-user.target.wants/$unit.service" ] \
    || die "$unit.service did not get enabled"
done
ok "three units enabled in multi-user.target.wants"

# The image says what it is. `cat /etc/opencastor-image.json` on a robot in
# somebody's kitchen is the only way to answer "which build is this?" without
# a build log nobody kept.
write_provenance "$ROOTMNT/etc/opencastor-image.json"
chmod 0644 "$ROOTMNT/etc/opencastor-image.json"
ok "/etc/opencastor-image.json  ${GIT_HEAD:0:12}$([ "$GIT_DIRTY" = true ] && echo " (dirty tree)" || echo "")"

# ===========================================================================
stage "verify inside the image"
# ===========================================================================
chroot_nonet "$ROOTMNT" "$VENV/bin/python" -c 'import castor; print("  castor", castor.__version__)'
chroot_nonet "$ROOTMNT" /bin/bash -n "$PAYLOAD_DIR/firstboot.sh"
chroot_nonet "$ROOTMNT" /usr/bin/python3 -m py_compile "$PAYLOAD_DIR/qr_server.py"
chroot_nonet "$ROOTMNT" /usr/bin/id "$OC_USER" >/dev/null
[ -f "$ROOTMNT/var/lib/systemd/linger/$OC_USER" ] || die "linger marker missing"
[ ! -f "$ROOTMNT$OC_STATE/.provisioned" ] || die "a .provisioned stamp is in the image"
FREE_MIB=$(( $(df --output=avail -B1M "$ROOTMNT" | tail -1) ))
say "rootfs free after install: ${FREE_MIB} MiB"
[ "$FREE_MIB" -ge 256 ] || die \
  "only ${FREE_MIB} MiB free in the image. Raise SLACK_MIB and rebuild — a Pi
   that boots with no room writes no logs and cannot pair."

BOOT_AFTER="$(oc_dir_fingerprint "$BOOTMNT")"
[ "$BOOT_AFTER" = "$BOOT_FINGERPRINT" ] || die \
  "the boot partition changed during the build. It belongs to the Imager."
ok "boot partition byte-identical; the Imager's customization is untouched"

sync
umount "$ROOTMNT/mnt/wheelhouse"; rmdir "$ROOTMNT/mnt/wheelhouse" 2>/dev/null || true
umount "$BOOTMNT"
umount "$ROOTMNT/sys" "$ROOTMNT/proc" "$ROOTMNT/dev/pts" "$ROOTMNT/dev"
umount "$ROOTMNT"
MOUNTED=0
e2fsck -f -p "$ROOTDEV" || [ $? -le 2 ] || die "the finished filesystem does not check clean"

# ---------------------------------------------------------------------------
# Optional shrink. THE FLASH IS PART OF THE TEN MINUTES.
#
# Everything above is measured in build-host minutes, which nobody is timing.
# The clock that matters starts at a blank card, and the largest slice of it is
# Raspberry Pi Imager writing — and then verifying — every byte of this image,
# including the ~3 GiB of empty space the grow stage added so `pip` and a 2.7 GB
# model would fit. Those bytes are needed during the BUILD and are dead weight
# afterwards: Raspberry Pi OS expands the last partition to fill the card on
# first boot all by itself, so a shrunk image gives the robot exactly the same
# disk and gives the operator back several minutes of writing and verifying.
#
# Opt-in, because it is the one stage here that destroys data if the arithmetic
# is wrong, and because as of this writing no root build has been run at all.
# ---------------------------------------------------------------------------
if [ "$SHRINK" -eq 1 ]; then
  stage "shrink — hand back the space the build needed and the robot does not"
  BSIZE="$(dumpe2fs -h "$ROOTDEV" 2>/dev/null | awk -F': *' '/^Block size/{print $2}')"
  MINBLK="$(resize2fs -P "$ROOTDEV" 2>/dev/null | awk -F': *' '/minimum size/{print $2}')"
  [ -n "$BSIZE" ] && [ -n "$MINBLK" ] || die "could not read the filesystem geometry to shrink it"
  TARGETBLK=$(( MINBLK + SLACK_MIB * 1048576 / BSIZE ))
  say "minimum $(oc_human $(( MINBLK * BSIZE ))) + ${SLACK_MIB} MiB slack -> $(oc_human $(( TARGETBLK * BSIZE )))"
  resize2fs "$ROOTDEV" "$TARGETBLK"
  e2fsck -f -p "$ROOTDEV" || [ $? -le 2 ] || die "the shrunk filesystem does not check clean"

  SECTORS=$(( TARGETBLK * BSIZE / 512 ))
  losetup -d "$LOOPDEV"; LOOPDEV=""
  # sfdisk -N keeps the partition's START and rewrites only its size. parted's
  # resizepart would do the same thing but argues about shrinking in script
  # mode; sfdisk just does what it is told, which is what a build wants.
  printf ',%s\n' "$SECTORS" | sfdisk -N "$ROOT_PART" --no-reread --force "$OUT_IMG" >/dev/null
  truncate -s $(( (ROOT_START + SECTORS) * 512 )) "$OUT_IMG"

  # Re-attach and check: a partition table that disagrees with the filesystem
  # produces a card that flashes perfectly and does not boot.
  LOOPDEV="$(losetup --show -fP "$OUT_IMG")"
  e2fsck -f -p "${LOOPDEV}p${ROOT_PART}" || [ $? -le 2 ] || die "the shrunk image does not check clean"
  ok "image is now $(oc_human "$(stat -c%s "$OUT_IMG")") — the Pi re-expands it on first boot"
fi

losetup -d "$LOOPDEV"; LOOPDEV=""
ok "unmounted and detached cleanly"

# ===========================================================================
stage "emit"
# ===========================================================================
# Beside the artifacts as well as inside them: the operator hands somebody an
# .img.xz far more often than they hand over a booted robot, and a provenance
# record that can only be read by flashing the card is not a record.
OUT_PROV="$WORK/opencastor-image.json"
write_provenance "$OUT_PROV"
chown "$OC_INVOKER" "$OUT_PROV" 2>/dev/null || true
ok "$OUT_PROV  ${GIT_HEAD:0:12}$([ "$GIT_DIRTY" = true ] && echo " (dirty tree)" || echo "")"

if [ "$NO_COMPRESS" -eq 1 ]; then
  ok "$OUT_IMG  $(oc_human "$(stat -c%s "$OUT_IMG")")   (--no-compress: no .xz written)"
  exit 0
fi
say "xz -T0 -$XZ_PRESET (10-20 min on a Pi 5; the model inside is already compressed)"
rm -f "$OUT_XZ"
xz -T0 "-$XZ_PRESET" --keep --force "$OUT_IMG"
( cd "$WORK" && sha256sum "$(basename "$OUT_XZ")" > "$(basename "$OUT_XZ").sha256" )
# The .img and the .xz both belong to the operator, not to root — otherwise the
# next rootless step (copying it to a laptop, checking it) needs sudo too.
chown "$OC_INVOKER" "$OUT_IMG" "$OUT_XZ" "$OUT_XZ.sha256" 2>/dev/null || true

stage "done"
ok "$OUT_XZ  $(oc_human "$(stat -c%s "$OUT_XZ")")"
ok "$OUT_XZ.sha256  $(cut -d' ' -f1 < "$OUT_XZ.sha256")"
cat <<NEXT

  Flash it with Raspberry Pi Imager: "Use custom" -> $OUT_XZ
  Set hostname and Wi-Fi in the Imager's own customization dialog — this image
  does not touch those, and the robot takes its NAME from the hostname you type.

  What is in it: $OUT_PROV (and /etc/opencastor-image.json on the robot).

  Then: docs/IMAGE.md, "The measured ten minutes".
NEXT
