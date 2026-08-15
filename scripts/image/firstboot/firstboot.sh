#!/usr/bin/env bash
# First boot: turn a flashed card into a paired-ready robot, with nobody watching.
#
# THE CLOCK THIS SERVES. Ten minutes, starting at a blank SD card, for someone
# who has never opened a terminal. Everything that used to be typed — the venv,
# the model pull, `castor up`, reading the QR path out of the scrollback — has
# to happen while the operator is still walking back from the card reader. The
# only interface is a web page on port 80 and a camera.
#
# WHY THIS SCRIPT DOES NOT `set -e`. Every other script in this directory does.
# This one must not, and the reason is the whole design: `castor up` reports
# honestly, which means it can come back degraded — no I2C bus, no LAN address
# yet, an ollama that has not finished loading. A `set -e` here turns any of
# those into a unit that exits non-zero with no status file, and the operator
# gets a blank page and a machine they cannot see into. A first boot that half
# worked must SAY which half. So: explicit `step` calls, failures recorded as
# structured degradations, and status.json written on every exit path.
#
# THE STAMP IS EARNED, NOT SPENT. /var/lib/opencastor/.provisioned is written
# only when `castor up` succeeded AND a pairing QR exists to scan. A boot that
# failed, was killed, or produced no QR leaves no stamp, so the next boot tries
# again — safe precisely because `up` reuses identity (keys, tokens, RRN) and
# regenerates only what is stale. A stamp written on failure would be a robot
# that is permanently broken and permanently sure it is finished.
#
# THE IMAGER'S FILES ARE NOT OURS. Hostname and Wi-Fi are the Raspberry Pi
# Imager's job, done from its own firstrun.sh on the boot partition before this
# unit is ever reached. This script reads the hostname and touches nothing else.

set -uo pipefail

OC_USER="${OC_USER:-opencastor}"
OC_STATE="${OC_STATE:-/var/lib/opencastor}"
ROBOT_HOME="${ROBOT_HOME:-$OC_STATE/robot}"
VENV="${VENV:-/opt/opencastor}"
STAMP="$OC_STATE/.provisioned"
STATUS="$OC_STATE/status.json"
LOG="$OC_STATE/firstboot.log"
OLLAMA_WAIT="${OLLAMA_WAIT:-90}"
USERBUS_WAIT="${USERBUS_WAIT:-45}"

STARTED="$(date +%s)"
DEGRADED=()          # "slug: what the operator can see and do about it"
PHASE="starting"
ROBOT_NAME=""
SIGNAL=""            # set by on_signal; read by on_exit so it does not re-narrate

log() { printf '[%4ds] %s\n' "$(( $(date +%s) - STARTED ))" "$*"; }

# json-escape a bash string using the stdlib, not sed. The degradation strings
# carry `castor up` output verbatim, quotes and all, and a hand-rolled escaper
# that gets one of them wrong produces a status file the QR page cannot parse —
# which is exactly the blank screen this script exists to avoid.
jstr() { OC_S="$1" python3 -c 'import json,os;print(json.dumps(os.environ["OC_S"]))'; }

write_status() {
  local ok="$1" tmp="$STATUS.tmp" first=1 d
  {
    printf '{\n'
    printf '  "schema": 1,\n'
    printf '  "ok": %s,\n' "$ok"
    printf '  "phase": %s,\n' "$(jstr "$PHASE")"
    printf '  "robot_name": %s,\n' "$(jstr "$ROBOT_NAME")"
    printf '  "robot_home": %s,\n' "$(jstr "$ROBOT_HOME")"
    printf '  "elapsed_s": %d,\n' "$(( $(date +%s) - STARTED ))"
    printf '  "finished_at": %d,\n' "$(date +%s)"
    printf '  "log": %s,\n' "$(jstr "$LOG")"
    printf '  "degraded": ['
    for d in ${DEGRADED[@]+"${DEGRADED[@]}"}; do
      [ $first -eq 1 ] || printf ','
      printf '\n    %s' "$(jstr "$d")"; first=0
    done
    [ $first -eq 1 ] || printf '\n  '
    printf ']\n}\n'
  } > "$tmp"
  mv -f "$tmp" "$STATUS"
  chown "$OC_USER:$OC_USER" "$STATUS" 2>/dev/null || true
  chmod 0644 "$STATUS"
}

degrade() { DEGRADED+=("$1"); log "DEGRADED  $1"; write_status false; }

# A SIGNAL IS NOT AN EXIT STATUS, AND THE EXIT TRAP CANNOT TELL THEM APART.
# This is the bug that made `TimeoutStartSec=` a lie. `trap on_exit EXIT` alone
# looks like it covers "killed by the unit's timeout" — it does not. When
# SIGTERM arrives while this script sits in a foreground `sleep`, the signal
# reaps the CHILD; `$?` inside the EXIT trap is then whatever the enclosing
# `while` loop returned, which is 0, routinely. So a first boot that systemd
# cut off at minute five wrote `"ok": true`, no `aborted` degradation, and —
# because the tail of the script had never run — the operator's only clue was a
# page claiming success. Worse, on the paths where bash resumes after a
# returning handler, the stamp could still be written.
#
# So the signals are trapped by name. The handler records the abort, writes the
# status file, and exits 128+n ITSELF, which is the only way a bash signal
# handler stops the script rather than resuming it.
on_signal() {
  local name="$1" num="$2"
  SIGNAL="$name"
  # File first, log second, on purpose. systemd signals the whole cgroup, which
  # includes the `tee` this script's stdout is piped through; once tee is gone
  # a `log` line can take the shell down with SIGPIPE. status.json is the thing
  # the operator actually sees, so it must already be on disk by then.
  DEGRADED+=("aborted: first boot was stopped by SIG$name during '$PHASE' — a unit timeout (TimeoutStartSec), a shutdown, or a Ctrl-C. No .provisioned stamp was written, so the next boot retries. See $LOG")
  write_status false
  log "DEGRADED  aborted: SIG$name during '$PHASE'"
  PHASE="failed"
  exit $(( 128 + num ))
}

# Any exit — clean, failed, or killed by the unit's timeout — leaves a status
# file behind. The QR page reads it and is therefore never blank.
on_exit() {
  local rc=$?
  # PHASE=failed means the script already said, in its own words, what went
  # wrong, and a signal was narrated by on_signal a moment ago. Adding a generic
  # line on top of a specific one buries the specific one. This branch is for
  # the deaths nobody narrated: the OOM killer, a power cut mid-install.
  if [ -z "$SIGNAL" ] && [ "$rc" -ne 0 ] \
     && [ "$PHASE" != "done" ] && [ "$PHASE" != "failed" ]; then
    degrade "aborted: first boot exited during '$PHASE' (rc=$rc) — see $LOG"
  fi
  write_status "$([ ${#DEGRADED[@]} -eq 0 ] && echo true || echo false)"
  log "status written to $STATUS"
}
# Order matters, and got this wrong once: `tee -a $LOG` cannot create the log
# if its directory does not exist yet, and a redirect that fails takes the whole
# script down before the trap that would have reported it is even installed.
# Directory, then log, then traps.
mkdir -p "$OC_STATE"
exec > >(tee -a "$LOG") 2>&1
trap on_exit EXIT
trap 'on_signal TERM 15' TERM
trap 'on_signal INT 2'   INT
trap 'on_signal HUP 1'   HUP

log "=== opencastor first boot ==="

# ---------------------------------------------------------------------------
PHASE="identity"
# ---------------------------------------------------------------------------
# The robot is named after the hostname the operator typed into the Imager.
# Nothing else on this machine knows a name a human chose, and a QR page
# headed "raspberrypi" when the sticker on the case says "rover" is the kind
# of small confusion that costs the whole ten minutes.
ROBOT_NAME="$(hostname -s 2>/dev/null || echo robot)"
ROBOT_NAME="$(printf '%s' "$ROBOT_NAME" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9-' '-' \
              | sed -E 's/-+/-/g; s/^-//; s/-$//')"
[ -n "$ROBOT_NAME" ] || ROBOT_NAME="robot"
log "robot name: $ROBOT_NAME"

OC_UID="$(id -u "$OC_USER" 2>/dev/null || true)"
if [ -z "$OC_UID" ]; then
  degrade "no-user: the '$OC_USER' account is missing from this image — the build did not finish"
  PHASE="failed"; exit 1
fi
log "service user: $OC_USER (uid $OC_UID)"
write_status false

# ---------------------------------------------------------------------------
PHASE="user-manager"
# ---------------------------------------------------------------------------
# `castor up` installs its three services as systemd USER units and runs
# `systemctl --user enable --now`. That needs a live user manager and a
# runtime dir, which a system account only gets when it lingers. The linger
# marker is laid down at image build time; asserting it again here costs
# nothing and covers an image that was edited by hand.
mkdir -p /var/lib/systemd/linger && touch "/var/lib/systemd/linger/$OC_USER"
systemctl start "user@${OC_UID}.service" >/dev/null 2>&1 || true
deadline=$(( $(date +%s) + USERBUS_WAIT ))
while [ ! -S "/run/user/$OC_UID/bus" ] && [ "$(date +%s)" -lt "$deadline" ]; do sleep 1; done

START_SERVICES=1
if [ -S "/run/user/$OC_UID/bus" ]; then
  log "user manager up: /run/user/$OC_UID/bus"
else
  START_SERVICES=0
  degrade "user-manager: systemd never started a session for '$OC_USER', so the gateway, runtime and console are configured but NOT running. Reboot once; if it persists, run: sudo loginctl enable-linger $OC_USER"
fi

# ---------------------------------------------------------------------------
PHASE="brain"
# ---------------------------------------------------------------------------
# castor.up.detect_brain asks ollama for its model list and writes whatever it
# finds into active-model.json. Ask BEFORE `up`, not after: on a cold Pi ollama
# takes tens of seconds to answer, and an `up` that raced it records a robot
# with no brain and no way to notice.
systemctl start ollama.service >/dev/null 2>&1 || true
deadline=$(( $(date +%s) + OLLAMA_WAIT ))
brain_up=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  if curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then brain_up=1; break; fi
  sleep 2
done
if [ "$brain_up" -eq 1 ]; then
  log "ollama answering on :11434"
else
  degrade "brain: ollama did not answer on :11434 within ${OLLAMA_WAIT}s. The robot still pairs and still drives; chat will have no local model until it starts. Check: systemctl status ollama"
fi

# ---------------------------------------------------------------------------
PHASE="castor-up"
# ---------------------------------------------------------------------------
[ -x "$VENV/bin/castor" ] || { degrade "no-castor: $VENV/bin/castor is missing — the image build did not install the runtime"; PHASE="failed"; exit 1; }
install -d -o "$OC_USER" -g "$OC_USER" -m 0755 "$ROBOT_HOME"

UP_ARGS=(up --home "$ROBOT_HOME" --name "$ROBOT_NAME" --python "$VENV/bin/python")
[ "$START_SERVICES" -eq 1 ] || UP_ARGS+=(--no-start)

log "running: castor ${UP_ARGS[*]}"
if runuser -u "$OC_USER" -- env \
      HOME="$OC_STATE" \
      XDG_RUNTIME_DIR="/run/user/$OC_UID" \
      DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$OC_UID/bus" \
      PATH="$VENV/bin:/usr/local/bin:/usr/bin:/bin" \
      "$VENV/bin/castor" "${UP_ARGS[@]}"; then
  log "castor up finished"
else
  degrade "castor-up: \`castor up\` exited non-zero. The last lines of $LOG say why. This boot left no .provisioned stamp, so the next reboot retries — identity (keys, tokens, RRN) is reused, not regenerated."
  PHASE="failed"
  exit 1
fi

# ---------------------------------------------------------------------------
PHASE="gaps"
# ---------------------------------------------------------------------------
# docs/SKILL-GAPS.md: a gap is not a failure, it is a skill nobody has written
# yet — and it NEVER closes itself. On this image the two gaps the bench used
# to ship with are closed by construction (rc-car-actuator is in the venv, a
# local model is in the store), so anything left is real news and belongs on
# the page in front of the operator rather than in a file they will never open.
GAPS="$ROBOT_HOME/gaps.json"
if [ -f "$GAPS" ]; then
  gap_summary="$(python3 - "$GAPS" <<'PY'
import json, sys
try:
    gaps = json.load(open(sys.argv[1])).get("gaps", [])
except Exception as exc:                                  # noqa: BLE001
    print(f"unreadable ({exc})"); sys.exit(0)
if not gaps:
    print(""); sys.exit(0)
print("; ".join(f"{g.get('kind','?')} — {g.get('suggestion') or g.get('evidence','')}"
                for g in gaps))
PY
)"
  if [ -n "$gap_summary" ]; then
    degrade "gaps: $gap_summary"
  else
    log "gaps: none — everything detected has a driver and a brain"
  fi
else
  log "gaps: no gaps.json written"
fi

# ---------------------------------------------------------------------------
PHASE="done"
# ---------------------------------------------------------------------------
# NO QR MEANS NO STAMP. This used to degrade and then stamp anyway, which is
# the one combination that cannot recover: the stamp is what the unit's
# `ConditionPathExists=!` reads, so a robot whose pairing code never got
# written was permanently unpairable AND permanently certain it was finished.
# Rebooting is the operator's only tool at this point, and the stamp is what
# takes it away from them. Everything `castor up` produced — identity, keys,
# tokens, RRN — is reused on the retry, so the cost of trying again is seconds.
if [ ! -f "$ROBOT_HOME/pair-qr.png" ]; then
  degrade "no-qr: pair-qr.png was not written, so there is nothing to scan. pair-payload.json in $ROBOT_HOME is the documented fallback — the app can take it pasted. NO .provisioned stamp was written: reboot and this runs again, reusing the identity it already has."
  PHASE="failed"
  exit 1
fi

date -u +%FT%TZ > "$STAMP"
chown "$OC_USER:$OC_USER" "$STAMP" 2>/dev/null || true
log "provisioned in $(( $(date +%s) - STARTED ))s — stamp written to $STAMP"
exit 0
