#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
from pathlib import Path

path = Path("/root/control_plane/.env.worker")
text = path.read_text()
repls = {
    "CONTROL_PLANE_BASE_URL=": "CONTROL_PLANE_BASE_URL=https://api.aether-3d.com",
    "CONTROL_PLANE_OBJECT_STORAGE_ENDPOINT_URL=": "CONTROL_PLANE_OBJECT_STORAGE_ENDPOINT_URL=https://sfo3.digitaloceanspaces.com",
    "CONTROL_PLANE_OBJECT_STORAGE_REGION=": "CONTROL_PLANE_OBJECT_STORAGE_REGION=sfo3",
    "CONTROL_PLANE_OBJECT_STORAGE_PUBLIC_BASE_URL=": "CONTROL_PLANE_OBJECT_STORAGE_PUBLIC_BASE_URL=https://sfo3.digitaloceanspaces.com/aether-control",
    "CONTROL_PLANE_OBJECT_STORAGE_ADDRESSING_STYLE=": "CONTROL_PLANE_OBJECT_STORAGE_ADDRESSING_STYLE=path",
    "WORKER_DONOR_PREP_SCRIPT=": "WORKER_DONOR_PREP_SCRIPT=/root/donor_whitebox/scripts/remote_run_hislam2_realvideo_prep_audit_phase.sh",
    "WORKER_DONOR_PREP_PYTHON=": "WORKER_DONOR_PREP_PYTHON=/venv/hislam2/bin/python",
    "WORKER_DONOR_TRAIN_SCRIPT=": "WORKER_DONOR_TRAIN_SCRIPT=/root/donor_whitebox/scripts/remote_run_hislam2_realvideo_train_phase.sh",
    "WORKER_SKIP_TRAINING_PROBE_GATE=": "WORKER_SKIP_TRAINING_PROBE_GATE=1",
    "WORKER_STREAM_PREWARM_ENABLED=": "WORKER_STREAM_PREWARM_ENABLED=0",
    "WORKER_STREAM_PROMOTE_LIVE_SFM_AFTER_UPLOAD=": "WORKER_STREAM_PROMOTE_LIVE_SFM_AFTER_UPLOAD=0",
    "WORKER_STREAM_PROMOTE_LIVE_SFM_MIN_SELECTED_FRAMES=": "WORKER_STREAM_PROMOTE_LIVE_SFM_MIN_SELECTED_FRAMES=20",
    "WORKER_STREAM_PROMOTE_LIVE_SFM_MIN_REGISTERED_IMAGES=": "WORKER_STREAM_PROMOTE_LIVE_SFM_MIN_REGISTERED_IMAGES=8",
    "WORKER_STREAM_TRAIN_SEED_ENABLED=": "WORKER_STREAM_TRAIN_SEED_ENABLED=0",
    "WORKER_STREAM_TRAIN_SEED_POLL_INTERVAL_SEC=": "WORKER_STREAM_TRAIN_SEED_POLL_INTERVAL_SEC=1",
    "WORKER_STREAM_TRAIN_SEED_MIN_SELECTED_FRAMES=": "WORKER_STREAM_TRAIN_SEED_MIN_SELECTED_FRAMES=20",
    "WORKER_STREAM_TRAIN_SEED_MIN_REGISTERED_IMAGES=": "WORKER_STREAM_TRAIN_SEED_MIN_REGISTERED_IMAGES=8",
    "WORKER_STREAM_TRAIN_SEED_FINALIZE_GRACE_SEC=": "WORKER_STREAM_TRAIN_SEED_FINALIZE_GRACE_SEC=2",
    "WORKER_STREAM_TRAIN_SEED_SKIP_TSDF=": "WORKER_STREAM_TRAIN_SEED_SKIP_TSDF=1",
    "WORKER_STREAM_ARTIFACT_STAGE_ENABLED=": "WORKER_STREAM_ARTIFACT_STAGE_ENABLED=0",
    "WORKER_STREAM_ARTIFACT_STAGE_MIN_STABLE_POLLS=": "WORKER_STREAM_ARTIFACT_STAGE_MIN_STABLE_POLLS=2",
    "WORKER_STREAM_ARTIFACT_PUBLISH_EARLY=": "WORKER_STREAM_ARTIFACT_PUBLISH_EARLY=0",
    "WORKER_MAX_PARALLEL_PREP_JOBS=": "WORKER_MAX_PARALLEL_PREP_JOBS=1",
    "WORKER_MAX_PARALLEL_GPU_JOBS=": "WORKER_MAX_PARALLEL_GPU_JOBS=1",
    "WORKER_MAX_ACTIVE_JOBS=": "WORKER_MAX_ACTIVE_JOBS=1",
    "WORKER_SCHEDULER_TICK_INTERVAL_SEC=": "WORKER_SCHEDULER_TICK_INTERVAL_SEC=2",
    "WORKER_STREAM_LIVE_SFM_ENABLED=": "WORKER_STREAM_LIVE_SFM_ENABLED=0",
    "WORKER_STREAM_PREWARM_POLL_INTERVAL_SEC=": "WORKER_STREAM_PREWARM_POLL_INTERVAL_SEC=1",
    "WORKER_STREAM_LIVE_SFM_MIN_FRAMES=": "WORKER_STREAM_LIVE_SFM_MIN_FRAMES=24",
    "WORKER_STREAM_LIVE_SFM_MIN_NEW_FRAMES=": "WORKER_STREAM_LIVE_SFM_MIN_NEW_FRAMES=12",
    "WORKER_STREAM_LIVE_SFM_MAX_FRAMES_CAP=": "WORKER_STREAM_LIVE_SFM_MAX_FRAMES_CAP=64",
    "WORKER_STREAM_LIVE_SFM_FINALIZE_GRACE_SEC=": "WORKER_STREAM_LIVE_SFM_FINALIZE_GRACE_SEC=20",
    "AETHER_AUTOFALLBACK_POLICY=": "AETHER_AUTOFALLBACK_POLICY=official_default",
}
out = []
seen = set()
for line in text.splitlines():
    replaced = False
    for prefix, newline in repls.items():
        if line.startswith(prefix):
            out.append(newline)
            seen.add(prefix)
            replaced = True
            break
    if not replaced:
        out.append(line)
for prefix, newline in repls.items():
    if prefix not in seen:
        out.append(newline)
path.write_text("\n".join(out) + "\n")
PY

pkill -f 'worker_agent.main' || true
sleep 1
mkdir -p /root/control_plane/runtime

cat >/root/control_plane/runtime/ensure_worker.sh <<'SH'
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
      printf '[%s] stale worker heartbeat (%ss) -> restarting pid=%s\n' "$(date -u +%FT%TZ)" "${AGE_SEC}" "${WORKER_PID}" >> /root/control_plane/runtime/worker_watchdog.log
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
      >> /root/control_plane/runtime/worker_agent.supervised.log 2>&1 < /dev/null &
  fi
  sleep 10
done
SH

chmod +x /root/control_plane/runtime/ensure_worker.sh
pkill -f 'ensure_worker.sh' || true
nohup /root/control_plane/runtime/ensure_worker.sh \
  >> /root/control_plane/runtime/worker_watchdog.log 2>&1 < /dev/null &

cat /root/control_plane/.env.worker
pgrep -af 'worker_agent.main|ensure_worker.sh' || true
