#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/root/donor_whitebox}"
MODE="${MODE:-global_room_closure_local_refine}"
OUT_ROOT="${OUT_ROOT:-${ROOT}/outputs/hislam2_capture_protocol_global_local_refine_20260317_r1}"
TARGET_FRAMES="${TARGET_FRAMES:-8000}"
DURATION="${DURATION:-1800}"
WRITE_DEPTH_BIN="${WRITE_DEPTH_BIN:-0}"
COMPRESS_IMAGES="${COMPRESS_IMAGES:-0}"
FULL_OUT_DIR="${FULL_OUT_DIR:-${ROOT}/outputs/hislam2_${MODE}_full_official_20260317_r1}"
SEQ_DIR="${OUT_ROOT}/seq_${MODE}"
COMPRESS_SLEEP="${COMPRESS_SLEEP:-10}"

echo "[global-local-refine] prepare mode=${MODE} out_root=${OUT_ROOT}"
ROOT="${ROOT}" \
MODE_SPEC="${MODE}" \
OUT_ROOT="${OUT_ROOT}" \
TARGET_FRAMES="${TARGET_FRAMES}" \
DURATION="${DURATION}" \
WRITE_DEPTH_BIN="${WRITE_DEPTH_BIN}" \
COMPRESS_IMAGES="${COMPRESS_IMAGES}" \
bash "${ROOT}/scripts/remote_prepare_hislam2_workpoint_variants.sh" &
PREP_PID=$!

COMP_PID=""
for _ in $(seq 1 240); do
  if [[ -d "${SEQ_DIR}/images" ]]; then
    bash "${ROOT}/scripts/remote_stream_compress_ppm.sh" "${SEQ_DIR}/images" "${COMPRESS_SLEEP}" &
    COMP_PID=$!
    break
  fi
  sleep 5
done

wait "${PREP_PID}"
if [[ -n "${COMP_PID}" ]]; then
  wait "${COMP_PID}"
fi

echo "[global-local-refine] run full_official variant=${MODE}"
ROOT="${ROOT}" \
VARIANT_ROOT="${OUT_ROOT}" \
VARIANT_NAME="${MODE}" \
HI_SLAM2_OUT_DIR="${FULL_OUT_DIR}" \
bash "${ROOT}/scripts/remote_run_hislam2_workpoint_variant_full_official.sh"
