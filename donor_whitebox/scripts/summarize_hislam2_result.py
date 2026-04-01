#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def count_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        return sum(1 for line in f if line.strip())


def load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> None:
    p = argparse.ArgumentParser(description="Summarize HI-SLAM2 output directory.")
    p.add_argument("--outdir", required=True)
    p.add_argument("--cleanup-summary", default="")
    p.add_argument("--preexport-summary", default="")
    p.add_argument("--segcut-summary", default="")
    p.add_argument("--masks-summary", default="")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    outdir = Path(args.outdir)
    traj_full_lines = count_lines(outdir / "traj_full.txt")
    traj_kf_lines = count_lines(outdir / "traj_kf.txt")
    render = load_json(outdir / "psnr" / "after_opt" / "final_result.json")
    render_kf = load_json(outdir / "psnr" / "after_opt" / "final_result_kf.json")
    cleanup_summary = load_json(Path(args.cleanup_summary)) if args.cleanup_summary else {}
    preexport_summary = load_json(Path(args.preexport_summary)) if args.preexport_summary else {}
    segcut_summary = load_json(Path(args.segcut_summary)) if args.segcut_summary else {}
    masks_summary = load_json(Path(args.masks_summary)) if args.masks_summary else {}

    summary = {
        "outdir": str(outdir),
        "has_3dgs_final": (outdir / "3dgs_final.ply").is_file(),
        "has_3dgs_original": (outdir / "3dgs_original.ply").is_file(),
        "has_3dgs_segcut": (outdir / "3dgs_segcut.ply").is_file(),
        "has_3dgs_legacy_cleaned": (outdir / "3dgs_legacy_cleaned.ply").is_file(),
        "has_tsdf_mesh": (outdir / "tsdf_mesh_w2.0.ply").is_file(),
        "traj_full_lines": traj_full_lines,
        "traj_kf_lines": traj_kf_lines,
        "keyframe_density": None if traj_full_lines <= 0 else float(traj_kf_lines) / float(traj_full_lines),
        "mean_psnr": render.get("mean_psnr"),
        "mean_ssim": render.get("mean_ssim"),
        "kf_mean_psnr": render_kf.get("mean_psnr"),
        "kf_mean_ssim": render_kf.get("mean_ssim"),
        "preexport_summary": preexport_summary or None,
        "cleanup_summary": cleanup_summary or None,
        "segcut_summary": segcut_summary or None,
        "masks_summary": masks_summary or None,
    }

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
