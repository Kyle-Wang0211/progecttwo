#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/root/donor_whitebox}"
RUN_NAME="${1:?usage: remote_start_realvideo_autofallback_run.sh RUN_NAME VIDEO_PATH}"
VIDEO_PATH="${2:?usage: remote_start_realvideo_autofallback_run.sh RUN_NAME VIDEO_PATH}"

mkdir -p "${ROOT}/logs"

pkill -f "prepare_real_video_owndata.py" >/dev/null 2>&1 || true
pkill -f "demo.py --imagedir" >/dev/null 2>&1 || true
pkill -f "tsdf_integrate" >/dev/null 2>&1 || true

cd "${ROOT}"
export RUN_NAME
export REAL_VIDEO_PATH="${VIDEO_PATH}"
# Force the worker into the original HI-SLAM mainline route only.
export AETHER_AUTOFALLBACK_POLICY="${AETHER_AUTOFALLBACK_POLICY:-official_default}"

nohup bash "${ROOT}/scripts/remote_run_hislam2_realvideo_autofallback.sh" \
  > "${ROOT}/logs/${RUN_NAME}.launch.log" 2>&1 < /dev/null &

echo "${!}"
