#!/usr/bin/env bash
# autodream.sh — Reference operator script for nightly robot health + memory consolidation
#
# OPERATOR SCRIPT — not installed or auto-enabled by the castor package.
# This is a reference implementation. Adapt it to your deployment.
#
# Required env vars before running:
#   CASTOR_RRN              — your robot's RRN (e.g. RRN-000000000001)
#   CASTOR_MODEL            — LLM model (default: claude-haiku-4-5-20251001)
# Optional (issue filing — disabled by default):
#   CASTOR_AUTODREAM_FILE_ISSUES=1
#   CASTOR_GITHUB_REPO=owner/repo
#
# Usage: ./scripts/autodream.sh [--dry-run]

set -euo pipefail

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

OPENCASTOR_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MEMORY_FILE="$HOME/.opencastor/robot-memory.md"
LOG_FILE="/tmp/autodream-$(date +%Y%m%d).log"
SESSION_LOG_DIR="$HOME/.opencastor/sessions"
TODAY=$(date +%Y-%m-%d)
YESTERDAY=$(date -d yesterday +%Y-%m-%d 2>/dev/null || date -v-1d +%Y-%m-%d)

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

log "=== autoDream starting ($TODAY) ==="
log "DRY_RUN=$DRY_RUN"

# ── 1. Health diagnostics ─────────────────────────────────────────────────────
log "--- Phase 1: Health diagnostics ---"

HEALTH_REPORT="$HOME/.opencastor/health-$(date +%Y%m%d).json"

# Collect system metrics
CPU_TEMP=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null | awk '{printf "%.1f", $1/1000}' || echo "unknown")
DISK_PCT=$(df -h "$HOME" | awk 'NR==2{print $5}' | tr -d '%')
MEM_FREE=$(free -m | awk '/^Mem:/{print $4}')
UPTIME=$(uptime -p 2>/dev/null || uptime)

# Check OpenCastor gateway
GW_STATUS="unknown"
if curl -sf http://127.0.0.1:8001/health &>/dev/null; then
  GW_STATUS="ok"
else
  GW_STATUS="down"
  log "WARNING: Gateway not responding at :8001"
fi

# Check bridge
BRIDGE_STATUS="unknown"
if pgrep -f "castor bridge" &>/dev/null; then
  BRIDGE_STATUS="ok"
else
  BRIDGE_STATUS="down"
  log "WARNING: Bridge process not found"
fi

cat > "$HEALTH_REPORT" <<EOF
{
  "date": "$TODAY",
  "cpu_temp_c": "$CPU_TEMP",
  "disk_used_pct": $DISK_PCT,
  "mem_free_mb": $MEM_FREE,
  "uptime": "$UPTIME",
  "gateway": "$GW_STATUS",
  "bridge": "$BRIDGE_STATUS"
}
EOF

log "Health: cpu=${CPU_TEMP}°C disk=${DISK_PCT}% mem_free=${MEM_FREE}MB gateway=$GW_STATUS bridge=$BRIDGE_STATUS"

# ── 2. LLM memory consolidation (autoDream brain) ────────────────────────────
log "--- Phase 2: LLM memory consolidation ---"

if [[ "$DRY_RUN" == "false" ]]; then
  DREAM_SUMMARY=$(cd "$OPENCASTOR_DIR" && venv/bin/python -m castor.brain.autodream_runner 2>>"$LOG_FILE" || echo "brain unavailable — shell fallback")
  log "Dream summary: $DREAM_SUMMARY"
else
  log "DRY_RUN: skipping LLM brain"
fi

# ── 3. Context pruning ────────────────────────────────────────────────────────
log "--- Phase 3: Context pruning ---"

# Prune old health reports (keep last 7)
while IFS= read -r f; do
  log "Pruning old health report: $f"
  [[ "$DRY_RUN" == "false" ]] && rm "$f"
done < <(find "$HOME/.opencastor" -name "health-*.json" | sort -r | tail -n +8 || true)

# Prune old autoDream logs (keep last 14)
while IFS= read -r f; do
  log "Pruning old log: $f"
  [[ "$DRY_RUN" == "false" ]] && rm "$f"
done < <(find /tmp -name "autodream-*.log" 2>/dev/null | sort -r | tail -n +15 || true)

log "Context pruning complete"

# ── 4. Autonomous issue detection ─────────────────────────────────────────────
log "--- Phase 4: Issue detection ---"

ISSUES_FOUND=false

# Check for disk pressure
if [[ $DISK_PCT -gt 85 ]]; then
  log "ISSUE: Disk usage ${DISK_PCT}% — over 85% threshold"
  ISSUES_FOUND=true
fi

# Check if bridge is down
if [[ "$BRIDGE_STATUS" == "down" ]]; then
  log "ISSUE: Bridge not running — auto-restart..."
  if [[ "$DRY_RUN" == "false" ]]; then
    cd "$OPENCASTOR_DIR"
    nohup venv/bin/castor bridge \
      --config bob.rcan.yaml \
      --firebase-project opencastor \
      --credentials "$HOME/.config/opencastor/firebase-sa-key.json" \
      --gateway-url http://127.0.0.1:8001 \
      >> /tmp/castor-bridge.log 2>&1 &
    log "Bridge restarted (PID $!)"
  fi
fi

# Check if gateway is down
if [[ "$GW_STATUS" == "down" ]]; then
  log "ISSUE: Gateway not responding — check /tmp/castor-gateway.log"
fi

# ── 5. Worker agent: OAK-D session summary (if sessions exist) ────────────────
log "--- Phase 5: OAK-D worker analysis ---"

OAK_SESSIONS_DIR="$HOME/oak_sessions"
if [[ -d "$OAK_SESSIONS_DIR" ]]; then
  LATEST_SESSION=$(ls -t "$OAK_SESSIONS_DIR" 2>/dev/null | head -1 || true)
  if [[ -n "$LATEST_SESSION" ]]; then
    log "Latest OAK-D session: $LATEST_SESSION"
    # TODO: spawn isolated worker agent once swarm worker pattern is implemented (#821)
    # For now, generate basic stats
    FRAME_COUNT=$(find "$OAK_SESSIONS_DIR/$LATEST_SESSION" -name "*.npy" 2>/dev/null | wc -l || echo 0)
    log "OAK-D frames in latest session: $FRAME_COUNT"
  else
    log "No OAK-D sessions found in $OAK_SESSIONS_DIR"
  fi
else
  log "OAK-D sessions dir not present (Alex offline or not configured)"
fi

# ── 6. Summary ────────────────────────────────────────────────────────────────
log "=== autoDream complete ==="
log "Memory: $MEMORY_FILE"
log "Health report: $HEALTH_REPORT"
log "Issues found: $ISSUES_FOUND"
log "Log: $LOG_FILE"

# Print summary to stdout for cron capture
echo "autoDream $(date +%Y-%m-%d): gateway=$GW_STATUS bridge=$BRIDGE_STATUS disk=${DISK_PCT}% issues=$ISSUES_FOUND"
