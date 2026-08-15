#!/usr/bin/env bash
# Everything about the image rail that can be proven WITHOUT root, proven now.
#
# WHY THIS IS NOT IN tests/. Nothing here is a unit test of importable Python;
# it is a rehearsal of a shell pipeline, a systemd unit set and an HTTP server,
# and pytest would only be a wrapper around the same subprocesses. What it
# shares with the test suite is the standard: a check that cannot fail is not a
# check. Every assertion below has a way to come out red.
#
# WHAT IT CANNOT COVER, STATED UP FRONT rather than implied by silence. Four
# things in build.sh need privileges this script refuses to take: losetup,
# mount, chroot, and the grow. Those are exercised only by a real
# `sudo ./build.sh`, and until somebody has run one on hardware the image is
# UNVERIFIED end to end. What this file does is make sure that when the
# operator spends those twenty minutes, they are not spent discovering a typo:
# every input is validated, every script parses, the wheelhouse is provably
# complete, the units are structurally sound, and the two things the operator
# will actually look at — the degradation report and the pairing page — are
# rehearsed against real fixtures.
#
# `assert DESC CMD...` AND NOT `[ x ]; check $?`. The first draft used the
# latter and shellcheck was right to hate it: `$?` a line later is whatever ran
# last, and one inserted debug line silently turns a real assertion into one
# that grades the echo. Passing the command to the asserter means the thing
# being tested and the thing being reported cannot drift apart.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"
set +e   # a failing check must report and continue, not abort the run

PASS=0; FAIL=0; SKIP=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass() { PASS=$((PASS+1)); printf '  %sPASS%s  %s\n' "$OC_C_GRN" "$OC_C_OFF" "$1"; }
fail() { FAIL=$((FAIL+1)); printf '  %sFAIL%s  %s\n' "$OC_C_RED" "$OC_C_OFF" "$1"; }
skip() { SKIP=$((SKIP+1)); printf '  %sSKIP%s  %s\n' "$OC_C_YEL" "$OC_C_OFF" "$1"; }

#: assert DESC CMD...   — run CMD, report DESC, show CMD's output when it fails.
assert() {
  local desc="$1"; shift
  local out; out="$("$@" 2>&1)"
  if [ $? -eq 0 ]; then pass "$desc"
  else fail "$desc${out:+ — $(printf '%s' "$out" | tail -4 | tr '\n' ' ')}"; fi
}
#: `assert "…" not grep -q …` reads better than a negated subshell.
not() { ! "$@"; }
#: check RC DESC [DETAIL] — for the handful of cases that already have a status.
check() { if [ "$1" -eq 0 ]; then pass "$2"; else fail "$2${3:+ — $3}"; fi; }

SHELL_FILES=(build.sh build-wheelhouse.sh selftest.sh lib/common.sh
             lib/chroot-stage.sh firstboot/firstboot.sh)
UNITS=(opencastor-firstboot.service opencastor-qr.service ollama.service)
PY="${OPENCASTOR_BUILD_PYTHON:-$HOME/venvs/castor/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"

# ===========================================================================
stage "1. syntax"
# ===========================================================================
for f in "${SHELL_FILES[@]}"; do
  assert "bash -n $f" bash -n "$HERE/$f"
done
# PYTHONPYCACHEPREFIX so the syntax check does not leave a __pycache__ in the
# repo. A verification step that dirties the working tree makes `git status`
# lie about what a change actually touched.
PYTHONPYCACHEPREFIX="$TMP/pycache" \
  assert "py_compile firstboot/qr_server.py (system python3, no venv)" \
    python3 -m py_compile "$HERE/firstboot/qr_server.py"

# lib/common.sh is sourced, never executed — it carries a `# shellcheck
# shell=bash` directive instead of a shebang and must stay non-executable, or
# somebody will eventually run it and wonder why nothing happened.
for f in "${SHELL_FILES[@]}" firstboot/qr_server.py; do
  case "$f" in lib/common.sh) continue ;; esac
  assert "$f is executable" test -x "$HERE/$f"
done
assert "lib/common.sh is a library, not executable" not test -x "$HERE/lib/common.sh"

# ===========================================================================
stage "2. shellcheck"
# ===========================================================================
if command -v shellcheck >/dev/null 2>&1; then
  for f in "${SHELL_FILES[@]}"; do
    assert "shellcheck $f" shellcheck -x -S warning "$HERE/$f"
  done
else
  skip "shellcheck is not installed on this host — bash -n only.
        Rootless fix: apt-get download shellcheck && dpkg-deb -x shellcheck_*.deb ./sc
        then re-run with PATH=\$PWD/sc/usr/bin:\$PATH"
fi

# ===========================================================================
stage "3. cross-file paths — the unit and the file it starts must agree"
# ===========================================================================
# This is the failure that costs a whole build: everything parses, everything
# installs, and the oneshot dies at boot on "No such file or directory"
# because a path was changed in one of two places.
for unit in "${UNITS[@]}"; do
  assert "firstboot/$unit exists" test -f "$HERE/firstboot/$unit"
  assert "$unit has an [Install] section" grep -q '^\[Install\]' "$HERE/firstboot/$unit"
  assert "$unit is WantedBy=multi-user.target" \
    grep -q '^WantedBy=multi-user.target' "$HERE/firstboot/$unit"
done

# build.sh composes destinations from $PAYLOAD_DIR, so a literal grep for the
# absolute path would never match. Read the variable out of build.sh and
# compare it to the directory the units name — that is the pair that has to
# agree, and the only way they can drift.
BUILD_PAYLOAD_DIR="$(sed -n 's/^PAYLOAD_DIR=//p' "$HERE/build.sh" | head -1)"
assert "build.sh PAYLOAD_DIR is /opt/opencastor-image (got '$BUILD_PAYLOAD_DIR')" \
  test "$BUILD_PAYLOAD_DIR" = "/opt/opencastor-image"
for base in firstboot.sh qr_server.py; do
  assert "a unit starts $BUILD_PAYLOAD_DIR/$base" \
    grep -qh "^ExecStart=.*$BUILD_PAYLOAD_DIR/$base\$" "$HERE/firstboot/opencastor-firstboot.service" "$HERE/firstboot/opencastor-qr.service"
  assert "build.sh installs firstboot/$base into \$PAYLOAD_DIR" \
    grep -q "install -D .*firstboot/$base\".*\$ROOTMNT\$PAYLOAD_DIR/$base\"" "$HERE/build.sh"
done

assert "firstboot is gated on the .provisioned stamp" \
  grep -q 'ConditionPathExists=!/var/lib/opencastor/.provisioned' \
    "$HERE/firstboot/opencastor-firstboot.service"
assert "firstboot.sh writes the stamp the unit's condition reads" \
  grep -q 'STAMP="\$OC_STATE/.provisioned"' "$HERE/firstboot/firstboot.sh"

# The page must not be ordered behind the thing it reports on.
assert "the QR page has NO ordering dependency on provisioning (it must come up degraded)" \
  not grep -qE '^(After|Requires|Wants)=.*opencastor-firstboot' "$HERE/firstboot/opencastor-qr.service"
assert "the QR page binds :80 via an ambient capability, not as root" \
  grep -q 'AmbientCapabilities=CAP_NET_BIND_SERVICE' "$HERE/firstboot/opencastor-qr.service"
assert "the QR page runs as the opencastor user" \
  grep -q '^User=opencastor' "$HERE/firstboot/opencastor-qr.service"

# Nothing anywhere may write to the Imager's partition.
assert "no script writes to cmdline.txt/config.txt/firstrun.sh/userconf.txt" \
  not grep -rnE '(cp|install|sed|tee|>>?)[^|]*(boot/firmware|/boot/)(cmdline|config|firstrun|userconf)' \
    "$HERE/build.sh" "$HERE/lib/common.sh" "$HERE/lib/chroot-stage.sh" "$HERE/firstboot/firstboot.sh"
assert "build.sh mounts the boot partition READ-ONLY" \
  grep -q 'mount -o ro "\$BOOTDEV"' "$HERE/build.sh"
assert "build.sh refuses a non-aarch64 host before it touches anything" \
  grep -q 'oc_require_aarch64' "$HERE/build.sh"
assert "the chroot stage asserts the image's python matches the wheelhouse" \
  grep -q 'EXPECT_PYVER' "$HERE/lib/chroot-stage.sh"

# -- the no-network invariant is a MECHANISM, and these are its screws --------
# It used to be a sentence in a comment: "no network, enforced by omission —
# we don't bind-mount /etc/resolv.conf". The base image ships its own
# /etc/resolv.conf (nameserver 8.8.8.8) and a chroot shares the host's network
# stack, so the sentence was false and an accidental `pip install` from PyPI
# would have worked perfectly. `unshare -n` is what makes it true; these
# checks are what keep it from being refactored back into a wish.
assert "build.sh's chroot wrapper is unshare -n" \
  grep -q 'chroot_nonet() { unshare -n chroot' "$HERE/build.sh"
assert "no bare \`chroot\` call survives in build.sh — every one goes through chroot_nonet" \
  not grep -nE '^[[:space:]]*chroot[[:space:]]' "$HERE/build.sh"
assert "build.sh refuses a host with no unshare, in preflight" \
  grep -q 'chroot unshare install' "$HERE/build.sh"
assert "the chroot stage checks its own namespace before installing a wheel" \
  grep -q '/proc/net/dev' "$HERE/lib/chroot-stage.sh"
assert "…and dies rather than warns when it sees an interface" \
  grep -q 'supposed to run inside an empty network namespace' "$HERE/lib/chroot-stage.sh"
assert "nothing claims the chroot is offline merely because resolv.conf is absent" \
  not grep -rniE '(asserted|enforced) by omission' \
    "$HERE/build.sh" "$HERE/lib/chroot-stage.sh" "$HERE/../../docs/IMAGE.md"
# The other retracted claim. Verified against this base image by reading its
# ext4 directly: /usr/bin/zstd, 1316424 bytes, mode 0755.
assert "nothing claims the base image lacks zstd (it ships /usr/bin/zstd)" \
  not grep -rniE '(has no|ships no|without|there is no) zstd' \
    "$HERE/build.sh" "$HERE/lib/chroot-stage.sh" "$HERE/../../docs/IMAGE.md"

# ===========================================================================
stage "4. systemd units parse"
# ===========================================================================
if command -v systemd-analyze >/dev/null 2>&1; then
  FAKE="$TMP/fakeroot"
  mkdir -p "$FAKE/etc/systemd/system" "$FAKE/opt/opencastor-image" "$FAKE/usr/local/bin" "$FAKE/usr/bin"
  cp "$HERE/firstboot/"*.service "$FAKE/etc/systemd/system/"
  # Stubs at the exact absolute paths the units name, so verify is judging our
  # unit files and not the absence of an image.
  printf '#!/bin/sh\n' > "$FAKE/opt/opencastor-image/firstboot.sh"
  printf '#!/bin/sh\n' > "$FAKE/opt/opencastor-image/qr_server.py"
  printf '#!/bin/sh\n' > "$FAKE/usr/local/bin/ollama"
  printf '#!/bin/sh\n' > "$FAKE/usr/bin/python3"
  chmod +x "$FAKE/opt/opencastor-image/"* "$FAKE/usr/local/bin/ollama" "$FAKE/usr/bin/python3"
  for unit in "${UNITS[@]}"; do
    out="$(systemd-analyze verify --root="$FAKE" "$unit" 2>&1 \
           | grep -vE 'Unit .* not found|ollama\.service|network-online\.target|systemd-user-sessions')"
    check "$([ -z "$out" ] && echo 0 || echo 1)" "systemd-analyze verify $unit" "$out"
  done
else
  skip "systemd-analyze not available"
fi

# ===========================================================================
stage "5. build.sh --dry-run (rootless, validates every staged input)"
# ===========================================================================
DRY="$TMP/dry.out"
"$HERE/build.sh" --dry-run > "$DRY" 2>&1
check $? "build.sh --dry-run exits clean" "$(tail -5 "$DRY")"
assert "--dry-run reports every input present" grep -q 'dry run: every input is present' "$DRY"
assert "--dry-run prints the computed growth" grep -q 'grow by' "$DRY"
assert "--dry-run names the model it will stage" grep -q 'qwen3.5:2b' "$DRY"
assert "--dry-run states the boot partition is read-only" grep -q 'READ-ONLY' "$DRY"
# Refusing to run as root is not the same as running: prove nothing was made.
assert "--dry-run created no work image" \
  not test -e "${OPENCASTOR_IMAGE_WORK:-$HOME/image-build/work}/opencastor-pi.img"
assert "build.sh rejects an unknown flag instead of guessing" \
  not "$HERE/build.sh" --nonsense-flag

# -- provenance: the dry run has to say WHAT it would bake -------------------
assert "--dry-run names the commit it would bake in" \
  grep -qE 'provenance [0-9a-f]{12}' "$DRY"
assert "--dry-run says provenance lands in the image" \
  grep -q '/etc/opencastor-image.json' "$DRY"
# The dirty warning is conditional, so BOTH branches are checked — otherwise
# this check quietly stops meaning anything the day the tree is committed.
if [ -n "$(git -c safe.directory='*' -C "$HERE/../.." status --porcelain 2>/dev/null)" ]; then
  assert "…and WARNS loudly, because this tree has uncommitted edits in it" \
    grep -q 'THE WORKING TREE IS DIRTY' "$DRY"
else
  assert "…and does not cry dirty on a clean tree" \
    not grep -q 'THE WORKING TREE IS DIRTY' "$DRY"
fi

# -- --xz-preset is checked at parse time, not at minute twenty-five ---------
# `xz` only rejects a bad preset when it is finally invoked, which on this rail
# is after the image is already built, mounted, populated and unmounted.
"$HERE/build.sh" --xz-preset >"$TMP/xz.out" 2>&1
check "$([ $? -ne 0 ] && echo 0 || echo 1)" "--xz-preset with no value errors instead of \$2-unbound"
assert "…and the message says what a preset looks like" grep -q '0-9' "$TMP/xz.out"
assert "…\$2-unbound is NOT what the operator sees" not grep -q 'unbound variable' "$TMP/xz.out"
assert "--xz-preset 42 is rejected"  not "$HERE/build.sh" --xz-preset 42 -h
assert "--xz-preset abc is rejected" not "$HERE/build.sh" --xz-preset abc -h
assert "--xz-preset -1 is rejected"  not "$HERE/build.sh" --xz-preset -1 -h
assert "--xz-preset 6 is accepted"   "$HERE/build.sh" --xz-preset 6 -h
assert "--xz-preset 9e is accepted (xz's 'extreme' suffix)" "$HERE/build.sh" --xz-preset 9e -h
assert "a bad OPENCASTOR_XZ_PRESET is caught too" \
  not env OPENCASTOR_XZ_PRESET=99 "$HERE/build.sh" -h
assert "a good OPENCASTOR_XZ_PRESET still works" \
  env OPENCASTOR_XZ_PRESET=0 "$HERE/build.sh" -h

# ===========================================================================
stage "6. wheelhouse completeness — a venv built with the network refused"
# ===========================================================================
WH="${OPENCASTOR_WHEELHOUSE:-$HOME/image-build/wheelhouse}"
if [ ! -f "$WH/requirements-image.txt" ]; then
  skip "no wheelhouse at $WH — run ./build-wheelhouse.sh first"
else
  for pat in 'opencastor-*.whl' 'rc_car_actuator-*.whl' 'pip-*.whl' 'qrcode-*.whl'; do
    assert "wheelhouse carries $pat" \
      test -n "$(find "$WH" -maxdepth 1 -name "$pat" -print -quit)"
  done
  ( cd "$WH" && sha256sum --quiet -c MANIFEST.sha256 ) >"$TMP/sha.out" 2>&1
  check $? "MANIFEST.sha256 verifies (no wheel changed under us)" "$(tail -3 "$TMP/sha.out")"

  "$PY" -m venv "$TMP/venv" >/dev/null 2>&1
  "$TMP/venv/bin/python" -m pip install --quiet --disable-pip-version-check \
      --no-index --find-links "$WH" -r "$WH/requirements-image.txt" >"$TMP/pip.out" 2>&1
  check $? "pip install --no-index --find-links resolves the whole closure" "$(tail -3 "$TMP/pip.out")"

  if [ -x "$TMP/venv/bin/castor" ]; then
    VER="$("$TMP/venv/bin/python" -c 'import castor;print(castor.__version__)' 2>&1)"
    check $? "import castor  (version $VER)" "$VER"
    assert "castor up --help  (the exact console script firstboot runs)" \
      "$TMP/venv/bin/castor" up --help
    assert "the rc-car actuator registers its entry point (else the rover has no wheels)" \
      "$TMP/venv/bin/python" -c '
from importlib.metadata import entry_points
n = {e.name for e in entry_points(group="robot_md_gateway.actuators")}
raise SystemExit(0 if "rc-car" in n else f"missing rc-car; saw {sorted(n)}")'
    assert "qrcode can make a PNG (without it the QR page has nothing to show)" \
      "$TMP/venv/bin/python" -c "import qrcode; qrcode.make('x').save('$TMP/probe.png')"
  else
    fail "the scratch venv has no castor console script"
  fi
fi

# ===========================================================================
stage "7. firstboot degradation — a bad boot must still produce a readable report"
# ===========================================================================
# Case A: the service account is missing. The earliest possible failure.
A="$TMP/caseA"; mkdir -p "$A"
OC_USER=definitely-no-such-user OC_STATE="$A" VENV="$A/nope" \
  OLLAMA_WAIT=1 USERBUS_WAIT=1 \
  "$HERE/firstboot/firstboot.sh" >"$A/out" 2>&1
check "$([ $? -ne 0 ] && echo 0 || echo 1)" \
  "firstboot exits non-zero when the service account is missing"
assert "…and still writes status.json" test -f "$A/status.json"
assert "…which is valid JSON, ok=false, and names the 'no-user' cause" \
  python3 -c "
import json
s = json.load(open('$A/status.json'))
assert s['ok'] is False, s
assert any('no-user' in d for d in s['degraded']), s['degraded']
assert s['phase'] == 'failed', s['phase']"
assert "…and leaves NO stamp, so the next boot retries" not test -f "$A/.provisioned"

# Case B: a real account with no venv. Walks identity -> user-manager -> brain
# -> castor-up, so the status file has to survive a multi-stage partial run.
B="$TMP/caseB"; mkdir -p "$B"
OC_USER="$(id -un)" OC_STATE="$B" VENV="$B/nope" ROBOT_HOME="$B/robot" \
  OLLAMA_WAIT=1 USERBUS_WAIT=1 \
  "$HERE/firstboot/firstboot.sh" >"$B/out" 2>&1
assert "a partial boot yields parseable JSON naming every cause" \
  python3 -c "
import json
s = json.load(open('$B/status.json'))
assert s['ok'] is False, s
assert s['degraded'], s
assert any('no-castor' in d for d in s['degraded']), s['degraded']
assert s['robot_name'], 'the robot name should come from the hostname'
assert isinstance(s['elapsed_s'], int), s"
assert "the log records each degradation as it happens" grep -q 'DEGRADED' "$B/firstboot.log"
assert "…and no stamp there either" not test -f "$B/.provisioned"

# ---------------------------------------------------------------------------
# Cases C and D: the END of a boot, where the stamp decision is made.
#
# Reaching that line rootless needs two stand-ins — `runuser` refuses to run as
# anyone but root, and there is no image to install a venv into. Everything
# AFTER them is the real script making the real decision, which is the part
# that was wrong: a boot with no pairing QR degraded and then stamped anyway,
# and the stamp is what the unit's ConditionPathExists=! reads. That robot was
# unpairable forever and certain it was done.
BIN="$TMP/bin"; mkdir -p "$BIN"
cat > "$BIN/runuser" <<'EOS'
#!/bin/sh
# runuser -u USER -- CMD... ; the real one is root-only by design.
while [ $# -gt 0 ]; do
  case "$1" in
    -u) shift 2 ;;
    --) shift; break ;;
    *)  break ;;
  esac
done
exec "$@"
EOS
# Stubs so these cases cost seconds rather than a polkit timeout apiece.
printf '#!/bin/sh\nexit 1\n' > "$BIN/systemctl"   # no session bus to start
printf '#!/bin/sh\nexit 7\n' > "$BIN/curl"        # ollama is not answering
FAKEVENV="$TMP/fakevenv"; mkdir -p "$FAKEVENV/bin"
printf '#!/bin/sh\necho "castor $*"\nexit 0\n' > "$FAKEVENV/bin/castor"
chmod +x "$BIN/runuser" "$BIN/systemctl" "$BIN/curl" "$FAKEVENV/bin/castor"

C="$TMP/caseC"; mkdir -p "$C/robot"          # `castor up` fine, but no QR written
PATH="$BIN:$PATH" OC_USER="$(id -un)" OC_STATE="$C" VENV="$FAKEVENV" \
  ROBOT_HOME="$C/robot" OLLAMA_WAIT=1 USERBUS_WAIT=1 \
  "$HERE/firstboot/firstboot.sh" >"$C/out" 2>&1
check "$([ $? -ne 0 ] && echo 0 || echo 1)" \
  "a boot that produced no pairing QR exits non-zero"
assert "…and says 'no-qr' in the status the page reads" \
  python3 -c "
import json
s = json.load(open('$C/status.json'))
assert s['ok'] is False, s
assert any(d.startswith('no-qr') for d in s['degraded']), s['degraded']"
assert "…and writes NO stamp, so the next boot retries instead of giving up forever" \
  not test -f "$C/.provisioned"

D="$TMP/caseD"; mkdir -p "$D/robot"          # same run, with a QR on disk
printf 'PNG-ish bytes\n' > "$D/robot/pair-qr.png"
PATH="$BIN:$PATH" OC_USER="$(id -un)" OC_STATE="$D" VENV="$FAKEVENV" \
  ROBOT_HOME="$D/robot" OLLAMA_WAIT=1 USERBUS_WAIT=1 \
  "$HERE/firstboot/firstboot.sh" >"$D/out" 2>&1
check $? "a boot that DID produce a pairing QR exits clean"
assert "…and stamps, so it never provisions twice (the no-qr fix must not break this)" \
  test -f "$D/.provisioned"
assert "…with phase=done" \
  python3 -c "
import json
s = json.load(open('$D/status.json'))
assert s['phase'] == 'done', s['phase']"

# ===========================================================================
stage "8. the pairing page — served by plain python3, curled for real"
# ===========================================================================
QRPID=""; QRPID2=""
stop_servers() { for p in $QRPID $QRPID2; do kill "$p" 2>/dev/null; wait "$p" 2>/dev/null; done; }
trap 'stop_servers; rm -rf "$TMP"' EXIT

free_port() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'; }
wait_up() { local p="$1" n=40; while [ "$n" -gt 0 ]; do
    curl -fsS --max-time 1 "http://127.0.0.1:$p/healthz" >/dev/null 2>&1 && return 0
    n=$((n-1)); sleep 0.25
  done; return 1; }

if ! command -v curl >/dev/null 2>&1; then
  skip "curl is not installed"
else
  FIX="$TMP/fixture"; mkdir -p "$FIX/robot"
  # A real scannable QR when the build venv can make one, a valid minimal PNG
  # otherwise. Either way the server is handed bytes on disk, not a mock.
  if ! "$PY" -c "
import qrcode
qrcode.make('{\"v\":1,\"rrn\":\"rrn:local:selftest\"}').save('$FIX/robot/pair-qr.png')" 2>/dev/null; then
    python3 -c "
import base64, pathlib
pathlib.Path('$FIX/robot/pair-qr.png').write_bytes(base64.b64decode(
 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='))"
  fi
  assert "fixture pair-qr.png written" test -s "$FIX/robot/pair-qr.png"
  cp "$B/status.json" "$FIX/status.json"
  NAME="$(python3 -c "import json;print(json.load(open('$FIX/status.json'))['robot_name'])")"

  PORT="$(free_port)"
  OC_STATE="$FIX" ROBOT_HOME="$FIX/robot" OPENCASTOR_QR_PORT="$PORT" \
    OPENCASTOR_QR_BIND=127.0.0.1 \
    /usr/bin/python3 "$HERE/firstboot/qr_server.py" >"$TMP/qr.log" 2>&1 &
  QRPID=$!
  if ! wait_up "$PORT"; then
    fail "the QR server never answered on :$PORT — $(tail -3 "$TMP/qr.log")"
  else
    pass "GET /healthz (server up under /usr/bin/python3, no venv)"
    curl -fsS --max-time 3 "http://127.0.0.1:$PORT/" > "$TMP/page.html" 2>"$TMP/page.err"
    check $? "GET / returns the page" "$(cat "$TMP/page.err")"
    assert "…the page embeds the QR image"        grep -q 'pair-qr.png'      "$TMP/page.html"
    assert "…and names the app to scan it with"   grep -q 'OpenCastor'       "$TMP/page.html"
    assert "…with two-line scan instructions"     grep -qi 'point the camera' "$TMP/page.html"
    assert "…reports what degraded, in words"     grep -q 'no castor'        "$TMP/page.html"
    assert "…headed with the robot's name"        grep -qF "$NAME"           "$TMP/page.html"

    curl -fsS -D "$TMP/hdr" -o "$TMP/qr.png" --max-time 3 "http://127.0.0.1:$PORT/pair-qr.png"
    check $? "GET /pair-qr.png"
    assert "…served as image/png" grep -qi 'content-type: image/png' "$TMP/hdr"
    assert "…byte-identical to the file on disk" cmp -s "$TMP/qr.png" "$FIX/robot/pair-qr.png"

    # The payload JSON holds the actuate bearer in curl-able form. The QR holds
    # the same bytes, but only for something that can decode a picture. Serving
    # both would be a strictly worse exposure for no extra capability.
    CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$PORT/pair-payload.json")"
    assert "pair-payload.json is NOT served (got $CODE)" test "$CODE" = "404"

    curl -fsS --max-time 3 "http://127.0.0.1:$PORT/status.json" > "$TMP/st.json" 2>/dev/null
    assert "GET /status.json is valid JSON" python3 -c "import json;json.load(open('$TMP/st.json'))"

    # A status file that is valid JSON but not an OBJECT. `[]`, `null` and
    # `"x"` all parse; none of them have .get(), so page() died with a 500 and
    # the operator got the blank screen this whole rail exists to prevent —
    # reached from the one direction nobody had checked. read_status() is read
    # fresh on every request, so the running server picks this up as-is.
    for junk in '[]' 'null' '"truncated"' '17'; do
      printf '%s' "$junk" > "$FIX/status.json"
      CODE="$(curl -s -o "$TMP/junk.html" -w '%{http_code}' --max-time 3 "http://127.0.0.1:$PORT/")"
      assert "the page survives a status.json of $junk (HTTP $CODE)" test "$CODE" = "200"
    done
    printf '%s' '[]' > "$FIX/status.json"
    curl -fsS --max-time 3 "http://127.0.0.1:$PORT/" > "$TMP/junk.html" 2>/dev/null
    assert "…and says the status file is the problem, rather than pretending" \
      grep -qi 'not a JSON object' "$TMP/junk.html"
    assert "GET /status.json also answers with an object, not the junk it read" \
      python3 -c "
import json, urllib.request
d = json.load(urllib.request.urlopen('http://127.0.0.1:$PORT/status.json', timeout=3))
assert isinstance(d, dict), d"
    cp "$B/status.json" "$FIX/status.json"
  fi

  # No status file at all — the first seconds of a real boot.
  E="$TMP/empty"; mkdir -p "$E"
  PORT2="$(free_port)"
  OC_STATE="$E" ROBOT_HOME="$E/robot" OPENCASTOR_QR_PORT="$PORT2" \
    OPENCASTOR_QR_BIND=127.0.0.1 \
    /usr/bin/python3 "$HERE/firstboot/qr_server.py" >"$TMP/qr2.log" 2>&1 &
  QRPID2=$!
  if ! wait_up "$PORT2"; then
    fail "the QR server did not start with an empty state dir"
  else
    curl -fsS --max-time 3 "http://127.0.0.1:$PORT2/" > "$TMP/page2.html" 2>/dev/null
    check $? "the page answers with no status file and no QR at all"
    assert "…and says the robot is still setting up" grep -qi 'setting itself up' "$TMP/page2.html"
    assert "…and auto-refreshes while it waits" grep -q 'http-equiv=refresh' "$TMP/page2.html"
  fi
  stop_servers
fi

# ===========================================================================
stage "9. signals — the two deaths nothing here used to simulate"
# ===========================================================================
# The gap the review named: not one check in this file ever signalled anything,
# and both scripts were wrong about signals in the same way. bash runs a trap
# handler and then RESUMES at the next command unless the handler exits, and
# `$?` in an EXIT trap is the last command's status, which after a killed
# foreground child is routinely 0. So build.sh tore its mounts down and kept
# building, and firstboot.sh recorded a timed-out first boot as a success.

# -- firstboot.sh: SIGTERM in a foreground sleep -----------------------------
# The real shape of this: systemd's TimeoutStartSec= expires and the oneshot
# gets SIGTERM'd while the script sits in one of its wait loops. The stubbed
# curl keeps the brain loop spinning on `sleep 2` so the signal lands where it
# lands in production, in a foreground child.
S="$TMP/caseS"; mkdir -p "$S/robot"
PATH="$BIN:$PATH" OC_USER="$(id -un)" OC_STATE="$S" VENV="$FAKEVENV" \
  ROBOT_HOME="$S/robot" OLLAMA_WAIT=45 USERBUS_WAIT=1 \
  "$HERE/firstboot/firstboot.sh" >"$S/out" 2>&1 &
SPID=$!
n=0; while [ ! -f "$S/firstboot.log" ] && [ "$n" -lt 100 ]; do sleep 0.1; n=$((n+1)); done
sleep 1                       # …and it is now inside the brain wait loop
kill -TERM "$SPID" 2>/dev/null
wait "$SPID"; SRC=$?
check "$([ "$SRC" -eq 143 ] && echo 0 || echo 1)" \
  "a SIGTERM'd first boot exits 143, not 0 (got $SRC)" "$(tail -3 "$S/out")"
assert "…status.json says ok:false — a timed-out boot must never read as success" \
  python3 -c "
import json
s = json.load(open('$S/status.json'))
assert s['ok'] is False, s"
assert "…and carries an 'aborted' degradation naming the signal and the phase" \
  python3 -c "
import json
s = json.load(open('$S/status.json'))
ab = [d for d in s['degraded'] if d.startswith('aborted')]
assert ab, s['degraded']
assert 'SIGTERM' in ab[0], ab
assert \"during 'brain'\" in ab[0] or \"during '\" in ab[0], ab"
assert "…and leaves NO stamp, so the Pi retries on the next boot" \
  not test -f "$S/.provisioned"
assert "…and the generic on_exit line does not bury the specific one" \
  python3 -c "
import json
s = json.load(open('$S/status.json'))
ab = [d for d in s['degraded'] if d.startswith('aborted')]
assert len(ab) == 1, ab"

# -- build.sh: the teardown trap, extracted and signalled --------------------
# Not a copy of build.sh's trap logic — build.sh's trap logic, read out of
# build.sh between its markers and sourced. Everything root-only (losetup,
# umount) is unreachable with MOUNTED=0 and LOOPDEV empty, which leaves exactly
# the control flow that was broken.
TRAPD="$TMP/traps"; mkdir -p "$TRAPD"
sed -n '/# --- BEGIN teardown-traps/,/# --- END teardown-traps/p' "$HERE/build.sh" \
  > "$TRAPD/region.sh"
assert "the teardown-trap region is still extractable from build.sh" \
  test -s "$TRAPD/region.sh"
assert "…and it defines the signal handler, not just the exit one" \
  grep -q 'on_signal()' "$TRAPD/region.sh"

# bash sets SIGINT (and SIGQUIT) to IGNORE for asynchronous commands, and a
# signal that was ignored on entry cannot be trapped at all — so a Ctrl-C
# rehearsal started with `&` would grade nothing. This shim restores the
# default disposition and then execs, which is what a terminal or systemd hands
# a real build.
cat > "$TRAPD/undefer.py" <<'EOS'
import os, signal, sys
for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(sig, signal.SIG_DFL)
os.execv(sys.argv[1], sys.argv[1:])
EOS

cat > "$TRAPD/harness.sh" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
OUT="$1"; REGION="$2"
OC_C_RED=''; OC_C_YEL=''; OC_C_OFF=''
say() { printf 'say %s\n' "$*"; }
MOUNTED=0; ROOTMNT=""; LOOPDEV=""
# shellcheck source=/dev/null
. "$REGION"
install_teardown_traps
: > "$OUT/ready"
# Many short sleeps rather than one long one: a pending trap runs only after
# the current foreground child returns, and this has to answer in milliseconds.
n=0; while [ "$n" -lt 600 ]; do sleep 0.05; n=$((n+1)); done
printf 'stages after the signal executed\n' > "$OUT/resumed"
EOS
chmod +x "$TRAPD/harness.sh"

#: signal_build SIG EXPECTED_RC OUTDIR  — run the harness, signal it, report rc
signal_build() {
  local sig="$1" out="$3" n=0
  mkdir -p "$out"
  python3 "$TRAPD/undefer.py" "$TRAPD/harness.sh" "$out" "$TRAPD/region.sh" >"$out/log" 2>&1 &
  local pid=$!
  while [ ! -e "$out/ready" ] && [ "$n" -lt 150 ]; do sleep 0.05; n=$((n+1)); done
  kill -"$sig" "$pid" 2>/dev/null
  wait "$pid"
  printf '%s' "$?"
}

TRC="$(signal_build TERM 143 "$TMP/trapT")"
check "$([ "$TRC" = "143" ] && echo 0 || echo 1)" \
  "a SIGTERM'd build exits 128+15 instead of returning into the next stage (got $TRC)"
assert "…and NO stage ran after the teardown (this is the whole bug)" \
  not test -e "$TMP/trapT/resumed"
assert "…and the teardown announced itself on the way out" \
  grep -q 'SIGTERM' "$TMP/trapT/log"

IRC="$(signal_build INT 130 "$TMP/trapI")"
check "$([ "$IRC" = "130" ] && echo 0 || echo 1)" \
  "Ctrl-C exits 128+2 (got $IRC)"
assert "…and nothing ran after it either" not test -e "$TMP/trapI/resumed"
assert "build.sh installs the handlers rather than aliasing cleanup onto INT/TERM" \
  grep -q '^install_teardown_traps$' "$HERE/build.sh"
# Anchored, because the comment explaining the bug quotes the broken line.
assert 'no live "trap cleanup EXIT INT TERM" survives in build.sh' \
  not grep -qE '^[[:space:]]*trap +cleanup +EXIT +INT' "$HERE/build.sh"

# ===========================================================================
stage "10. the guards, extracted and exercised"
# ===========================================================================
# Three fixes whose bug was arithmetic or algorithm rather than plumbing. Each
# is pulled out of the file that owns it and run for real, including a probe
# that shows the OLD version passing where the new one fails — a regression
# test that cannot regress quietly.

# -- the boot-partition fingerprint ------------------------------------------
FP="$TMP/fp/boot"; mkdir -p "$FP/overlays"
printf 'console=serial0,115200 root=PARTUUID=aa11bb22-02 rootwait\n' > "$FP/cmdline.txt"
printf 'dtparam=audio=on\n' > "$FP/config.txt"
printf 'compiled overlay\n' > "$FP/overlays/rc-car.dtbo"
#: the algorithm this replaced: names and sizes, one level deep.
old_fp() { find "$1" -maxdepth 1 -type f -printf '%f %s\n' | LC_ALL=C sort | sha256sum; }
FP_NEW_BEFORE="$(oc_dir_fingerprint "$FP")"; FP_OLD_BEFORE="$(old_fp "$FP")"
assert "the fingerprint is stable across two reads of the same tree" \
  test "$FP_NEW_BEFORE" = "$(oc_dir_fingerprint "$FP")"
# A same-length edit to cmdline.txt: exactly what the Imager owns, and exactly
# what a name+size digest cannot see.
sed -i 's/rootwait/rootwaiT/' "$FP/cmdline.txt"
FP_NEW_AFTER="$(oc_dir_fingerprint "$FP")"; FP_OLD_AFTER="$(old_fp "$FP")"
assert "a same-size rewrite of cmdline.txt CHANGES the fingerprint" \
  not test "$FP_NEW_BEFORE" = "$FP_NEW_AFTER"
assert "…and the name+size version this replaced did not notice it at all" \
  test "$FP_OLD_BEFORE" = "$FP_OLD_AFTER"
printf 'tampered\n' > "$FP/overlays/rc-car.dtbo"
assert "…and it reaches into subdirectories, which -maxdepth 1 never did" \
  not test "$FP_NEW_AFTER" = "$(oc_dir_fingerprint "$FP")"

# -- the --reuse-img grow guard ----------------------------------------------
GROWD="$TMP/grow"; mkdir -p "$GROWD"
sed -n '/# --- BEGIN grow-target/,/# --- END grow-target/p' "$HERE/build.sh" > "$GROWD/region.sh"
assert "the grow-target region is still extractable from build.sh" test -s "$GROWD/region.sh"
#: grow_needed BASE_UNPACKED GROW_MIB REUSE_IMG IMAGE_SIZE -> 0 or 1
grow_needed() (
  set -euo pipefail
  # These are the region's INPUTS; shellcheck cannot follow a sourced "$VAR".
  # shellcheck disable=SC2034
  BASE_UNPACKED="$1"
  # shellcheck disable=SC2034
  GROW_MIB="$2"
  # shellcheck disable=SC2034
  REUSE_IMG="$3"
  OUT_IMG="$GROWD/img"; truncate -s "$4" "$OUT_IMG"
  # shellcheck source=/dev/null
  . "$GROWD/region.sh"
  printf '%s' "$GROW_NEEDED"
)
BASE_SZ=$((3000 * 1048576)); GROWN_SZ=$((BASE_SZ + 5120 * 1048576))
assert "a freshly unpacked image still gets grown" \
  test "$(grow_needed "$BASE_SZ" 5120 1 "$BASE_SZ")" = "1"
assert "an ALREADY grown --reuse-img image is left alone (the guard used to be dead code)" \
  test "$(grow_needed "$BASE_SZ" 5120 1 "$GROWN_SZ")" = "0"
assert "…and without --reuse-img it grows regardless" \
  test "$(grow_needed "$BASE_SZ" 5120 0 "$GROWN_SZ")" = "1"
assert "an image grown further than asked is still left alone" \
  test "$(grow_needed "$BASE_SZ" 5120 1 $(( GROWN_SZ + 1048576 )))" = "0"

# -- provenance --------------------------------------------------------------
PROVD="$TMP/prov"; mkdir -p "$PROVD/wh"
sed -n '/# --- BEGIN provenance/,/# --- END provenance/p' "$HERE/build.sh" > "$PROVD/region.sh"
assert "the provenance region is still extractable from build.sh" test -s "$PROVD/region.sh"
printf 'deadbeef  some-1.0-py3-none-any.whl\n' > "$PROVD/wh/MANIFEST.sha256"
: > "$PROVD/wh/some-1.0-py3-none-any.whl"
( set -euo pipefail
  # Inputs to the sourced region; see the SC2034 note on grow_needed above.
  # shellcheck disable=SC2034
  REPO_ROOT="$(cd "$HERE/../.." && pwd)"
  # shellcheck disable=SC2034
  WHEELHOUSE="$PROVD/wh"
  # shellcheck disable=SC2034
  MODEL="qwen3.5:2b"
  # shellcheck disable=SC2034
  BASE_IMG="/nowhere/raspios.img.xz"
  # shellcheck source=/dev/null
  . "$PROVD/region.sh"
  write_provenance "$PROVD/out.json" ) >"$PROVD/err" 2>&1
check $? "write_provenance runs" "$(tail -3 "$PROVD/err")"
assert "…and emits an object with the three facts that identify a build" \
  python3 -c "
import json, subprocess
d = json.load(open('$PROVD/out.json'))
head = subprocess.run(['git','-c','safe.directory=*','-C','$HERE/../..','rev-parse','HEAD'],
                      capture_output=True, text=True).stdout.strip()
assert d['git_head'] == head, (d['git_head'], head)
assert isinstance(d['git_dirty'], bool), d
assert len(d['wheelhouse_manifest_sha256']) == 64, d
assert d['wheelhouse_wheels'] == 1, d
assert d['model'] == 'qwen3.5:2b', d"
assert "…and reports the tree's real dirty state, not a constant" \
  python3 -c "
import json, subprocess
d = json.load(open('$PROVD/out.json'))
porcelain = subprocess.run(['git','-c','safe.directory=*','-C','$HERE/../..','status','--porcelain'],
                           capture_output=True, text=True).stdout.strip()
assert d['git_dirty'] is bool(porcelain), (d['git_dirty'], bool(porcelain))"

# ===========================================================================
stage "summary"
# ===========================================================================
printf '\n  %d passed, %d failed, %d skipped\n\n' "$PASS" "$FAIL" "$SKIP"
if [ "$FAIL" -gt 0 ]; then
  printf '  %sself-test FAILED%s\n' "$OC_C_RED" "$OC_C_OFF"; exit 1
fi
cat <<'ROOT'
  Rootless checks all green. STILL UNVERIFIED, and only a real
  `sudo ./scripts/image/build.sh` can change that:
    * losetup -P / mount / umount / the teardown on a real failure (the trap
      LOGIC is proven above; the umounts and losetup -d it drives are not)
    * truncate + parted resizepart + resize2fs on the real partition table
    * the native aarch64 chroot under `unshare -n`: useradd, dpkg -i of the
      venv .debs, pip, and chroot-stage.sh's own empty-namespace assertion
    * the boot-partition fingerprint against a real mounted FAT partition (the
      hash function itself is proven above)
    * /etc/opencastor-image.json actually landing in the image
    * and then, on hardware: flash, boot twice, watch the clock.
ROOT
