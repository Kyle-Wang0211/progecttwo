#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/root/gs_refs/HI-SLAM2}"
FILE="${ROOT}/hislam2/gaussian/utils/slam_utils.py"

python3 - <<'PY' "${FILE}"
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
old = """def get_loss_normal(depth_mean, viewpoint):\n    prior_normal = torch.as_tensor(viewpoint.normal, dtype=torch.float32).clone()\n    prior_normal = prior_normal.to(device=depth_mean.device, non_blocking=False)\n    prior_normal = prior_normal.reshape(3, *depth_mean.shape[-2:]).permute(1,2,0)\n    prior_normal_normalized = torch.nn.functional.normalize(prior_normal, dim=-1)\n\n    normal_mean, _ = depth_to_normal(viewpoint, depth_mean, world_frame=False)\n    normal_error = 1 - (prior_normal_normalized * normal_mean).sum(dim=-1)\n    normal_error[prior_normal.norm(dim=-1) < 0.2] = 0\n    return normal_error.mean()\n"""
new = """def get_loss_normal(depth_mean, viewpoint):\n    prior_src = viewpoint.normal\n    if isinstance(prior_src, torch.Tensor):\n        prior_cpu = prior_src.detach().to(device=\"cpu\", dtype=torch.float32).contiguous()\n    else:\n        prior_cpu = torch.from_numpy(np.array(prior_src, copy=True)).to(dtype=torch.float32)\n    prior_normal = prior_cpu.reshape(3, *depth_mean.shape[-2:]).permute(1, 2, 0).contiguous()\n    prior_normal = prior_normal.to(device=depth_mean.device, dtype=torch.float32, non_blocking=False)\n    prior_normal_normalized = torch.nn.functional.normalize(prior_normal, dim=-1)\n\n    normal_mean, _ = depth_to_normal(viewpoint, depth_mean, world_frame=False)\n    normal_error = 1 - (prior_normal_normalized * normal_mean).sum(dim=-1)\n    normal_error[prior_normal.norm(dim=-1) < 0.2] = 0\n    return normal_error.mean()\n"""
if old not in text:
    raise SystemExit(f"expected get_loss_normal block not found in {path}")
path.write_text(text.replace(old, new, 1))
print(path)
PY

sed -n '208,228p' "${FILE}"
