#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/root/gs_refs/WildGS-SLAM.clean}"
TARGET="${ROOT_DIR}/thirdparty/gaussian_splatting/scene/gaussian_model.py"

python3 - <<'PY'
from pathlib import Path
import os

path = Path(os.environ.get("TARGET", "/root/gs_refs/WildGS-SLAM.clean/thirdparty/gaussian_splatting/scene/gaussian_model.py"))
text = path.read_text(encoding="utf-8")

old = """        stds = self.get_scaling[selected_pts_mask].repeat(N, 1)\n        means = torch.zeros((stds.size(0), 3), device=\"cuda\")\n        samples = torch.normal(mean=means, std=stds)\n        rots = build_rotation(self._rotation[selected_pts_mask]).repeat(N, 1, 1)\n        new_xyz = torch.bmm(rots, samples.unsqueeze(-1)).squeeze(-1) + self.get_xyz[\n            selected_pts_mask\n        ].repeat(N, 1)\n"""
mid = """        stds = self.get_scaling[selected_pts_mask].repeat(N, 1)\n        means = torch.zeros((stds.size(0), 3), device=\"cuda\")\n        samples = torch.normal(mean=means, std=stds)\n        rots = build_rotation(self._rotation[selected_pts_mask]).repeat(N, 1, 1)\n        with torch.autocast(device_type=\"cuda\", enabled=False):\n            rots_f = rots.float().contiguous()\n            samples_f = samples.float().unsqueeze(-1).contiguous()\n            new_xyz = torch.stack([\n                torch.matmul(r, s).squeeze(-1)\n                for r, s in zip(rots_f, samples_f)\n            ], dim=0)\n        new_xyz = new_xyz.to(self.get_xyz.dtype) + self.get_xyz[\n            selected_pts_mask\n        ].repeat(N, 1)\n"""
new = """        stds = self.get_scaling[selected_pts_mask].repeat(N, 1)\n        if stds.size(0) == 0:\n            return\n        means = torch.zeros((stds.size(0), 3), device=\"cuda\")\n        samples = torch.normal(mean=means, std=stds)\n        rots = build_rotation(self._rotation[selected_pts_mask]).repeat(N, 1, 1)\n        with torch.autocast(device_type=\"cuda\", enabled=False):\n            rots_f = rots.float().contiguous()\n            samples_f = samples.float().unsqueeze(-1).contiguous()\n            new_xyz = torch.stack([\n                torch.matmul(r, s).squeeze(-1)\n                for r, s in zip(rots_f, samples_f)\n            ], dim=0)\n        new_xyz = new_xyz.to(self.get_xyz.dtype) + self.get_xyz[\n            selected_pts_mask\n        ].repeat(N, 1)\n"""

if old in text:
    text = text.replace(old, new)
elif mid in text:
    text = text.replace(mid, new)
elif new in text:
    pass
else:
    raise SystemExit(f"target block not found in {path}")

path.write_text(text, encoding="utf-8")
print(f"[remote_patch_wildgs_gaussian_fp32] patched {path}")
PY
