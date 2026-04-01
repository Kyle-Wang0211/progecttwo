#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/root/donor_whitebox}"
REPO="${HI_SLAM2_REPO:-/root/gs_refs/HI-SLAM2}"
RUN_NAME="${1:?usage: remote_run_hislam2_realvideo_prep_audit_phase.sh RUN_NAME VIDEO_PATH TIER_NAME}"
VIDEO_PATH="${2:?usage: remote_run_hislam2_realvideo_prep_audit_phase.sh RUN_NAME VIDEO_PATH TIER_NAME}"
TIER_NAME="${3:?usage: remote_run_hislam2_realvideo_prep_audit_phase.sh RUN_NAME VIDEO_PATH TIER_NAME}"
PREP_PYTHON="${PREP_PYTHON:-/venv/hislam2/bin/python}"

BASE_DIR="${ROOT}/outputs/hislam2_${RUN_NAME}"
LOG_DIR="${ROOT}/logs/${RUN_NAME}"
SUMMARY_DIR="${BASE_DIR}/summaries"
RUNTIME_STATUS_JSON="${SUMMARY_DIR}/RUNTIME_STATUS.json"
PREP_READY_JSON="${SUMMARY_DIR}/PREP_READY.json"

mkdir -p "${BASE_DIR}" "${LOG_DIR}" "${SUMMARY_DIR}"

CURRENT_FAILURE_STAGE="prep"
CURRENT_FAILURE_REASON="official_preprocess_failed"

write_runtime_status() {
  local state="$1"
  local stage="$2"
  local detail="$3"
  local progress="$4"
  local progress_basis="$5"
  local tier_name="${6:-}"
  local phase_name="${7:-}"
  python3 - "${RUNTIME_STATUS_JSON}" "${state}" "${stage}" "${detail}" "${progress}" "${progress_basis}" "${tier_name}" "${phase_name}" <<'PY'
import json
import sys
import time
from pathlib import Path

status_path = Path(sys.argv[1])
payload = {
    "state": sys.argv[2],
    "stage": sys.argv[3],
    "detail": sys.argv[4],
    "progress": float(sys.argv[5]),
    "progress_basis": sys.argv[6],
    "current_tier": sys.argv[7] or None,
    "phase_name": sys.argv[8] or None,
    "elapsed_sec": 0,
    "estimated_remaining_sec": 0,
    "updated_at_epoch": int(time.time()),
}
status_path.parent.mkdir(parents=True, exist_ok=True)
tmp_path = status_path.with_suffix(status_path.suffix + ".tmp")
tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
tmp_path.replace(status_path)
PY
}

record_failure() {
  local tier_name="$1"
  local stage="$2"
  local reason="$3"
  local detail="${4:-}"
  local out="${SUMMARY_DIR}/${tier_name}_${stage}_failure.json"
  python3 - "$tier_name" "$stage" "$reason" "$detail" > "${out}" <<'PY'
import json
import sys

payload = {
    "tier": sys.argv[1],
    "stage": sys.argv[2],
    "reason": sys.argv[3],
}
detail = sys.argv[4].strip()
if detail:
    payload["detail"] = detail
print(json.dumps(payload, indent=2, ensure_ascii=False))
PY
}

write_prep_ready() {
  local tier_name="$1"
  local prep_root="$2"
  local feed_root="$3"
  local selected_frames="$4"
  python3 - "${PREP_READY_JSON}" "${tier_name}" "${prep_root}" "${feed_root}" "${selected_frames}" <<'PY'
import json
import sys
import time
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "tier": sys.argv[2],
    "prep_root": sys.argv[3],
    "feed_root": sys.argv[4],
    "selected_frames": int(sys.argv[5]),
    "ready_at_epoch": int(time.time()),
    "source": "official_hislam_preprocess",
}
path.parent.mkdir(parents=True, exist_ok=True)
tmp_path = path.with_suffix(path.suffix + ".tmp")
tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
tmp_path.replace(path)
PY
}

run_official_prep() {
  local prep_root="$1"
  local feed_root="$2"
  rm -rf "${prep_root}" "${feed_root}"
  mkdir -p "${prep_root}" "${feed_root}"
  (
    cd "${REPO}"
    "${PREP_PYTHON}" scripts/preprocess_owndata.py "${VIDEO_PATH}" "${prep_root}"
  )
  ln -sfn "${prep_root}/images" "${feed_root}/images"
  ln -sfn "${prep_root}/calib.txt" "${feed_root}/calib.txt"
  if [[ -d "${prep_root}/images_colmap" ]]; then
    ln -sfn "${prep_root}/images_colmap" "${feed_root}/images_colmap"
  fi
  if [[ -d "${prep_root}/sparse" ]]; then
    ln -sfn "${prep_root}/sparse" "${feed_root}/sparse"
  fi
  if [[ -d "${prep_root}/sparse_txt" ]]; then
    ln -sfn "${prep_root}/sparse_txt" "${feed_root}/sparse_txt"
  fi
  printf 'ok\n' > "${feed_root}/.complete"
}

validate_official_prep() {
  local prep_root="$1"
  python3 - "${prep_root}" <<'PY'
import sys
from pathlib import Path

prep_root = Path(sys.argv[1])
images_dir = prep_root / "images"
calib_path = prep_root / "calib.txt"
sparse_dir = prep_root / "sparse"
sparse_txt_dir = prep_root / "sparse_txt"

images = sorted(path for path in images_dir.glob("*.jpg") if path.is_file())
if not images:
    raise SystemExit(2)
if not calib_path.is_file():
    raise SystemExit(3)
if not sparse_dir.is_dir() and not sparse_txt_dir.is_dir():
    raise SystemExit(4)
print(len(images))
PY
}

cleanup() {
  local exit_code=$?
  if [[ "${exit_code}" -ne 0 && ! -f "${PREP_READY_JSON}" && ! -f "${SUMMARY_DIR}/${TIER_NAME}_prep_failure.json" ]]; then
    record_failure "${TIER_NAME}" "${CURRENT_FAILURE_STAGE}" "${CURRENT_FAILURE_REASON}"
  fi
  return "${exit_code}"
}

trap cleanup EXIT TERM INT

if [[ "${TIER_NAME}" != "official_default" ]]; then
  record_failure "${TIER_NAME}" "prep" "unsupported_tier" "default mainline only supports official_default"
  exit 64
fi

prep_root="${BASE_DIR}/${TIER_NAME}_prep"
feed_root="${BASE_DIR}/${TIER_NAME}_feed"
prep_log="${LOG_DIR}/${TIER_NAME}_prep.log"

rm -f "${PREP_READY_JSON}"
write_runtime_status "processing" "sfm" "正在运行官方 HI-SLAM 预处理（preprocess_owndata.py）。" "24" "official_preprocess" "${TIER_NAME}" "prep"

if ! run_official_prep "${prep_root}" "${feed_root}" > "${prep_log}" 2>&1; then
  record_failure "${TIER_NAME}" "prep" "official_preprocess_failed"
  exit 1
fi

selected_frames="$(validate_official_prep "${prep_root}" 2>> "${prep_log}" || true)"
if [[ -z "${selected_frames}" || "${selected_frames}" -le 0 ]]; then
  record_failure "${TIER_NAME}" "prep" "official_preprocess_incomplete" "$(tail -n 20 "${prep_log}" 2>/dev/null | tr '\n' ' ' | sed 's/  */ /g')"
  exit 1
fi

write_prep_ready "${TIER_NAME}" "${prep_root}" "${feed_root}" "${selected_frames}"
write_runtime_status "processing" "sfm" "官方 HI-SLAM 预处理完成，正在等待 GPU 训练。" "52" "official_preprocess" "${TIER_NAME}" "gpu_wait"
