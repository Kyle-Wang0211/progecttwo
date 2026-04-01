#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_URL="${BASE_URL:-https://api.aether-3d.com}"
FILE_PATH="${FILE_PATH:-/Users/kaidongwang/Documents/progecttwo/background_upload_broker/runtime/uploads/job_1744ed3710504fa7b805408c163358a4_66280ABA-7119-4AC7-8E31-FB639C2FDE97.MOV}"
CONTENT_TYPE="${CONTENT_TYPE:-video/quicktime}"
CLIENT_RECORD_ID="${CLIENT_RECORD_ID:-codex-known-good-1744-$(date +%s)}"

python3 "${SCRIPT_DIR}/public_job_e2e_regression.py" upload-only \
  --base-url "${BASE_URL}" \
  --file "${FILE_PATH}" \
  --content-type "${CONTENT_TYPE}" \
  --client-record-id "${CLIENT_RECORD_ID}"
