#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-/root/gs_refs/WildGS-SLAM.clean}"
TARGET="${REPO}/src/motion_filter.py"

cd "${REPO}"
git show HEAD:src/motion_filter.py > "${TARGET}"
git diff -- src/motion_filter.py
