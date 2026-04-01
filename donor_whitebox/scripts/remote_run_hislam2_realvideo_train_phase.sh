#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/root/donor_whitebox}"
REPO="${HI_SLAM2_REPO:-/root/gs_refs/HI-SLAM2}"
RUN_NAME="${1:?usage: remote_run_hislam2_realvideo_train_phase.sh RUN_NAME TIER_NAME}"
TIER_NAME="${2:?usage: remote_run_hislam2_realvideo_train_phase.sh RUN_NAME TIER_NAME}"
FULL_TIMEOUT="${FULL_TIMEOUT:-7200}"

BASE_DIR="${ROOT}/outputs/hislam2_${RUN_NAME}"
LOG_DIR="${ROOT}/logs/${RUN_NAME}"
SUMMARY_DIR="${BASE_DIR}/summaries"
RUNTIME_STATUS_JSON="${SUMMARY_DIR}/RUNTIME_STATUS.json"
SUCCESS_JSON="${SUMMARY_DIR}/SUCCESS.json"
RUN_STARTED_EPOCH="$(date +%s)"

mkdir -p "${BASE_DIR}" "${LOG_DIR}" "${SUMMARY_DIR}"

ACTIVE_PHASE_MONITOR_PID=""
CURRENT_FAILURE_STAGE="full"
CURRENT_FAILURE_REASON="official_train_failed"

stop_phase_runtime_monitor() {
  if [[ -n "${ACTIVE_PHASE_MONITOR_PID:-}" ]]; then
    kill "${ACTIVE_PHASE_MONITOR_PID}" >/dev/null 2>&1 || true
    wait "${ACTIVE_PHASE_MONITOR_PID}" >/dev/null 2>&1 || true
    ACTIVE_PHASE_MONITOR_PID=""
  fi
}

cleanup() {
  local exit_code=$?
  stop_phase_runtime_monitor
  if [[ "${exit_code}" -ne 0 && ! -f "${SUCCESS_JSON}" && ! -f "${SUMMARY_DIR}/${TIER_NAME}_full_failure.json" ]]; then
    record_failure "${TIER_NAME}" "${CURRENT_FAILURE_STAGE}" "${CURRENT_FAILURE_REASON}" "$(compact_log_tail "${full_log:-}")"
  fi
  return "${exit_code}"
}

trap cleanup EXIT TERM INT

write_runtime_status() {
  local state="$1"
  local stage="$2"
  local detail="$3"
  local progress_start="$4"
  local progress_end="$5"
  local phase_budget_sec="$6"
  local progress_basis="$7"
  local tier_name="${8:-}"
  local phase_name="${9:-}"
  local phase_started_epoch
  phase_started_epoch="$(date +%s)"
  python3 - "${RUNTIME_STATUS_JSON}" "${state}" "${stage}" "${detail}" "${progress_start}" "${progress_end}" "${phase_budget_sec}" "${progress_basis}" "${tier_name}" "${phase_name}" "${RUN_STARTED_EPOCH}" "${phase_started_epoch}" <<'PY'
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
    "progress_start": float(sys.argv[5]),
    "progress_end": float(sys.argv[6]),
    "phase_budget_sec": int(float(sys.argv[7])),
    "progress_basis": sys.argv[8],
    "current_tier": sys.argv[9] or None,
    "phase_name": sys.argv[10] or None,
    "run_started_at_epoch": int(float(sys.argv[11])),
    "phase_started_at_epoch": int(float(sys.argv[12])),
    "elapsed_sec": max(0, int(time.time()) - int(float(sys.argv[11]))),
    "estimated_remaining_sec": int(float(sys.argv[7])),
}
status_path.parent.mkdir(parents=True, exist_ok=True)
tmp_path = status_path.with_suffix(status_path.suffix + ".tmp")
tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
tmp_path.replace(status_path)
PY
}

write_runtime_status_completed() {
  local detail="${1:-远端完整训练已经结束，最终 3DGS 已经生成并开始回传。}"
  local now
  now="$(date +%s)"
  python3 - "${RUNTIME_STATUS_JSON}" "${detail}" "${RUN_STARTED_EPOCH}" "${now}" <<'PY'
import json
import sys
from pathlib import Path

status_path = Path(sys.argv[1])
detail = sys.argv[2]
run_started_epoch = int(float(sys.argv[3]))
phase_started_epoch = int(float(sys.argv[4]))
payload = {
    "state": "completed",
    "stage": "complete",
    "detail": detail,
    "progress": 100.0,
    "progress_start": 100.0,
    "progress_end": 100.0,
    "phase_budget_sec": 0,
    "phase_started_at_epoch": phase_started_epoch,
    "run_started_at_epoch": run_started_epoch,
    "elapsed_sec": max(0, phase_started_epoch - run_started_epoch),
    "estimated_remaining_sec": 0,
    "progress_basis": "runtime_complete",
}
status_path.parent.mkdir(parents=True, exist_ok=True)
tmp_path = status_path.with_suffix(status_path.suffix + ".tmp")
tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
tmp_path.replace(status_path)
PY
}

start_phase_runtime_monitor() {
  local work_root="$1"
  local stage="$2"
  local detail="$3"
  local progress_start="$4"
  local progress_end="$5"
  local phase_budget_sec="$6"
  local tier_name="$7"
  local phase_name="$8"
  local target_units="$9"
  local log_path="${10:-}"
  local phase_started_epoch
  phase_started_epoch="$(date +%s)"

  stop_phase_runtime_monitor

  (
    while true; do
      python3 - "${RUNTIME_STATUS_JSON}" "${work_root}" "${stage}" "${detail}" "${progress_start}" "${progress_end}" "${phase_budget_sec}" "${tier_name}" "${phase_name}" "${target_units}" "${RUN_STARTED_EPOCH}" "${phase_started_epoch}" "${log_path}" <<'PY'
import json
import os
import re
import sys
import time
from pathlib import Path

status_path = Path(sys.argv[1])
work_root = Path(sys.argv[2])
stage = sys.argv[3]
detail = sys.argv[4]
progress_start = float(sys.argv[5])
progress_end = float(sys.argv[6])
phase_budget_sec = max(int(float(sys.argv[7])), 1)
tier_name = sys.argv[8]
phase_name = sys.argv[9]
target_units = max(int(float(sys.argv[10])), 1)
run_started_epoch = int(float(sys.argv[11]))
phase_started_epoch = int(float(sys.argv[12]))
log_path = Path(sys.argv[13]) if len(sys.argv) > 13 and sys.argv[13] else None

ansi_re = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
tqdm_re = re.compile(
    r"(?P<label>.+?):\s*(?P<pct>\d{1,3})%\|.*?\|\s*(?P<cur>\d+)\/(?P<tot>\d+)\s*\[(?P<elapsed>[0-9:]+)<(?P<remaining>[0-9:]+)"
)

def read_log_tail(path: Path, max_bytes: int = 512 * 1024) -> str:
    if not path or not path.exists():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes), os.SEEK_SET)
        data = handle.read().decode("utf-8", errors="ignore")
    return ansi_re.sub("", data)

def find_tqdm_snapshot(text: str):
    latest = None
    for line in text.splitlines():
        match = tqdm_re.search(line)
        if match:
            latest = match
    return latest

now = int(time.time())
elapsed_total = max(0, now - run_started_epoch)
elapsed_phase = max(0, now - phase_started_epoch)
budget_ratio = min(1.0, elapsed_phase / phase_budget_sec)
progress = progress_start + (progress_end - progress_start) * budget_ratio
detail_live = detail
current_units = None
target_units_live = target_units

snapshot = find_tqdm_snapshot(read_log_tail(log_path))
if snapshot is not None:
    current_units = int(snapshot.group("cur"))
    target_units_live = max(int(snapshot.group("tot")), 1)
    ratio = min(current_units / target_units_live, 1.0)
    progress = progress_start + (progress_end - progress_start) * ratio
    detail_live = f"{detail}（{current_units}/{target_units_live} 步）。"

payload = {
    "state": "processing",
    "stage": stage,
    "detail": detail_live,
    "progress": round(progress, 4),
    "progress_start": progress_start,
    "progress_end": progress_end,
    "phase_budget_sec": phase_budget_sec,
    "phase_started_at_epoch": phase_started_epoch,
    "run_started_at_epoch": run_started_epoch,
    "elapsed_sec": elapsed_total,
    "estimated_remaining_sec": max(0, int(phase_budget_sec - elapsed_phase)),
    "progress_basis": "runtime_tqdm_steps" if snapshot is not None else "runtime_budget",
    "current_tier": tier_name or None,
    "phase_name": phase_name or None,
}
if current_units is not None:
    payload["current_units"] = current_units
    payload["target_units"] = target_units_live
    payload["unit_label"] = "步"

status_path.parent.mkdir(parents=True, exist_ok=True)
tmp_path = status_path.with_suffix(status_path.suffix + ".tmp")
tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
tmp_path.replace(status_path)
PY
      sleep 1
    done
  ) &
  ACTIVE_PHASE_MONITOR_PID="$!"
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

compact_log_tail() {
  local log_path="${1:-}"
  local max_lines="${2:-12}"
  python3 - "${log_path}" "${max_lines}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
max_lines = max(int(sys.argv[2]), 1)
if not path.is_file():
    raise SystemExit(0)
lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()]
if not lines:
    raise SystemExit(0)
print(" | ".join(lines[-max_lines:]))
PY
}

summarize_out() {
  local out_dir="$1"
  local summary_json="$2"
  python3 "${ROOT}/scripts/summarize_hislam2_result.py" \
    --outdir "${out_dir}" \
    --json > "${summary_json}"
}

write_success_json() {
  local tier_name="$1"
  local artifact_path="$2"
  local summary_path="$3"
  python3 - "${SUCCESS_JSON}" "${tier_name}" "${artifact_path}" "${summary_path}" <<'PY'
import json
import sys
from pathlib import Path

success_path = Path(sys.argv[1])
tier_name = sys.argv[2]
artifact_path = sys.argv[3]
summary_path = sys.argv[4]

summary_payload = {}
if summary_path and Path(summary_path).is_file():
    try:
        summary_payload = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    except Exception:
        summary_payload = {}

payload = {
    "tier": tier_name,
    "artifact_path": artifact_path,
    "summary_path": summary_path,
    "verdict_path": None,
    "best_effort": False,
    "source": "official_hislam_full",
    "failures": [],
    "summary": {
        "has_3dgs_final": bool(summary_payload.get("has_3dgs_final")),
        "psnr": summary_payload.get("mean_psnr"),
        "ssim": summary_payload.get("mean_ssim"),
        "traj_full_lines": summary_payload.get("traj_full_lines"),
    },
}
success_path.parent.mkdir(parents=True, exist_ok=True)
tmp_path = success_path.with_suffix(success_path.suffix + ".tmp")
tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
tmp_path.replace(success_path)
PY
}

run_full() {
  local feed_root="$1"
  local out_dir="$2"
  local full_log="$3"
  rm -rf "${out_dir}"
  mkdir -p "${out_dir}"
  HI_SLAM2_SEQ_DIR="${feed_root}" \
  HI_SLAM2_CALIB="${feed_root}/calib.txt" \
  HI_SLAM2_CONFIG="${REPO}/config/owndata_config.yaml" \
  HI_SLAM2_OUT_DIR="${out_dir}" \
  HI_SLAM2_DEMO_TIMEOUT="${FULL_TIMEOUT}" \
  HI_SLAM2_UNDISTORT=auto \
  HI_SLAM2_SKIP_TSDF=0 \
  bash "${ROOT}/scripts/remote_run_hislam2_room3x3_official_owndata_runtime.sh" > "${full_log}" 2>&1
}

if [[ "${TIER_NAME}" != "official_default" ]]; then
  record_failure "${TIER_NAME}" "full" "unsupported_tier" "default mainline only supports official_default"
  exit 64
fi

feed_root="${BASE_DIR}/${TIER_NAME}_feed"
full_out="${BASE_DIR}/${TIER_NAME}_out"
full_log="${LOG_DIR}/${TIER_NAME}_full.log"
full_summary="${SUMMARY_DIR}/${TIER_NAME}_full_summary.json"

if [[ ! -d "${feed_root}/images" || ! -f "${feed_root}/calib.txt" ]]; then
  record_failure "${TIER_NAME}" "full" "missing_prepared_input" "${feed_root}"
  exit 3
fi

full_target_units="$(find "${feed_root}/images" -maxdepth 1 -type f -name '*.jpg' ! -name '*.tmp.jpg' | wc -l | tr -d ' ')"
if [[ -z "${full_target_units}" || "${full_target_units}" -le 0 ]]; then
  full_target_units="1000"
fi

rm -f "${full_summary}" "${SUCCESS_JSON}" || true

write_runtime_status "processing" "train" "正在运行官方 HI-SLAM 训练（demo.py）。" "52" "92" "960" "official_hislam_runtime" "${TIER_NAME}" "full"
start_phase_runtime_monitor "${full_out}" "train" "正在运行官方 HI-SLAM 训练（demo.py）。" "52" "92" "960" "${TIER_NAME}" "full" "${full_target_units}" "${full_log}"
run_full "${feed_root}" "${full_out}" "${full_log}"
stop_phase_runtime_monitor

summarize_out "${full_out}" "${full_summary}" || true
write_runtime_status "processing" "export" "正在整理官方 HI-SLAM 输出并准备回传。" "92" "98" "180" "official_hislam_runtime" "${TIER_NAME}" "export"

if [[ -f "${full_out}/3dgs_final.ply" ]]; then
  write_success_json "${TIER_NAME}" "${full_out}/3dgs_final.ply" "${full_summary}"
  write_runtime_status_completed "远端完整训练已经结束，最终 3DGS 已经生成并开始回传。"
  exit 0
fi

record_failure "${TIER_NAME}" "full" "official_train_no_final_3dgs" "$(compact_log_tail "${full_log}")"
exit 1
