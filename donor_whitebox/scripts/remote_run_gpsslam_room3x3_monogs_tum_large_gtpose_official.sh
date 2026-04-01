#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-/root/gs_refs/GPS-SLAM.clean}"
RAW_SEQ="${RAW_SEQ:-/root/donor_whitebox/outputs/gpsslam_room3x3_seq_monogs_tum_large_r1}"
SEQ_ROOT="${SEQ_ROOT:-/root/donor_whitebox/outputs/gpsslam_room3x3_tumrgbd_monogs_tum_large_r1}"
DATA_ROOT="${DATA_ROOT:-${REPO}/data/gps_slam/room3x3_monogs_tum_large_gtpose_r1}"
CONFIG="${CONFIG:-/root/donor_whitebox/configs/gpsslam_room3x3_monogs_tum_large_gtpose_official_r2.yaml}"
LOG_PATH="${LOG_PATH:-/root/donor_whitebox/logs/gpsslam_room3x3_monogs_tum_large_gtpose_official_r2.log}"
TORCH_ROOT="${TORCH_ROOT:-/venv/hislam2/lib/python3.10/site-packages/torch}"

mkdir -p "$(dirname "$LOG_PATH")"
export LD_LIBRARY_PATH="${TORCH_ROOT}/lib:${LD_LIBRARY_PATH:-}"

/root/donor_whitebox/bin/export_room_sequence \
  --output "$RAW_SEQ" \
  --width 640 \
  --height 480 \
  --frames 600 \
  --duration 37 \
  --protocol monogs_tum \
  --fx 535.4 \
  --fy 539.2 \
  --cx 320.1 \
  --cy 247.6 \
  --write-depth-bin

python3 /root/donor_whitebox/scripts/make_wildgs_room3x3_tumrgbd_exact.py \
  --sequence-root "$RAW_SEQ" \
  --output-dir "$SEQ_ROOT" \
  --width 640 \
  --height 480 \
  --depth-scale 5000.0 \
  --overwrite

python3 /root/donor_whitebox/scripts/make_gpsslam_rgbd_dataset.py \
  --input-root "$SEQ_ROOT" \
  --output-dir "$DATA_ROOT" \
  --width 640 \
  --height 480 \
  --fx 535.4 \
  --fy 539.2 \
  --cx 320.1 \
  --cy 247.6 \
  --overwrite

cd "$REPO"
./build/slam_trainer "$CONFIG" 2>&1 | tee "$LOG_PATH"
