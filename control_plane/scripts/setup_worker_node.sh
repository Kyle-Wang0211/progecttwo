#!/usr/bin/env bash
set -euo pipefail

CONTROL_PLANE_ROOT="${CONTROL_PLANE_ROOT:-/root/control_plane}"
CONTROL_PLANE_BASE_URL="${CONTROL_PLANE_BASE_URL:?CONTROL_PLANE_BASE_URL is required}"
OBJECT_STORAGE_ENDPOINT_URL="${OBJECT_STORAGE_ENDPOINT_URL:?OBJECT_STORAGE_ENDPOINT_URL is required}"
OBJECT_STORAGE_REGION="${OBJECT_STORAGE_REGION:-}"
OBJECT_STORAGE_BUCKET="${OBJECT_STORAGE_BUCKET:?OBJECT_STORAGE_BUCKET is required}"
OBJECT_STORAGE_ACCESS_KEY_ID="${OBJECT_STORAGE_ACCESS_KEY_ID:?OBJECT_STORAGE_ACCESS_KEY_ID is required}"
OBJECT_STORAGE_SECRET_ACCESS_KEY="${OBJECT_STORAGE_SECRET_ACCESS_KEY:?OBJECT_STORAGE_SECRET_ACCESS_KEY is required}"
OBJECT_STORAGE_PUBLIC_BASE_URL="${OBJECT_STORAGE_PUBLIC_BASE_URL:?OBJECT_STORAGE_PUBLIC_BASE_URL is required}"

WORKER_PROVIDER="${WORKER_PROVIDER:-vast}"
WORKER_REGION="${WORKER_REGION:-eu}"
WORKER_INSTANCE_LABEL="${WORKER_INSTANCE_LABEL:-vast-5090-worker}"
WORKER_GPU_MODEL="${WORKER_GPU_MODEL:-RTX 5090}"
WORKER_GPU_COUNT="${WORKER_GPU_COUNT:-1}"
WORKER_VRAM_MB="${WORKER_VRAM_MB:-32607}"
WORKER_CPU_CORES="${WORKER_CPU_CORES:-32}"
WORKER_RAM_MB="${WORKER_RAM_MB:-131072}"
WORKER_DISK_FREE_MB="${WORKER_DISK_FREE_MB:-200000}"
WORKER_SOFTWARE_VERSION="${WORKER_SOFTWARE_VERSION:-worker-0.2.0}"

WORKER_DONOR_ROOT="${WORKER_DONOR_ROOT:-/root/donor_whitebox}"
WORKER_DONOR_OUTPUT_DIR="${WORKER_DONOR_OUTPUT_DIR:-/root/donor_whitebox/outputs}"
WORKER_DONOR_LOGS_DIR="${WORKER_DONOR_LOGS_DIR:-/root/donor_whitebox/logs}"
WORKER_DONOR_START_SCRIPT="${WORKER_DONOR_START_SCRIPT:-/root/donor_whitebox/scripts/remote_start_realvideo_autofallback_run.sh}"
WORKER_DONOR_PREP_SCRIPT="${WORKER_DONOR_PREP_SCRIPT:-/root/donor_whitebox/scripts/remote_run_hislam2_realvideo_prep_audit_phase.sh}"
WORKER_DONOR_PREP_PYTHON="${WORKER_DONOR_PREP_PYTHON:-/venv/hislam2/bin/python}"
WORKER_DONOR_TRAIN_SCRIPT="${WORKER_DONOR_TRAIN_SCRIPT:-/root/donor_whitebox/scripts/remote_run_hislam2_realvideo_train_phase.sh}"
WORKER_MAX_PARALLEL_PREP_JOBS="${WORKER_MAX_PARALLEL_PREP_JOBS:-1}"
WORKER_MAX_PARALLEL_GPU_JOBS="${WORKER_MAX_PARALLEL_GPU_JOBS:-1}"
WORKER_MAX_ACTIVE_JOBS="${WORKER_MAX_ACTIVE_JOBS:-1}"
WORKER_SKIP_TRAINING_PROBE_GATE="${WORKER_SKIP_TRAINING_PROBE_GATE:-1}"
WORKER_STREAM_PREWARM_ENABLED="${WORKER_STREAM_PREWARM_ENABLED:-0}"
WORKER_STREAM_PROMOTE_LIVE_SFM_AFTER_UPLOAD="${WORKER_STREAM_PROMOTE_LIVE_SFM_AFTER_UPLOAD:-0}"
WORKER_STREAM_PROMOTE_LIVE_SFM_MIN_SELECTED_FRAMES="${WORKER_STREAM_PROMOTE_LIVE_SFM_MIN_SELECTED_FRAMES:-20}"
WORKER_STREAM_PROMOTE_LIVE_SFM_MIN_REGISTERED_IMAGES="${WORKER_STREAM_PROMOTE_LIVE_SFM_MIN_REGISTERED_IMAGES:-8}"
WORKER_STREAM_TRAIN_SEED_ENABLED="${WORKER_STREAM_TRAIN_SEED_ENABLED:-0}"
WORKER_STREAM_TRAIN_SEED_POLL_INTERVAL_SEC="${WORKER_STREAM_TRAIN_SEED_POLL_INTERVAL_SEC:-1}"
WORKER_STREAM_TRAIN_SEED_MIN_SELECTED_FRAMES="${WORKER_STREAM_TRAIN_SEED_MIN_SELECTED_FRAMES:-20}"
WORKER_STREAM_TRAIN_SEED_MIN_REGISTERED_IMAGES="${WORKER_STREAM_TRAIN_SEED_MIN_REGISTERED_IMAGES:-8}"
WORKER_STREAM_TRAIN_SEED_FINALIZE_GRACE_SEC="${WORKER_STREAM_TRAIN_SEED_FINALIZE_GRACE_SEC:-2}"
WORKER_STREAM_TRAIN_SEED_SKIP_TSDF="${WORKER_STREAM_TRAIN_SEED_SKIP_TSDF:-1}"
WORKER_STREAM_ARTIFACT_STAGE_ENABLED="${WORKER_STREAM_ARTIFACT_STAGE_ENABLED:-0}"
WORKER_STREAM_ARTIFACT_STAGE_MIN_STABLE_POLLS="${WORKER_STREAM_ARTIFACT_STAGE_MIN_STABLE_POLLS:-2}"
WORKER_STREAM_ARTIFACT_PUBLISH_EARLY="${WORKER_STREAM_ARTIFACT_PUBLISH_EARLY:-0}"
AETHER_AUTOFALLBACK_POLICY="${AETHER_AUTOFALLBACK_POLICY:-official_default}"

export DEBIAN_FRONTEND=noninteractive

ensure_apt_packages() {
  local missing=()
  local pkg
  for pkg in "$@"; do
    if ! dpkg -s "${pkg}" >/dev/null 2>&1; then
      missing+=("${pkg}")
    fi
  done

  if (( ${#missing[@]} == 0 )); then
    return
  fi

  apt-get update
  apt-get install -y "${missing[@]}"
}

write_env_line() {
  local key="$1"
  local value="${2-}"
  printf '%s=%q\n' "${key}" "${value}" >> "${CONTROL_PLANE_ROOT}/.env.worker"
}

infer_object_storage_region() {
  local raw="${1:-}"
  raw="${raw#http://}"
  raw="${raw#https://}"
  raw="${raw%%/*}"

  if [[ -z "${raw}" ]]; then
    printf 'auto\n'
    return
  fi

  if [[ "${raw}" == *.digitaloceanspaces.com ]]; then
    local first="${raw%%.*}"
    local rest="${raw#*.}"
    if [[ "${rest}" == "digitaloceanspaces.com" ]]; then
      printf '%s\n' "${first}"
      return
    fi
    printf '%s\n' "${rest%%.*}"
    return
  fi

  printf 'auto\n'
}

if [[ -z "${OBJECT_STORAGE_REGION}" || "${OBJECT_STORAGE_REGION}" == "auto" ]]; then
  OBJECT_STORAGE_REGION="$(infer_object_storage_region "${OBJECT_STORAGE_ENDPOINT_URL}")"
fi

ensure_apt_packages ca-certificates curl python3-venv

cd "${CONTROL_PLANE_ROOT}"
find "${CONTROL_PLANE_ROOT}" -name '._*' -delete

python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

mkdir -p /root/control-plane-logs /tmp/aether-control-worker/inputs /tmp/aether-control-worker/artifacts

: > "${CONTROL_PLANE_ROOT}/.env.worker"
write_env_line CONTROL_PLANE_BASE_URL "${CONTROL_PLANE_BASE_URL}"
write_env_line CONTROL_PLANE_OBJECT_STORAGE_ENDPOINT_URL "${OBJECT_STORAGE_ENDPOINT_URL}"
write_env_line CONTROL_PLANE_OBJECT_STORAGE_REGION "${OBJECT_STORAGE_REGION}"
write_env_line CONTROL_PLANE_OBJECT_STORAGE_BUCKET "${OBJECT_STORAGE_BUCKET}"
write_env_line CONTROL_PLANE_OBJECT_STORAGE_ACCESS_KEY_ID "${OBJECT_STORAGE_ACCESS_KEY_ID}"
write_env_line CONTROL_PLANE_OBJECT_STORAGE_SECRET_ACCESS_KEY "${OBJECT_STORAGE_SECRET_ACCESS_KEY}"
write_env_line CONTROL_PLANE_OBJECT_STORAGE_SESSION_TOKEN ""
write_env_line CONTROL_PLANE_OBJECT_STORAGE_PUBLIC_BASE_URL "${OBJECT_STORAGE_PUBLIC_BASE_URL}"
write_env_line CONTROL_PLANE_OBJECT_STORAGE_ADDRESSING_STYLE "path"
write_env_line CONTROL_PLANE_OBJECT_STORAGE_PRESIGN_EXPIRY_SEC "3600"

write_env_line WORKER_PROVIDER "${WORKER_PROVIDER}"
write_env_line WORKER_REGION "${WORKER_REGION}"
write_env_line WORKER_INSTANCE_LABEL "${WORKER_INSTANCE_LABEL}"
write_env_line WORKER_HOST_FINGERPRINT "$(hostname)"
write_env_line WORKER_GPU_MODEL "${WORKER_GPU_MODEL}"
write_env_line WORKER_GPU_COUNT "${WORKER_GPU_COUNT}"
write_env_line WORKER_VRAM_MB "${WORKER_VRAM_MB}"
write_env_line WORKER_CPU_CORES "${WORKER_CPU_CORES}"
write_env_line WORKER_RAM_MB "${WORKER_RAM_MB}"
write_env_line WORKER_DISK_FREE_MB "${WORKER_DISK_FREE_MB}"
write_env_line WORKER_SOFTWARE_VERSION "${WORKER_SOFTWARE_VERSION}"

write_env_line WORKER_DONOR_ROOT "${WORKER_DONOR_ROOT}"
write_env_line WORKER_DONOR_OUTPUT_DIR "${WORKER_DONOR_OUTPUT_DIR}"
write_env_line WORKER_DONOR_LOGS_DIR "${WORKER_DONOR_LOGS_DIR}"
write_env_line WORKER_DONOR_START_SCRIPT "${WORKER_DONOR_START_SCRIPT}"
write_env_line WORKER_DONOR_PREP_SCRIPT "${WORKER_DONOR_PREP_SCRIPT}"
write_env_line WORKER_DONOR_PREP_PYTHON "${WORKER_DONOR_PREP_PYTHON}"
write_env_line WORKER_DONOR_TRAIN_SCRIPT "${WORKER_DONOR_TRAIN_SCRIPT}"
write_env_line WORKER_MAX_PARALLEL_PREP_JOBS "${WORKER_MAX_PARALLEL_PREP_JOBS}"
write_env_line WORKER_MAX_PARALLEL_GPU_JOBS "${WORKER_MAX_PARALLEL_GPU_JOBS}"
write_env_line WORKER_MAX_ACTIVE_JOBS "${WORKER_MAX_ACTIVE_JOBS}"
write_env_line WORKER_SKIP_TRAINING_PROBE_GATE "${WORKER_SKIP_TRAINING_PROBE_GATE}"
write_env_line WORKER_STREAM_PREWARM_ENABLED "${WORKER_STREAM_PREWARM_ENABLED}"
write_env_line WORKER_STREAM_PROMOTE_LIVE_SFM_AFTER_UPLOAD "${WORKER_STREAM_PROMOTE_LIVE_SFM_AFTER_UPLOAD}"
write_env_line WORKER_STREAM_PROMOTE_LIVE_SFM_MIN_SELECTED_FRAMES "${WORKER_STREAM_PROMOTE_LIVE_SFM_MIN_SELECTED_FRAMES}"
write_env_line WORKER_STREAM_PROMOTE_LIVE_SFM_MIN_REGISTERED_IMAGES "${WORKER_STREAM_PROMOTE_LIVE_SFM_MIN_REGISTERED_IMAGES}"
write_env_line WORKER_STREAM_TRAIN_SEED_ENABLED "${WORKER_STREAM_TRAIN_SEED_ENABLED}"
write_env_line WORKER_STREAM_TRAIN_SEED_POLL_INTERVAL_SEC "${WORKER_STREAM_TRAIN_SEED_POLL_INTERVAL_SEC}"
write_env_line WORKER_STREAM_TRAIN_SEED_MIN_SELECTED_FRAMES "${WORKER_STREAM_TRAIN_SEED_MIN_SELECTED_FRAMES}"
write_env_line WORKER_STREAM_TRAIN_SEED_MIN_REGISTERED_IMAGES "${WORKER_STREAM_TRAIN_SEED_MIN_REGISTERED_IMAGES}"
write_env_line WORKER_STREAM_TRAIN_SEED_FINALIZE_GRACE_SEC "${WORKER_STREAM_TRAIN_SEED_FINALIZE_GRACE_SEC}"
write_env_line WORKER_STREAM_TRAIN_SEED_SKIP_TSDF "${WORKER_STREAM_TRAIN_SEED_SKIP_TSDF}"
write_env_line WORKER_STREAM_ARTIFACT_STAGE_ENABLED "${WORKER_STREAM_ARTIFACT_STAGE_ENABLED}"
write_env_line WORKER_STREAM_ARTIFACT_STAGE_MIN_STABLE_POLLS "${WORKER_STREAM_ARTIFACT_STAGE_MIN_STABLE_POLLS}"
write_env_line WORKER_STREAM_ARTIFACT_PUBLISH_EARLY "${WORKER_STREAM_ARTIFACT_PUBLISH_EARLY}"
write_env_line WORKER_SCHEDULER_TICK_INTERVAL_SEC "2"
write_env_line WORKER_STREAM_LIVE_SFM_ENABLED "0"
write_env_line WORKER_STREAM_PREWARM_POLL_INTERVAL_SEC "1"
write_env_line WORKER_STREAM_LIVE_SFM_MIN_FRAMES "24"
write_env_line WORKER_STREAM_LIVE_SFM_MIN_NEW_FRAMES "12"
write_env_line WORKER_STREAM_LIVE_SFM_MAX_FRAMES_CAP "64"
write_env_line WORKER_STREAM_LIVE_SFM_FINALIZE_GRACE_SEC "20"
write_env_line AETHER_AUTOFALLBACK_POLICY "${AETHER_AUTOFALLBACK_POLICY}"
write_env_line WORKER_LOCAL_ROOT "/tmp/aether-control-worker"
write_env_line WORKER_LOCAL_INPUT_DIR "/tmp/aether-control-worker/inputs"
write_env_line WORKER_LOCAL_ARTIFACT_DIR "/tmp/aether-control-worker/artifacts"
write_env_line WORKER_RUNTIME_DIR "/root/control_plane/runtime"
write_env_line WORKER_LIVENESS_HEARTBEAT_FILE "/root/control_plane/runtime/worker_agent.liveness.json"
write_env_line WORKER_LIVENESS_STALE_RESTART_SEC "180"

mkdir -p /root/control_plane/runtime
cat > /root/control_plane/runtime/ensure_worker.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail
cd /root/control_plane
HEARTBEAT_FILE="/root/control_plane/runtime/worker_agent.liveness.json"
STALE_SEC="${WORKER_LIVENESS_STALE_RESTART_SEC:-180}"
while true; do
  WORKER_PID="$(pgrep -fo '/root/control_plane/.venv/bin/python -m worker_agent.main' || true)"
  if [[ -n "${WORKER_PID}" && -f "${HEARTBEAT_FILE}" ]]; then
    NOW_EPOCH="$(date +%s)"
    FILE_EPOCH="$(stat -c %Y "${HEARTBEAT_FILE}" 2>/dev/null || echo 0)"
    AGE_SEC="$(( NOW_EPOCH - FILE_EPOCH ))"
    if (( AGE_SEC > STALE_SEC )); then
      printf '[%s] stale worker heartbeat (%ss) -> restarting pid=%s\n' "$(date -u +%FT%TZ)" "${AGE_SEC}" "${WORKER_PID}" >> /root/control-plane-logs/worker-watchdog.log
      kill "${WORKER_PID}" >/dev/null 2>&1 || true
      sleep 2
      WORKER_PID=""
    fi
  fi

  if [[ -z "${WORKER_PID}" ]]; then
    set -a
    . ./.env.worker
    set +a
    nohup /root/control_plane/.venv/bin/python -m worker_agent.main \
      >> /root/control-plane-logs/worker-agent.log 2>&1 < /dev/null &
  fi
  sleep 10
done
SH

chmod +x /root/control_plane/runtime/ensure_worker.sh
nohup /root/control_plane/runtime/ensure_worker.sh \
  >/root/control-plane-logs/worker-watchdog.log 2>&1 &

sleep 2
ps -axo pid,command | grep -E 'worker_agent.main|ensure_worker.sh' | grep -v grep
echo "WORKER_AGENT_READY"
