#!/usr/bin/env bash
set -euo pipefail

TARGET=/root/gs_refs/WildGS-SLAM/src/motion_filter.py

/venv/hislam2/bin/python - <<'PY'
from pathlib import Path

path = Path("/root/gs_refs/WildGS-SLAM/src/motion_filter.py")
text = path.read_text(encoding="utf-8")

if "self.use_precomputed_mono_depth" not in text:
    text = text.replace(
        "import torch\nimport lietorch\n",
        "import os\nimport torch\nimport lietorch\n",
    )
    text = text.replace(
        "        self.uncertainty_aware = cfg['tracking'][\"uncertainty_params\"]['activate']\n        self.save_dir = cfg['data']['output'] + '/' + cfg['scene']\n        self.metric_depth_estimator = get_metric_depth_estimator(cfg)\n",
        "        self.uncertainty_aware = cfg['tracking'][\"uncertainty_params\"]['activate']\n        self.save_dir = cfg['data']['output'] + '/' + cfg['scene']\n        self.use_precomputed_mono_depth = cfg['tracking'].get('use_precomputed_mono_depth', False)\n        self.metric_depth_estimator = None if self.use_precomputed_mono_depth else get_metric_depth_estimator(cfg)\n",
    )
    marker = "    @torch.amp.autocast('cuda',enabled=True)\n    @torch.no_grad()\n    def track(self, tstamp, image, intrinsics=None):\n"
    helper = """    def _get_metric_depth(self, tstamp, image):\n        idx = int(tstamp)\n        depth_path = f\"{self.save_dir}/mono_priors/depths/{idx:05d}.npy\"\n        if self.use_precomputed_mono_depth and os.path.exists(depth_path):\n            return load_metric_depth(idx, self.save_dir).to(self.device)\n        return predict_metric_depth(self.metric_depth_estimator, tstamp, image, self.cfg, self.device)\n\n"""
    text = text.replace(marker, helper + marker)
    text = text.replace(
        "            mono_depth = predict_metric_depth(self.metric_depth_estimator,tstamp,image,self.cfg,self.device)\n",
        "            mono_depth = self._get_metric_depth(tstamp, image)\n",
    )

path.write_text(text, encoding="utf-8")
print("[remote_patch_wildgs_precomputed_depth] patched", path)
PY
