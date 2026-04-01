#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "usage: $0 <name> <local_port> <log_file> <pid_file>" >&2
  exit 2
fi

NAME="$1"
LOCAL_PORT="$2"
LOG_FILE="$3"
PID_FILE="$4"
RUNTIME_DIR="$(dirname "$LOG_FILE")"
KNOWN_HOSTS="$RUNTIME_DIR/known_hosts"

mkdir -p "$RUNTIME_DIR"
touch "$KNOWN_HOSTS"

pkill -f "127.0.0.1:${LOCAL_PORT} nokey@localhost.run" || true
rm -f "$LOG_FILE" "$PID_FILE"

setsid nohup script -q -c \
  "ssh -o UserKnownHostsFile=${KNOWN_HOSTS} -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -R 80:127.0.0.1:${LOCAL_PORT} nokey@localhost.run" \
  "$LOG_FILE" >/dev/null 2>&1 < /dev/null &
echo $! > "$PID_FILE"

sleep 10

URL="$(grep -Eo 'https://[A-Za-z0-9.-]+\.lhr\.life' "$LOG_FILE" | head -n1 || true)"
if [[ -n "$URL" ]]; then
  echo "$NAME url: $URL"
else
  echo "$NAME url: <missing>"
  sed -n '1,160p' "$LOG_FILE" || true
  exit 1
fi
