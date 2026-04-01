#!/usr/bin/env bash
set -euo pipefail

REPO="${1:-/root/gs_refs/HI-SLAM2}"
TARGET="${REPO}/hislam2/pgo_buffer.py"

python3 - "${TARGET}" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text()

helper = """

def _stable_pairwise_mm(a, b):
    if a.ndim < 3 or b.ndim < 3:
        return torch.matmul(a, b)
    try:
        lead_shape = torch.broadcast_shapes(a.shape[:-2], b.shape[:-2])
    except RuntimeError:
        return torch.matmul(a, b)
    lead = 1
    for dim in lead_shape:
        lead *= dim
    if lead == 0:
        return torch.empty((*lead_shape, a.shape[-2], b.shape[-1]), device=a.device, dtype=a.dtype)
    a_flat = a.expand(*lead_shape, a.shape[-2], a.shape[-1]).reshape(lead, a.shape[-2], a.shape[-1])
    b_flat = b.expand(*lead_shape, b.shape[-2], b.shape[-1]).reshape(lead, b.shape[-2], b.shape[-1])
    out = []
    for idx in range(lead):
        out.append(a_flat[idx] @ b_flat[idx])
    return torch.stack(out, dim=0).reshape(*lead_shape, a.shape[-2], b.shape[-1])


def _stable_pairwise_inv(x):
    if x.ndim < 3:
        return torch.linalg.inv(x)
    lead = 1
    for dim in x.shape[:-2]:
        lead *= dim
    if lead == 0:
        return torch.empty_like(x)
    x_flat = x.reshape(lead, x.shape[-2], x.shape[-1])
    out = []
    for idx in range(lead):
        out.append(torch.linalg.inv(x_flat[idx]))
    return torch.stack(out, dim=0).reshape_as(x)
"""

helper_pattern = re.compile(
    r"\n\ndef _stable_pairwise_mm\(a, b\):.*?\n\ndef _stable_pairwise_inv\(x\):.*?(?=\n\neps = 1e-8\n)",
    re.S,
)

if helper_pattern.search(text):
    text = helper_pattern.sub(helper.rstrip("\n"), text, count=1)
elif "def _stable_pairwise_mm(" not in text:
    marker = "\n\neps = 1e-8\n"
    if marker not in text:
        raise SystemExit("failed to find insertion marker in pgo_buffer.py")
    text = text.replace(marker, helper + marker, 1)

old_top = """    chi2 = torch.sum(r.transpose(2, 3) @ r)
    chi2_scaled = torch.sum(r.transpose(2, 3) @ infos @ r)

    wJiT = ((pw * Ji.double()).transpose(2, 3) @ infos.double()).float()
    wJjT = ((pw * Jj.double()).transpose(2, 3) @ infos.double()).float()
    Hsp = torch.stack([torch.matmul(wJiT, Ji), torch.matmul(wJiT, Jj), torch.matmul(wJjT, Ji), torch.matmul(wJjT, Jj)])    # 4x1xNx7x7
    vsp = -torch.stack([torch.matmul(wJiT, r), torch.matmul(wJjT, r)]).squeeze(-1)  # 2x1xNx7
"""

new_top = """    chi2 = torch.sum(_stable_pairwise_mm(r.transpose(2, 3), r))
    chi2_scaled = torch.sum(_stable_pairwise_mm(r.transpose(2, 3), _stable_pairwise_mm(infos, r)))

    wJiT = _stable_pairwise_mm((pw * Ji.double()).transpose(2, 3), infos.double()).float()
    wJjT = _stable_pairwise_mm((pw * Jj.double()).transpose(2, 3), infos.double()).float()
    Hsp = torch.stack([
        _stable_pairwise_mm(wJiT, Ji),
        _stable_pairwise_mm(wJiT, Jj),
        _stable_pairwise_mm(wJjT, Ji),
        _stable_pairwise_mm(wJjT, Jj),
    ])    # 4x1xNx7x7
    vsp = -torch.stack([
        _stable_pairwise_mm(wJiT, r),
        _stable_pairwise_mm(wJjT, r),
    ]).squeeze(-1)  # 2x1xNx7
"""

if old_top in text:
    text = text.replace(old_top, new_top, 1)
elif new_top not in text:
    raise SystemExit("failed to find top PGBA block in pgo_buffer.py")

old = """            Hjj = torch.matmul(wJjT, Jj) + 1e-4*torch.eye(6, device='cuda')[None, None]
            vj = torch.matmul(wJjT, r)

            Hinv = torch.linalg.inv(Hjj)
            dx = Hinv @ vj
            rel_poses = rel_poses.retr(dx.squeeze(-1))

        V = Jj @ dx - r
        sig2 = (w * V).transpose(2, 3) @ V
"""

new = """            Hjj = _stable_pairwise_mm(wJjT, Jj) + 1e-4*torch.eye(6, device='cuda')[None, None]
            vj = _stable_pairwise_mm(wJjT, r)

            Hinv = _stable_pairwise_inv(Hjj)
            dx = _stable_pairwise_mm(Hinv, vj)
            rel_poses = rel_poses.retr(dx.squeeze(-1))

        V = _stable_pairwise_mm(Jj, dx) - r
        sig2 = _stable_pairwise_mm((w * V).transpose(2, 3), V)
"""

if old in text:
    text = text.replace(old, new, 1)
elif new not in text:
    raise SystemExit("failed to find target block in pgo_buffer.py")

path.write_text(text)
PY
