#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/root/gs_refs/HI-SLAM2}"
FILE="${ROOT}/hislam2/geom/chol.py"

python3 - <<'PY' "${FILE}"
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

helper_anchor = "import geom.projective_ops as pops\n"
helper_block = """import geom.projective_ops as pops\n\n\ndef _runtime_safe_left_solve(L, rhs):\n    if rhs.shape[0] == 0:\n        return rhs\n\n    if not L.is_cuda:\n        return torch.linalg.solve(L, rhs)\n\n    solved = []\n    for b in range(L.shape[0]):\n        lb = L[b].contiguous().float().cpu()\n        rhsb = rhs[b].contiguous().float().cpu()\n        solved.append(torch.linalg.solve(lb, rhsb).to(rhs.device, dtype=rhs.dtype))\n    return torch.stack(solved, dim=0)\n"""
if "_runtime_safe_left_solve" not in text:
    if helper_anchor not in text:
        raise SystemExit(f"expected import anchor not found in {path}")
    text = text.replace(helper_anchor, helper_block, 1)

replacements = [
    (
        "    F = torch.linalg.inv(L) @ (E * Q[...,0])\n",
        "    F = _runtime_safe_left_solve(L, E * Q[...,0])\n",
    ),
    (
        "    F = torch.linalg.inv(L) @ (E * Q[...,0])\n",
        "    F = _runtime_safe_left_solve(L, E * Q[...,0])\n",
    ),
]

for old, new in replacements:
    if old not in text:
        raise SystemExit(f"expected chol solve block not found in {path}")
    text = text.replace(old, new, 1)

path.write_text(text)
print(path)
PY

sed -n '1,130p' "${FILE}"
