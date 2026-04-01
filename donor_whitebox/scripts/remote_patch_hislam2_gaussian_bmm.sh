#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/root/gs_refs/HI-SLAM2}"
FILE="${ROOT}/hislam2/gaussian/scene/gaussian_model.py"

python3 - <<'PY' "${FILE}"
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
old = """        new_xyz = torch.bmm(rots, samples.unsqueeze(-1)).squeeze(-1) + self.get_xyz[\n            selected_pts_mask\n        ].repeat(N, 1)\n"""
new = """        if rots.shape[0] == 0:\n            rotated_samples = torch.empty((0, 3), device=\"cuda\", dtype=torch.float)\n        else:\n            rotated_samples = torch.stack([\n                rots[i].float() @ samples[i].float().unsqueeze(-1)\n                for i in range(rots.shape[0])\n            ], dim=0).squeeze(-1)\n        new_xyz = rotated_samples + self.get_xyz[selected_pts_mask].repeat(N, 1)\n"""
if old not in text:
    raise SystemExit(f"expected gaussian densify block not found in {path}")
path.write_text(text.replace(old, new, 1))
print(path)
PY

sed -n '482,500p' "${FILE}"
