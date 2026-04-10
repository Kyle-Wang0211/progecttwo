from __future__ import annotations

import importlib.util
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any

import numpy as np

from ..config import config
from ..context import JobContext


def bridge_slam3r_to_sparse2dgs_scene(ctx: JobContext) -> Path:
    assert ctx.slam3r_dir is not None
    assert ctx.sparse2dgs_dir is not None

    preds_dir = ctx.slam3r_dir / "preds"
    if not preds_dir.exists():
        raise RuntimeError(f"slam3r_preds_missing: {preds_dir}")

    local_pcds = np.load(preds_dir / "local_pcds.npy")
    registered_pcds = np.load(preds_dir / "registered_pcds.npy")
    rgb_imgs = np.load(preds_dir / "input_imgs.npy")

    if local_pcds.shape[0] == 0 or registered_pcds.shape[0] == 0 or rgb_imgs.shape[0] == 0:
        raise RuntimeError("slam3r_preds_empty")
    if not (local_pcds.shape[0] == registered_pcds.shape[0] == rgb_imgs.shape[0]):
        raise RuntimeError("slam3r_preds_count_mismatch")

    recon_utils = _load_slam3r_recon_utils()
    rotmat2qvec = _load_sparse2dgs_rotmat2qvec()

    scene_root = Path(config.sparse2dgs_repo) / "DTU_Sparse" / ctx.job_id
    if scene_root.exists():
        shutil.rmtree(scene_root)
    images_dir = scene_root / "images"
    sparse_dir = scene_root / "sparse" / "0"
    images_dir.mkdir(parents=True, exist_ok=True)
    sparse_dir.mkdir(parents=True, exist_ok=True)

    width = int(rgb_imgs.shape[2])
    height = int(rgb_imgs.shape[1])
    image_lines: list[str] = []
    camera_lines: list[str] = []
    points_lines: list[str] = []
    point_id = 1

    from PIL import Image
    import torch

    for frame_idx in range(local_pcds.shape[0]):
        img_name = f"{frame_idx:05d}"
        image_filename = f"{img_name}.png"
        image_path = images_dir / image_filename

        image = _normalize_image(rgb_imgs[frame_idx])
        Image.fromarray(image, mode="RGB").save(image_path)

        local_pts = torch.from_numpy(local_pcds[frame_idx : frame_idx + 1]).float()
        intrinsic = np.asarray(recon_utils.estimate_intrinsics(local_pts), dtype=np.float64)

        registered_pts = torch.from_numpy(registered_pcds[frame_idx]).float()
        c2w, success = recon_utils.estimate_camera_pose(registered_pts, intrinsic)
        if not success:
            raise RuntimeError(f"slam3r_pose_estimation_failed:{img_name}")
        w2c = np.linalg.inv(np.asarray(c2w, dtype=np.float64))

        fx = float(intrinsic[0, 0])
        fy = float(intrinsic[1, 1])
        cx = float(intrinsic[0, 2])
        cy = float(intrinsic[1, 2])
        camera_id = frame_idx + 1
        camera_lines.append(
            f"{camera_id} PINHOLE {width} {height} {fx:.8f} {fy:.8f} {cx:.8f} {cy:.8f}"
        )

        rotation = w2c[:3, :3]
        translation = w2c[:3, 3]
        qvec = rotmat2qvec(rotation)
        image_lines.append(
            f"{camera_id} {qvec[0]:.12f} {qvec[1]:.12f} {qvec[2]:.12f} {qvec[3]:.12f} "
            f"{translation[0]:.12f} {translation[1]:.12f} {translation[2]:.12f} "
            f"{camera_id} {image_filename}"
        )
        image_lines.append("")

        depth_values = np.asarray(local_pcds[frame_idx][..., 2], dtype=np.float64)
        valid_depths = depth_values[np.isfinite(depth_values) & (depth_values > 0)]
        if valid_depths.size == 0:
            raise RuntimeError(f"slam3r_depth_range_failed:{img_name}")
        depth_min = float(valid_depths.min())
        depth_max = float(valid_depths.max())
        _write_sparse2dgs_cam_file(
            scene_root / f"cam_{img_name}.txt",
            intrinsic=intrinsic,
            w2c=w2c,
            depth_min=depth_min,
            depth_max=depth_max,
        )

        frame_points = np.asarray(registered_pcds[frame_idx], dtype=np.float64).reshape(-1, 3)
        frame_colors = image.reshape(-1, 3)
        finite_mask = np.isfinite(frame_points).all(axis=1)
        frame_points = frame_points[finite_mask]
        frame_colors = frame_colors[finite_mask]
        for xyz, rgb in zip(frame_points, frame_colors):
            points_lines.append(
                f"{point_id} {xyz[0]:.8f} {xyz[1]:.8f} {xyz[2]:.8f} "
                f"{int(rgb[0])} {int(rgb[1])} {int(rgb[2])} 1.0"
            )
            point_id += 1

    (sparse_dir / "cameras.txt").write_text(
        "# Camera list with one line of data per camera:\n"
        "#   CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n"
        "# Number of cameras: {}\n".format(len(camera_lines))
        + "\n".join(camera_lines)
        + "\n",
        encoding="utf-8",
    )
    (sparse_dir / "images.txt").write_text(
        "# Image list with two lines of data per image:\n"
        "#   IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, IMAGE_NAME\n"
        "#   POINTS2D[] as (X, Y, POINT3D_ID)\n"
        "# Number of images: {}, mean observations per image: 0\n".format(local_pcds.shape[0])
        + "\n".join(image_lines)
        + "\n",
        encoding="utf-8",
    )
    (sparse_dir / "points3D.txt").write_text(
        "# 3D point list with one line of data per point:\n"
        "#   POINT3D_ID, X, Y, Z, R, G, B, ERROR\n"
        "# Number of points: {}\n".format(point_id - 1)
        + "\n".join(points_lines)
        + "\n",
        encoding="utf-8",
    )

    contract = {
        "scene_dir": str(scene_root),
        "images_dir": str(images_dir),
        "sparse_dir": str(sparse_dir),
        "frame_count": int(local_pcds.shape[0]),
        "point_count": int(point_id - 1),
        "paper_stack": {
            "reconstruction": "SLAM3R",
            "surface": "Sparse2DGS",
        },
    }
    summary_path = ctx.slam3r_dir / "sparse2dgs_scene_contract.json"
    summary_path.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")

    ctx.sparse2dgs_scene_dir = scene_root
    return scene_root


def bridge_sparse2dgs_to_sugar_inputs(ctx: JobContext) -> tuple[Path, Path]:
    assert ctx.sparse2dgs_dir is not None
    assert ctx.sugar_dir is not None

    scene_dir = ctx.sparse2dgs_scene_dir
    if scene_dir is None or not scene_dir.exists():
        raise RuntimeError("sparse2dgs_scene_contract_missing")

    checkpoint_dir = ctx.sparse2dgs_dir
    source_cameras = checkpoint_dir / "cameras.json"
    source_cfg_args = checkpoint_dir / "cfg_args"
    source_input_ply = checkpoint_dir / "input.ply"
    source_point_cloud_root = checkpoint_dir / "point_cloud"
    if not source_point_cloud_root.exists():
        raise RuntimeError(f"sugar_native_checkpoint_missing:{source_point_cloud_root}")

    resolved_iteration = _resolve_sparse2dgs_iteration_to_load(source_point_cloud_root)
    source_point_cloud_ply = (
        source_point_cloud_root
        / f"iteration_{resolved_iteration}"
        / "point_cloud.ply"
    )
    required = [
        source_cameras,
        source_cfg_args,
        source_point_cloud_ply,
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"sugar_native_checkpoint_missing:{';'.join(missing)}")

    compat_dir = ctx.sugar_dir / "gaussian_splatting_checkpoint"
    if compat_dir.exists():
        shutil.rmtree(compat_dir)
    compat_dir.mkdir(parents=True, exist_ok=True)
    (compat_dir / "point_cloud").mkdir(parents=True, exist_ok=True)

    _link_or_copy(source_cameras, compat_dir / "cameras.json")
    _link_or_copy(source_cfg_args, compat_dir / "cfg_args")
    if source_input_ply.exists():
        _link_or_copy(source_input_ply, compat_dir / "input.ply")

    compat_point_cloud_dir = compat_dir / "point_cloud" / "iteration_7000"
    compat_point_cloud_dir.mkdir(parents=True, exist_ok=True)
    _link_or_copy(source_point_cloud_ply, compat_point_cloud_dir / "point_cloud.ply")

    contract = {
        "scene_dir": str(scene_dir),
        "gs_output_dir": str(compat_dir),
        "sparse2dgs_output_dir": str(checkpoint_dir),
        "source_iteration_to_load": int(resolved_iteration),
        "sugar_compat_iteration_to_load": 7000,
        "compat_files": {
            "cameras_json": str(compat_dir / "cameras.json"),
            "cfg_args": str(compat_dir / "cfg_args"),
            "input_ply": str(compat_dir / "input.ply") if source_input_ply.exists() else None,
            "point_cloud_ply": str(compat_point_cloud_dir / "point_cloud.ply"),
        },
        "paper_stack": {
            "surface": "Sparse2DGS",
            "mesh": "SuGaR",
        },
    }
    summary_path = ctx.sparse2dgs_dir / "sugar_contract.json"
    summary_path.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")

    ctx.sugar_scene_dir = scene_dir
    ctx.sugar_gs_output_dir = compat_dir
    return scene_dir, compat_dir


def _resolve_sparse2dgs_iteration_to_load(point_cloud_root: Path) -> int:
    iterations: list[int] = []
    for child in point_cloud_root.iterdir():
        if not child.is_dir():
            continue
        if not child.name.startswith("iteration_"):
            continue
        suffix = child.name.split("iteration_", 1)[1]
        try:
            iteration = int(suffix)
        except ValueError:
            continue
        if (child / "point_cloud.ply").exists():
            iterations.append(iteration)
    if not iterations:
        raise RuntimeError(f"sparse2dgs_point_cloud_missing:{point_cloud_root}")
    return max(iterations)


def _link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        destination.unlink()
    try:
        destination.symlink_to(source)
    except OSError:
        shutil.copy2(source, destination)


def _load_slam3r_recon_utils():
    repo_dir = Path(config.slam3r_repo)
    if not repo_dir.exists():
        raise RuntimeError(f"slam3r_repo_missing:{repo_dir}")
    repo_root = str(repo_dir)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    module_path = repo_dir / "slam3r" / "utils" / "recon_utils.py"
    spec = importlib.util.spec_from_file_location("slam3r_recon_utils", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("slam3r_recon_utils_load_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_sparse2dgs_rotmat2qvec():
    repo_dir = Path(config.sparse2dgs_repo)
    module_path = repo_dir / "scene" / "colmap_loader.py"
    spec = importlib.util.spec_from_file_location("sparse2dgs_colmap_loader", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("sparse2dgs_colmap_loader_missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.rotmat2qvec


def _normalize_image(array: np.ndarray) -> np.ndarray:
    image = np.asarray(array)
    if image.dtype.kind == "f":
        max_value = float(np.nanmax(image)) if image.size else 0.0
        if max_value <= 1.0 + 1e-6:
            image = image * 255.0
    image = np.nan_to_num(image, nan=0.0, posinf=255.0, neginf=0.0)
    image = np.clip(image, 0.0, 255.0).astype(np.uint8)
    return image


def _write_sparse2dgs_cam_file(
    path: Path,
    *,
    intrinsic: np.ndarray,
    w2c: np.ndarray,
    depth_min: float,
    depth_max: float,
) -> None:
    if not math.isfinite(depth_min) or not math.isfinite(depth_max) or depth_max <= depth_min:
        raise RuntimeError(f"sparse2dgs_cam_depth_range_invalid:{path.name}")
    rows = []
    for row in intrinsic:
        rows.append(" ".join(f"{float(value):.12f}" for value in row))
    for row in w2c:
        rows.append(" ".join(f"{float(value):.12f}" for value in row))
    rows.append(f"{depth_min:.12f} {depth_max:.12f}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
