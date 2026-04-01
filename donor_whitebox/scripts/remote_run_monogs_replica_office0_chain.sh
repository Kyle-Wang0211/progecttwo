#!/usr/bin/env bash
set -euo pipefail

SEQ="${SEQ:-/root/donor_whitebox/outputs/hislam2_room3x3_seq_monogs_replica_20260310}"
REP="${REP:-/root/donor_whitebox/outputs/monogs_room3x3_replica_office0_20260310}"
LOG_DIR="${LOG_DIR:-/root/donor_whitebox/logs}"

mkdir -p "$LOG_DIR"

/root/donor_whitebox/bin/export_room_sequence \
  --output "$SEQ" \
  --width 1200 \
  --height 680 \
  --frames 600 \
  --duration 12 \
  --protocol monogs_tum \
  --fx 600 \
  --fy 600 \
  --cx 599.5 \
  --cy 339.5

/venv/hislam2/bin/python /root/donor_whitebox/scripts/make_monogs_room3x3_replica.py \
  --hislam2-root /root/gs_refs/HI-SLAM2 \
  --sequence-root "$SEQ" \
  --output-dir "$REP" \
  --overwrite

CONFIG_PATH=/root/donor_whitebox/configs/monogs_room3x3_replica_office0_sp.yaml \
RMSE_STOP_THRESHOLD_M="${RMSE_STOP_THRESHOLD_M:-0.03}" \
bash /root/donor_whitebox/scripts/remote_run_monogs_official_sp_guarded.sh
