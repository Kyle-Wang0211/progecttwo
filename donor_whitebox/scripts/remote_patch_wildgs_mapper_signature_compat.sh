#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/root/gs_refs/WildGS-SLAM.clean}"
FILE="${ROOT}/src/utils/slam_utils.py"

python3 - "$FILE" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "def get_loss_mapping(config, image, depth, viewpoint, initialization=False):"
new = "def get_loss_mapping(config, image, depth, viewpoint, opacity=None, initialization=False):"
if new in text:
    print("already_patched")
    raise SystemExit(0)
if old not in text:
    raise SystemExit(f"missing signature: {old}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("patched", path)
PY
