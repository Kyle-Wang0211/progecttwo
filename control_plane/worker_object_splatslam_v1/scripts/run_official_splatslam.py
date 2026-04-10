#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import os
import sys
from pathlib import Path

import cv2
import numpy as np

_OFFICIAL_SAVE_GAUSSIANS = None


def emit_progress(
    *,
    stage: str,
    title: str,
    detail: str,
    progress_fraction: float,
    metrics: dict[str, object] | None = None,
) -> None:
    payload = {
        "stage": stage,
        "title": title,
        "detail": detail,
        "progress_fraction": progress_fraction,
        "metrics": metrics or {},
    }
    print(f"AETHER_PROGRESS {json.dumps(payload, ensure_ascii=False)}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run official Splat-SLAM on curated object frames.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--entrypoint", default="run.py")
    parser.add_argument("--images-dir", required=True)
    parser.add_argument("--masks-dir", default="")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--summary-json", required=True)
    parser.add_argument("--export-format", default="ply")
    parser.add_argument("--horizontal-fov-degrees", type=float, default=60.0)
    parser.add_argument("--final-refine-iters", type=int, default=60)
    parser.add_argument("--tracking-warmup", type=int, default=4)
    parser.add_argument("--enable-online-ba", action="store_true")
    parser.add_argument("--init-iters", type=int, default=90)
    parser.add_argument("--mapping-iters", type=int, default=12)
    parser.add_argument("--frontend-init-update-iters", type=int, default=2)
    parser.add_argument("--frontend-init-proximity-radius", type=int, default=1)
    parser.add_argument("--frontend-init-proximity-nms", type=int, default=1)
    parser.add_argument("--frontend-keyframe-thresh", type=float, default=2.0)
    parser.add_argument("--frontend-motion-filter-thresh", type=float, default=1.0)
    parser.add_argument("--frontend-window", type=int, default=12)
    parser.add_argument("--frontend-max-factors", type=int, default=48)
    parser.add_argument("--frontend-use-lowmem-update", action="store_true")
    parser.add_argument("--frontend-lowmem-steps", type=int, default=1)
    parser.add_argument("--frontend-skip-video-ba", default="0")
    parser.add_argument("--output-max-edge", type=int, default=320)
    parser.add_argument("--apply-masks-to-rgb", action="store_true")
    return parser.parse_args()


def ensure_repo_importable(repo_path: Path) -> None:
    repo_str = str(repo_path.resolve())
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)


def infer_intrinsics(image_shape: tuple[int, int], horizontal_fov_degrees: float) -> tuple[float, float, float, float]:
    height, width = image_shape
    hfov = math.radians(max(35.0, min(horizontal_fov_degrees, 95.0)))
    fx = (0.5 * width) / math.tan(0.5 * hfov)
    fy = fx
    cx = width * 0.5
    cy = height * 0.5
    return fx, fy, cx, cy


def write_masked_frame(
    source: Path,
    mask_path: Path | None,
    destination: Path,
    *,
    apply_masks_to_rgb: bool,
) -> None:
    bgr = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if bgr is None:
        raise RuntimeError(f"failed_to_read_image:{source}")
    if apply_masks_to_rgb and mask_path is not None and mask_path.exists():
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if mask is not None:
            keep = mask > 0
            softened = bgr.copy()
            softened[~keep] = (softened[~keep] * 0.08).astype(np.uint8)
            bgr = softened
    cv2.imwrite(str(destination), bgr)


def build_pseudo_tum_dataset(
    *,
    images_dir: Path,
    masks_dir: Path | None,
    dataset_root: Path,
    scene_name: str,
    apply_masks_to_rgb: bool,
) -> tuple[Path, tuple[int, int]]:
    scene_root = dataset_root / scene_name
    rgb_dir = scene_root / "rgb"
    depth_dir = scene_root / "depth"
    rgb_dir.mkdir(parents=True, exist_ok=True)
    depth_dir.mkdir(parents=True, exist_ok=True)

    image_paths = sorted(path for path in images_dir.iterdir() if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"})
    if len(image_paths) < 4:
        raise RuntimeError("splatslam_not_enough_curated_frames")

    first_image = cv2.imread(str(image_paths[0]), cv2.IMREAD_COLOR)
    if first_image is None:
        raise RuntimeError("splatslam_first_frame_unreadable")
    height, width = first_image.shape[:2]
    depth_template = np.full((height, width), 1000, dtype=np.uint16)

    rgb_lines = ["# timestamp rgb_path"]
    depth_lines = ["# timestamp depth_path"]
    gt_lines = ["# timestamp tx ty tz qx qy qz qw"]
    for index, image_path in enumerate(image_paths):
        timestamp = float(index) * 0.0333333
        rgb_name = f"{index:05d}.jpg"
        depth_name = f"{index:05d}.png"
        mask_path = masks_dir / f"{image_path.stem}.png" if masks_dir else None
        write_masked_frame(
            image_path,
            mask_path,
            rgb_dir / rgb_name,
            apply_masks_to_rgb=apply_masks_to_rgb,
        )
        cv2.imwrite(str(depth_dir / depth_name), depth_template)
        rgb_lines.append(f"{timestamp:.6f} rgb/{rgb_name}")
        depth_lines.append(f"{timestamp:.6f} depth/{depth_name}")
        gt_lines.append(f"{timestamp:.6f} 0 0 0 0 0 0 1")

    (scene_root / "rgb.txt").write_text("\n".join(rgb_lines) + "\n", encoding="utf-8")
    (scene_root / "depth.txt").write_text("\n".join(depth_lines) + "\n", encoding="utf-8")
    (scene_root / "groundtruth.txt").write_text("\n".join(gt_lines) + "\n", encoding="utf-8")
    return scene_root, (height, width)


def write_scene_config(
    *,
    repo_path: Path,
    output_dir: Path,
    dataset_root: Path,
    scene_name: str,
    image_shape: tuple[int, int],
    horizontal_fov_degrees: float,
    final_refine_iters: int,
    tracking_warmup: int,
    enable_online_ba: bool,
    init_iters: int,
    mapping_iters: int,
    frontend_init_update_iters: int,
    frontend_init_proximity_radius: int,
    frontend_init_proximity_nms: int,
    frontend_keyframe_thresh: float,
    frontend_motion_filter_thresh: float,
    frontend_window: int,
    frontend_max_factors: int,
    frontend_use_lowmem_update: bool,
    frontend_lowmem_steps: int,
    frontend_skip_video_ba: bool,
    output_max_edge: int,
) -> Path:
    height, width = image_shape
    fx, fy, cx, cy = infer_intrinsics(image_shape, horizontal_fov_degrees)
    scale = min(1.0, float(max(192, output_max_edge)) / max(float(height), float(width)))
    out_height = max(192, int(round(height * scale)))
    out_width = max(192, int(round(width * scale)))

    # The official DepthVideo preallocates coarse tensors with integer //8 sizes,
    # while mono priors are sampled using [3::8]. Keep output resolution aligned to
    # multiples of 8 so mono depth and coarse buffers agree.
    out_height = max(192, (out_height // 8) * 8)
    out_width = max(192, (out_width // 8) * 8)
    if (out_height % 8) != 0 or (out_width % 8) != 0:
        raise RuntimeError(
            f"splatslam_invalid_output_resolution:{out_height}x{out_width}"
        )
    config_path = output_dir / "object_capture.yaml"
    lines = [
        f"inherit_from: {repo_path / 'configs' / 'TUM_RGBD' / 'tum.yaml'}",
        "dataset: tumrgbd",
        f"scene: {scene_name}",
        "verbose: True",
        "stride: 1",
        "max_frames: -1",
        "only_tracking: False",
        "tracking:",
        f"  pretrained: {repo_path / 'pretrained' / 'droid.pth'}",
        f"  warmup: {max(2, tracking_warmup)}",
        "  motion_filter:",
        f"    thresh: {max(0.1, frontend_motion_filter_thresh):.3f}",
        "  frontend:",
        "    enable_loop: False",
        f"    enable_online_ba: {'True' if enable_online_ba else 'False'}",
        f"    keyframe_thresh: {max(0.1, frontend_keyframe_thresh):.3f}",
        f"    window: {max(4, frontend_window)}",
        f"    max_factors: {max(8, frontend_max_factors)}",
        f"    init_update_iters: {max(1, frontend_init_update_iters)}",
        f"    init_proximity_radius: {max(0, frontend_init_proximity_radius)}",
        f"    init_proximity_nms: {max(0, frontend_init_proximity_nms)}",
        f"    use_lowmem_update: {'True' if frontend_use_lowmem_update else 'False'}",
        f"    lowmem_steps: {max(1, frontend_lowmem_steps)}",
        f"    skip_video_ba: {'True' if frontend_skip_video_ba else 'False'}",
        "  backend:",
        "    final_ba: True",
        "mapping:",
        f"  final_refine_iters: {max(0, final_refine_iters)}",
        "  every_keyframe: 1",
        "  BA: False",
        "  Training:",
        f"    init_itr_num: {max(20, init_iters)}",
        f"    mapping_itr_num: {max(5, mapping_iters)}",
        "meshing:",
        "  mesh: True",
        "mono_prior:",
        f"  depth_pretrained: {repo_path / 'pretrained' / 'omnidata_dpt_depth_v2.ckpt'}",
        "  predict_online: True",
        "cam:",
        f"  H: {height}",
        f"  W: {width}",
        f"  fx: {fx:.4f}",
        f"  fy: {fy:.4f}",
        f"  cx: {cx:.4f}",
        f"  cy: {cy:.4f}",
        "  png_depth_scale: 5000.0",
        "  H_edge: 0",
        "  W_edge: 0",
        f"  H_out: {out_height}",
        f"  W_out: {out_width}",
        "data:",
        f"  dataset_root: {dataset_root}",
        f"  output: {output_dir / 'official_output'}",
        f"  input_folder: {scene_name}",
    ]
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path


def load_output_resolution(config_path: Path) -> tuple[int | None, int | None]:
    out_height = None
    out_width = None
    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("H_out:"):
            try:
                out_height = int(line.split(":", 1)[1].strip())
            except ValueError:
                out_height = None
        elif line.startswith("W_out:"):
            try:
                out_width = int(line.split(":", 1)[1].strip())
            except ValueError:
                out_width = None
    return out_height, out_width


def _product_terminate(self):
    global _OFFICIAL_SAVE_GAUSSIANS

    if not self.only_tracking and self.mapper is not None:
        final_refine_iters = int(self.cfg.get("mapping", {}).get("final_refine_iters", 0) or 0)
        if final_refine_iters > 0:
            self.printer.print(
                f"Product final refine triggered ({final_refine_iters} iters)."
            )
            self.mapper.final_refine(iters=final_refine_iters)
    if (
        not self.only_tracking
        and self.mapper is not None
        and getattr(self.mapper, "gaussians", None) is not None
        and _OFFICIAL_SAVE_GAUSSIANS is not None
    ):
        _OFFICIAL_SAVE_GAUSSIANS(self.mapper.gaussians, self.save_dir, 0, final=True)
        payload = {
            "keyframe_count": int(len(getattr(self.mapper, "video_idxs", []))),
            "video_indices": [int(value) for value in getattr(self.mapper, "video_idxs", [])],
        }
        summary_path = Path(self.save_dir) / "product_terminate.json"
        summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    self.printer.print("Product terminate completed (benchmark evaluation skipped).")


def import_official(repo_path: Path):
    ensure_repo_importable(repo_path)
    from src.slam import SLAM as OfficialSLAM
    from src.utils.datasets import get_dataset
    from src.utils.eval_utils import save_gaussians
    from thirdparty.glorie_slam import config as official_config

    global _OFFICIAL_SAVE_GAUSSIANS
    _OFFICIAL_SAVE_GAUSSIANS = save_gaussians
    OfficialSLAM.terminate = _product_terminate
    return OfficialSLAM, official_config, get_dataset


def find_exported_ply(save_dir: Path) -> Path:
    candidate = save_dir / "point_cloud" / "final" / "point_cloud.ply"
    if candidate.exists():
        return candidate
    for fallback in sorted(save_dir.rglob("point_cloud.ply")):
        return fallback
    raise RuntimeError("splatslam_export_missing")


def load_product_summary(save_dir: Path) -> dict[str, object]:
    path = save_dir / "product_terminate.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_video_pose_count(save_dir: Path) -> int | None:
    path = save_dir / "video.npz"
    if not path.exists():
        return None
    try:
        data = np.load(path)
        poses = data.get("poses")
        if poses is None:
            return None
        return int(len(poses))
    except Exception:
        return None


def write_summary(*, summary_path: Path, scene_root: Path, config_path: Path, save_dir: Path, exported_ply: Path) -> None:
    from plyfile import PlyData

    ply = PlyData.read(str(exported_ply))
    vertices = ply["vertex"]
    xyz = np.stack(
        (
            np.asarray(vertices["x"], dtype=np.float64),
            np.asarray(vertices["y"], dtype=np.float64),
            np.asarray(vertices["z"], dtype=np.float64),
        ),
        axis=1,
    )
    center = xyz.mean(axis=0)
    extents = xyz.max(axis=0) - xyz.min(axis=0)
    radius = max(float(np.linalg.norm(extents) * 0.5), 0.25)
    product_summary = load_product_summary(save_dir)
    video_pose_count = load_video_pose_count(save_dir)
    payload = {
        "backend": "official_splat_slam",
        "dataset_scene_root": str(scene_root),
        "config_path": str(config_path),
        "save_dir": str(save_dir),
        "default_asset": str(exported_ply),
        "gaussian_ply": str(exported_ply),
        "center": [float(v) for v in center],
        "radius": radius,
        "up": [0.0, 1.0, 0.0],
        "point_count": int(len(xyz)),
        "raw_bounds": {
            "min": [float(v) for v in xyz.min(axis=0)],
            "max": [float(v) for v in xyz.max(axis=0)],
        },
        "keyframe_count": int(product_summary.get("keyframe_count", 0) or 0),
        "video_indices": product_summary.get("video_indices", []),
        "video_pose_count": video_pose_count,
    }
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    try:
        mp.set_start_method("spawn", force=True)
    except RuntimeError:
        pass

    args = parse_args()
    repo_path = Path(args.repo).expanduser().resolve()
    images_dir = Path(args.images_dir).expanduser().resolve()
    masks_dir = Path(args.masks_dir).expanduser().resolve() if args.masks_dir else None
    output_dir = Path(args.output_dir).expanduser().resolve()
    summary_path = Path(args.summary_json).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    scene_root, image_shape = build_pseudo_tum_dataset(
        images_dir=images_dir,
        masks_dir=masks_dir,
        dataset_root=output_dir / "dataset",
        scene_name="object_capture",
        apply_masks_to_rgb=args.apply_masks_to_rgb,
    )
    emit_progress(
        stage="splatslam_prepare",
        title="正在准备 Splat-SLAM 输入",
        detail="已完成对象关键帧整理，正在生成 Splat-SLAM 数据集。",
        progress_fraction=0.46,
        metrics={
            "curated_frame_count": len(list(images_dir.glob('*.jpg'))),
            "output_height": int(image_shape[0]),
            "output_width": int(image_shape[1]),
        },
    )
    config_path = write_scene_config(
        repo_path=repo_path,
        output_dir=output_dir,
        dataset_root=output_dir / "dataset",
        scene_name="object_capture",
        image_shape=image_shape,
        horizontal_fov_degrees=args.horizontal_fov_degrees,
        final_refine_iters=args.final_refine_iters,
        tracking_warmup=args.tracking_warmup,
        enable_online_ba=args.enable_online_ba,
        init_iters=args.init_iters,
        mapping_iters=args.mapping_iters,
        frontend_init_update_iters=args.frontend_init_update_iters,
        frontend_init_proximity_radius=args.frontend_init_proximity_radius,
        frontend_init_proximity_nms=args.frontend_init_proximity_nms,
        frontend_keyframe_thresh=args.frontend_keyframe_thresh,
        frontend_motion_filter_thresh=args.frontend_motion_filter_thresh,
        frontend_window=args.frontend_window,
        frontend_max_factors=args.frontend_max_factors,
        frontend_use_lowmem_update=args.frontend_use_lowmem_update,
        frontend_lowmem_steps=args.frontend_lowmem_steps,
        frontend_skip_video_ba=args.frontend_skip_video_ba.strip().lower() in {"1", "true", "yes", "on"},
        output_max_edge=args.output_max_edge,
    )
    out_height, out_width = load_output_resolution(config_path)
    if out_height is None or out_width is None:
        raise RuntimeError("splatslam_generated_config_missing_output_resolution")
    emit_progress(
        stage="splatslam_bootstrap",
        title="正在启动 Splat-SLAM",
        detail="配置已生成，正在初始化 tracking 和 mapping。",
        progress_fraction=0.52,
        metrics={
            "configured_output_height": out_height,
            "configured_output_width": out_width,
        },
    )

    ProductSLAM, official_config, get_dataset = import_official(repo_path)
    cwd_before = Path.cwd()
    os.chdir(repo_path)
    try:
        cfg = official_config.load_config(str(config_path), str(repo_path / "configs" / "splat_slam.yaml"))
        dataset = get_dataset(cfg)
        slam = ProductSLAM(cfg, dataset)
        emit_progress(
            stage="splatslam_tracking",
            title="正在执行 Splat-SLAM 跟踪",
            detail="正在恢复位姿并建立 object-level gaussian map。",
            progress_fraction=0.56,
        )
        slam.run()
    finally:
        os.chdir(cwd_before)

    save_dir = Path(cfg["data"]["output"]) / cfg["scene"]
    emit_progress(
        stage="splatslam_finalize",
        title="正在完成 Splat-SLAM",
        detail="Tracking 和 mapping 已完成，正在写出默认对象 splat。",
        progress_fraction=0.74,
    )
    exported_ply = find_exported_ply(save_dir)
    write_summary(
        summary_path=summary_path,
        scene_root=scene_root,
        config_path=config_path,
        save_dir=save_dir,
        exported_ply=exported_ply,
    )


if __name__ == "__main__":
    main()
