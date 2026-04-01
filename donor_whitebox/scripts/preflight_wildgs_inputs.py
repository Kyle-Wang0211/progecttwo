#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
import yaml


def deep_merge(base, override):
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    out = dict(base)
    for key, value in override.items():
        if key in out:
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def resolve_inherit_path(cfg_path: Path, inherit: str) -> Path:
    inherit_path = Path(inherit)
    if inherit_path.is_absolute():
        return inherit_path

    for base in (cfg_path.parent, *cfg_path.parents):
        candidate = (base / inherit_path).resolve()
        if candidate.is_file():
            return candidate

    return (cfg_path.parent / inherit_path).resolve()


def load_config(path: Path) -> dict:
    cfg_path = path.resolve()
    with cfg_path.open("r", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle) or {}
    inherit = cfg.get("inherit_from")
    if inherit:
        parent = load_config(resolve_inherit_path(cfg_path, inherit))
        cfg = deep_merge(parent, cfg)
    return cfg


def resolve_input_root(cfg: dict) -> Path:
    input_folder = str(cfg["data"]["input_folder"])
    root_folder = str(cfg["data"].get("root_folder", ""))
    if "ROOT_FOLDER_PLACEHOLDER" in input_folder:
        input_folder = input_folder.replace("ROOT_FOLDER_PLACEHOLDER", root_folder)
    return Path(input_folder)


def first_image_size(path: Path):
    files = sorted(path.glob("*"))
    for file in files:
        if file.is_file():
            with Image.open(file) as image:
                return image.size[1], image.size[0]
    return None


def first_depth_shape(path: Path):
    files = sorted(path.glob("*.npy"))
    for file in files:
        arr = np.load(file)
        return arr.shape
    return None


def collect_numeric_stems(path: Path, pattern: str):
    stems = []
    for file in sorted(path.glob(pattern)):
        try:
            stems.append(int(file.stem))
        except ValueError:
            continue
    return stems


def load_tum_entries(path: Path):
    entries = []
    if not path.is_file():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            entries.append((float(fields[0]), fields[1]))
        except ValueError:
            continue
    return entries


def associate_tum_entries(rgb_entries, depth_entries, pose_entries, max_dt=0.08):
    associations = []
    if not rgb_entries or not depth_entries:
        return associations
    depth_timestamps = np.asarray([timestamp for timestamp, _ in depth_entries], dtype=np.float64)
    pose_timestamps = None
    if pose_entries:
        pose_timestamps = np.asarray([timestamp for timestamp, _ in pose_entries], dtype=np.float64)

    for rgb_index, (rgb_timestamp, _) in enumerate(rgb_entries):
        depth_index = int(np.argmin(np.abs(depth_timestamps - rgb_timestamp)))
        depth_delta = abs(float(depth_timestamps[depth_index]) - float(rgb_timestamp))
        if pose_timestamps is None:
            if depth_delta < max_dt:
                associations.append((rgb_index, depth_index, None))
            continue

        pose_index = int(np.argmin(np.abs(pose_timestamps - rgb_timestamp)))
        pose_delta = abs(float(pose_timestamps[pose_index]) - float(rgb_timestamp))
        if depth_delta < max_dt and pose_delta < max_dt:
            associations.append((rgb_index, depth_index, pose_index))
    return associations


def select_loader_indices(rgb_entries, associations, frame_rate=60.0):
    if not associations:
        return []
    selected = [0]
    for assoc_index in range(1, len(associations)):
        prev_rgb_index = associations[selected[-1]][0]
        curr_rgb_index = associations[assoc_index][0]
        prev_t = float(rgb_entries[prev_rgb_index][0])
        curr_t = float(rgb_entries[curr_rgb_index][0])
        if curr_t - prev_t > 1.0 / frame_rate:
            selected.append(assoc_index)
    return selected


def print_cadence_stats(label: str, timestamps):
    if len(timestamps) < 2:
        print(f"{label}_MEAN_DT_S nan")
        print(f"{label}_MIN_DT_S nan")
        print(f"{label}_MAX_DT_S nan")
        return
    deltas = np.diff(np.asarray(timestamps, dtype=np.float64))
    print(f"{label}_MEAN_DT_S {float(np.mean(deltas)):.6f}")
    print(f"{label}_MIN_DT_S {float(np.min(deltas)):.6f}")
    print(f"{label}_MAX_DT_S {float(np.max(deltas)):.6f}")


def sample_depth_distribution(depth_dir: Path, entries, depth_scale: float, depth_trunc: float):
    positive = []
    sampled = entries
    if len(entries) > 8:
        sample_ids = np.linspace(0, len(entries) - 1, num=8, dtype=int)
        sampled = [entries[index] for index in sample_ids]

    total_pixels = 0
    valid_pixels = 0
    trunc_pixels = 0
    for _, rel_path in sampled:
        depth_path = depth_dir / Path(rel_path).name
        if not depth_path.is_file():
            continue
        depth = np.asarray(Image.open(depth_path), dtype=np.float32) / depth_scale
        finite = np.isfinite(depth)
        total_pixels += int(finite.sum())
        valid = finite & (depth > 0.0)
        valid_pixels += int(valid.sum())
        trunc_pixels += int(np.count_nonzero(valid & (depth <= depth_trunc)))
        if np.any(valid):
            positive.append(depth[valid])

    if not positive or total_pixels <= 0:
        return None

    merged = np.concatenate(positive, axis=0)
    return {
        "valid_rate": valid_pixels / total_pixels,
        "trunc_rate": trunc_pixels / total_pixels,
        "p01_mm": float(np.percentile(merged, 1) * 1000.0),
        "p50_mm": float(np.percentile(merged, 50) * 1000.0),
        "p95_mm": float(np.percentile(merged, 95) * 1000.0),
        "p99_mm": float(np.percentile(merged, 99) * 1000.0),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cfg", required=True)
    parser.add_argument("--depth-scale", type=float, default=5000.0)
    parser.add_argument("--depth-trunc", type=float, default=5.0)
    args = parser.parse_args()

    cfg = load_config(Path(args.cfg))
    dataset = cfg["dataset"]
    cam = cfg["cam"]
    scene = cfg["scene"]
    input_root = resolve_input_root(cfg)
    output_root = Path(cfg["data"]["output"])
    scene_root = output_root / scene
    max_frames = int(cfg.get("max_frames", -1))
    use_precomputed = bool(cfg.get("tracking", {}).get("use_precomputed_mono_depth", False))

    expected_rgb = (int(cam["H"]), int(cam["W"]))
    expected_prior = (int(cam["H_out"]), int(cam["W_out"]))

    print(f"DATASET {dataset}")
    print(f"SCENE {scene}")
    print(f"INPUT_ROOT {input_root}")
    print(f"SCENE_ROOT {scene_root}")
    print(f"EXPECTED_RGB_HW {expected_rgb[0]} {expected_rgb[1]}")
    print(f"EXPECTED_PRIOR_HW {expected_prior[0]} {expected_prior[1]}")
    print(f"USE_PRECOMPUTED_MONO_DEPTH {int(use_precomputed)}")

    rgb_dir = input_root / "rgb"
    if not rgb_dir.is_dir():
        raise SystemExit(f"PRECHECK_FAIL missing_rgb_dir {rgb_dir}")
    rgb_files = sorted(rgb_dir.glob("*"))
    rgb_count = len([file for file in rgb_files if file.is_file()])
    print(f"RGB_COUNT {rgb_count}")
    if dataset != "tumrgbd" and max_frames > 0 and rgb_count < max_frames:
        raise SystemExit(f"PRECHECK_FAIL rgb_count_lt_max_frames {rgb_count} < {max_frames}")
    if rgb_count <= 0:
        raise SystemExit("PRECHECK_FAIL no_rgb_images")
    rgb_size = first_image_size(rgb_dir)
    if rgb_size is None:
        raise SystemExit("PRECHECK_FAIL no_rgb_images")
    print(f"RGB_HW {rgb_size[0]} {rgb_size[1]}")
    if rgb_size != expected_rgb:
        raise SystemExit(f"PRECHECK_FAIL rgb_shape_mismatch got={rgb_size} expected={expected_rgb}")

    if dataset == "tumrgbd":
        depth_dir = input_root / "depth"
        if not depth_dir.is_dir():
            raise SystemExit(f"PRECHECK_FAIL missing_depth_dir {depth_dir}")
        depth_files = sorted(depth_dir.glob("*"))
        depth_count = len([file for file in depth_files if file.is_file()])
        print(f"DEPTH_COUNT {depth_count}")
        if depth_count <= 0:
            raise SystemExit("PRECHECK_FAIL no_depth_images")
        depth_size = first_image_size(depth_dir)
        if depth_size is None:
            raise SystemExit("PRECHECK_FAIL no_depth_images")
        print(f"DEPTH_HW {depth_size[0]} {depth_size[1]}")
        if depth_size != expected_rgb:
            raise SystemExit(f"PRECHECK_FAIL tumrgbd_depth_shape_mismatch got={depth_size} expected={expected_rgb}")
        gt_txt = input_root / "groundtruth.txt"
        if not gt_txt.is_file():
            raise SystemExit(f"PRECHECK_FAIL missing_groundtruth {gt_txt}")
        gt_count = len([line for line in gt_txt.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")])
        print(f"GROUNDTRUTH_COUNT {gt_count}")
        if max_frames > 0 and gt_count < max_frames:
            raise SystemExit(f"PRECHECK_FAIL groundtruth_count_lt_max_frames {gt_count} < {max_frames}")

        workpoint_path = input_root / "capture_workpoint.json"
        if workpoint_path.is_file():
            workpoint = json.loads(workpoint_path.read_text(encoding="utf-8"))
            print(f"WORKPOINT_META {workpoint_path}")
            print(f"WORKPOINT_PARSER {workpoint.get('parser', 'unknown')}")

        rgb_entries = load_tum_entries(input_root / "rgb.txt")
        depth_entries = load_tum_entries(input_root / "depth.txt")
        pose_entries = load_tum_entries(gt_txt)
        if rgb_entries:
            print_cadence_stats("RGB", [timestamp for timestamp, _ in rgb_entries])
        if depth_entries:
            print_cadence_stats("DEPTH", [timestamp for timestamp, _ in depth_entries])
        if rgb_entries and depth_entries:
            pair_count = min(len(rgb_entries), len(depth_entries))
            sync_deltas = [
                abs(rgb_entries[index][0] - depth_entries[index][0])
                for index in range(pair_count)
            ]
            if sync_deltas:
                print(f"RGB_DEPTH_SYNC_MEAN_DT_S {float(np.mean(sync_deltas)):.6f}")
                print(f"RGB_DEPTH_SYNC_MAX_DT_S {float(np.max(sync_deltas)):.6f}")
        associations = associate_tum_entries(rgb_entries, depth_entries, pose_entries)
        if associations:
            assoc_sync = [
                abs(float(rgb_entries[rgb_index][0]) - float(depth_entries[depth_index][0]))
                for rgb_index, depth_index, _ in associations
            ]
            selected_associations = select_loader_indices(rgb_entries, associations)
            selected_rgb_timestamps = [
                float(rgb_entries[associations[assoc_index][0]][0])
                for assoc_index in selected_associations
            ]
            print(f"TUM_ASSOC_COUNT {len(associations)}")
            print(f"TUM_ASSOC_RGB_DEPTH_MEAN_DT_S {float(np.mean(assoc_sync)):.6f}")
            print(f"TUM_ASSOC_RGB_DEPTH_MAX_DT_S {float(np.max(assoc_sync)):.6f}")
            print(f"TUM_ASSOC_FRAME_RATE60_COUNT {len(selected_associations)}")
            print_cadence_stats("TUM_ASSOC_RGB", selected_rgb_timestamps)

        depth_stats = sample_depth_distribution(
            depth_dir,
            depth_entries,
            args.depth_scale,
            args.depth_trunc,
        )
        if depth_stats is None:
            raise SystemExit("PRECHECK_FAIL unreadable_depth_distribution")
        print(f"DEPTH_SCALE {args.depth_scale}")
        print(f"DEPTH_TRUNC_M {args.depth_trunc}")
        print(f"DEPTH_VALID_RATE {depth_stats['valid_rate']:.6f}")
        print(f"DEPTH_TRUNC_RATE {depth_stats['trunc_rate']:.6f}")
        print(f"DEPTH_P01_MM {depth_stats['p01_mm']:.3f}")
        print(f"DEPTH_P50_MM {depth_stats['p50_mm']:.3f}")
        print(f"DEPTH_P95_MM {depth_stats['p95_mm']:.3f}")
        print(f"DEPTH_P99_MM {depth_stats['p99_mm']:.3f}")
        if depth_stats["valid_rate"] <= 0.01:
            raise SystemExit(f"PRECHECK_FAIL depth_valid_rate_too_low {depth_stats['valid_rate']:.6f}")
        if depth_stats["trunc_rate"] <= 0.01:
            raise SystemExit(f"PRECHECK_FAIL depth_trunc_rate_too_low {depth_stats['trunc_rate']:.6f}")

    if use_precomputed:
        prior_dir = scene_root / "mono_priors" / "depths"
        if not prior_dir.is_dir():
            raise SystemExit(f"PRECHECK_FAIL missing_prior_dir {prior_dir}")
        prior_files = sorted(prior_dir.glob("*.npy"))
        print(f"PRIOR_COUNT {len(prior_files)}")
        if not prior_files:
            raise SystemExit("PRECHECK_FAIL no_precomputed_priors")
        prior_shape = first_depth_shape(prior_dir)
        if prior_shape is None:
            raise SystemExit("PRECHECK_FAIL unreadable_precomputed_priors")
        print(f"PRIOR_HW {prior_shape[0]} {prior_shape[1]}")
        if tuple(prior_shape) != expected_prior:
            raise SystemExit(
                f"PRECHECK_FAIL prior_shape_mismatch got={prior_shape} expected={expected_prior}"
            )
        indices = collect_numeric_stems(prior_dir, "*.npy")
        if indices:
            print(f"PRIOR_MINMAX {min(indices)} {max(indices)}")
            if max_frames > 0 and max(indices) >= max_frames:
                raise SystemExit(
                    f"PRECHECK_FAIL prior_index_out_of_range max={max(indices)} max_frames={max_frames}"
                )

    print("PRECHECK_OK")


if __name__ == "__main__":
    main()
