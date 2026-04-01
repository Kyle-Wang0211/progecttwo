#!/usr/bin/env python3
import argparse
import json
import math
import os
from pathlib import Path

import numpy as np
from PIL import Image


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sequence-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--depth-scale", type=float, default=5000.0)
    parser.add_argument("--rgb-sample-dt", type=float, default=0.0)
    parser.add_argument("--depth-sample-dt", type=float, default=0.0)
    parser.add_argument("--rgb-timestamps-file")
    parser.add_argument("--depth-timestamps-file")
    parser.add_argument("--depth-reference-root")
    parser.add_argument("--reference-depth-scale", type=float, default=5000.0)
    parser.add_argument("--apply-reference-depth-profile", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def matrix_to_quaternion_xyzw(rot):
    trace = float(rot[0, 0] + rot[1, 1] + rot[2, 2])
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        qw = 0.25 * s
        qx = (rot[2, 1] - rot[1, 2]) / s
        qy = (rot[0, 2] - rot[2, 0]) / s
        qz = (rot[1, 0] - rot[0, 1]) / s
    elif rot[0, 0] > rot[1, 1] and rot[0, 0] > rot[2, 2]:
        s = math.sqrt(1.0 + rot[0, 0] - rot[1, 1] - rot[2, 2]) * 2.0
        qw = (rot[2, 1] - rot[1, 2]) / s
        qx = 0.25 * s
        qy = (rot[0, 1] + rot[1, 0]) / s
        qz = (rot[0, 2] + rot[2, 0]) / s
    elif rot[1, 1] > rot[2, 2]:
        s = math.sqrt(1.0 + rot[1, 1] - rot[0, 0] - rot[2, 2]) * 2.0
        qw = (rot[0, 2] - rot[2, 0]) / s
        qx = (rot[0, 1] + rot[1, 0]) / s
        qy = 0.25 * s
        qz = (rot[1, 2] + rot[2, 1]) / s
    else:
        s = math.sqrt(1.0 + rot[2, 2] - rot[0, 0] - rot[1, 1]) * 2.0
        qw = (rot[1, 0] - rot[0, 1]) / s
        qx = (rot[0, 2] + rot[2, 0]) / s
        qy = (rot[1, 2] + rot[2, 1]) / s
        qz = 0.25 * s
    quat = np.array([qx, qy, qz, qw], dtype=np.float64)
    quat /= np.linalg.norm(quat) + 1e-12
    return quat


def load_cam2world_rows(traj_path):
    rows = []
    with open(traj_path, "r", encoding="utf-8") as handle:
        for line in handle:
            fields = line.strip().split()
            if len(fields) != 17:
                continue
            timestamp = float(fields[0])
            cam2world = np.asarray([float(v) for v in fields[1:]], dtype=np.float64).reshape(4, 4, order="F")
            rows.append((timestamp, cam2world))
    return rows


def subsample_indices(timestamps, sample_dt):
    if sample_dt <= 0.0:
        return list(range(len(timestamps)))
    keep = [0]
    last_t = float(timestamps[0])
    for idx in range(1, len(timestamps)):
        t = float(timestamps[idx])
        if t - last_t >= sample_dt - 1e-9:
            keep.append(idx)
            last_t = t
    if keep[-1] != len(timestamps) - 1:
        keep.append(len(timestamps) - 1)
    return keep


def load_reference_entries(path):
    rows = []
    if not path:
        return rows
    ref_path = Path(path).resolve()
    with ref_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            fields = line.strip().split()
            if len(fields) < 2 or fields[0].startswith("#"):
                continue
            try:
                rows.append((float(fields[0]), fields[1]))
            except ValueError:
                continue
    if not rows:
        return rows
    start = rows[0][0]
    return [(timestamp - start, rel_path) for timestamp, rel_path in rows]


def select_indices_for_reference_entries(source_timestamps, target_entries):
    if not target_entries:
        return [], []
    source = np.asarray(source_timestamps, dtype=np.float64)
    keep = []
    selected_entries = []
    search_start = 0
    for target, rel_path in target_entries:
        if search_start >= len(source):
            break
        pos = int(np.searchsorted(source, target, side="left"))
        if pos < search_start:
            pos = search_start
        candidates = []
        if pos < len(source):
            candidates.append(pos)
        if pos - 1 >= search_start:
            candidates.append(pos - 1)
        if not candidates:
            break
        chosen = min(candidates, key=lambda idx: abs(float(source[idx]) - float(target)))
        keep.append(chosen)
        selected_entries.append((float(target), rel_path))
        search_start = chosen + 1
    return keep, selected_entries


def remap_values_to_reference_quantiles(values, reference_values):
    if values.size == 0 or reference_values.size == 0:
        return values
    quantile_count = 129
    quantiles = np.linspace(0.0, 1.0, num=quantile_count, dtype=np.float64)
    source_q = np.quantile(values, quantiles)
    reference_q = np.quantile(reference_values, quantiles)
    source_unique, unique_indices = np.unique(source_q, return_index=True)
    reference_unique = reference_q[unique_indices]
    if source_unique.size == 1:
        return np.full(values.shape, float(reference_unique[0]), dtype=np.float32)
    return np.interp(
        values,
        source_unique,
        reference_unique,
        left=float(reference_unique[0]),
        right=float(reference_unique[-1]),
    ).astype(np.float32)


def load_reference_depth(reference_root: Path, rel_path: str, depth_scale: float):
    depth_path = reference_root / rel_path
    if not depth_path.is_file():
        raise RuntimeError(f"missing reference depth {depth_path}")
    return np.asarray(Image.open(depth_path), dtype=np.float32) / depth_scale


def apply_reference_depth_profile(depth, reference_depth):
    output = np.zeros_like(depth, dtype=np.float32)
    source_valid = np.isfinite(depth) & (depth > 0.0)
    reference_valid = np.isfinite(reference_depth) & (reference_depth > 0.0)
    common_valid = source_valid & reference_valid
    if not np.any(common_valid) or not np.any(reference_valid):
        return output
    remapped = remap_values_to_reference_quantiles(
        depth[common_valid].astype(np.float32),
        reference_depth[reference_valid].astype(np.float32),
    )
    output[common_valid] = remapped
    return output


def cadence_stats(timestamps):
    if len(timestamps) < 2:
        return {
            "count": len(timestamps),
            "first_timestamp_s": float(timestamps[0]) if timestamps else None,
            "last_timestamp_s": float(timestamps[-1]) if timestamps else None,
            "mean_dt_s": None,
            "min_dt_s": None,
            "max_dt_s": None,
        }
    deltas = np.diff(np.asarray(timestamps, dtype=np.float64))
    return {
        "count": len(timestamps),
        "first_timestamp_s": float(timestamps[0]),
        "last_timestamp_s": float(timestamps[-1]),
        "mean_dt_s": float(np.mean(deltas)),
        "min_dt_s": float(np.min(deltas)),
        "max_dt_s": float(np.max(deltas)),
    }


def remove_existing_pngs(path):
    if not path.is_dir():
        return
    for file in path.glob("*.png"):
        file.unlink()


def main():
    args = parse_args()
    sequence_root = Path(args.sequence_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    images_dir = sequence_root / "images"
    depth_raw_dir = sequence_root / "depth_raw"
    traj_path = sequence_root / "traj_gt.txt"

    rgb_dir = output_dir / "rgb"
    depth_dir = output_dir / "depth"
    rgb_dir.mkdir(parents=True, exist_ok=True)
    depth_dir.mkdir(parents=True, exist_ok=True)
    if args.overwrite:
        remove_existing_pngs(rgb_dir)
        remove_existing_pngs(depth_dir)

    image_paths = sorted(images_dir.glob("*.ppm"))
    depth_paths = sorted(depth_raw_dir.glob("*.bin"))
    pose_rows = load_cam2world_rows(traj_path)

    if not image_paths:
        raise RuntimeError(f"no images found under {images_dir}")
    if len(image_paths) != len(depth_paths):
        raise RuntimeError(f"image/depth mismatch: {len(image_paths)} vs {len(depth_paths)}")
    if len(image_paths) != len(pose_rows):
        raise RuntimeError(f"trajectory/image mismatch: {len(pose_rows)} vs {len(image_paths)}")

    timestamps = [timestamp for timestamp, _ in pose_rows]
    rgb_reference_entries = load_reference_entries(args.rgb_timestamps_file)
    depth_reference_entries = load_reference_entries(args.depth_timestamps_file)
    if rgb_reference_entries:
        rgb_indices, selected_rgb_entries = select_indices_for_reference_entries(
            timestamps, rgb_reference_entries
        )
        rgb_output_timestamps = [timestamp for timestamp, _ in selected_rgb_entries]
    else:
        rgb_indices = subsample_indices(timestamps, args.rgb_sample_dt)
        rgb_output_timestamps = [float(timestamps[index]) for index in rgb_indices]
    if depth_reference_entries:
        depth_indices, selected_depth_entries = select_indices_for_reference_entries(
            timestamps, depth_reference_entries
        )
        depth_output_timestamps = [timestamp for timestamp, _ in selected_depth_entries]
    else:
        depth_indices = subsample_indices(timestamps, args.depth_sample_dt)
        depth_output_timestamps = [float(timestamps[index]) for index in depth_indices]
    rgb_time_by_index = dict(zip(rgb_indices, rgb_output_timestamps))
    depth_time_by_index = dict(zip(depth_indices, depth_output_timestamps))
    depth_reference_path_by_index = {
        index: rel_path for index, (_, rel_path) in zip(depth_indices, selected_depth_entries)
    } if depth_reference_entries else {}
    depth_reference_root = Path(args.depth_reference_root).resolve() if args.depth_reference_root else None

    rgb_txt = []
    depth_txt = []
    gt_txt = ["# timestamp tx ty tz qx qy qz qw"]
    rgb_kept_timestamps = []
    depth_kept_timestamps = []

    for idx, (image_path, depth_raw_path, (timestamp, cam2world)) in enumerate(zip(image_paths, depth_paths, pose_rows)):
        rgb_name = image_path.name.rsplit(".", 1)[0] + ".png"
        depth_name = depth_raw_path.name.rsplit(".", 1)[0] + ".png"
        rgb_out = rgb_dir / rgb_name
        depth_out = depth_dir / depth_name

        if idx in rgb_time_by_index and not rgb_out.exists():
            Image.open(image_path).convert("RGB").save(rgb_out)

        if idx in depth_time_by_index and not depth_out.exists():
            depth = np.fromfile(depth_raw_path, dtype=np.float32)
            expected = args.width * args.height
            if depth.size != expected:
                raise RuntimeError(f"unexpected depth size in {depth_raw_path}: {depth.size} != {expected}")
            depth = depth.reshape(args.height, args.width)
            if args.apply_reference_depth_profile:
                if depth_reference_root is None:
                    raise RuntimeError("depth reference root is required when applying reference depth profile")
                reference_rel_path = depth_reference_path_by_index.get(idx)
                if not reference_rel_path:
                    raise RuntimeError(f"missing reference depth entry for frame index {idx}")
                reference_depth = load_reference_depth(
                    depth_reference_root,
                    reference_rel_path,
                    args.reference_depth_scale,
                )
                if reference_depth.shape != depth.shape:
                    raise RuntimeError(
                        f"reference depth shape mismatch {reference_depth.shape} vs {depth.shape} for {reference_rel_path}"
                    )
                depth = apply_reference_depth_profile(depth, reference_depth)
            depth_png = np.clip(depth * args.depth_scale, 0.0, 65535.0).astype(np.uint16)
            Image.fromarray(depth_png).save(depth_out)

        if idx in rgb_time_by_index:
            rgb_timestamp = float(rgb_time_by_index[idx])
            rgb_txt.append(f"{rgb_timestamp:.6f} rgb/{rgb_name}")
            rgb_kept_timestamps.append(rgb_timestamp)
        if idx in depth_time_by_index:
            depth_timestamp = float(depth_time_by_index[idx])
            depth_txt.append(f"{depth_timestamp:.6f} depth/{depth_name}")
            depth_kept_timestamps.append(depth_timestamp)

        rot = cam2world[:3, :3]
        trans = cam2world[:3, 3]
        qx, qy, qz, qw = matrix_to_quaternion_xyzw(rot)
        gt_txt.append(
            f"{timestamp:.6f} {trans[0]:.6f} {trans[1]:.6f} {trans[2]:.6f} "
            f"{qx:.6f} {qy:.6f} {qz:.6f} {qw:.6f}"
        )

        if (idx + 1) % 25 == 0 or idx + 1 == len(image_paths):
            print(f"[make_wildgs_room3x3_tumrgbd_exact] frames {idx + 1}/{len(image_paths)}")

    (output_dir / "rgb.txt").write_text("\n".join(rgb_txt) + "\n", encoding="utf-8")
    (output_dir / "depth.txt").write_text("\n".join(depth_txt) + "\n", encoding="utf-8")
    (output_dir / "groundtruth.txt").write_text("\n".join(gt_txt) + "\n", encoding="utf-8")
    workpoint_meta = {
        "parser": "tum_rgbd_png16",
        "camera": {
            "width": args.width,
            "height": args.height,
        },
        "depth": {
            "scale": args.depth_scale,
            "format": "png16_mm_like",
            "profile_mode": "reference_mask_quantiles" if args.apply_reference_depth_profile else "native",
            "reference_root": str(depth_reference_root) if depth_reference_root else None,
            "reference_depth_scale": args.reference_depth_scale if args.apply_reference_depth_profile else None,
        },
        "timestamp_profiles": {
            "rgb": Path(args.rgb_timestamps_file).name if args.rgb_timestamps_file else None,
            "depth": Path(args.depth_timestamps_file).name if args.depth_timestamps_file else None,
        },
        "cadence": {
            "rgb_requested_dt_s": args.rgb_sample_dt,
            "depth_requested_dt_s": args.depth_sample_dt,
            "rgb": cadence_stats(rgb_kept_timestamps),
            "depth": cadence_stats(depth_kept_timestamps),
        },
        "counts": {
            "rgb": len(rgb_txt),
            "depth": len(depth_txt),
            "groundtruth": len(gt_txt) - 1,
        },
    }
    (output_dir / "capture_workpoint.json").write_text(
        json.dumps(workpoint_meta, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "[make_wildgs_room3x3_tumrgbd_exact] done "
        f"output={output_dir} rgb={len(rgb_txt)} depth={len(depth_txt)} gt={len(gt_txt) - 1}"
    )


if __name__ == "__main__":
    main()
