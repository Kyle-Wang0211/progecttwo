#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/root/gs_refs/WildGS-SLAM.clean}"
TARGET="${ROOT_DIR}/src/modules/droid_net/corr.py"

python3 - <<'PY'
from pathlib import Path
import os

path = Path(os.environ.get("TARGET", "/root/gs_refs/WildGS-SLAM.clean/src/modules/droid_net/corr.py"))
text = path.read_text(encoding="utf-8")

old = """        fmap1 = fmap1.reshape(batch*num, dim, ht*wd) / 4.0\n        fmap2 = fmap2.reshape(batch*num, dim, ht*wd) / 4.0\n\n        corr = torch.matmul(fmap1.transpose(1, 2), fmap2)\n\n        return corr.view(batch, num, ht, wd, ht, wd)\n"""
mid = """        fmap1 = fmap1.reshape(batch*num, dim, ht*wd) / 4.0\n        fmap2 = fmap2.reshape(batch*num, dim, ht*wd) / 4.0\n\n        out_dtype = fmap1.dtype\n        corr = torch.matmul(fmap1.float().transpose(1, 2), fmap2.float()).to(out_dtype)\n\n        return corr.view(batch, num, ht, wd, ht, wd)\n"""
mid2 = """        fmap1 = fmap1.reshape(batch*num, dim, ht*wd) / 4.0\n        fmap2 = fmap2.reshape(batch*num, dim, ht*wd) / 4.0\n\n        out_dtype = fmap1.dtype\n        with torch.autocast(device_type=\"cuda\", enabled=False):\n            corr = torch.matmul(fmap1.float().transpose(1, 2), fmap2.float())\n        corr = corr.to(out_dtype)\n\n        return corr.view(batch, num, ht, wd, ht, wd)\n"""
new = """        fmap1 = fmap1.reshape(batch*num, dim, ht*wd) / 4.0\n        fmap2 = fmap2.reshape(batch*num, dim, ht*wd) / 4.0\n\n        out_dtype = fmap1.dtype\n        with torch.autocast(device_type=\"cuda\", enabled=False):\n            fmap1f = fmap1.float().transpose(1, 2).contiguous()\n            fmap2f = fmap2.float().contiguous()\n            corr = torch.stack([\n                torch.matmul(f1, f2)\n                for f1, f2 in zip(fmap1f, fmap2f)\n            ], dim=0)\n        corr = corr.to(out_dtype)\n\n        return corr.view(batch, num, ht, wd, ht, wd)\n"""

if old in text:
    text = text.replace(old, new)
elif mid in text:
    text = text.replace(mid, new)
elif mid2 in text:
    text = text.replace(mid2, new)
elif new in text:
    pass
else:
    raise SystemExit(f"target block not found in {path}")

path.write_text(text, encoding="utf-8")
print(f"[remote_patch_wildgs_corr_fp32] patched {path}")
PY
