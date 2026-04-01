#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/root/donor_whitebox}"
RUN_NAME="${RUN_NAME:?set RUN_NAME}"
VIDEO_PATH="${REAL_VIDEO_PATH:?set REAL_VIDEO_PATH}"
AUTOFALLBACK_POLICY="${AETHER_AUTOFALLBACK_POLICY:-official_default}"
TIER_NAME="official_default"

BASE_DIR="${ROOT}/outputs/hislam2_${RUN_NAME}"
LOG_DIR="${ROOT}/logs/${RUN_NAME}"
SUMMARY_DIR="${BASE_DIR}/summaries"
RUNTIME_STATUS_JSON="${SUMMARY_DIR}/RUNTIME_STATUS.json"
PID_FILE="${ROOT}/logs/${RUN_NAME}.pid"
RUN_STARTED_EPOCH="$(date +%s)"

mkdir -p "${BASE_DIR}" "${LOG_DIR}" "${SUMMARY_DIR}" "${ROOT}/logs"
echo "$$" > "${PID_FILE}"

record_failure() {
  local stage="$1"
  local reason="$2"
  local detail="${3:-}"
  python3 - "${SUMMARY_DIR}/all_tiers_failed.json" "${reason}" "${detail}" "${stage}" "${RUN_STARTED_EPOCH}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "reason": sys.argv[2],
    "detail": sys.argv[3],
    "stage": sys.argv[4],
    "run_started_at_epoch": int(float(sys.argv[5])),
}
path.parent.mkdir(parents=True, exist_ok=True)
tmp_path = path.with_suffix(path.suffix + ".tmp")
tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
tmp_path.replace(path)
PY
}

cleanup() {
  local exit_code=$?
  rm -f "${PID_FILE}"
  if [[ "${exit_code}" -ne 0 && ! -f "${SUMMARY_DIR}/SUCCESS.json" && ! -f "${SUMMARY_DIR}/all_tiers_failed.json" ]]; then
    record_failure "failed" "official_default_failed" "官方 HI-SLAM 默认主路失败。"
  fi
  exit "${exit_code}"
}

trap cleanup EXIT TERM INT

if [[ "${AUTOFALLBACK_POLICY}" != "official_default" ]]; then
  record_failure "failed" "unsupported_policy" "当前默认主路只允许 official_default。"
  exit 64
fi

bash "${ROOT}/scripts/remote_run_hislam2_realvideo_prep_audit_phase.sh" \
  "${RUN_NAME}" \
  "${VIDEO_PATH}" \
  "${TIER_NAME}"

bash "${ROOT}/scripts/remote_run_hislam2_realvideo_train_phase.sh" \
  "${RUN_NAME}" \
  "${TIER_NAME}"
