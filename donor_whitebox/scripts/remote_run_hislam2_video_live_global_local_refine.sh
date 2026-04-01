#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/root/donor_whitebox}"
MODE="${MODE:-global_room_closure_local_refine}"
OUT_ROOT="${OUT_ROOT:-${ROOT}/outputs/hislam2_capture_video_global_local_refine_20260317_r1}"
FEED_ROOT="${FEED_ROOT:-${ROOT}/outputs/hislam2_capture_video_global_local_refine_feed_20260317_r1}"
TARGET_FRAMES="${TARGET_FRAMES:-8000}"
DURATION="${DURATION:-1800}"
REALTIME_SCALE="${REALTIME_SCALE:-1.0}"
FULL_OUT_DIR="${FULL_OUT_DIR:-${ROOT}/outputs/hislam2_global_local_refine_video_live_full_official_20260317_r1}"
SEQ_DIR="${OUT_ROOT}/seq_${MODE}"
COMPRESS_SLEEP="${COMPRESS_SLEEP:-1}"
COMPRESS_MIN_AGE="${COMPRESS_MIN_AGE:-1}"
AUDIT_PYTHON="${AUDIT_PYTHON:-/venv/hislam2/bin/python}"
AUDIT_POLL="${AUDIT_POLL:-0.5}"
AUDIT_STABLE="${AUDIT_STABLE:-1.0}"
AUDIT_FORCE_KEEP_GAP="${AUDIT_FORCE_KEEP_GAP:-6}"
AUDIT_MIN_MEAN_DIFF="${AUDIT_MIN_MEAN_DIFF:-2.5}"
AUDIT_MIN_LAPLACIAN_VAR="${AUDIT_MIN_LAPLACIAN_VAR:-8.0}"
AUDIT_DEPTH_BACKEND="${AUDIT_DEPTH_BACKEND:-none}"
AUDIT_MIN_DEPTH_P50="${AUDIT_MIN_DEPTH_P50:-0.0}"
AUDIT_MAX_NEAR_RATIO_05M="${AUDIT_MAX_NEAR_RATIO_05M:-1.0}"
AUDIT_MAX_NEAR_RATIO_1M="${AUDIT_MAX_NEAR_RATIO_1M:-1.0}"
AUDIT_MAX_NEAR_RATIO_2M="${AUDIT_MAX_NEAR_RATIO_2M:-1.0}"
AUDIT_UNIK3D_REPO="${AUDIT_UNIK3D_REPO:-/root/gs_refs/UniK3D}"
AUDIT_UNIK3D_BACKBONE="${AUDIT_UNIK3D_BACKBONE:-vitl}"
AUDIT_UNIK3D_DEVICE="${AUDIT_UNIK3D_DEVICE:-cuda}"
AUDIT_UNIK3D_RESOLUTION_LEVEL="${AUDIT_UNIK3D_RESOLUTION_LEVEL:-7}"

echo "[video-live-global-local-refine] prepare mode=${MODE} out_root=${OUT_ROOT}"
ROOT="${ROOT}" \
MODE_SPEC="${MODE}" \
OUT_ROOT="${OUT_ROOT}" \
TARGET_FRAMES="${TARGET_FRAMES}" \
DURATION="${DURATION}" \
REALTIME_SCALE="${REALTIME_SCALE}" \
WRITE_DEPTH_BIN=0 \
COMPRESS_IMAGES=0 \
bash "${ROOT}/scripts/remote_prepare_hislam2_workpoint_variants.sh" &
PREP_PID=$!

while [[ ! -d "${SEQ_DIR}/images" ]]; do
  sleep 1
done

bash "${ROOT}/scripts/remote_stream_compress_ppm.sh" "${SEQ_DIR}/images" "${COMPRESS_SLEEP}" "${COMPRESS_MIN_AGE}" &
COMP_PID=$!

"${AUDIT_PYTHON}" "${ROOT}/scripts/stream_frame_audit.py" \
  --src-root "${SEQ_DIR}" \
  --dst-root "${FEED_ROOT}" \
  --poll-interval "${AUDIT_POLL}" \
  --stable-seconds "${AUDIT_STABLE}" \
  --force-keep-gap "${AUDIT_FORCE_KEEP_GAP}" \
  --min-mean-diff "${AUDIT_MIN_MEAN_DIFF}" \
  --min-laplacian-var "${AUDIT_MIN_LAPLACIAN_VAR}" \
  --depth-audit-backend "${AUDIT_DEPTH_BACKEND}" \
  --min-depth-p50 "${AUDIT_MIN_DEPTH_P50}" \
  --max-near-ratio-05m "${AUDIT_MAX_NEAR_RATIO_05M}" \
  --max-near-ratio-1m "${AUDIT_MAX_NEAR_RATIO_1M}" \
  --max-near-ratio-2m "${AUDIT_MAX_NEAR_RATIO_2M}" \
  --unik3d-repo "${AUDIT_UNIK3D_REPO}" \
  --unik3d-backbone "${AUDIT_UNIK3D_BACKBONE}" \
  --unik3d-device "${AUDIT_UNIK3D_DEVICE}" \
  --unik3d-resolution-level "${AUDIT_UNIK3D_RESOLUTION_LEVEL}" &
AUDIT_PID=$!

ROOT="${ROOT}" \
HI_SLAM2_SEQ_DIR="${FEED_ROOT}" \
HI_SLAM2_OUT_DIR="${FULL_OUT_DIR}" \
bash "${ROOT}/scripts/remote_run_hislam2_live_owndata_runtime.sh" &
LIVE_PID=$!

wait "${PREP_PID}"
wait "${COMP_PID}"
wait "${AUDIT_PID}"
wait "${LIVE_PID}"

echo "[video-live-global-local-refine] done"
