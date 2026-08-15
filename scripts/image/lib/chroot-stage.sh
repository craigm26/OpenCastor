#!/usr/bin/env bash
# Runs INSIDE the mounted image, as root, with no network. Accounts and venv.
#
# WHY THIS IS THE ONLY THING IN THE CHROOT. Everything else the build does to
# the image — unpacking ollama, laying down 2.7 GB of model blobs, writing unit
# files — is file placement, and file placement is faster, safer and easier to
# undo from the host side, writing straight into the mount. Two jobs genuinely
# need the image's own userland and get a chroot for it:
#
#   * the venv, because `pyvenv.cfg` and every console-script shebang bake in
#     the interpreter's path, and a venv built against the HOST's python is a
#     venv that happens to work only because both machines are trixie/3.13.
#     Depending on that coincidence is how an image breaks on the day the host
#     is upgraded first.
#   * the accounts, because useradd has to see the image's /etc/passwd, its
#     shadow file, and its existing gpio/i2c/spi groups.
#
# One thing that deliberately does NOT run here: unpacking the ollama tarball.
# The reason given for that used to be a missing zstd binary in the base image,
# and it was wrong — the trixie arm64 Lite rootfs carries /usr/bin/zstd,
# verified by reading the base image's ext4 directly. The actual reason is the
# paragraph above: it is file placement, and file placement belongs on the host.
#
# NO NETWORK, AND IT IS A NAMESPACE, NOT A HOPE. build.sh runs every chroot
# under `unshare -n`, so this stage lives in a network namespace holding one
# down loopback interface: no addresses, no routes, nowhere for a `pip install`
# to go. The first version of this comment claimed the guarantee came from
# declining to bind-mount /etc/resolv.conf, which guaranteed nothing: the base
# image ships its own /etc/resolv.conf (nameserver 8.8.8.8, verified) and a
# chroot shares the host's network stack. The check below is what makes the
# claim true, and it runs before a single wheel is installed.
set -euo pipefail

OC_USER="${OC_USER:-opencastor}"
OC_STATE="${OC_STATE:-/var/lib/opencastor}"
VENV="${VENV:-/opt/opencastor}"
WHEELHOUSE="${WHEELHOUSE:-/mnt/wheelhouse}"
EXPECT_PYVER="${EXPECT_PYVER:-3.13}"

say() { printf '  chroot | %s\n' "$*"; }
die() { printf '  chroot | FAIL %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
say "network namespace — the no-network invariant, checked"
# ---------------------------------------------------------------------------
# /proc/net is a symlink to /proc/self/net, so this reads THIS process's
# network namespace even though /proc was mounted before the unshare. An empty
# namespace has exactly one interface, `lo`, and nothing else.
[ -r /proc/net/dev ] || die \
  "cannot read /proc/net/dev, so the no-network invariant cannot be checked.
   build.sh mounts /proc before entering; if you are running this stage by hand,
   run it the way build.sh does: unshare -n chroot <mnt> ... .chroot-stage.sh"
OC_IFACES="$(awk -F: 'NR>2 {gsub(/[ \t]/,"",$1); if ($1 != "lo") printf "%s ", $1}' /proc/net/dev)"
[ -z "$OC_IFACES" ] || die \
  "this stage is supposed to run inside an empty network namespace and sees: $OC_IFACES
   build.sh wraps every chroot in \`unshare -n\` for exactly this reason. Without
   it, a stray \`pip install\` reaches PyPI and the image's contents depend on the
   day it was built. Do not 'fix' this by deleting the check."
say "network: loopback only — an index is unreachable from here, not merely unconfigured"

# ---------------------------------------------------------------------------
say "python check"
# ---------------------------------------------------------------------------
command -v python3 >/dev/null || die "the base image has no python3"
IMG_PYVER="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
[ "$IMG_PYVER" = "$EXPECT_PYVER" ] || die \
  "image python is $IMG_PYVER, wheelhouse was built for $EXPECT_PYVER.
   Compiled wheels are tagged cp3XX; installing them across a minor version
   produces a venv that imports nothing. Rebuild the wheelhouse on a host
   whose python matches the base image, or use a matching base image."
say "python $IMG_PYVER ($(command -v python3))"

# ---------------------------------------------------------------------------
say "venv module"
# ---------------------------------------------------------------------------
# Debian splits `venv` and `ensurepip` out of the base python package, and
# Raspberry Pi OS Lite does not always carry them. The .debs were fetched
# rootless on the build host precisely so this can be fixed offline.
if ! python3 -c 'import venv' 2>/dev/null; then
  say "python3-venv is absent — installing the staged .debs (no network involved)"
  shopt -s nullglob
  debs=("$WHEELHOUSE"/debs/*.deb)
  shopt -u nullglob
  [ ${#debs[@]} -gt 0 ] || die \
    "python3-venv is missing from the image and no .debs were staged.
     Re-run scripts/image/build-wheelhouse.sh on a networked host."
  dpkg -i "${debs[@]}"
  python3 -c 'import venv' || die "python3-venv still unimportable after dpkg -i"
fi

# ---------------------------------------------------------------------------
say "accounts"
# ---------------------------------------------------------------------------
# A SYSTEM account, explicitly. The Raspberry Pi Imager's firstrun.sh creates
# the operator's own account and expects uid 1000 to be free; a service user
# that grabbed 1000 at build time would break the one customization flow this
# image promises to leave working.
if ! id -u "$OC_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$OC_STATE" \
          --shell /bin/bash --comment "OpenCastor robot runtime" "$OC_USER"
  say "created system user $OC_USER (uid $(id -u "$OC_USER"))"
else
  say "user $OC_USER already present (uid $(id -u "$OC_USER"))"
fi
# /bin/bash and not nologin: this account can never be logged into (no
# password, system uid), but `sudo -u opencastor -i` is the operator's only
# way to inspect a robot that will not start, and nologin takes that away for
# no security gain.

# The hardware groups castor's peripheral scan and the PCA9685 backend need.
# Missing groups are not an error — a Pi without an spi group is a valid
# machine, and gpasswd would abort the whole build over it.
for grp in gpio i2c spi dialout video plugdev render; do
  getent group "$grp" >/dev/null 2>&1 && usermod -aG "$grp" "$OC_USER" || true
done
say "groups: $(id -Gn "$OC_USER" | tr ' ' ',')"

if ! id -u ollama >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /usr/share/ollama \
          --shell /bin/false --comment "Ollama" ollama
  say "created system user ollama (uid $(id -u ollama))"
fi
# The upstream installer puts the operator in the ollama group so a human can
# drop models into the store by hand. The robot's own account needs it for the
# same reason.
usermod -aG ollama "$OC_USER" || true

# ---------------------------------------------------------------------------
say "linger marker"
# ---------------------------------------------------------------------------
# `castor up` installs its gateway/runtime/console as systemd USER units and
# runs `systemctl --user enable --now`. Without lingering there is no user
# manager for a system account at boot, and `up` writes three unit files that
# nothing ever starts — a robot that looks configured and answers no port.
# This is the same file `loginctl enable-linger` writes; loginctl itself needs
# a live dbus, which a chroot does not have.
install -d -m 0755 /var/lib/systemd/linger
touch "/var/lib/systemd/linger/$OC_USER"

# ---------------------------------------------------------------------------
say "venv at $VENV"
# ---------------------------------------------------------------------------
[ -d "$WHEELHOUSE" ] || die "wheelhouse is not mounted at $WHEELHOUSE"
[ -f "$WHEELHOUSE/requirements-image.txt" ] || die "$WHEELHOUSE has no requirements-image.txt"

if [ ! -x "$VENV/bin/python" ]; then
  # --without-pip, then bootstrap from the wheelhouse's own pip wheel: ensurepip
  # bundles whatever pip Debian froze into the python package, and that pip is
  # the one thing in this venv we did not choose. Running the wheel directly as
  # a zipapp is not a trick, it is pip's documented bootstrap.
  python3 -m venv --without-pip "$VENV"
  PIP_WHEEL="$(find "$WHEELHOUSE" -maxdepth 1 -name 'pip-*.whl' -print -quit)"
  [ -n "$PIP_WHEEL" ] || die "no pip wheel in $WHEELHOUSE — rebuild the wheelhouse"
  "$VENV/bin/python" "$PIP_WHEEL/pip" install --no-index --find-links "$WHEELHOUSE" \
      --disable-pip-version-check pip setuptools wheel
  say "venv created and pip bootstrapped from $(basename "$PIP_WHEEL")"
else
  say "venv already present — reinstalling into it"
fi

# --no-index is the contract, not a precaution: if this line ever needs the
# network, the wheelhouse is incomplete and the operator must find out on the
# build host, where there is a shell, not on a robot in somebody's kitchen.
"$VENV/bin/python" -m pip install --no-index --find-links "$WHEELHOUSE" \
    --disable-pip-version-check --no-warn-script-location \
    -r "$WHEELHOUSE/requirements-image.txt"

# ---------------------------------------------------------------------------
say "venv smoke test"
# ---------------------------------------------------------------------------
"$VENV/bin/python" -c 'import castor; print("  chroot | opencastor", castor.__version__)'
"$VENV/bin/castor" up --help >/dev/null || die "\`castor up --help\` failed inside the image"
[ -x "$VENV/bin/robot-md-gateway" ] || die \
  "robot-md-gateway is not next to the venv's python. castor.up writes a unit
   pointing at exactly that path and would ship a crash-loop."
"$VENV/bin/python" - <<'PY'
from importlib.metadata import entry_points
names = {ep.name for ep in entry_points(group="robot_md_gateway.actuators")}
assert "rc-car" in names, f"rc-car actuator entry point missing; saw {sorted(names)}"
print("  chroot | actuator entry points:", ", ".join(sorted(names)))
PY

chown -R "$OC_USER:$OC_USER" "$OC_STATE"
chmod 0755 "$OC_STATE"
say "done"
