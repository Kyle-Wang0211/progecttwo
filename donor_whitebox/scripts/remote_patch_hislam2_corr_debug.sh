#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/root/gs_refs/HI-SLAM2}"
MOTION_FILE="${ROOT}/hislam2/motion_filter.py"
CORR_FILE="${ROOT}/hislam2/modules/corr.py"

python3 - <<'PY' "${MOTION_FILE}" "${CORR_FILE}"
from pathlib import Path
import sys

motion = Path(sys.argv[1])
corr = Path(sys.argv[2])

motion_text = motion.read_text()
motion_old = """            # index correlation volume\n            coords0 = pops.coords_grid(ht, wd, device=self.device)[None,None]\n            corr = CorrBlock(self.fmap[None,[0]], gmap[None,[0]])(coords0)\n"""
motion_new = """            # index correlation volume\n            coords0 = pops.coords_grid(ht, wd, device=self.device)[None,None]\n            print(f\"[motion-filter-debug] self.fmap={tuple(self.fmap.shape)} dtype={self.fmap.dtype} gmap={tuple(gmap.shape)} dtype={gmap.dtype} coords0={tuple(coords0.shape)} ht={ht} wd={wd}\", flush=True)\n            corr = CorrBlock(self.fmap[None,[0]], gmap[None,[0]])(coords0)\n"""
if motion_old not in motion_text:
    raise SystemExit(f"expected motion_filter block not found in {motion}")
motion.write_text(motion_text.replace(motion_old, motion_new, 1))

corr_text = corr.read_text()
corr_old = """        corr = torch.matmul(fmap1.transpose(1,2), fmap2)\n"""
corr_new = """        print(f\"[corr-debug] fmap1={tuple(fmap1.shape)} dtype={fmap1.dtype} contiguous={fmap1.is_contiguous()} fmap2={tuple(fmap2.shape)} dtype={fmap2.dtype} contiguous={fmap2.is_contiguous()}\", flush=True)\n        corr = torch.matmul(fmap1.transpose(1,2), fmap2)\n"""
if corr_old not in corr_text:
    raise SystemExit(f"expected corr matmul block not found in {corr}")
corr.write_text(corr_text.replace(corr_old, corr_new, 1))

print(motion)
print(corr)
PY

sed -n '96,112p' "${MOTION_FILE}"
echo "---"
sed -n '62,76p' "${CORR_FILE}"
