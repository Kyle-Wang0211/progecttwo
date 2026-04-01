#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/root/gs_refs/HI-SLAM2}"
MOTION_FILE="${ROOT}/hislam2/motion_filter.py"
CORR_FILE="${ROOT}/hislam2/modules/corr.py"

python3 - <<'PY' "${MOTION_FILE}" "${CORR_FILE}"
from pathlib import Path
import re
import sys

motion = Path(sys.argv[1])
corr = Path(sys.argv[2])

motion_text = motion.read_text()
motion_debug = """            # index correlation volume\n            coords0 = pops.coords_grid(ht, wd, device=self.device)[None,None]\n            print(f\"[motion-filter-debug] self.fmap={tuple(self.fmap.shape)} dtype={self.fmap.dtype} gmap={tuple(gmap.shape)} dtype={gmap.dtype} coords0={tuple(coords0.shape)} ht={ht} wd={wd}\", flush=True)\n            corr = CorrBlock(self.fmap[None,[0]], gmap[None,[0]])(coords0)\n"""
motion_clean = """            # index correlation volume\n            coords0 = pops.coords_grid(ht, wd, device=self.device)[None,None]\n            corr = CorrBlock(self.fmap[None,[0]], gmap[None,[0]])(coords0)\n"""
if motion_debug in motion_text:
    motion.write_text(motion_text.replace(motion_debug, motion_clean, 1))

corr_text = corr.read_text()
corr_pattern = re.compile(
    r"""    @staticmethod\n    def corr\(fmap1, fmap2\):\n        \"\"\" all-pairs correlation \"\"\"\n        batch, num, dim, ht, wd = fmap1\.shape\n        fmap1 = fmap1\.reshape\(batch\*num, dim, ht\*wd\) / 4\.0\n        fmap2 = fmap2\.reshape\(batch\*num, dim, ht\*wd\) / 4\.0\n(?:        .*\n)+?        return corr\.view\(batch, num, ht, wd, ht, wd\)\n""",
    re.MULTILINE,
)
corr_clean = """    @staticmethod\n    def corr(fmap1, fmap2):\n        \"\"\" all-pairs correlation \"\"\"\n        batch, num, dim, ht, wd = fmap1.shape\n        fmap1 = fmap1.reshape(batch*num, dim, ht*wd) / 4.0\n        fmap2 = fmap2.reshape(batch*num, dim, ht*wd) / 4.0\n\n        with torch.amp.autocast('cuda', enabled=False):\n            lhs = fmap1.transpose(1,2).contiguous().float()\n            rhs = fmap2.contiguous().float()\n            corr = torch.bmm(lhs, rhs)\n        return corr.view(batch, num, ht, wd, ht, wd)\n"""
new_corr_text, n = corr_pattern.subn(corr_clean, corr_text, count=1)
if n != 1:
    raise SystemExit(f"expected corr() block not found in {corr}")
corr.write_text(new_corr_text)

print(motion)
print(corr)
PY

sed -n '100,110p' "${MOTION_FILE}"
echo "---"
sed -n '64,74p' "${CORR_FILE}"
