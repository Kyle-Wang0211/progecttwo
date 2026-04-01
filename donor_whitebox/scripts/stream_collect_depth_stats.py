#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np


def parse_index(path: Path) -> int:
    stem = path.stem
    head = stem.split("_", 1)[0]
    if head.isdigit():
        return int(head)
    digits = "".join(ch for ch in stem if ch.isdigit())
    if digits:
        return int(digits)
    raise ValueError(f"cannot parse frame index from {path}")


def depth_stats(path: Path) -> dict[str, float | int | None]:
    data = np.fromfile(path, dtype=np.float32)
    if data.size == 0:
        return {
            "valid_rate": 0.0,
            "p50_m": None,
            "p95_m": None,
            "mean_m": None,
            "count": 0,
        }
    mask = np.isfinite(data) & (data > 0)
    valid = data[mask]
    if valid.size == 0:
        return {
            "valid_rate": 0.0,
            "p50_m": None,
            "p95_m": None,
            "mean_m": None,
            "count": int(data.size),
        }
    return {
        "valid_rate": float(mask.mean()),
        "p50_m": float(np.percentile(valid, 50)),
        "p95_m": float(np.percentile(valid, 95)),
        "mean_m": float(valid.mean()),
        "count": int(data.size),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream raw depth stats while deleting transient files.")
    parser.add_argument("--seq-root", required=True, help="Sequence output root containing images/ and depth_raw/.")
    parser.add_argument("--out-json", required=True, help="Where to write final per-frame stats JSON.")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--idle-rounds", type=int, default=4)
    args = parser.parse_args()

    seq_root = Path(args.seq_root)
    images_dir = seq_root / "images"
    depth_dir = seq_root / "depth_raw"
    complete_flag = seq_root / ".complete"
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    seen_depth: set[Path] = set()
    stats_by_index: dict[str, dict[str, float | int | None]] = {}
    deleted_ppm = 0
    idle_rounds = 0

    while True:
        progressed = False

        for ppm in sorted(images_dir.glob("*.ppm")):
            try:
                ppm.unlink()
                deleted_ppm += 1
                progressed = True
            except FileNotFoundError:
                continue

        for depth_path in sorted(depth_dir.glob("*.bin")):
            if depth_path in seen_depth:
                continue
            try:
                idx = parse_index(depth_path)
                stats_by_index[str(idx)] = depth_stats(depth_path)
                depth_path.unlink()
                seen_depth.add(depth_path)
                progressed = True
            except FileNotFoundError:
                continue

        if progressed:
            idle_rounds = 0
        else:
            idle_rounds += 1

        done = (
            complete_flag.exists()
            and not any(images_dir.glob("*.ppm"))
            and not any(depth_dir.glob("*.bin"))
            and idle_rounds >= args.idle_rounds
        )
        if done:
            break
        time.sleep(args.poll_seconds)

    payload = {
        "seq_root": str(seq_root),
        "frames": len(stats_by_index),
        "deleted_ppm": deleted_ppm,
        "by_index": stats_by_index,
    }
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"frames": len(stats_by_index), "deleted_ppm": deleted_ppm}, sort_keys=True))


if __name__ == "__main__":
    main()
