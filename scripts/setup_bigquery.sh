#!/usr/bin/env bash
# setup_bigquery.sh — Grant BigQuery access to firebase-adminsdk SA and create dataset.
# Run this once from GCP Console Cloud Shell or any machine with gcloud + project owner rights.
#
# USAGE:
#   gcloud auth login
#   gcloud config set project opencastor
#   bash scripts/setup_bigquery.sh

set -euo pipefail
PROJECT="opencastor"
SA="firebase-adminsdk-fbsvc@opencastor.iam.gserviceaccount.com"

echo "==> Granting BigQuery dataEditor to firebase-adminsdk SA..."
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$SA" \
  --role="roles/bigquery.dataEditor" \
  --condition=None

echo "==> Granting Storage Object Admin to firebase-adminsdk SA..."
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$SA" \
  --role="roles/storage.objectAdmin" \
  --condition=None

echo "==> Creating BigQuery dataset opencastor_telemetry..."
bq --project_id="$PROJECT" mk \
  --dataset \
  --location=US \
  --description="OpenCastor robot telemetry time-series" \
  "$PROJECT:opencastor_telemetry" || echo "  (already exists)"

echo "==> Creating robot_telemetry table (partitioned by day, 90d expiry)..."
bq --project_id="$PROJECT" mk \
  --table \
  --time_partitioning_type=DAY \
  --time_partitioning_field=ts \
  --time_partitioning_expiration=7776000 \
  --clustering_fields=rrn \
  --description="Per-robot telemetry samples (bridge push every 30s)" \
  "$PROJECT:opencastor_telemetry.robot_telemetry" \
  rrn:STRING,ts:TIMESTAMP,online:BOOL,cpu_temp_c:FLOAT64,ram_used_pct:FLOAT64,disk_used_pct:FLOAT64,ram_total_gb:FLOAT64,disk_free_gb:FLOAT64,tokens_per_sec:FLOAT64,active_model:STRING,provider:STRING,model_size_gb:FLOAT64,kv_compression:STRING,llmfit_status:STRING,llmfit_headroom_gb:FLOAT64,opencastor_version:STRING,rcan_version:STRING,loa_enforcement:BOOL,local_ip:STRING,raw_json:JSON \
  || echo "  (already exists)"

echo ""
echo "✅ Done. The bridge will now stream telemetry to BigQuery automatically."
echo "   Query example:"
echo "   SELECT DATE(ts), rrn, AVG(cpu_temp_c) AS avg_temp, AVG(ram_used_pct) AS avg_ram"
echo "   FROM \`opencastor.opencastor_telemetry.robot_telemetry\`"
echo "   WHERE ts > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)"
echo "   GROUP BY 1, 2 ORDER BY 1 DESC"
