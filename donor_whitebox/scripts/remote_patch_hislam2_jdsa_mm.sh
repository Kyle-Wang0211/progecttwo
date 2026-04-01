#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/root/gs_refs/HI-SLAM2}"
FILE="${ROOT}/hislam2/geom/ba.py"

python3 - <<'PY' "${FILE}"
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
old = """    wJsoT = (alpha * Jso).transpose(2,3)\n    Hs = safe_scatter_add_mat(wJsoT @ Jso, kx, kx, M, M).view(B, M, M, D, D)\n    Es = safe_scatter_add_mat(wJsoT * Jd, kx, kx, M, M).view(B, M, M, D, ht*wd)\n    vs = safe_scatter_add_vec(-wJsoT @ rd[None].unsqueeze(-1), kx, M)\n"""
new = """    wJsoT = (alpha * Jso).transpose(2,3)\n    rd_vec = rd[None].unsqueeze(-1).float()\n    hs_blocks = []\n    vs_blocks = []\n    for b in range(wJsoT.shape[0]):\n        hs_row = []\n        vs_row = []\n        for m_idx in range(wJsoT.shape[1]):\n            lhs = wJsoT[b, m_idx].contiguous().float()\n            rhs = Jso[b, m_idx].contiguous().float()\n            hs_row.append(lhs @ rhs)\n            vs_row.append((-lhs @ rd_vec[b, m_idx]).contiguous())\n        hs_blocks.append(torch.stack(hs_row, dim=0))\n        vs_blocks.append(torch.stack(vs_row, dim=0))\n    Hs = safe_scatter_add_mat(torch.stack(hs_blocks, dim=0), kx, kx, M, M).view(B, M, M, D, D)\n    Es = safe_scatter_add_mat(wJsoT * Jd, kx, kx, M, M).view(B, M, M, D, ht*wd)\n    vs = safe_scatter_add_vec(torch.stack(vs_blocks, dim=0), kx, M)\n"""
if old not in text:
    raise SystemExit(f"expected JDSA block not found in {path}")
path.write_text(text.replace(old, new, 1))
print(path)
PY

sed -n '188,214p' "${FILE}"
