#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/donor_whitebox"
REPO="${HI_SLAM2_REPO:-/root/gs_refs/HI-SLAM2}"
VARIANT_ROOT="${VARIANT_ROOT:-${ROOT}/outputs/hislam2_workpoint_variants_20260316}"
VARIANT_NAME="${VARIANT_NAME:?set VARIANT_NAME to farther_cam|denser_sampling|farther_plus_denser}"
SEQ_DIR="${HI_SLAM2_SEQ_DIR:-${VARIANT_ROOT}/seq_${VARIANT_NAME}}"
CONFIG_PATH="${HI_SLAM2_CONFIG:-${ROOT}/configs/hislam2_owndata_replica_full_official_r1.yaml}"
OUT_DIR="${HI_SLAM2_OUT_DIR:-${ROOT}/outputs/hislam2_workpoint_${VARIANT_NAME}_full_official_20260316_r1}"
PYTHON_BIN="${HI_SLAM2_PYTHON:-/venv/hislam2/bin/python}"
CALIB_PATH="${HI_SLAM2_CALIB:-${SEQ_DIR}/calib.txt}"
BUFFER_SIZE="${HI_SLAM2_BUFFER:-1000}"
TSDF_VOXEL_SIZE="${HI_SLAM2_TSDF_VOXEL_SIZE:-0.01}"
TSDF_WEIGHT="${HI_SLAM2_TSDF_WEIGHT:-2}"

export HI_SLAM2_REPO="${REPO}"
export HI_SLAM2_PYTHON="${PYTHON_BIN}"
export HI_SLAM2_SEQ_DIR="${SEQ_DIR}"
export HI_SLAM2_CALIB="${CALIB_PATH}"
export HI_SLAM2_CONFIG="${CONFIG_PATH}"
export HI_SLAM2_OUT_DIR="${OUT_DIR}"
export HI_SLAM2_BUFFER="${BUFFER_SIZE}"
export HI_SLAM2_TSDF_VOXEL_SIZE="${TSDF_VOXEL_SIZE}"
export HI_SLAM2_TSDF_WEIGHT="${TSDF_WEIGHT}"

exec "${ROOT}/scripts/remote_run_hislam2_room3x3_official_owndata_runtime.sh"
