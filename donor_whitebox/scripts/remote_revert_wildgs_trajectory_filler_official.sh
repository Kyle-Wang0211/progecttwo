#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-/root/gs_refs/WildGS-SLAM.clean}"
TARGET="${REPO}/src/trajectory_filler.py"

cd "${REPO}"
git show HEAD:src/trajectory_filler.py > "${TARGET}"
git diff -- src/trajectory_filler.py
