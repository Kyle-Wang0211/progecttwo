#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-/root/gs_refs/GPS-SLAM.clean}"
SEQ_ROOT="${SEQ_ROOT:-/root/donor_whitebox/outputs/wildgs_room3x3_tumrgbd_wildgs_static_20260314_p2d}"
DATA_ROOT="${DATA_ROOT:-${REPO}/data/gps_slam/room3x3_p2d_gtpose_r1}"
CONFIG="${CONFIG:-/root/donor_whitebox/configs/gpsslam_room3x3_p2d_gtpose_official_r1.yaml}"
LOG_PATH="${LOG_PATH:-/root/donor_whitebox/logs/gpsslam_room3x3_p2d_gtpose_official_r1.log}"
TORCH_ROOT="${TORCH_ROOT:-/venv/hislam2/lib/python3.10/site-packages/torch}"

mkdir -p "$(dirname "$LOG_PATH")"
export LD_LIBRARY_PATH="${TORCH_ROOT}/lib:${LD_LIBRARY_PATH:-}"

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
