#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/root/donor_whitebox}"
MODE="${MODE:-global_room_closure_local_refine}"
ASSET_ROOT="${ASSET_ROOT:-${ROOT}/outputs/hislam2_capture_videoasset_global_local_refine_native_20260317_r1}"
FEED_ROOT="${FEED_ROOT:-${ROOT}/outputs/hislam2_capture_videoasset_global_local_refine_directfeed_20260317_r1}"
FULL_OUT_DIR="${FULL_OUT_DIR:-${ROOT}/outputs/hislam2_global_local_refine_videoasset_direct_live_full_official_20260317_r1}"
TARGET_FRAMES="${TARGET_FRAMES:-16000}"
DURATION="${DURATION:-1800}"
VIDEO_REALTIME_SCALE="${VIDEO_REALTIME_SCALE:-1.0}"
VIDEO_FPS="${VIDEO_FPS:-0}"
UNI_K3D_VENV_DIR="${UNI_K3D_VENV_DIR:-/venv/unik3d}"
VIDEO_PYTHON="${VIDEO_PYTHON:-${UNI_K3D_VENV_DIR}/bin/python}"
SEQ_DIR="${ASSET_ROOT}/seq_${MODE}"
VIDEO_PATH="${ASSET_ROOT}/${MODE}.mp4"
AUDIT_PYTHON="${AUDIT_PYTHON:-${UNI_K3D_VENV_DIR}/bin/python}"
AUDIT_DEPTH_BACKEND="${AUDIT_DEPTH_BACKEND:-unik3d}"
AUDIT_UNIK3D_REPO="${AUDIT_UNIK3D_REPO:-/root/gs_refs/UniK3D}"
AUDIT_UNIK3D_BACKBONE="${AUDIT_UNIK3D_BACKBONE:-vitl}"
AUDIT_UNIK3D_DEVICE="${AUDIT_UNIK3D_DEVICE:-cuda}"
AUDIT_UNIK3D_RESOLUTION_LEVEL="${AUDIT_UNIK3D_RESOLUTION_LEVEL:-7}"
AUDIT_MIN_BRIGHTNESS="${AUDIT_MIN_BRIGHTNESS:-12}"
AUDIT_MAX_BRIGHTNESS="${AUDIT_MAX_BRIGHTNESS:-245}"
AUDIT_MIN_DEPTH_P50="${AUDIT_MIN_DEPTH_P50:-0.8}"
AUDIT_MAX_NEAR_RATIO_05M="${AUDIT_MAX_NEAR_RATIO_05M:-0.40}"
AUDIT_MAX_NEAR_RATIO_1M="${AUDIT_MAX_NEAR_RATIO_1M:-0.85}"
AUDIT_MAX_NEAR_RATIO_2M="${AUDIT_MAX_NEAR_RATIO_2M:-1.0}"
AUDIT_MIN_MEAN_DIFF="${AUDIT_MIN_MEAN_DIFF:-2.5}"
AUDIT_MIN_LAPLACIAN_VAR="${AUDIT_MIN_LAPLACIAN_VAR:-6.0}"
GATE_MIN_FEED_FPM="${GATE_MIN_FEED_FPM:-150}"
GATE_MIN_ACCEPT_RATE="${GATE_MIN_ACCEPT_RATE:-0.90}"
GATE_MAX_TOO_BRIGHT_RATE="${GATE_MAX_TOO_BRIGHT_RATE:-0.10}"
GATE_MIN_LIVE_FRAMES="${GATE_MIN_LIVE_FRAMES:-360}"
GATE_MAX_LIVE_FRAMES="${GATE_MAX_LIVE_FRAMES:-1600}"

if [[ ! -d "${SEQ_DIR}" ]]; then
  ROOT="${ROOT}" \
  MODE_SPEC="${MODE}" \
  OUT_ROOT="${ASSET_ROOT}" \
  TARGET_FRAMES="${TARGET_FRAMES}" \
  DURATION="${DURATION}" \
  REALTIME_SCALE=0 \
  WRITE_DEPTH_BIN=0 \
  COMPRESS_IMAGES=1 \
  bash "${ROOT}/scripts/remote_prepare_hislam2_workpoint_variants.sh"
fi

SOURCE_CHECK="$("${VIDEO_PYTHON}" - <<PY
import json, math, pathlib, sys
seq = pathlib.Path("${SEQ_DIR}")
summary = json.loads((seq / "sequence_summary.json").read_text())
duration_sec = float(summary.get("duration_sec", 0.0))
frames = int(summary.get("frames", 0))
frames_per_meter = float(summary.get("frames_per_meter", 0.0))
min_source_fpm = float(${GATE_MIN_FEED_FPM}) / max(float(${GATE_MIN_ACCEPT_RATE}), 1e-9)
ok = duration_sec >= float(${DURATION}) * 0.99 and frames == int(${TARGET_FRAMES}) and frames_per_meter >= min_source_fpm
print(json.dumps({
    "ok": ok,
    "duration_sec": duration_sec,
    "frames": frames,
    "frames_per_meter": frames_per_meter,
    "min_source_fpm": min_source_fpm,
}))
PY
)"
echo "[videoasset-direct-live] source_check=${SOURCE_CHECK}"
if [[ "$(printf '%s' "${SOURCE_CHECK}" | python3 -c 'import json,sys; print("1" if json.load(sys.stdin)["ok"] else "0")')" != "1" ]]; then
  echo "[videoasset-direct-live] source sequence does not satisfy native-duration gate" >&2
  exit 10
fi

if [[ ! -f "${VIDEO_PATH}" ]]; then
  "${VIDEO_PYTHON}" "${ROOT}/scripts/encode_sequence_video.py" \
    --images-dir "${SEQ_DIR}/images" \
    --output "${VIDEO_PATH}" \
    --fps "$(python3 - <<PY
target_frames = float(${TARGET_FRAMES})
duration = float(${DURATION})
print(f"{target_frames / duration:.6f}")
PY
)"
fi

rm -rf "${FEED_ROOT}" "${FULL_OUT_DIR}"
mkdir -p "${FEED_ROOT}" "${FULL_OUT_DIR}"

"${AUDIT_PYTHON}" "${ROOT}/scripts/stream_video_audit.py" \
  --video "${VIDEO_PATH}" \
  --src-root "${SEQ_DIR}" \
  --dst-root "${FEED_ROOT}" \
  --realtime-scale "${VIDEO_REALTIME_SCALE}" \
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
  --gate-min-feed-frames-per-meter "${GATE_MIN_FEED_FPM}" \
  --gate-min-accept-rate "${GATE_MIN_ACCEPT_RATE}" \
  --gate-max-too-bright-rate "${GATE_MAX_TOO_BRIGHT_RATE}" &
AUDIT_PID=$!

ROOT="${ROOT}" \
HI_SLAM2_SEQ_DIR="${FEED_ROOT}" \
HI_SLAM2_OUT_DIR="${FULL_OUT_DIR}" \
HI_SLAM2_MIN_START_FRAMES="${HI_SLAM2_MIN_START_FRAMES:-12}" \
bash "${ROOT}/scripts/remote_run_hislam2_live_owndata_runtime.sh" &
LIVE_PID=$!

if ! wait "${AUDIT_PID}"; then
  kill "${LIVE_PID}" >/dev/null 2>&1 || true
  wait "${LIVE_PID}" || true
  exit 11
fi
wait "${LIVE_PID}"

echo "[videoasset-direct-live-global-local-refine-unik3d] done"
