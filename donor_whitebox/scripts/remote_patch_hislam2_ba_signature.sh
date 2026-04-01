#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/root/gs_refs/HI-SLAM2}"
FILE="${ROOT}/hislam2/depth_video.py"

python3 - <<'PY' "${FILE}"
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
old = """            droid_backends.ba(self.poses, self.disps, self.intrinsics[0], target, weight, eta, ii, jj, t0, t1, itrs, lm, ep, motion_only)\n"""
new = """            droid_backends.ba(self.poses, self.disps, self.intrinsics[0], target, weight, eta, ii, jj, self.disps_prior, t0, t1, itrs, lm, ep, motion_only, use_mono)\n"""
if old not in text:
    raise SystemExit(f"expected call site not found in {path}")
path.write_text(text.replace(old, new, 1))
print(path)
PY

sed -n '224,238p' "${FILE}"
