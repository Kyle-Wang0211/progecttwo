#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(description="Decide whether a HI-SLAM2 probe/full run passes gating.")
    p.add_argument("--summary-json", required=True)
    p.add_argument("--min-psnr", type=float, default=24.0)
    p.add_argument("--min-ssim", type=float, default=0.82)
    p.add_argument("--max-keyframe-density", type=float, default=0.18)
    p.add_argument("--min-traj-full", type=int, default=300)
    p.add_argument("--require-3dgs", action="store_true")
    args = p.parse_args()

    summary = json.loads(Path(args.summary_json).read_text(encoding="utf-8"))
    failures: list[str] = []

    if args.require_3dgs and not summary.get("has_3dgs_final", False):
        failures.append("missing_3dgs_final")
    if int(summary.get("traj_full_lines", 0)) < args.min_traj_full:
        failures.append("traj_too_short")

    mean_psnr = summary.get("mean_psnr")
    mean_ssim = summary.get("mean_ssim")
    kf_density = summary.get("keyframe_density")

    if mean_psnr is None or float(mean_psnr) < args.min_psnr:
        failures.append("psnr_below_gate")
    if mean_ssim is None or float(mean_ssim) < args.min_ssim:
        failures.append("ssim_below_gate")
    if kf_density is None or float(kf_density) > args.max_keyframe_density:
        failures.append("keyframe_density_above_gate")

    out = {
        "passed": len(failures) == 0,
        "failures": failures,
        "summary": summary,
    }
    print(json.dumps(out, indent=2, sort_keys=True))
    sys.exit(0 if not failures else 1)


if __name__ == "__main__":
    main()
