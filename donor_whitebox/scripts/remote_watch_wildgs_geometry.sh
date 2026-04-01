#!/usr/bin/env bash
set -euo pipefail

PID="${PID:-}"
SCENE_NAME="${SCENE_NAME:-room3x3_dense600_whitebox}"
OUTPUT_ROOT="${OUTPUT_ROOT:-/root/donor_whitebox/outputs/wildgs_runs}"
EVAL_SCRIPT="${EVAL_SCRIPT:-/root/donor_whitebox/scripts/eval_room3x3_geometry.py}"
GT_TRAJ="${GT_TRAJ:-/root/donor_whitebox/outputs/hislam2_room3x3_seq_dense_600f_12s/traj_gt.txt}"
PYTHON_BIN="${PYTHON_BIN:-/venv/hislam2/bin/python}"

SCENE_DIR="${OUTPUT_ROOT}/${SCENE_NAME}"
PLY_PATH="${SCENE_DIR}/final_gs.ply"
TRAJ_PATH="${SCENE_DIR}/traj/est_poses_full.txt"
OUT_PATH="${SCENE_DIR}/geometry_eval_aligned.txt"

mkdir -p /root/donor_whitebox/logs

if [[ -n "$PID" ]]; then
  while kill -0 "$PID" 2>/dev/null; do
    sleep 20
  done
fi

for _ in $(seq 1 180); do
  if [[ -f "$PLY_PATH" && -f "$TRAJ_PATH" ]]; then
    break
  fi
  sleep 10
done

if [[ ! -f "$PLY_PATH" ]]; then
  echo "missing ply: $PLY_PATH" >&2
  exit 1
fi

if [[ ! -f "$TRAJ_PATH" ]]; then
  echo "missing traj: $TRAJ_PATH" >&2
  exit 1
fi

"$PYTHON_BIN" "$EVAL_SCRIPT" \
  --ply "$PLY_PATH" \
  --traj "$TRAJ_PATH" \
  --gt-traj "$GT_TRAJ" \
  --sample-max 100000 \
  > "$OUT_PATH"

echo "$OUT_PATH"
