#!/usr/bin/env bash
set -euo pipefail

mkdir -p /root/control-plane-logs /root/control_plane/runtime
touch /root/control_plane/runtime/known_hosts

pkill -f "minio server /root/minio-data" || true
pkill -f "uvicorn app.main:app" || true
pkill -f "127.0.0.1:8787 nokey@localhost.run" || true
pkill -f "127.0.0.1:9000 nokey@localhost.run" || true

tmux kill-session -t control_tunnel 2>/dev/null || true
tmux kill-session -t minio_tunnel 2>/dev/null || true

sleep 1

set -a
. /root/control_plane/.env.runtime
set +a

nohup env \
  MINIO_ROOT_USER="${CONTROL_PLANE_OBJECT_STORAGE_ACCESS_KEY_ID}" \
  MINIO_ROOT_PASSWORD="${CONTROL_PLANE_OBJECT_STORAGE_SECRET_ACCESS_KEY}" \
  /usr/local/bin/minio server /root/minio-data --address :9000 --console-address :9001 \
  </dev/null >/root/control-plane-logs/minio.log 2>&1 &

sleep 3

bash /root/control_plane/scripts/start_control_plane.sh /root/control_plane /root/control-plane-logs 8787

tmux new-session -d -s control_tunnel \
  "ssh -o UserKnownHostsFile=/root/control_plane/runtime/known_hosts -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -R 80:127.0.0.1:8787 nokey@localhost.run"

tmux new-session -d -s minio_tunnel \
  "ssh -o UserKnownHostsFile=/root/control_plane/runtime/known_hosts -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -R 80:127.0.0.1:9000 nokey@localhost.run"

sleep 12

CONTROL_CAPTURE="$(tmux capture-pane -pt control_tunnel -S -80 || true)"
MINIO_CAPTURE="$(tmux capture-pane -pt minio_tunnel -S -80 || true)"

CONTROL_URL="$(printf "%s\n" "${CONTROL_CAPTURE}" | grep -Eo "[A-Za-z0-9.-]+\\.lhr\\.life" | head -n1 || true)"
MINIO_URL="$(printf "%s\n" "${MINIO_CAPTURE}" | grep -Eo "[A-Za-z0-9.-]+\\.lhr\\.life" | head -n1 || true)"

if [[ -z "${CONTROL_URL}" || -z "${MINIO_URL}" ]]; then
  echo "FAILED_TO_PARSE_TUNNEL_URLS"
  echo "CONTROL_CAPTURE_START"
  printf "%s\n" "${CONTROL_CAPTURE}"
  echo "CONTROL_CAPTURE_END"
  echo "MINIO_CAPTURE_START"
  printf "%s\n" "${MINIO_CAPTURE}"
  echo "MINIO_CAPTURE_END"
  exit 1
fi

python3 - <<PY
from pathlib import Path
env_path = Path("/root/control_plane/.env.runtime")
lines = env_path.read_text().splitlines()
updates = {
    "CONTROL_PLANE_PUBLIC_BASE_URL": "https://${CONTROL_URL}",
    "CONTROL_PLANE_OBJECT_STORAGE_ENDPOINT_URL": "https://${MINIO_URL}",
    "CONTROL_PLANE_OBJECT_STORAGE_PUBLIC_BASE_URL": "https://${MINIO_URL}/aether-control",
}
new_lines = []
for line in lines:
    replaced = False
    for key, value in updates.items():
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            replaced = True
            break
    if not replaced:
        new_lines.append(line)
env_path.write_text("\n".join(new_lines) + "\n")
PY

bash /root/control_plane/scripts/start_control_plane.sh /root/control_plane /root/control-plane-logs 8787 >/dev/null

printf "CONTROL_URL=https://%s\n" "${CONTROL_URL}"
printf "MINIO_URL=https://%s\n" "${MINIO_URL}"
