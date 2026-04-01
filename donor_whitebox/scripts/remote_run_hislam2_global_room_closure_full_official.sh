#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/root/donor_whitebox}"
MODE="${MODE:-global_room_closure}"
OUT_ROOT="${OUT_ROOT:-${ROOT}/outputs/hislam2_capture_protocol_global_20260316}"
TARGET_FRAMES="${TARGET_FRAMES:-4000}"
DURATION="${DURATION:-60}"
WRITE_DEPTH_BIN="${WRITE_DEPTH_BIN:-0}"
COMPRESS_IMAGES="${COMPRESS_IMAGES:-1}"
FULL_OUT_DIR="${FULL_OUT_DIR:-${ROOT}/outputs/hislam2_${MODE}_full_official_20260316_r1}"

echo "[global-room-closure] prepare mode=${MODE} out_root=${OUT_ROOT}"
ROOT="${ROOT}" \
MODE_SPEC="${MODE}" \
OUT_ROOT="${OUT_ROOT}" \
TARGET_FRAMES="${TARGET_FRAMES}" \
DURATION="${DURATION}" \
WRITE_DEPTH_BIN="${WRITE_DEPTH_BIN}" \
COMPRESS_IMAGES="${COMPRESS_IMAGES}" \
bash "${ROOT}/scripts/remote_prepare_hislam2_workpoint_variants.sh"

echo "[global-room-closure] run full_official variant=${MODE}"
ROOT="${ROOT}" \
VARIANT_ROOT="${OUT_ROOT}" \
VARIANT_NAME="${MODE}" \
HI_SLAM2_OUT_DIR="${FULL_OUT_DIR}" \
bash "${ROOT}/scripts/remote_run_hislam2_workpoint_variant_full_official.sh"
