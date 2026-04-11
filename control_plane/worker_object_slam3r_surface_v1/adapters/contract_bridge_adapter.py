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
    assert ctx.curated_dir is not None

    preds_dir = ctx.slam3r_dir / "preds"
    if not preds_dir.exists():
        raise RuntimeError(f"slam3r_preds_missing: {preds_dir}")

    local_pcds = np.load(preds_dir / "local_pcds.npy")
    registered_pcds = np.load(preds_dir / "registered_pcds.npy")
    rgb_imgs = np.load(preds_dir / "input_imgs.npy")
    metadata = json.loads((preds_dir / "metadata.json").read_text(encoding="utf-8"))

    if local_pcds.shape[0] == 0 or registered_pcds.shape[0] == 0 or rgb_imgs.shape[0] == 0:
        raise RuntimeError("slam3r_preds_empty")
    if not (local_pcds.shape[0] == registered_pcds.shape[0] == rgb_imgs.shape[0]):
        raise RuntimeError("slam3r_preds_count_mismatch")

    recon_utils = _load_slam3r_recon_utils()
    rotmat2qvec = _load_sparse2dgs_rotmat2qvec()

    curated_frame_paths = sorted(
        path
        for path in ctx.curated_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    frame_quality_weights = _load_curated_frame_weights(ctx, curated_frame_paths)
    selected_frame_target = max(3, min(int(config.sparse2dgs_target_views), local_pcds.shape[0]))
    selected_frame_indices = _select_sparse2dgs_view_indices(
        local_pcds.shape[0],
        target_views=selected_frame_target,
        quality_weights=frame_quality_weights,
    )
    scene_root = Path(config.sparse2dgs_repo) / "DTU_Sparse" / ctx.job_id
    if scene_root.exists():
        shutil.rmtree(scene_root)
    images_dir = scene_root / "images"
    sparse_dir = scene_root / "sparse" / "0"
    images_dir.mkdir(parents=True, exist_ok=True)
    sparse_dir.mkdir(parents=True, exist_ok=True)

    predictor_source_width = int(rgb_imgs.shape[2])
    predictor_source_height = int(rgb_imgs.shape[1])
    source_width, source_height, width, height, bridge_image_source = _resolve_bridge_export_dimensions(
        curated_frame_paths=curated_frame_paths,
        fallback_width=predictor_source_width,
        fallback_height=predictor_source_height,
    )
    init_winsize = int(metadata.get("init_winsize", 0))
    kf_stride = int(metadata.get("kf_stride", 1))
    init_ref_id = int(metadata.get("init_ref_id", 0)) * kf_stride
    init_ids = list(range(0, init_winsize * kf_stride, kf_stride)) if init_winsize > 0 else []
    image_lines: list[str] = []
    camera_lines: list[str] = []
    points_lines: list[str] = []
    point_id = 1
    pose_failed_indices: list[int] = []

    from PIL import Image
    import torch

    principal_point = torch.tensor((local_pcds[0].shape[0] // 2, local_pcds[0].shape[1] // 2))
    init_window_focal = recon_utils.estimate_focal_knowing_depth(
        torch.tensor(local_pcds[init_ref_id : init_ref_id + 1]),
        principal_point,
        focal_mode="weiszfeld",
    )

    intrinsics: list[np.ndarray] = []
    for frame_idx in range(local_pcds.shape[0]):
        if frame_idx in init_ids:
            focal = init_window_focal
        else:
            focal = recon_utils.estimate_focal_knowing_depth(
                torch.tensor(local_pcds[frame_idx : frame_idx + 1]),
                principal_point,
                focal_mode="weiszfeld",
            )
        intrinsic = np.eye(3, dtype=np.float64)
        intrinsic[0, 0] = float(focal)
        intrinsic[1, 1] = float(focal)
        intrinsic[:2, 2] = principal_point.numpy().astype(np.float64)
        intrinsics.append(intrinsic)

    mean_intrinsic = np.mean(np.stack(intrinsics, axis=0), axis=0)

    for frame_idx in selected_frame_indices:
        img_name = f"{frame_idx:05d}"
        image_filename = f"{img_name}.png"
        image_path = images_dir / image_filename

        predictor_image = _normalize_image(rgb_imgs[frame_idx])
        curated_path = curated_frame_paths[frame_idx] if 0 <= frame_idx < len(curated_frame_paths) else None
        export_image = _load_bridge_export_image(
            curated_path=curated_path,
            fallback_image=predictor_image,
            width=width,
            height=height,
        )
        Image.fromarray(export_image, mode="RGB").save(image_path)

        intrinsic = intrinsics[frame_idx]
        if width != predictor_source_width or height != predictor_source_height:
            scale_x = float(width) / float(predictor_source_width)
            scale_y = float(height) / float(predictor_source_height)
            intrinsic = intrinsic.copy()
            intrinsic[0, :] *= scale_x
            intrinsic[1, :] *= scale_y

        registered_pts = torch.from_numpy(registered_pcds[frame_idx]).float()
        c2w, success = recon_utils.estimate_camera_pose(registered_pts, mean_intrinsic)
        if not success:
            pose_failed_indices.append(frame_idx)
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

        registered_frame_points = np.asarray(registered_pcds[frame_idx], dtype=np.float64).reshape(-1, 3)
        registered_h = np.concatenate(
            [registered_frame_points, np.ones((registered_frame_points.shape[0], 1), dtype=np.float64)],
            axis=1,
        )
        camera_points = (w2c @ registered_h.T).T[:, :3]
        depth_values = np.asarray(camera_points[:, 2], dtype=np.float64)
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

        frame_points = registered_frame_points
        frame_colors = predictor_image.reshape(-1, 3)
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
        "selected_frame_target": int(selected_frame_target),
        "selected_frame_indices": selected_frame_indices,
        "selected_frame_count": len(selected_frame_indices),
        "selected_curated_filenames": [
            curated_frame_paths[index].name
            for index in selected_frame_indices
            if 0 <= index < len(curated_frame_paths)
        ],
        "point_count": int(point_id - 1),
        "source_image_size": [source_width, source_height],
        "predictor_image_size": [predictor_source_width, predictor_source_height],
        "exported_image_size": [width, height],
        "bridge_image_source": bridge_image_source,
        "pose_failed_indices": pose_failed_indices,
        "pose_failed_count": len(pose_failed_indices),
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


def _load_curated_frame_weights(ctx: JobContext, curated_frame_paths: list[Path]) -> list[float] | None:
    summary_path = ctx.output_dir / "curate_frames.json"
    if not summary_path.exists():
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    frames = summary.get("frames")
    if not isinstance(frames, list):
        return None
    by_name: dict[str, float] = {}
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        name = str(frame.get("name") or "").strip()
        if not name:
            continue
        target_signal = float(frame.get("target_signal") or 0.0)
        orb_feature_count = float(frame.get("orb_feature_count") or 0.0)
        hard_penalty = len(frame.get("hard_reject_reasons") or [])
        soft_penalty = len(frame.get("soft_downgrade_reasons") or [])
        weight = 1.0
        weight += min(target_signal, 1.0) * 2.0
        weight += min(orb_feature_count / 1200.0, 1.5)
        weight -= hard_penalty * 0.50
        weight -= soft_penalty * 0.20
        by_name[name] = max(weight, 0.05)
    weights = [by_name.get(path.name, 1.0) for path in curated_frame_paths]
    return weights if weights else None


def _select_sparse2dgs_view_indices(
    frame_count: int,
    *,
    target_views: int,
    quality_weights: list[float] | None = None,
) -> list[int]:
    if frame_count < target_views:
        raise RuntimeError(
            f"sparse2dgs_requires_{target_views}_views_but_only_{frame_count}_available"
        )
    if frame_count == target_views:
        return list(range(frame_count))
    if quality_weights and len(quality_weights) == frame_count:
        anchors = np.linspace(0, frame_count - 1, num=target_views).tolist()
        search_radius = max(2, frame_count // max(target_views * 2, 1))
        selected: list[int] = []
        used: set[int] = set()
        for anchor in anchors:
            best_index: int | None = None
            best_score = float("-inf")
            for candidate in range(frame_count):
                if candidate in used:
                    continue
                distance = abs(candidate - anchor)
                if distance > search_radius and best_index is not None:
                    continue
                score = float(quality_weights[candidate]) - (distance / max(search_radius, 1)) * 0.18
                if score > best_score:
                    best_score = score
                    best_index = candidate
            if best_index is None:
                raise RuntimeError("sparse2dgs_view_selection_failed")
            used.add(best_index)
            selected.append(best_index)
        selected.sort()
        return selected
    indices = np.linspace(0, frame_count - 1, num=target_views)
    unique = sorted({int(round(value)) for value in indices.tolist()})
    if len(unique) != target_views:
        unique = []
        for value in indices.tolist():
            candidate = int(round(value))
            while candidate in unique and candidate < frame_count - 1:
                candidate += 1
            while candidate in unique and candidate > 0:
                candidate -= 1
            if candidate in unique:
                raise RuntimeError("sparse2dgs_view_selection_failed")
            unique.append(candidate)
        unique.sort()
    return unique


def _resize_image_if_needed(image: np.ndarray, *, width: int, height: int) -> np.ndarray:
    if image.shape[1] == width and image.shape[0] == height:
        return image
    from PIL import Image

    pil_image = Image.fromarray(image, mode="RGB")
    return np.asarray(pil_image.resize((width, height), Image.BILINEAR), dtype=np.uint8)


def _load_bridge_export_image(
    *,
    curated_path: Path | None,
    fallback_image: np.ndarray,
    width: int,
    height: int,
) -> np.ndarray:
    from PIL import Image

    if curated_path is not None and curated_path.exists():
        try:
            with Image.open(curated_path) as image:
                return np.asarray(
                    image.convert("RGB").resize((width, height), Image.BILINEAR),
                    dtype=np.uint8,
                )
        except Exception:
            pass
    return _resize_image_if_needed(fallback_image, width=width, height=height)


def _resolve_bridge_export_dimensions(
    *,
    curated_frame_paths: list[Path],
    fallback_width: int,
    fallback_height: int,
) -> tuple[int, int, int, int, str]:
    source_width = fallback_width
    source_height = fallback_height
    image_source = "slam3r_lowres_contract"
    for curated_path in curated_frame_paths:
        image_size = _read_image_size(curated_path)
        if image_size is None:
            continue
        source_width, source_height = image_size
        image_source = "curated_original_resized"
        break

    max_dim = max(256, int(config.sparse2dgs_contract_max_image_size))
    longest_side = max(source_width, source_height)
    scale = min(1.0, float(max_dim) / float(longest_side)) if longest_side > 0 else 1.0
    export_width = max(64, _round_up_to_multiple(int(round(source_width * scale)), 64))
    export_height = max(64, _round_up_to_multiple(int(round(source_height * scale)), 64))
    return source_width, source_height, export_width, export_height, image_source


def _read_image_size(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
        if width <= 0 or height <= 0:
            return None
        return int(width), int(height)
    except Exception:
        return None


def _round_up_to_multiple(value: int, multiple: int) -> int:
    if multiple <= 0:
        return value
    return int(math.ceil(float(value) / float(multiple)) * multiple)


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
