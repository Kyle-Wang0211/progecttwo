#!/usr/bin/env bash
set -euo pipefail

mkdir -p /root/control-plane-logs

pkill -f jupyter-notebook || true
pkill -f "minio server /root/minio-data" || true
pkill -f "uvicorn app.main:app" || true

sleep 1

nohup /usr/local/bin/minio server /root/minio-data --address :8080 --console-address :9001 \
  </dev/null >/root/control-plane-logs/minio.log 2>&1 &

sleep 3

bash /root/control_plane/scripts/start_control_plane.sh /root/control_plane /root/control-plane-logs 72299
