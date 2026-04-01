#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-/venv/hislam2/bin/python}"
SEQ_ROOT="${SEQ_ROOT:-/root/donor_whitebox/outputs/wildgs_room3x3_dense600_exactpriors_seq}"
INPUT_ROOT="${INPUT_ROOT:-/root/donor_whitebox/outputs/wildgs_room3x3_dense600_input_exactpriors}"
CFG_PATH="${CFG_PATH:-/root/donor_whitebox/configs/wildgs_room3x3_dense600_clean_exactpriors.yaml}"
OUTPUT_ROOT="${OUTPUT_ROOT:-/root/donor_whitebox/outputs/wildgs_runs}"
SCENE_NAME="${SCENE_NAME:-room3x3_dense600_clean_exactpriors_r1}"
DIRTY_PRIORS_ROOT="${DIRTY_PRIORS_ROOT:-/root/donor_whitebox/outputs/wildgs_runs/room3x3_dense600_whitebox/mono_priors/depths}"
LOG_PATH="${LOG_PATH:-/root/donor_whitebox/logs/${SCENE_NAME}_chain.log}"
WILDGS_REPO_ROOT="${WILDGS_REPO_ROOT:-/root/gs_refs/WildGS-SLAM.clean}"
export PYTHON_BIN SEQ_ROOT INPUT_ROOT CFG_PATH OUTPUT_ROOT SCENE_NAME DIRTY_PRIORS_ROOT LOG_PATH
export PYTHONUNBUFFERED=1
export WILDGS_REPO_ROOT

{
  echo "[chain] start $(date -Iseconds)"
  echo "[chain] scene ${SCENE_NAME}"
  mkdir -p "$(dirname "$LOG_PATH")"
  mkdir -p /root/donor_whitebox/bin
  if command -v g++ >/dev/null 2>&1; then
    CXX=g++
  elif command -v c++ >/dev/null 2>&1; then
    CXX=c++
  else
    CXX=clang++
  fi
  "$CXX" -std=c++20 -O3 /root/donor_whitebox/scripts/export_room_sequence.cpp -o /root/donor_whitebox/bin/export_room_sequence

  /root/donor_whitebox/bin/export_room_sequence \
    --output "${SEQ_ROOT}" \
    --width 960 --height 720 \
    --frames 600 --duration 12 \
    --seed 7 \
    --fx 831.384399 --fy 831.384399 --cx 480 --cy 360 \
    --write-depth-bin
  echo "[chain] export_done"

  mkdir -p "${INPUT_ROOT}/rgb"
  "${PYTHON_BIN}" - <<'PY'
from pathlib import Path
from PIL import Image
import os
src = Path(os.environ["SEQ_ROOT"]) / "images"
dst = Path(os.environ["INPUT_ROOT"]) / "rgb"
dst.mkdir(parents=True, exist_ok=True)
for i, p in enumerate(sorted(src.glob("*.ppm"))):
    out = dst / f"frame_{i:05d}.png"
    Image.open(p).save(out)
print("converted_rgb", len(list(dst.glob("frame_*.png"))))
PY
  echo "[chain] rgb_done"

  mkdir -p "${OUTPUT_ROOT}/${SCENE_NAME}/mono_priors/depths"
  mkdir -p "${OUTPUT_ROOT}/${SCENE_NAME}/mono_priors/features"
  "${PYTHON_BIN}" - <<'PY'
from pathlib import Path
import numpy as np
from PIL import Image
import os

seq_depth = Path(os.environ["SEQ_ROOT"]) / "depth_raw"
dirty = Path(os.environ["DIRTY_PRIORS_ROOT"])
out = Path(os.environ["OUTPUT_ROOT"]) / os.environ["SCENE_NAME"] / "mono_priors" / "depths"
out.mkdir(parents=True, exist_ok=True)
seq_depth_files = sorted(seq_depth.glob("*.bin"))
count = 0
for prior in sorted(dirty.glob("*.npy")):
    idx = int(prior.stem)
    if idx < 0 or idx >= len(seq_depth_files):
        continue
    src = seq_depth_files[idx]
    arr = np.fromfile(src, dtype=np.float32)
    if arr.size != 960 * 720:
        raise RuntimeError(f"unexpected depth size in {src}: {arr.size}")
    arr = arr.reshape(720, 960)
    arr[~np.isfinite(arr)] = 0.0
    arr[arr < 0.0] = 0.0
    arr = np.array(
        Image.fromarray(arr.astype(np.float32), mode="F").resize((480, 360), resample=Image.Resampling.NEAREST),
        dtype=np.float32,
    )
    arr[~np.isfinite(arr)] = 0.0
    np.save(out / f"{idx:05d}.npy", arr)
    count += 1
print("converted_depth_priors", count)
PY
  echo "[chain] priors_done"

  "${PYTHON_BIN}" /root/donor_whitebox/scripts/preflight_wildgs_inputs.py --cfg "${CFG_PATH}"
  echo "[chain] preflight_done"

  cd "${WILDGS_REPO_ROOT}"
  export OPENBLAS_NUM_THREADS=1
  export OMP_NUM_THREADS=1
  export MKL_NUM_THREADS=1
  export CUDA_VISIBLE_DEVICES=0
  TORCH_LIB_DIR="$("${PYTHON_BIN}" - <<'PY'
import pathlib, torch
print(pathlib.Path(torch.__file__).resolve().parent / "lib")
PY
)"
  WILDGS_BUILD_DIR="$(find "${WILDGS_REPO_ROOT}/build" -maxdepth 1 -type d -name 'lib.linux-*' | head -n 1)"
  LIETORCH_BUILD_DIR="$(find "${WILDGS_REPO_ROOT}/thirdparty/lietorch/build" -maxdepth 1 -type d -name 'lib.linux-*' | head -n 1)"
  RASTER_BUILD_DIR="$(find "${WILDGS_REPO_ROOT}/thirdparty/diff-gaussian-rasterization-w-pose/build" -maxdepth 1 -type d -name 'lib.linux-*' | head -n 1)"
  LD_LIBRARY_PATH="${TORCH_LIB_DIR}:${LD_LIBRARY_PATH:-}" \
  PYTHONPATH="${WILDGS_REPO_ROOT}${WILDGS_BUILD_DIR:+:$WILDGS_BUILD_DIR}${LIETORCH_BUILD_DIR:+:$LIETORCH_BUILD_DIR}:${WILDGS_REPO_ROOT}/thirdparty/lietorch:${WILDGS_REPO_ROOT}/thirdparty/diff-gaussian-rasterization-w-pose${RASTER_BUILD_DIR:+:$RASTER_BUILD_DIR}:${WILDGS_REPO_ROOT}/thirdparty/simple-knn:${PYTHONPATH:-}" \
  "${PYTHON_BIN}" /root/donor_whitebox/scripts/preflight_wildgs_runtime.py
  echo "[chain] runtime_preflight_done"
  LD_LIBRARY_PATH="${TORCH_LIB_DIR}:${LD_LIBRARY_PATH:-}" \
  PYTHONPATH="${WILDGS_REPO_ROOT}${WILDGS_BUILD_DIR:+:$WILDGS_BUILD_DIR}${LIETORCH_BUILD_DIR:+:$LIETORCH_BUILD_DIR}:${WILDGS_REPO_ROOT}/thirdparty/lietorch:${WILDGS_REPO_ROOT}/thirdparty/diff-gaussian-rasterization-w-pose${RASTER_BUILD_DIR:+:$RASTER_BUILD_DIR}:${WILDGS_REPO_ROOT}/thirdparty/simple-knn:${PYTHONPATH:-}" \
  "${PYTHON_BIN}" run.py "${CFG_PATH}"
  echo "[chain] done $(date -Iseconds)"
} 2>&1 | tee "${LOG_PATH}"
