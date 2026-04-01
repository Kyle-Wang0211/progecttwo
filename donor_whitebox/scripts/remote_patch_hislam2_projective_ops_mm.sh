#!/usr/bin/env bash
set -euo pipefail

REPO="${1:-/root/gs_refs/HI-SLAM2}"
TARGET="${REPO}/hislam2/geom/projective_ops.py"

python3 - "${TARGET}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

helper = """

def _stable_last2_mm(a, b):
    if a.ndim < 3 or b.ndim < 3 or a.shape[:-2] != b.shape[:-2]:
        return torch.matmul(a, b)
    lead = 1
    for dim in a.shape[:-2]:
        lead *= dim
    if lead == 0:
        return torch.empty((*a.shape[:-2], a.shape[-2], b.shape[-1]), device=a.device, dtype=a.dtype)
    a_flat = a.reshape(lead, a.shape[-2], a.shape[-1])
    b_flat = b.reshape(lead, b.shape[-2], b.shape[-1])
    out = []
    for idx in range(lead):
        out.append(a_flat[idx] @ b_flat[idx])
    return torch.stack(out, dim=0).reshape(*a.shape[:-2], a.shape[-2], b.shape[-1])
"""

if "def _stable_last2_mm(" not in text:
    marker = "\nMIN_DEPTH = 0.2\n"
    if marker not in text:
        raise SystemExit("failed to find insertion marker in projective_ops.py")
    text = text.replace(marker, helper + marker, 1)

old = """        Jj = torch.matmul(Jp, Ja)
        Ji = -Gij[:,:,None,None,None].adjT(Jj)

        Jz = Gij[:,:,None,None] * Jz
        Jz = torch.matmul(Jp, Jz.unsqueeze(-1))
"""

new = """        Jj = _stable_last2_mm(Jp, Ja)
        Ji = -Gij[:,:,None,None,None].adjT(Jj)

        Jz = Gij[:,:,None,None] * Jz
        Jz = _stable_last2_mm(Jp, Jz.unsqueeze(-1))
"""

if old not in text:
    raise SystemExit("failed to find target block in projective_ops.py")

path.write_text(text.replace(old, new, 1))
PY
