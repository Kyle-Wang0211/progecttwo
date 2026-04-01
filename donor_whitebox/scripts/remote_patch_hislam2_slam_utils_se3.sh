#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/root/gs_refs/HI-SLAM2}"
FILE="${ROOT}/hislam2/gaussian/utils/slam_utils.py"

python3 - <<'PY' "${FILE}"
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
old = """    t = V(theta) @ rho\n"""
new = """    v_theta = V(theta).contiguous().float()\n    rho_vec = rho.contiguous().float().unsqueeze(0)\n    t = torch.sum(v_theta * rho_vec, dim=1).to(device=device, dtype=dtype)\n"""
if old not in text:
    raise SystemExit(f"expected SE3_exp translation block not found in {path}")
path.write_text(text.replace(old, new, 1))
print(path)
PY

sed -n '54,72p' "${FILE}"
