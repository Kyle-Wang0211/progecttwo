#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/root/donor_whitebox}"
UNI_K3D_VENV_DIR="${UNI_K3D_VENV_DIR:-/venv/unik3d}"

if [[ ! -x "${UNI_K3D_VENV_DIR}/bin/python" ]]; then
  echo "[video-live-unik3d] missing UniK3D runtime at ${UNI_K3D_VENV_DIR}" >&2
  exit 1
fi

export AUDIT_PYTHON="${AUDIT_PYTHON:-${UNI_K3D_VENV_DIR}/bin/python}"
export AUDIT_DEPTH_BACKEND="${AUDIT_DEPTH_BACKEND:-unik3d}"
export AUDIT_UNIK3D_REPO="${AUDIT_UNIK3D_REPO:-/root/gs_refs/UniK3D}"
export AUDIT_UNIK3D_BACKBONE="${AUDIT_UNIK3D_BACKBONE:-vitl}"
export AUDIT_UNIK3D_DEVICE="${AUDIT_UNIK3D_DEVICE:-cuda}"
export AUDIT_UNIK3D_RESOLUTION_LEVEL="${AUDIT_UNIK3D_RESOLUTION_LEVEL:-7}"
export AUDIT_MIN_DEPTH_P50="${AUDIT_MIN_DEPTH_P50:-1.5}"
export AUDIT_MAX_NEAR_RATIO_05M="${AUDIT_MAX_NEAR_RATIO_05M:-0.25}"
export AUDIT_MAX_NEAR_RATIO_1M="${AUDIT_MAX_NEAR_RATIO_1M:-0.70}"
export AUDIT_MAX_NEAR_RATIO_2M="${AUDIT_MAX_NEAR_RATIO_2M:-0.98}"
export AUDIT_FORCE_KEEP_GAP="${AUDIT_FORCE_KEEP_GAP:-8}"
export AUDIT_MIN_MEAN_DIFF="${AUDIT_MIN_MEAN_DIFF:-2.5}"
export AUDIT_MIN_LAPLACIAN_VAR="${AUDIT_MIN_LAPLACIAN_VAR:-8.0}"

exec bash "${ROOT}/scripts/remote_run_hislam2_video_live_global_local_refine.sh"
