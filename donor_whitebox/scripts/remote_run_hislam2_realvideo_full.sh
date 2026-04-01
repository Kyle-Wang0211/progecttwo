#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/root/donor_whitebox}"
REPO="${HI_SLAM2_REPO:-/root/gs_refs/HI-SLAM2}"
VIDEO_PATH="${REAL_VIDEO_PATH:?set REAL_VIDEO_PATH}"
RUN_NAME="${RUN_NAME:-realvideo_$(date +%Y%m%d_%H%M%S)}"
PREP_ROOT="${PREP_ROOT:-${ROOT}/outputs/hislam2_realvideo_${RUN_NAME}_prep}"
FEED_ROOT="${FEED_ROOT:-${ROOT}/outputs/hislam2_realvideo_${RUN_NAME}_feed}"
OUT_DIR="${OUT_DIR:-${ROOT}/outputs/hislam2_realvideo_${RUN_NAME}_out}"
PREP_PYTHON="${PREP_PYTHON:-/venv/hislam2/bin/python}"
AUDIT_PYTHON="${AUDIT_PYTHON:-/venv/unik3d/bin/python}"
REALVIDEO_COLMAP_BINARY="${REALVIDEO_COLMAP_BINARY:-colmap}"
REALVIDEO_COLMAP_FRAME_STRIDE="${REALVIDEO_COLMAP_FRAME_STRIDE:-4}"
REALVIDEO_COLMAP_MAX_FRAMES="${REALVIDEO_COLMAP_MAX_FRAMES:-600}"
REALVIDEO_COLMAP_MATCHER="${REALVIDEO_COLMAP_MATCHER:-sequential}"
REALVIDEO_COLMAP_USE_GPU="${REALVIDEO_COLMAP_USE_GPU:-0}"
AUDIT_DEPTH_BACKEND="${AUDIT_DEPTH_BACKEND:-unik3d}"
AUDIT_UNIK3D_REPO="${AUDIT_UNIK3D_REPO:-/root/gs_refs/UniK3D}"
AUDIT_UNIK3D_BACKBONE="${AUDIT_UNIK3D_BACKBONE:-vitl}"
AUDIT_UNIK3D_DEVICE="${AUDIT_UNIK3D_DEVICE:-cuda}"
AUDIT_UNIK3D_RESOLUTION_LEVEL="${AUDIT_UNIK3D_RESOLUTION_LEVEL:-7}"
AUDIT_MIN_BRIGHTNESS="${AUDIT_MIN_BRIGHTNESS:-12}"
AUDIT_MAX_BRIGHTNESS="${AUDIT_MAX_BRIGHTNESS:-245}"
AUDIT_MIN_MEAN_DIFF="${AUDIT_MIN_MEAN_DIFF:-0.4}"
AUDIT_MIN_LAPLACIAN_VAR="${AUDIT_MIN_LAPLACIAN_VAR:-6.0}"
AUDIT_MIN_DEPTH_P50="${AUDIT_MIN_DEPTH_P50:-0.6}"
AUDIT_MAX_NEAR_RATIO_05M="${AUDIT_MAX_NEAR_RATIO_05M:-0.55}"
AUDIT_MAX_NEAR_RATIO_1M="${AUDIT_MAX_NEAR_RATIO_1M:-0.92}"
AUDIT_MAX_NEAR_RATIO_2M="${AUDIT_MAX_NEAR_RATIO_2M:-1.0}"
GATE_MIN_LIVE_FRAMES="${GATE_MIN_LIVE_FRAMES:-180}"
GATE_MAX_LIVE_FRAMES="${GATE_MAX_LIVE_FRAMES:-1200}"
GATE_MIN_ACCEPT_RATE="${GATE_MIN_ACCEPT_RATE:-0.90}"
GATE_MAX_TOO_BRIGHT_RATE="${GATE_MAX_TOO_BRIGHT_RATE:-0.10}"
GATE_MIN_FEED_FPS="${GATE_MIN_FEED_FPS:-8.0}"

if [[ ! -f "${VIDEO_PATH}" ]]; then
  echo "[realvideo-full] missing video: ${VIDEO_PATH}" >&2
  exit 2
fi

rm -rf "${PREP_ROOT}" "${FEED_ROOT}" "${OUT_DIR}"
mkdir -p "${PREP_ROOT}" "${FEED_ROOT}" "${OUT_DIR}"

echo "[realvideo-full] preprocess video=${VIDEO_PATH} prep=${PREP_ROOT}"
cd "${REPO}"
if command -v colmap >/dev/null 2>&1; then
  echo "[realvideo-full] colmap found, using enhanced real-video colmap preprocess"
  PREP_ARGS=(
    --video "${VIDEO_PATH}"
    --prep-root "${PREP_ROOT}"
    --extract-if-missing
    --run-colmap
    --colmap-binary "${REALVIDEO_COLMAP_BINARY}"
    --colmap-frame-stride "${REALVIDEO_COLMAP_FRAME_STRIDE}"
    --colmap-max-frames "${REALVIDEO_COLMAP_MAX_FRAMES}"
    --colmap-matcher "${REALVIDEO_COLMAP_MATCHER}"
  )
  if [[ "${REALVIDEO_COLMAP_USE_GPU}" == "1" ]]; then
    PREP_ARGS+=(--colmap-use-gpu)
  fi
else
  echo "[realvideo-full] colmap not found, using fallback extract + heuristic calib"
  PREP_ARGS=(
    --video "${VIDEO_PATH}"
    --prep-root "${PREP_ROOT}"
    --extract-if-missing
  )
fi
"${PREP_PYTHON}" "${ROOT}/scripts/prepare_real_video_owndata.py" "${PREP_ARGS[@]}"

echo "[realvideo-full] audit prep=${PREP_ROOT} feed=${FEED_ROOT}"
"${AUDIT_PYTHON}" "${ROOT}/scripts/audit_real_video_frames.py" \
  --video "${VIDEO_PATH}" \
  --src-root "${PREP_ROOT}" \
  --dst-root "${FEED_ROOT}" \
  --realtime-scale 0.0 \
  --min-brightness "${AUDIT_MIN_BRIGHTNESS}" \
  --max-brightness "${AUDIT_MAX_BRIGHTNESS}" \
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
  --unik3d-resolution-level "${AUDIT_UNIK3D_RESOLUTION_LEVEL}" \
  --gate-min-live-frames "${GATE_MIN_LIVE_FRAMES}" \
  --gate-max-live-frames "${GATE_MAX_LIVE_FRAMES}" \
  --gate-min-accept-rate "${GATE_MIN_ACCEPT_RATE}" \
  --gate-max-too-bright-rate "${GATE_MAX_TOO_BRIGHT_RATE}" \
  --gate-min-feed-fps "${GATE_MIN_FEED_FPS}"

echo "[realvideo-full] donor feed=${FEED_ROOT} out=${OUT_DIR}"
ROOT="${ROOT}" \
HI_SLAM2_SEQ_DIR="${FEED_ROOT}" \
HI_SLAM2_CALIB="${FEED_ROOT}/calib.txt" \
HI_SLAM2_CONFIG="${REPO}/config/owndata_config.yaml" \
HI_SLAM2_OUT_DIR="${OUT_DIR}" \
bash "${ROOT}/scripts/remote_run_hislam2_room3x3_official_owndata_runtime.sh"

echo "[realvideo-full] done"
