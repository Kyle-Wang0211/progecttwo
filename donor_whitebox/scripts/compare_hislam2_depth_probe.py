#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def summarize(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "p50": None, "p95": None, "mean": None}
    arr = np.asarray(values, dtype=np.float64)
    return {
        "count": int(arr.size),
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "mean": float(arr.mean()),
    }


def read_png_depth(path: Path, depth_scale: float) -> np.ndarray:
    arr = np.array(Image.open(path), dtype=np.float32)
    return arr / depth_scale


def depth_stats(arr: np.ndarray) -> tuple[float | None, float | None, float | None]:
    mask = np.isfinite(arr) & (arr > 0)
    if not mask.any():
        return None, None, None
    vals = arr[mask]
    return (
        float(np.percentile(vals, 50)),
        float(np.percentile(vals, 95)),
        float(vals.mean()),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare streamed raw depth stats with HI-SLAM2 depth_after_opt.")
    parser.add_argument("--raw-json", required=True)
    parser.add_argument("--opt-dir", required=True)
    parser.add_argument("--opt-depth-scale", type=float, default=6553.5)
    parser.add_argument("--match-mode", choices=("auto", "keyed", "ordered"), default="auto")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    raw_payload = json.loads(Path(args.raw_json).read_text(encoding="utf-8"))
    raw_by_index = raw_payload["by_index"]
    opt_dir = Path(args.opt_dir)
    ordered_raw = sorted(((int(k), v) for k, v in raw_by_index.items()), key=lambda kv: kv[0])
    ordered_opt = sorted(opt_dir.glob("*.png"), key=lambda p: int(p.stem))

    raw_p50: list[float] = []
    raw_p95: list[float] = []
    raw_mean: list[float] = []
    opt_p50: list[float] = []
    opt_p95: list[float] = []
    opt_mean: list[float] = []
    ratio_p50: list[float] = []
    ratio_p95: list[float] = []
    ratio_mean: list[float] = []

    if args.match_mode == "keyed":
        pairs = []
        for png in ordered_opt:
            raw_stats = raw_by_index.get(str(int(png.stem)))
            if raw_stats:
                pairs.append((raw_stats, png))
    elif args.match_mode == "ordered":
        pairs = [(raw_stats, png) for (_, raw_stats), png in zip(ordered_raw, ordered_opt)]
    else:
        keyed_pairs = []
        for png in ordered_opt:
            raw_stats = raw_by_index.get(str(int(png.stem)))
            if raw_stats:
                keyed_pairs.append((raw_stats, png))
        ordered_pairs = [(raw_stats, png) for (_, raw_stats), png in zip(ordered_raw, ordered_opt)]
        pairs = ordered_pairs if len(keyed_pairs) < max(10, len(ordered_pairs) // 2) else keyed_pairs

    matched = 0
    for raw_stats, png in pairs:
        opt = read_png_depth(png, args.opt_depth_scale)
        opt50, opt95, optm = depth_stats(opt)
        raw50 = raw_stats.get("p50_m")
        raw95 = raw_stats.get("p95_m")
        rawm = raw_stats.get("mean_m")
        if raw50 is not None and opt50 is not None and raw50 > 0:
            raw_p50.append(float(raw50))
            opt_p50.append(float(opt50))
            ratio_p50.append(float(opt50) / float(raw50))
        if raw95 is not None and opt95 is not None and raw95 > 0:
            raw_p95.append(float(raw95))
            opt_p95.append(float(opt95))
            ratio_p95.append(float(opt95) / float(raw95))
        if rawm is not None and optm is not None and rawm > 0:
            raw_mean.append(float(rawm))
            opt_mean.append(float(optm))
            ratio_mean.append(float(optm) / float(rawm))
        matched += 1

    result = {
        "matched_frames": matched,
        "raw_p50_m": summarize(raw_p50),
        "opt_p50_m": summarize(opt_p50),
        "raw_p95_m": summarize(raw_p95),
        "opt_p95_m": summarize(opt_p95),
        "raw_mean_m": summarize(raw_mean),
        "opt_mean_m": summarize(opt_mean),
        "frame_median_inflation_p50": summarize(ratio_p50),
        "frame_p95_inflation": summarize(ratio_p95),
        "frame_mean_inflation": summarize(ratio_mean),
    }
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
