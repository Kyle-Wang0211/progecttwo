#!/bin/zsh
set -euo pipefail

HOST="${1:?host required}"
PORT="${2:?port required}"
REMOTE_PORT="${3:?remote port required}"
NAME="${4:?name required}"

ssh -o StrictHostKeyChecking=accept-new -p "$PORT" "root@$HOST" '
mkdir -p /root/control_plane/runtime
pkill -f "localhost.run.*127.0.0.1:'"$REMOTE_PORT"'" || true
nohup ssh \
  -o StrictHostKeyChecking=accept-new \
  -o ServerAliveInterval=30 \
  -o ExitOnForwardFailure=yes \
  -R 80:127.0.0.1:'"$REMOTE_PORT"' \
  nokey@localhost.run \
  > /root/control_plane/runtime/'"$NAME"'_tunnel.log 2>&1 < /dev/null &
echo $! > /root/control_plane/runtime/'"$NAME"'_tunnel.pid
sleep 8
cat /root/control_plane/runtime/'"$NAME"'_tunnel.log
'
