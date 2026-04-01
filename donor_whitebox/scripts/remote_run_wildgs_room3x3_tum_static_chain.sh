#!/bin/bash
set -euo pipefail

SCENE="${SCENE:-room3x3_tumrgbd_wildgs_scan_r16}"
SEQ="${SEQ:-/root/donor_whitebox/outputs/hislam2_${SCENE}_seq}"
OUT="${OUT:-/root/donor_whitebox/outputs/wildgs_${SCENE}}"
CONFIG_PATH="${CONFIG_PATH:-/root/donor_whitebox/configs/wildgs_room3x3_tumrgbd_wildgs_scan_r16.yaml}"
RUN_OUTPUT="${RUN_OUTPUT:-/root/donor_whitebox/outputs/wildgs_runs/${SCENE}}"
PYTHON_BIN="${PYTHON_BIN:-/venv/hislam2/bin/python}"
WILDGS_REPO_ROOT="${WILDGS_REPO_ROOT:-/root/gs_refs/WildGS-SLAM.clean}"
STATIC_TAIL_SECONDS="${STATIC_TAIL_SECONDS:-2.0}"
STATIC_TAIL_JITTER_MM="${STATIC_TAIL_JITTER_MM:-0.5}"
RGB_SAMPLE_DT="${RGB_SAMPLE_DT:-0.0333333333}"
DEPTH_SAMPLE_DT="${DEPTH_SAMPLE_DT:-0.0333333333}"
DEPTH_SCALE="${DEPTH_SCALE:-5000.0}"
DEPTH_TRUNC_M="${DEPTH_TRUNC_M:-5.0}"
CAPTURE_PROTOCOL="${CAPTURE_PROTOCOL:-wildgs_tum_static}"
RGB_TIMESTAMPS_FILE="${RGB_TIMESTAMPS_FILE:-}"
DEPTH_TIMESTAMPS_FILE="${DEPTH_TIMESTAMPS_FILE:-}"
DEPTH_REFERENCE_ROOT="${DEPTH_REFERENCE_ROOT:-}"
REFERENCE_DEPTH_SCALE="${REFERENCE_DEPTH_SCALE:-5000.0}"
APPLY_REFERENCE_DEPTH_PROFILE="${APPLY_REFERENCE_DEPTH_PROFILE:-0}"

cd /root/donor_whitebox
echo "[chain] start $(date -Iseconds)"
echo "[chain] scene ${SCENE}"
echo "[chain] protocol ${CAPTURE_PROTOCOL} rgb_dt=${RGB_SAMPLE_DT} depth_dt=${DEPTH_SAMPLE_DT} depth_scale=${DEPTH_SCALE} depth_trunc=${DEPTH_TRUNC_M} static_tail=${STATIC_TAIL_SECONDS}s jitter_mm=${STATIC_TAIL_JITTER_MM}"
echo "[chain] timestamp_profiles rgb=${RGB_TIMESTAMPS_FILE:-none} depth=${DEPTH_TIMESTAMPS_FILE:-none}"
echo "[chain] depth_profile apply=${APPLY_REFERENCE_DEPTH_PROFILE} reference_root=${DEPTH_REFERENCE_ROOT:-none} reference_scale=${REFERENCE_DEPTH_SCALE}"

count_files() {
  local dir="$1"
  local pattern="$2"
  if [[ ! -d "$dir" ]]; then
    echo 0
    return 0
  fi
  find "$dir" -maxdepth 1 -name "$pattern" 2>/dev/null | wc -l
}

manifest_matches() {
  local manifest="$1"
  [[ -f "$manifest" ]] || return 1
  python3 - "$manifest" "$CAPTURE_PROTOCOL" "$STATIC_TAIL_SECONDS" "$STATIC_TAIL_JITTER_MM" <<'PY'
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
expected_protocol = sys.argv[2]
expected_tail = float(sys.argv[3])
expected_jitter = float(sys.argv[4])
values = {}
for line in manifest_path.read_text(encoding="utf-8").splitlines():
    if "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip()
if values.get("protocol") != expected_protocol:
    raise SystemExit(1)
if abs(float(values.get("static_tail_sec", "nan")) - expected_tail) > 1e-6:
    raise SystemExit(1)
if abs(float(values.get("static_tail_jitter_mm", "nan")) - expected_jitter) > 1e-6:
    raise SystemExit(1)
PY
}

workpoint_matches() {
  local workpoint_json="$1"
  [[ -f "$workpoint_json" ]] || return 1
  python3 - "$workpoint_json" "$RGB_SAMPLE_DT" "$DEPTH_SAMPLE_DT" "$DEPTH_SCALE" "$RGB_TIMESTAMPS_FILE" "$DEPTH_TIMESTAMPS_FILE" "$APPLY_REFERENCE_DEPTH_PROFILE" "$DEPTH_REFERENCE_ROOT" "$REFERENCE_DEPTH_SCALE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
expected_rgb_dt = float(sys.argv[2])
expected_depth_dt = float(sys.argv[3])
expected_depth_scale = float(sys.argv[4])
expected_rgb_profile = Path(sys.argv[5]).name if sys.argv[5] else None
expected_depth_profile = Path(sys.argv[6]).name if sys.argv[6] else None
expected_apply_reference_profile = bool(int(sys.argv[7]))
expected_reference_root = sys.argv[8] or None
expected_reference_scale = float(sys.argv[9])
data = json.loads(path.read_text(encoding="utf-8"))
cadence = data.get("cadence", {})
depth = data.get("depth", {})
profiles = data.get("timestamp_profiles", {})
if abs(float(cadence.get("rgb_requested_dt_s", float("nan"))) - expected_rgb_dt) > 1e-6:
    raise SystemExit(1)
if abs(float(cadence.get("depth_requested_dt_s", float("nan"))) - expected_depth_dt) > 1e-6:
    raise SystemExit(1)
if abs(float(depth.get("scale", float("nan"))) - expected_depth_scale) > 1e-6:
    raise SystemExit(1)
if profiles.get("rgb") != expected_rgb_profile:
    raise SystemExit(1)
if profiles.get("depth") != expected_depth_profile:
    raise SystemExit(1)
profile_mode = depth.get("profile_mode")
if expected_apply_reference_profile:
    if profile_mode != "reference_mask_quantiles":
        raise SystemExit(1)
    if depth.get("reference_root") != expected_reference_root:
        raise SystemExit(1)
    if abs(float(depth.get("reference_depth_scale", float("nan"))) - expected_reference_scale) > 1e-6:
        raise SystemExit(1)
else:
    if profile_mode not in (None, "native"):
        raise SystemExit(1)
PY
}

SEQ_RGB_COUNT="$(count_files "$SEQ/images" '*.ppm')"
SEQ_DEPTH_COUNT="$(count_files "$SEQ/depth_raw" '*.bin')"
if [[ "$SEQ_RGB_COUNT" -eq 2364 && "$SEQ_DEPTH_COUNT" -eq 2364 ]] && \
   manifest_matches "$SEQ/manifest.txt"; then
  echo "[chain] reuse_export ${SEQ}"
else
  build_args=(
    --output "$SEQ"
    --width 640
    --height 480
    --frames 2364
    --duration 23.63
    --seed 7
    --fx 535.4
    --fy 539.2
    --cx 320.1
    --cy 247.6
    --protocol "${CAPTURE_PROTOCOL}"
    --write-depth-bin
  )
  if [[ "${STATIC_TAIL_SECONDS}" != "0" && "${STATIC_TAIL_SECONDS}" != "0.0" ]]; then
    build_args+=(--static-tail-seconds "${STATIC_TAIL_SECONDS}" --static-tail-jitter-mm "${STATIC_TAIL_JITTER_MM}")
  fi
  bash scripts/build_room_sequence.sh \
    "${build_args[@]}"
fi

TUM_RGB_COUNT="$(count_files "$OUT/rgb" '*.png')"
TUM_DEPTH_COUNT="$(count_files "$OUT/depth" '*.png')"
if [[ "$TUM_RGB_COUNT" -gt 0 && "$TUM_DEPTH_COUNT" -gt 0 && -f "$OUT/rgb.txt" && -f "$OUT/depth.txt" && -f "$OUT/groundtruth.txt" ]] && \
   workpoint_matches "$OUT/capture_workpoint.json"; then
  echo "[chain] reuse_tumrgbd ${OUT}"
else
  tum_args=(
    --sequence-root "$SEQ"
    --output-dir "$OUT"
    --width 640
    --height 480
    --depth-scale "$DEPTH_SCALE"
    --rgb-sample-dt "$RGB_SAMPLE_DT"
    --depth-sample-dt "$DEPTH_SAMPLE_DT"
    --overwrite
  )
  if [[ -n "${RGB_TIMESTAMPS_FILE}" ]]; then
    tum_args+=(--rgb-timestamps-file "$RGB_TIMESTAMPS_FILE")
  fi
  if [[ -n "${DEPTH_TIMESTAMPS_FILE}" ]]; then
    tum_args+=(--depth-timestamps-file "$DEPTH_TIMESTAMPS_FILE")
  fi
  if [[ "${APPLY_REFERENCE_DEPTH_PROFILE}" == "1" ]]; then
    tum_args+=(
      --depth-reference-root "$DEPTH_REFERENCE_ROOT"
      --reference-depth-scale "$REFERENCE_DEPTH_SCALE"
      --apply-reference-depth-profile
    )
  fi
  python3 /root/donor_whitebox/scripts/make_wildgs_room3x3_tumrgbd_exact.py \
    "${tum_args[@]}"
fi

export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export CUDA_VISIBLE_DEVICES=0
TORCH_LIB_DIR="$("$PYTHON_BIN" - <<'PY'
import pathlib, torch
print(pathlib.Path(torch.__file__).resolve().parent / "lib")
PY
)"
WILDGS_BUILD_DIR="$(find "${WILDGS_REPO_ROOT}/build" -maxdepth 1 -type d -name 'lib.linux-*' | head -n 1)"
LIETORCH_BUILD_DIR="$(find "${WILDGS_REPO_ROOT}/thirdparty/lietorch/build" -maxdepth 1 -type d -name 'lib.linux-*' | head -n 1)"
RASTER_BUILD_DIR="$(find "${WILDGS_REPO_ROOT}/thirdparty/diff-gaussian-rasterization-w-pose/build" -maxdepth 1 -type d -name 'lib.linux-*' | head -n 1)"
export LD_LIBRARY_PATH="${TORCH_LIB_DIR}:${LD_LIBRARY_PATH:-}"
export PYTHONPATH="${WILDGS_REPO_ROOT}${WILDGS_BUILD_DIR:+:$WILDGS_BUILD_DIR}${LIETORCH_BUILD_DIR:+:$LIETORCH_BUILD_DIR}:${WILDGS_REPO_ROOT}/thirdparty/lietorch:${WILDGS_REPO_ROOT}/thirdparty/diff-gaussian-rasterization-w-pose${RASTER_BUILD_DIR:+:$RASTER_BUILD_DIR}:${WILDGS_REPO_ROOT}/thirdparty/simple-knn:${PYTHONPATH:-}"

"$PYTHON_BIN" /root/donor_whitebox/scripts/preflight_wildgs_inputs.py --cfg "$CONFIG_PATH" --depth-scale "$DEPTH_SCALE" --depth-trunc "$DEPTH_TRUNC_M"
"$PYTHON_BIN" /root/donor_whitebox/scripts/preflight_wildgs_runtime.py --repo "$WILDGS_REPO_ROOT"

cd "$WILDGS_REPO_ROOT"
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
"$PYTHON_BIN" run.py "$CONFIG_PATH"

if [[ -f "${RUN_OUTPUT}/${SCENE}/final_gs.ply" && -f "${RUN_OUTPUT}/${SCENE}/traj/est_poses_full.txt" ]]; then
  "$PYTHON_BIN" /root/donor_whitebox/scripts/eval_room3x3_geometry.py \
    --ply "${RUN_OUTPUT}/${SCENE}/final_gs.ply" \
    --traj "${RUN_OUTPUT}/${SCENE}/traj/est_poses_full.txt" \
    --gt-traj "${SEQ}/traj_gt.txt" \
    > "${RUN_OUTPUT}/${SCENE}/geometry_eval_aligned.txt"
fi

echo "[chain] done $(date -Iseconds)"
