#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def parse_raw_index(path: Path) -> int:
    stem = path.stem
    head = stem.split("_", 1)[0]
    if head.isdigit():
        return int(head)
    digits = "".join(ch for ch in stem if ch.isdigit())
    if digits:
        return int(digits)
    raise ValueError(f"cannot parse raw depth index from {path}")


def parse_opt_index(path: Path) -> int:
    return int(path.stem)


def read_raw_bin(path: Path, width: int, height: int) -> np.ndarray:
    expected = width * height
    data = np.fromfile(path, dtype=np.float32, count=expected)
    if data.size != expected:
        raise ValueError(f"unexpected bin size in {path}: {data.size} != {expected}")
    return data.reshape(height, width)


def read_png_depth(path: Path, depth_scale: float) -> np.ndarray:
    from PIL import Image

    arr = np.array(Image.open(path), dtype=np.float32)
    return arr / depth_scale


def depth_stats(arr: np.ndarray) -> dict[str, float | None]:
    mask = np.isfinite(arr) & (arr > 0)
    valid = float(mask.mean())
    if not mask.any():
        return {
            "valid_rate": valid,
            "p50_m": None,
            "p95_m": None,
            "mean_m": None,
        }
    vals = arr[mask]
    return {
        "valid_rate": valid,
        "p50_m": float(np.percentile(vals, 50)),
        "p95_m": float(np.percentile(vals, 95)),
        "mean_m": float(vals.mean()),
    }


def summarize(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"count": 0, "p50": None, "p95": None, "mean": None}
    arr = np.asarray(values, dtype=np.float64)
    return {
        "count": int(arr.size),
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "mean": float(arr.mean()),
    }


def parse_thresholds(spec: str) -> list[float]:
    vals = []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        vals.append(float(item))
    return vals


def parse_pose_line(line: str, matrix_order: str) -> np.ndarray | None:
    vals = [float(x) for x in line.strip().split()]
    if len(vals) == 8:
        T = np.eye(4, dtype=np.float64)
        T[:3, 3] = np.asarray(vals[1:4], dtype=np.float64)
        return T
    if len(vals) == 17:
        vals = vals[1:]
    if len(vals) != 16:
        return None
    order = "F" if matrix_order == "col-major" else "C"
    return np.asarray(vals, dtype=np.float64).reshape(4, 4, order=order)


def load_positions(path: Path, matrix_order: str) -> np.ndarray:
    positions: list[np.ndarray] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            mat = parse_pose_line(line, matrix_order)
            if mat is None:
                continue
            positions.append(mat[:3, 3].astype(np.float64))
    if not positions:
        raise RuntimeError(f"no valid poses found in {path}")
    return np.stack(positions, axis=0)


def trajectory_stats(positions: np.ndarray) -> dict[str, float | int | None]:
    if len(positions) < 2:
        return {
            "frames": int(len(positions)),
            "step_mean_m": None,
            "step_p95_m": None,
            "step_max_m": None,
            "total_path_m": 0.0,
            "frames_per_meter": None,
            "max_from_start_m": 0.0,
            "max_from_center_m": 0.0,
        }
    steps = np.linalg.norm(np.diff(positions, axis=0), axis=1)
    center = positions.mean(axis=0)
    dstart = np.linalg.norm(positions - positions[0], axis=1)
    dcenter = np.linalg.norm(positions - center, axis=1)
    total_path = float(steps.sum())
    return {
        "frames": int(len(positions)),
        "step_mean_m": float(steps.mean()),
        "step_p95_m": float(np.percentile(steps, 95)),
        "step_max_m": float(steps.max()),
        "total_path_m": total_path,
        "frames_per_meter": float(len(positions) / max(total_path, 1e-9)),
        "max_from_start_m": float(dstart.max()),
        "max_from_center_m": float(dcenter.max()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare HI-SLAM2 raw depth and rendered depth on matched frames.")
    parser.add_argument("--raw-dir", required=True, help="Directory containing raw depth .bin or .png files.")
    parser.add_argument("--raw-format", choices=("auto", "bin", "png"), default="auto")
    parser.add_argument("--raw-depth-scale", type=float, default=1.0,
                        help="Depth scale for raw PNG depth, ignored for .bin raw depth.")
    parser.add_argument("--opt-dir", required=True, help="Directory containing rendered depth .png files.")
    parser.add_argument("--traj", default=None, help="Optional input/full trajectory file.")
    parser.add_argument("--traj-format", choices=("row-major", "col-major"), default="col-major")
    parser.add_argument("--kf-traj", default=None, help="Optional keyframe trajectory file for keyframe density.")
    parser.add_argument("--kf-traj-format", choices=("row-major", "col-major"), default="row-major")
    parser.add_argument("--near-thresholds-m", default="0.5,1.0,2.0",
                        help="Comma-separated raw depth near-field thresholds in meters.")
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--opt-depth-scale", type=float, default=6553.5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    thresholds = parse_thresholds(args.near_thresholds_m)
    raw_candidates: list[Path] = []
    raw_root = Path(args.raw_dir)
    if args.raw_format in {"auto", "bin"}:
        raw_candidates.extend(sorted(raw_root.glob("*.bin")))
    if args.raw_format in {"auto", "png"}:
        raw_candidates.extend(sorted(raw_root.glob("*.png")))
    raw_files = {parse_raw_index(p): p for p in raw_candidates}
    opt_files = {parse_opt_index(p): p for p in sorted(Path(args.opt_dir).glob("*.png"))}
    common = sorted(set(raw_files) & set(opt_files))

    frame_ratio_p50: list[float] = []
    frame_ratio_p95: list[float] = []
    frame_ratio_mean: list[float] = []
    pixel_ratio_p50: list[float] = []
    raw_p50: list[float] = []
    opt_p50: list[float] = []
    raw_p95: list[float] = []
    opt_p95: list[float] = []
    raw_mean: list[float] = []
    opt_mean: list[float] = []
    valid_rates_raw: list[float] = []
    valid_rates_opt: list[float] = []
    near_ratios: dict[float, list[float]] = {thr: [] for thr in thresholds}

    for idx in common:
        raw_path = raw_files[idx]
        if raw_path.suffix.lower() == ".png":
            raw = read_png_depth(raw_path, args.raw_depth_scale)
        else:
            raw = read_raw_bin(raw_path, args.width, args.height)
        opt = read_png_depth(opt_files[idx], args.opt_depth_scale)

        s_raw = depth_stats(raw)
        s_opt = depth_stats(opt)
        valid_rates_raw.append(float(s_raw["valid_rate"]))
        valid_rates_opt.append(float(s_opt["valid_rate"]))

        if s_raw["p50_m"] is not None and s_opt["p50_m"] is not None and s_raw["p50_m"] > 0:
            raw_p50.append(float(s_raw["p50_m"]))
            opt_p50.append(float(s_opt["p50_m"]))
            frame_ratio_p50.append(float(s_opt["p50_m"]) / float(s_raw["p50_m"]))
        if s_raw["p95_m"] is not None and s_opt["p95_m"] is not None and s_raw["p95_m"] > 0:
            raw_p95.append(float(s_raw["p95_m"]))
            opt_p95.append(float(s_opt["p95_m"]))
            frame_ratio_p95.append(float(s_opt["p95_m"]) / float(s_raw["p95_m"]))
        if s_raw["mean_m"] is not None and s_opt["mean_m"] is not None and s_raw["mean_m"] > 0:
            raw_mean.append(float(s_raw["mean_m"]))
            opt_mean.append(float(s_opt["mean_m"]))
            frame_ratio_mean.append(float(s_opt["mean_m"]) / float(s_raw["mean_m"]))

        raw_mask = np.isfinite(raw) & (raw > 0)
        if raw_mask.any():
            raw_vals = raw[raw_mask]
            for thr in thresholds:
                near_ratios[thr].append(float(np.mean(raw_vals < thr)))

        if raw.shape == opt.shape:
            common_mask = np.isfinite(raw) & (raw > 0) & np.isfinite(opt) & (opt > 0)
            if common_mask.any():
                ratio = opt[common_mask] / raw[common_mask]
                pixel_ratio_p50.append(float(np.percentile(ratio, 50)))

    traj_block = None
    rel_block = None
    kf_block = None
    if args.traj:
        traj_positions = load_positions(Path(args.traj), args.traj_format)
        traj_block = trajectory_stats(traj_positions)

    result = {
        "matched_frames": len(common),
        "raw_valid_rate_mean": float(np.mean(valid_rates_raw)) if valid_rates_raw else None,
        "opt_valid_rate_mean": float(np.mean(valid_rates_opt)) if valid_rates_opt else None,
        "raw_p50_m": summarize(raw_p50),
        "opt_p50_m": summarize(opt_p50),
        "raw_p95_m": summarize(raw_p95),
        "opt_p95_m": summarize(opt_p95),
        "raw_mean_m": summarize(raw_mean),
        "opt_mean_m": summarize(opt_mean),
        "frame_median_inflation_p50": summarize(frame_ratio_p50),
        "frame_p95_inflation": summarize(frame_ratio_p95),
        "frame_mean_inflation": summarize(frame_ratio_mean),
        "pixel_ratio_p50": summarize(pixel_ratio_p50),
        "raw_near_field_ratio": {
            f"lt_{str(thr).replace('.', 'p')}m": summarize(vals)
            for thr, vals in near_ratios.items()
        },
    }

    if traj_block is not None:
        result["trajectory"] = traj_block
        raw_p50_med = result["raw_p50_m"]["p50"]
        if raw_p50_med is not None and raw_p50_med > 0:
            rel_block = {
                "step_mean_over_raw_p50": float(traj_block["step_mean_m"]) / float(raw_p50_med),
                "step_p95_over_raw_p50": float(traj_block["step_p95_m"]) / float(raw_p50_med),
                "frames_per_meter": float(traj_block["frames_per_meter"]),
            }
            result["relative_workpoint"] = rel_block

    if args.kf_traj:
        kf_positions = load_positions(Path(args.kf_traj), args.kf_traj_format)
        total_frames = len(traj_positions) if args.traj else len(common)
        density = None if total_frames <= 0 else float(len(kf_positions)) / float(total_frames)
        kf_block = {
            "keyframes": int(len(kf_positions)),
            "total_frames": int(total_frames),
            "keyframe_density": density,
        }
        result["keyframe"] = kf_block

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
