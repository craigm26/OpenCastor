#!/usr/bin/env bash
# Assemble every wheel the image needs, and PROVE the set is complete.
#
# WHAT THIS EXISTS TO PREVENT. The chroot in build.sh has no network. That is
# not a limitation to work around, it is the point: a `pip install` inside the
# chroot resolves against whatever PyPI holds on build day, so two builds of
# the "same" image ship different code, and a build run on a train ships
# nothing at all. Every byte of Python the image runs is therefore resolved
# here — rootless, on a host that is the same architecture and the same Python
# minor version as the image — and copied in as files.
#
# COMPLETENESS IS PROVEN, NOT ASSERTED. A wheelhouse that is missing one
# transitive dependency looks exactly like a complete one until the chroot
# fails at minute fourteen of a twenty-minute build. So the last stage here
# builds a scratch venv with `--no-index`, installs the real requirements into
# it, imports `castor`, and runs `castor --version`. If that venv can be built
# with the network unplugged, so can the image's.
#
# ROOTLESS ON PURPOSE. Nothing in this file needs a privilege the operator
# does not already have, including the .deb fetch — `apt-get download` writes
# to the current directory and never touches dpkg's database. The one command
# that needs sudo is build.sh, and it should need it for as short a time as
# possible.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"

REPO_ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${OPENCASTOR_WHEELHOUSE:-$HOME/image-build/wheelhouse}"
RC_CAR_DIST="${RC_CAR_DIST:-$HOME/projects/RobotRegistryFoundation/rc-car-actuator/dist}"
PYTHON="${OPENCASTOR_BUILD_PYTHON:-$HOME/venvs/castor/bin/python}"
REQS="$HERE/requirements-image.txt"
SKIP_PROOF=0
CLEAN=0

usage() {
  cat <<'EOF'
build-wheelhouse.sh — assemble and prove the offline wheelhouse (no sudo).

  --out DIR         where the wheelhouse lands   (default ~/image-build/wheelhouse)
  --rc-car DIR      rc-car-actuator dist/ dir    (default ~/projects/RobotRegistryFoundation/rc-car-actuator/dist)
  --python PATH     build interpreter            (default ~/venvs/castor/bin/python)
  --clean           delete the wheelhouse first
  --skip-proof      assemble only; do not build the scratch-venv proof
  -h, --help        this

Environment: OPENCASTOR_WHEELHOUSE, RC_CAR_DIST, OPENCASTOR_BUILD_PYTHON.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --rc-car) RC_CAR_DIST="$2"; shift 2 ;;
    --python) PYTHON="$2"; shift 2 ;;
    --clean) CLEAN=1; shift ;;
    --skip-proof) SKIP_PROOF=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

# ---------------------------------------------------------------------------
stage "preflight"
# ---------------------------------------------------------------------------
oc_require_not_root
oc_require_aarch64
oc_require_cmds apt-get sha256sum
oc_require_file "$PYTHON" "build interpreter"
oc_require_file "$REQS" "image requirements"
oc_require_dir "$REPO_ROOT/castor" "opencastor working tree"
oc_require_dir "$RC_CAR_DIST" "rc-car-actuator dist/"

PYVER="$(oc_pyver "$PYTHON")"
[ "$PYVER" = "3.13" ] || die \
  "build interpreter is Python $PYVER but the image ships 3.13.
   Compiled wheels are tagged cp3XX and will not import across minor versions."
say "python      $PYVER  ($PYTHON)"
say "repo        $REPO_ROOT"
say "wheelhouse  $OUT"

if [ "$CLEAN" -eq 1 ]; then say "cleaning $OUT"; rm -rf "$OUT"; fi
mkdir -p "$OUT" "$OUT/debs"

# ---------------------------------------------------------------------------
stage "opencastor wheel — from THIS working tree"
# ---------------------------------------------------------------------------
# --no-deps: the tree's own metadata names rc-car-actuator, which is not on any
# index. Resolving deps here would fail before we have had a chance to hand pip
# the local copy. The dependency closure is built in the next stage, with the
# wheelhouse itself on --find-links.
TREE_WHEEL_DIR="$(mktemp -d)"
trap 'rm -rf "$TREE_WHEEL_DIR"' EXIT
"$PYTHON" -m pip wheel --no-deps --no-build-isolation \
  --wheel-dir "$TREE_WHEEL_DIR" "$REPO_ROOT" >/dev/null 2>&1 \
  || "$PYTHON" -m pip wheel --no-deps --wheel-dir "$TREE_WHEEL_DIR" "$REPO_ROOT"
OC_WHEEL_SRC="$(find "$TREE_WHEEL_DIR" -maxdepth 1 -name 'opencastor-*.whl' -print -quit)"
[ -n "$OC_WHEEL_SRC" ] || die "pip wheel produced no opencastor wheel from $REPO_ROOT"
# Replace any older tree build so the wheelhouse never holds two candidates.
rm -f "$OUT"/opencastor-*.whl
cp "$OC_WHEEL_SRC" "$OUT/"
OC_WHEEL="$OUT/$(basename "$OC_WHEEL_SRC")"
ok "$(basename "$OC_WHEEL")  ($(oc_human "$(stat -c%s "$OC_WHEEL")"))"

# ---------------------------------------------------------------------------
stage "rc-car-actuator wheel — from dist/, not PyPI"
# ---------------------------------------------------------------------------
RC_WHEEL_SRC="$(find "$RC_CAR_DIST" -maxdepth 1 -name 'rc_car_actuator-*.whl' -print -quit)"
[ -n "$RC_WHEEL_SRC" ] || die "no rc_car_actuator wheel in $RC_CAR_DIST"
cp -n "$RC_WHEEL_SRC" "$OUT/" || true
RC_WHEEL="$OUT/$(basename "$RC_WHEEL_SRC")"
ok "$(basename "$RC_WHEEL")"

# ---------------------------------------------------------------------------
stage "dependency closure — every transitive wheel, built for this platform"
# ---------------------------------------------------------------------------
# The two local wheels go in BY PATH. Naming them by requirement instead would
# let an index copy of the same version win the resolution, and the image would
# quietly ship released code in place of the tree we just tested.
say "resolving (this is the slow part — sdists get built here, not in the chroot)"
mapfile -t INDEX_REQS < <(grep -vE '^\s*(#|$)' "$REQS" \
                          | grep -vE '^(opencastor|rc-car-actuator)\s*$')
"$PYTHON" -m pip wheel \
  --wheel-dir "$OUT" --find-links "$OUT" \
  "$OC_WHEEL" "$RC_WHEEL" "${INDEX_REQS[@]}"

# pip/setuptools/wheel themselves: the image's venv is created --without-pip
# when Debian's ensurepip is absent, and then bootstrapped from these.
"$PYTHON" -m pip wheel --wheel-dir "$OUT" --find-links "$OUT" pip setuptools wheel >/dev/null
ok "$(find "$OUT" -maxdepth 1 -name '*.whl' | wc -l) wheels, $(oc_human "$(oc_du_bytes "$OUT")")"

# ---------------------------------------------------------------------------
stage "venv .debs — the one apt fallback, fetched HERE so the chroot stays offline"
# ---------------------------------------------------------------------------
# Raspberry Pi OS Lite does not always ship python3-venv, and a chroot that
# discovers this with no network has nowhere to go. `apt-get download` is
# rootless and touches no dpkg state; build.sh dpkg -i's these only if the
# image's own python cannot import venv.
( cd "$OUT/debs" && apt-get download python3-venv python3.13-venv >/dev/null 2>&1 ) \
  || warn "could not pre-fetch python3-venv .debs — build.sh will fail loudly if the image needs them"
ok "$(find "$OUT/debs" -name '*.deb' | wc -l) .deb(s) staged"

# ---------------------------------------------------------------------------
stage "manifest"
# ---------------------------------------------------------------------------
cp "$REQS" "$OUT/requirements-image.txt"
( cd "$OUT" && find . -name '*.whl' -o -name '*.deb' | LC_ALL=C sort \
    | xargs -r sha256sum > MANIFEST.sha256 )
ok "MANIFEST.sha256  ($(wc -l < "$OUT/MANIFEST.sha256") entries)"

# ---------------------------------------------------------------------------
stage "PROOF — a scratch venv built with the network refused"
# ---------------------------------------------------------------------------
if [ "$SKIP_PROOF" -eq 1 ]; then
  warn "--skip-proof: completeness is UNPROVEN. build.sh will find out the hard way."
  exit 0
fi
PROOF="$(mktemp -d)"
trap 'rm -rf "$TREE_WHEEL_DIR" "$PROOF"' EXIT
"$PYTHON" -m venv "$PROOF/venv"
say "installing --no-index --find-links $OUT"
"$PROOF/venv/bin/python" -m pip install --quiet --disable-pip-version-check \
  --no-index --find-links "$OUT" -r "$OUT/requirements-image.txt"

"$PROOF/venv/bin/python" -c 'import castor, qrcode, robot_md_gateway; print("imports ok")'
"$PROOF/venv/bin/python" -c 'import castor; print("opencastor", castor.__version__)'
# `castor up --help` and not `--version`: this CLI has no global --version (the
# version lives on `castor.__version__`, checked above), and `up --help` is a
# far better smoke test anyway — it is the exact console script firstboot.sh
# invokes, and building its parser imports the whole subcommand tree. A missing
# transitive dependency shows up here as an ImportError instead of at 3 a.m. on
# a Pi with no keyboard.
"$PROOF/venv/bin/castor" up --help > /dev/null
ok "castor console script runs; \`castor up --help\` parses"
# The whole reason rc-car-actuator is in here: castor.up.resolve_actuator asks
# the entry-point registry, and answers "noop" — a robot with no wheels — if
# this lookup comes up empty. Prove the plugin registered, not just installed.
"$PROOF/venv/bin/python" - <<'PY'
from importlib.metadata import entry_points
names = {ep.name for ep in entry_points(group="robot_md_gateway.actuators")}
assert "rc-car" in names, f"rc-car actuator entry point missing; saw {sorted(names)}"
print("actuator entry points:", ", ".join(sorted(names)))
PY
ok "wheelhouse is complete — the chroot can build this venv with no network"
say "size: $(oc_human "$(oc_du_bytes "$OUT")")   path: $OUT"
