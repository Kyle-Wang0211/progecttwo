#!/usr/bin/env python3
import argparse
import json
import math
import shutil
from bisect import bisect_left
from pathlib import Path

import numpy as np
from PIL import Image


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--fx", type=float, required=True)
    parser.add_argument("--fy", type=float, required=True)
    parser.add_argument("--cx", type=float, required=True)
    parser.add_argument("--cy", type=float, required=True)
    parser.add_argument("--max-pose-dt", type=float, default=1e-4)
    parser.add_argument("--max-depth-dt", type=float, default=1e-4)
    parser.add_argument("--jpg-quality", type=int, default=95)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def load_index(path: Path):
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) < 2:
                continue
            rows.append((float(fields[0]), fields[1]))
    if not rows:
        raise RuntimeError(f"no usable rows in {path}")
    return rows


def load_groundtruth(path: Path):
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) != 8:
                continue
            rows.append(
                (
                    float(fields[0]),
                    np.asarray([float(v) for v in fields[1:4]], dtype=np.float64),
                    np.asarray([float(v) for v in fields[4:8]], dtype=np.float64),
                )
            )
    if not rows:
        raise RuntimeError(f"no usable pose rows in {path}")
    return rows


def nearest_row(rows, timestamp, tolerance):
    timestamps = [row[0] for row in rows]
    pos = bisect_left(timestamps, timestamp)
    candidates = []
    if pos < len(rows):
        candidates.append(rows[pos])
    if pos > 0:
        candidates.append(rows[pos - 1])
    if not candidates:
        raise RuntimeError(f"no candidates for timestamp {timestamp:.6f}")
    best = min(candidates, key=lambda row: abs(row[0] - timestamp))
    if abs(best[0] - timestamp) > tolerance:
        raise RuntimeError(
            f"no match within tolerance for timestamp {timestamp:.6f}: "
            f"closest {best[0]:.6f}, tolerance {tolerance}"
        )
    return best


def quaternion_xyzw_to_matrix(quat):
    x, y, z, w = quat
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm <= 1e-12:
        raise RuntimeError("zero-length quaternion")
    x /= norm
    y /= norm
    z /= norm
    w /= norm
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return np.array(
        [
            [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
            [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
            [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)],
        ],
        dtype=np.float32,
    )


def ensure_clean_dir(path: Path, overwrite: bool):
    if path.exists():
        if not overwrite:
            raise RuntimeError(f"{path} already exists; pass --overwrite to replace it")
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def write_pose(path: Path, translation, quaternion_xyzw):
    pose = np.eye(4, dtype=np.float32)
    pose[:3, :3] = quaternion_xyzw_to_matrix(quaternion_xyzw)
    pose[:3, 3] = translation.astype(np.float32)
    np.savetxt(path, pose, fmt="%.8f")


def main():
    args = parse_args()
    input_root = Path(args.input_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    rgb_rows = load_index(input_root / "rgb.txt")
    depth_rows = load_index(input_root / "depth.txt")
    gt_rows = load_groundtruth(input_root / "groundtruth.txt")

    camera_dir = output_dir / "camera"
    depth_dir = output_dir / "depth"
    ensure_clean_dir(output_dir, args.overwrite)
    camera_dir.mkdir(parents=True, exist_ok=True)
    depth_dir.mkdir(parents=True, exist_ok=True)

    intrinsics = np.array(
        [
            [args.fx, 0.0, args.cx],
            [0.0, args.fy, args.cy],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float32,
    )
    np.savetxt(camera_dir / "intrinsics.txt", intrinsics, fmt="%.8f")
    np.savetxt(camera_dir / "img_shape.txt", np.array([args.width, args.height], dtype=np.int32), fmt="%d")

    converted = []
    for frame_idx, (rgb_timestamp, rgb_rel) in enumerate(rgb_rows):
        depth_timestamp, depth_rel = nearest_row(depth_rows, rgb_timestamp, args.max_depth_dt)
        pose_timestamp, translation, quaternion_xyzw = nearest_row(gt_rows, rgb_timestamp, args.max_pose_dt)

        rgb_src = input_root / rgb_rel
        depth_src = input_root / depth_rel
        if not rgb_src.is_file():
            raise RuntimeError(f"missing rgb frame {rgb_src}")
        if not depth_src.is_file():
            raise RuntimeError(f"missing depth frame {depth_src}")

        frame_name = f"{frame_idx:06d}"
        frame_dst = camera_dir / f"frame{frame_name}.jpg"
        pose_dst = camera_dir / f"pose{frame_name}.txt"
        depth_dst = depth_dir / f"depth{frame_name}.png"

        rgb = Image.open(rgb_src).convert("RGB")
        if rgb.size != (args.width, args.height):
            raise RuntimeError(f"unexpected rgb size in {rgb_src}: {rgb.size} != {(args.width, args.height)}")
        rgb.save(frame_dst, quality=args.jpg_quality, subsampling=0)

        depth = Image.open(depth_src)
        if depth.size != (args.width, args.height):
            raise RuntimeError(f"unexpected depth size in {depth_src}: {depth.size} != {(args.width, args.height)}")
        depth.save(depth_dst)
        write_pose(pose_dst, translation, quaternion_xyzw)

        converted.append(
            {
                "frame_id": frame_idx,
                "rgb_timestamp_s": rgb_timestamp,
                "depth_timestamp_s": depth_timestamp,
                "pose_timestamp_s": pose_timestamp,
                "rgb_rel": rgb_rel,
                "depth_rel": depth_rel,
            }
        )
        if (frame_idx + 1) % 50 == 0 or frame_idx + 1 == len(rgb_rows):
            print(f"[make_gpsslam_rgbd_dataset] frames {frame_idx + 1}/{len(rgb_rows)}")

    manifest = {
        "source_root": str(input_root),
        "output_dir": str(output_dir),
        "counts": {
            "rgb": len(rgb_rows),
            "depth": len(depth_rows),
            "groundtruth": len(gt_rows),
            "converted_frames": len(converted),
        },
        "camera": {
            "width": args.width,
            "height": args.height,
            "fx": args.fx,
            "fy": args.fy,
            "cx": args.cx,
            "cy": args.cy,
        },
        "tolerances": {
            "max_pose_dt_s": args.max_pose_dt,
            "max_depth_dt_s": args.max_depth_dt,
        },
        "frames": converted,
    }
    (output_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "[make_gpsslam_rgbd_dataset] done "
        f"output={output_dir} frames={len(converted)}"
    )


if __name__ == "__main__":
    main()
