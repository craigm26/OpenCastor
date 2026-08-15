# shellcheck shell=bash
# Shared plumbing for the image scripts — logging, asserts, and byte math.
#
# WHY A LIBRARY AND NOT THREE COPIES. The image rail is three scripts that all
# have to agree on the same facts: which paths are inputs, what "aarch64" means,
# how big the payload is. The first draft duplicated the aarch64 check in two of
# them and got it wrong in one — `uname -p` on this Pi answers "unknown", so a
# build that should have refused a wrong host sailed straight into a chroot it
# could not execute. One definition, used everywhere, is the fix.
#
# LOUD BY DEFAULT. Every stage announces itself with a wall-clock offset. A
# build that takes twenty minutes and prints nothing is indistinguishable from
# a build that has hung, and the operator's only recourse is Ctrl-C at minute
# nineteen. `say` is cheap; a lost build is not.

set -euo pipefail

OC_STARTED="${OC_STARTED:-$(date +%s)}"

# ANSI only when a human is watching; a log file gets clean text.
if [ -t 1 ]; then
  OC_C_BOLD=$'\033[1m'; OC_C_DIM=$'\033[2m'; OC_C_RED=$'\033[31m'
  OC_C_GRN=$'\033[32m'; OC_C_YEL=$'\033[33m'; OC_C_OFF=$'\033[0m'
else
  OC_C_BOLD=''; OC_C_DIM=''; OC_C_RED=''; OC_C_GRN=''; OC_C_YEL=''; OC_C_OFF=''
fi

oc_elapsed() { printf '%5ds' "$(( $(date +%s) - OC_STARTED ))"; }

say()   { printf '%s  %s\n' "$(oc_elapsed)" "$*"; }
stage() { printf '\n%s%s  == %s ==%s\n' "$OC_C_BOLD" "$(oc_elapsed)" "$*" "$OC_C_OFF"; }
ok()    { printf '%s  %sok%s   %s\n' "$(oc_elapsed)" "$OC_C_GRN" "$OC_C_OFF" "$*"; }
warn()  { printf '%s  %swarn%s %s\n' "$(oc_elapsed)" "$OC_C_YEL" "$OC_C_OFF" "$*" >&2; }
die()   { printf '%s  %sFAIL%s %s\n' "$(oc_elapsed)" "$OC_C_RED" "$OC_C_OFF" "$*" >&2; exit 1; }
note()  { printf '%s  %s%s%s\n' "$(oc_elapsed)" "$OC_C_DIM" "$*" "$OC_C_OFF"; }

# --- asserts ---------------------------------------------------------------

# `uname -m` and nothing else: -p and -i answer "unknown" on Raspberry Pi OS.
oc_require_aarch64() {
  local arch; arch="$(uname -m)"
  [ "$arch" = "aarch64" ] || die \
    "this build must run natively on arm64 — this host is '$arch'.
     The chroot executes the image's own aarch64 binaries; there is no qemu
     here on purpose. Run it on the Pi."
}

oc_require_root() {
  [ "$(id -u)" -eq 0 ] || die \
    "must run as root (loop devices, mounts, chroot). Re-run with sudo, or
     pass --dry-run to validate every input without touching root-only ops."
}

oc_require_not_root() {
  [ "$(id -u)" -ne 0 ] || die \
    "refusing to run as root — this stage writes into your own directories and
     a root-owned wheelhouse is a wheelhouse the build cannot read back."
}

oc_require_cmds() {
  local missing=() c
  for c in "$@"; do command -v "$c" >/dev/null 2>&1 || missing+=("$c"); done
  [ ${#missing[@]} -eq 0 ] || die "missing required commands: ${missing[*]}"
}

oc_require_file() {
  [ -f "$1" ] || die "${2:-required input} not found: $1"
}

oc_require_dir() {
  [ -d "$1" ] || die "${2:-required directory} not found: $1"
}

# The image's python and the host's python must be the same minor version or
# the wheelhouse's compiled wheels (cp313-cp313-linux_aarch64) will not import.
oc_pyver() { "$1" -c 'import sys;print("%d.%d"%sys.version_info[:2])'; }

# --- fingerprints ------------------------------------------------------------

# A recursive content hash of a directory tree: every regular file's path AND
# its bytes, in a stable order, folded into one sha256.
#
# The first version of this hashed `find -printf '%f %s\n'` — names and sizes.
# That is not a fingerprint, it is a directory listing: `sed -i s/1/0/` on
# cmdline.txt changes no name and no size, and the "the boot partition is
# byte-identical" claim it backed would have passed with the Imager's kernel
# arguments rewritten underneath it. If a check exists to make a claim
# provable, it has to actually read the bytes.
oc_dir_fingerprint() {
  local dir="$1"
  ( cd "$dir" 2>/dev/null || { printf 'unreadable\n'; exit 0; }
    find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum
  ) | sha256sum | cut -d' ' -f1
}

# --- byte math -------------------------------------------------------------

oc_du_bytes() { du -sb --apparent-size "$1" 2>/dev/null | cut -f1; }

oc_human() {
  local b="$1"
  awk -v b="$b" 'BEGIN{
    split("B KiB MiB GiB TiB", u, " "); i=1
    while (b >= 1024 && i < 5) { b /= 1024; i++ }
    printf (i==1 ? "%d %s" : "%.1f %s"), b, u[i]
  }'
}
