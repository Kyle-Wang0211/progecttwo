#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/root/gs_refs/MonoGS}"

git -C "${ROOT}" checkout -- slam.py utils/slam_frontend.py

bash /root/donor_whitebox/scripts/remote_patch_monogs_single_thread_backend.sh
bash /root/donor_whitebox/scripts/remote_restore_monogs_official_kf_logic.sh

git -C "${ROOT}" diff --name-only
