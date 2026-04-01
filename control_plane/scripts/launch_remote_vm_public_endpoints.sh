#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${1:-79.112.78.124}"
REMOTE_PORT="${2:-34427}"

ssh -o StrictHostKeyChecking=accept-new -p "${REMOTE_PORT}" "root@${REMOTE_HOST}" 'bash -s' <<'REMOTE'
set -euo pipefail

mkdir -p /root/control_plane/runtime
touch /root/control_plane/runtime/known_hosts

pkill -f "127.0.0.1:8787 nokey@localhost.run" || true
pkill -f "127.0.0.1:9000 nokey@localhost.run" || true

tmux kill-session -t control_tunnel 2>/dev/null || true
tmux kill-session -t minio_tunnel 2>/dev/null || true

tmux new-session -d -s control_tunnel \
  "ssh -o UserKnownHostsFile=/root/control_plane/runtime/known_hosts -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -R 80:127.0.0.1:8787 nokey@localhost.run"

tmux new-session -d -s minio_tunnel \
  "ssh -o UserKnownHostsFile=/root/control_plane/runtime/known_hosts -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -R 80:127.0.0.1:9000 nokey@localhost.run"

sleep 12

CONTROL_CAPTURE="$(tmux capture-pane -pt control_tunnel -S -80 || true)"
MINIO_CAPTURE="$(tmux capture-pane -pt minio_tunnel -S -80 || true)"

CONTROL_URL="$(printf "%s\n" "${CONTROL_CAPTURE}" | grep -Eo "https://[A-Za-z0-9.-]+\.lhr\.life" | head -n1 || true)"
MINIO_URL="$(printf "%s\n" "${MINIO_CAPTURE}" | grep -Eo "https://[A-Za-z0-9.-]+\.lhr\.life" | head -n1 || true)"

printf "CONTROL_URL=%s\n" "${CONTROL_URL}"
printf "MINIO_URL=%s\n" "${MINIO_URL}"

if [[ -z "${CONTROL_URL}" ]]; then
  printf "\nCONTROL_CAPTURE\n%s\n" "${CONTROL_CAPTURE}"
fi

if [[ -z "${MINIO_URL}" ]]; then
  printf "\nMINIO_CAPTURE\n%s\n" "${MINIO_CAPTURE}"
fi
REMOTE
