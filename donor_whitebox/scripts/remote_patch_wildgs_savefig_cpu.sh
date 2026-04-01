#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/root/gs_refs/WildGS-SLAM.clean}"
FILE="${ROOT}/src/mapper.py"

python3 - "$FILE" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

def replace_once(src: str, old: str, new: str) -> str:
    if new in src:
        return src
    if old not in src:
        raise SystemExit(f"missing target snippet in {path}: {old.strip()}")
    return src.replace(old, new, 1)

old_uncertainty = (
    "            uncertainty_map = self.get_viewpoint_uncertainty_no_grad(viewpoint)\n"
    "            uncertainty_map = uncertainty_map.cpu().squeeze(0)\n"
)
new_uncertainty = (
    "            uncertainty_map = self.get_viewpoint_uncertainty_no_grad(viewpoint)\n"
    "            uncertainty_map = uncertainty_map.detach().cpu()\n"
    "            if uncertainty_map.ndim == 3:\n"
    "                if uncertainty_map.shape[0] == 1:\n"
    "                    uncertainty_map = uncertainty_map.squeeze(0)\n"
    "                elif uncertainty_map.shape[0] == 3:\n"
    "                    uncertainty_map = uncertainty_map.mean(dim=0)\n"
)
text = replace_once(text, old_uncertainty, new_uncertainty)

old_ssim = (
    "            ssim_loss = self._get_uncertainty_ssim_loss_vis(\n"
    "                gt_image, rendered_img, opacity\n"
    "            )\n"
    "            ssim_loss = ssim_loss.cpu().squeeze(0)\n"
)
new_ssim = (
    "            ssim_loss = self._get_uncertainty_ssim_loss_vis(\n"
    "                gt_image, rendered_img, opacity\n"
    "            )\n"
    "            ssim_loss = ssim_loss.detach().cpu()\n"
    "            if ssim_loss.ndim == 3:\n"
    "                if ssim_loss.shape[0] == 1:\n"
    "                    ssim_loss = ssim_loss.squeeze(0)\n"
    "                elif ssim_loss.shape[0] == 3:\n"
    "                    ssim_loss = ssim_loss.mean(dim=0)\n"
)
text = replace_once(text, old_ssim, new_ssim)

old_loop = (
    "        for kf_idx in video_idxs:\n"
    "            self.save_fig_everything(kf_idx, plot_dir)\n"
    "        # Create gif\n"
)
new_loop = (
    "        for kf_idx in video_idxs:\n"
    "            try:\n"
    "                self.save_fig_everything(kf_idx, plot_dir)\n"
    "            except Exception as e:\n"
    "                print(f\"save_fig_everything_failed {kf_idx}: {e}\")\n"
    "        # Create gif\n"
)
text = replace_once(text, old_loop, new_loop)

changed = True
path.write_text(text, encoding="utf-8")
print("patched" if changed else "already_patched", path)
PY
