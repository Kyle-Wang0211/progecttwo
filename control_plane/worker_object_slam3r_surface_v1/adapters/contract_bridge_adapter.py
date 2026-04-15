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
from ..quality_gate import update_quality_card


def bridge_slam3r_to_sparse2dgs_scene(ctx: JobContext) -> Path:
    assert ctx.slam3r_dir is not None
    assert ctx.curated_dir is not None

    requested_target_views = (
        ctx.sparse2dgs_target_views_override
        if ctx.sparse2dgs_target_views_override is not None
        else int(config.sparse2dgs_target_views)
    )
    contract = build_sparse2dgs_scene_contract(
        ctx,
        requested_target_views=requested_target_views,
        max_image_size_override=ctx.sparse2dgs_contract_max_image_size_override,
        total_pixel_budget_override=ctx.sparse2dgs_contract_total_pixel_budget_override,
        write_geometry_report=True,
    )
    summary_path = ctx.slam3r_dir / "sparse2dgs_scene_contract.json"
    summary_path.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")
    ctx.sparse2dgs_scene_dir = Path(str(contract["scene_dir"]))
    return ctx.sparse2dgs_scene_dir


def build_sparse2dgs_scene_contract(
    ctx: JobContext,
    *,
    requested_target_views: int | None = None,
    max_image_size_override: int | None = None,
    total_pixel_budget_override: int | None = None,
    write_geometry_report: bool = False,
) -> dict[str, Any]:
    assert ctx.slam3r_dir is not None
    assert ctx.curated_dir is not None

    scene_inputs = _load_slam3r_scene_inputs(ctx)
    frame_count = int(scene_inputs["local_pcds"].shape[0])
    curated_frame_paths = scene_inputs["curated_frame_paths"]
    source_width = int(scene_inputs["source_width"])
    source_height = int(scene_inputs["source_height"])

    support_frame_indices = list(range(frame_count))
    if requested_target_views is None:
        requested_target_views = int(config.sparse2dgs_target_views)
    geometry_candidate_target = min(frame_count, max(8, int(requested_target_views)))
    geometry_candidate_indices = _select_sparse2dgs_view_indices(
        frame_count,
        target_views=geometry_candidate_target,
        quality_weights=scene_inputs["frame_quality_weights"],
        camera_matrices=scene_inputs["selection_camera_matrices"],
        images=scene_inputs["rgb_imgs"],
    )

    geometry_windows = _build_geometry_windows(
        frame_indices=geometry_candidate_indices,
        trigger_views=int(config.sparse2dgs_window_trigger_views),
        window_target_views=int(config.sparse2dgs_window_target_views),
        overlap_views=int(config.sparse2dgs_window_overlap_views),
        max_windows=int(config.sparse2dgs_window_max_count),
        enabled=bool(config.sparse2dgs_window_enabled),
    )
    geometry_batch_view_count = max(
        (len(window.get("selected_frame_indices") or []) for window in geometry_windows),
        default=len(geometry_candidate_indices),
    )
    geometry_target_views, geometry_width, geometry_height, bridge_budget = _resolve_bridge_export_plan(
        frame_count=max(geometry_batch_view_count, 1),
        requested_target_views=max(geometry_batch_view_count, 1),
        source_width=source_width,
        source_height=source_height,
        max_image_size_override=max_image_size_override,
        total_pixel_budget_override=total_pixel_budget_override,
    )
    if len(geometry_windows) <= 1 and geometry_target_views < len(geometry_candidate_indices):
        geometry_candidate_indices = geometry_candidate_indices[:geometry_target_views]
        geometry_windows = _build_geometry_windows(
            frame_indices=geometry_candidate_indices,
            trigger_views=int(config.sparse2dgs_window_trigger_views),
            window_target_views=int(config.sparse2dgs_window_target_views),
            overlap_views=int(config.sparse2dgs_window_overlap_views),
            max_windows=int(config.sparse2dgs_window_max_count),
            enabled=bool(config.sparse2dgs_window_enabled),
        )
        geometry_batch_view_count = max(
            (len(window.get("selected_frame_indices") or []) for window in geometry_windows),
            default=len(geometry_candidate_indices),
        )
        bridge_budget["resolved_target_views"] = int(len(geometry_candidate_indices))

    support_width, support_height = _resolve_support_scene_export_size(
        source_width=source_width,
        source_height=source_height,
    )
    scene_root = _scene_root_for_label(ctx.job_id, label="support")
    support_summary = _write_sparse2dgs_scene_variant(
        ctx=ctx,
        scene_inputs=scene_inputs,
        scene_root=scene_root,
        frame_indices=support_frame_indices,
        width=support_width,
        height=support_height,
    )

    contract = {
        "scene_dir": str(scene_root),
        "images_dir": str(scene_root / "images"),
        "sparse_dir": str(scene_root / "sparse" / "0"),
        "frame_count": frame_count,
        "support_frame_indices": support_frame_indices,
        "support_frame_count": len(support_frame_indices),
        "support_curated_filenames": [path.name for path in curated_frame_paths],
        "support_exported_image_size": [int(support_width), int(support_height)],
        "selected_frame_target": int(geometry_target_views),
        "selected_frame_target_requested": int(requested_target_views),
        "selected_frame_indices": geometry_candidate_indices,
        "selected_frame_count": len(geometry_candidate_indices),
        "selected_curated_filenames": [
            curated_frame_paths[index].name
            for index in geometry_candidate_indices
            if 0 <= index < len(curated_frame_paths)
        ],
        "geometry_windows": geometry_windows,
        "geometry_window_count": len(geometry_windows),
        "geometry_batch_view_count": int(geometry_batch_view_count),
        "point_count": int(support_summary["point_count"]),
        "source_image_size": [source_width, source_height],
        "predictor_image_size": [
            int(scene_inputs["predictor_source_width"]),
            int(scene_inputs["predictor_source_height"]),
        ],
        "exported_image_size": [int(geometry_width), int(geometry_height)],
        "bridge_image_source": "curated_original_resized",
        "bridge_budget": bridge_budget,
        "attempt_index": int(ctx.sparse2dgs_attempt_index),
        "pose_failed_indices": support_summary["pose_failed_indices"],
        "pose_failed_count": len(support_summary["pose_failed_indices"]),
        "paper_stack": {
            "reconstruction": "SLAM3R",
            "surface": "Sparse2DGS",
        },
    }
    if write_geometry_report:
        _write_geometry_hq_report(
            ctx=ctx,
            support_frame_indices=support_frame_indices,
            selected_frame_indices=geometry_candidate_indices,
            curated_frame_paths=curated_frame_paths,
            camera_matrices=scene_inputs["selection_camera_matrices"],
            exported_width=int(geometry_width),
            exported_height=int(geometry_height),
            pose_failed_indices=support_summary["pose_failed_indices"],
        )
    return contract


def build_sparse2dgs_window_scene_contract(
    ctx: JobContext,
    *,
    frame_indices: list[int],
    scene_label: str,
    max_image_size_override: int | None = None,
    total_pixel_budget_override: int | None = None,
) -> dict[str, Any]:
    scene_inputs = _load_slam3r_scene_inputs(ctx)
    source_width = int(scene_inputs["source_width"])
    source_height = int(scene_inputs["source_height"])
    target_views, width, height, bridge_budget = _resolve_bridge_export_plan(
        frame_count=len(frame_indices),
        requested_target_views=len(frame_indices),
        source_width=source_width,
        source_height=source_height,
        max_image_size_override=max_image_size_override,
        total_pixel_budget_override=total_pixel_budget_override,
    )
    frame_indices = list(frame_indices[:target_views])
    scene_root = _scene_root_for_label(ctx.job_id, label=scene_label)
    scene_summary = _write_sparse2dgs_scene_variant(
        ctx=ctx,
        scene_inputs=scene_inputs,
        scene_root=scene_root,
        frame_indices=frame_indices,
        width=width,
        height=height,
    )
    return {
        "scene_dir": str(scene_root),
        "images_dir": str(scene_root / "images"),
        "sparse_dir": str(scene_root / "sparse" / "0"),
        "selected_frame_indices": frame_indices,
        "selected_frame_count": len(frame_indices),
        "exported_image_size": [int(width), int(height)],
        "bridge_budget": bridge_budget,
        "pose_failed_indices": scene_summary["pose_failed_indices"],
        "pose_failed_count": len(scene_summary["pose_failed_indices"]),
        "point_count": int(scene_summary["point_count"]),
        "bridge_image_source": "curated_original_resized",
        "scene_label": scene_label,
    }


def _load_slam3r_scene_inputs(ctx: JobContext) -> dict[str, Any]:
    assert ctx.slam3r_dir is not None
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

    curated_frame_paths = sorted(
        path
        for path in ctx.curated_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    if not curated_frame_paths:
        raise RuntimeError("curated_frames_missing")

    predictor_source_width = int(rgb_imgs.shape[2])
    predictor_source_height = int(rgb_imgs.shape[1])
    source_width, source_height = _resolve_bridge_source_dimensions(
        curated_frame_paths=curated_frame_paths,
        fallback_width=predictor_source_width,
        fallback_height=predictor_source_height,
    )
    frame_quality_weights = _load_curated_frame_weights(ctx, curated_frame_paths)

    recon_utils = _load_slam3r_recon_utils()
    init_winsize = int(metadata.get("init_winsize", 0))
    kf_stride = int(metadata.get("kf_stride", 1))
    init_ref_id = int(metadata.get("init_ref_id", 0)) * kf_stride
    init_ids = list(range(0, init_winsize * kf_stride, kf_stride)) if init_winsize > 0 else []

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
    selection_camera_matrices = _estimate_selection_camera_matrices(
        registered_pcds=registered_pcds,
        mean_intrinsic=mean_intrinsic,
        recon_utils=recon_utils,
    )

    return {
        "preds_dir": preds_dir,
        "local_pcds": local_pcds,
        "registered_pcds": registered_pcds,
        "rgb_imgs": rgb_imgs,
        "curated_frame_paths": curated_frame_paths,
        "frame_quality_weights": frame_quality_weights,
        "metadata": metadata,
        "predictor_source_width": predictor_source_width,
        "predictor_source_height": predictor_source_height,
        "source_width": source_width,
        "source_height": source_height,
        "selection_camera_matrices": selection_camera_matrices,
        "intrinsics": intrinsics,
    }


def _scene_root_for_label(job_id: str, *, label: str) -> Path:
    safe_label = label.replace("/", "_")
    return Path(config.sparse2dgs_repo) / "DTU_Sparse" / f"{job_id}_{safe_label}"


def _resolve_support_scene_export_size(*, source_width: int, source_height: int) -> tuple[int, int]:
    max_dim = max(256, int(config.sparse2dgs_support_scene_max_image_size))
    longest_side = max(source_width, source_height)
    if longest_side <= max_dim:
        return int(source_width), int(source_height)
    scale = float(max_dim) / float(longest_side)
    width = _round_down_to_multiple(max(256, int(math.floor(source_width * scale))), 64)
    height = _round_down_to_multiple(max(256, int(math.floor(source_height * scale))), 64)
    return int(width), int(height)


def _write_sparse2dgs_scene_variant(
    *,
    ctx: JobContext,
    scene_inputs: dict[str, Any],
    scene_root: Path,
    frame_indices: list[int],
    width: int,
    height: int,
) -> dict[str, Any]:
    if scene_root.exists():
        shutil.rmtree(scene_root)
    images_dir = scene_root / "images"
    sparse_dir = scene_root / "sparse" / "0"
    images_dir.mkdir(parents=True, exist_ok=True)
    sparse_dir.mkdir(parents=True, exist_ok=True)

    from PIL import Image
    import torch

    rotmat2qvec = _load_sparse2dgs_rotmat2qvec()
    local_pcds = scene_inputs["local_pcds"]
    registered_pcds = scene_inputs["registered_pcds"]
    rgb_imgs = scene_inputs["rgb_imgs"]
    intrinsics = scene_inputs["intrinsics"]
    curated_frame_paths = scene_inputs["curated_frame_paths"]
    predictor_source_width = int(scene_inputs["predictor_source_width"])
    predictor_source_height = int(scene_inputs["predictor_source_height"])
    selection_camera_matrices = scene_inputs["selection_camera_matrices"]

    image_lines: list[str] = []
    camera_lines: list[str] = []
    points_lines: list[str] = []
    point_id = 1
    pose_failed_indices: list[int] = []
    mean_intrinsic = np.mean(np.stack(intrinsics, axis=0), axis=0)
    recon_utils = _load_slam3r_recon_utils()

    for frame_idx in frame_indices:
        img_name = f"{frame_idx:05d}"
        image_filename = f"{img_name}.png"
        image_path = images_dir / image_filename

        predictor_image = _normalize_image(rgb_imgs[frame_idx])
        curated_path = curated_frame_paths[frame_idx] if 0 <= frame_idx < len(curated_frame_paths) else None
        if curated_path is None:
            raise RuntimeError(f"curated_bridge_frame_missing:{frame_idx}")
        export_image = _load_bridge_export_image(
            curated_path=curated_path,
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

        w2c = selection_camera_matrices[frame_idx]
        if w2c is None:
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
        _write_sparse2dgs_cam_file(
            scene_root / f"cam_{img_name}.txt",
            intrinsic=intrinsic,
            w2c=w2c,
            depth_min=float(valid_depths.min()),
            depth_max=float(valid_depths.max()),
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
        "# Number of images: {}, mean observations per image: 0\n".format(len(frame_indices))
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
    return {
        "point_count": int(point_id - 1),
        "pose_failed_indices": pose_failed_indices,
    }


def _build_geometry_windows(
    *,
    frame_indices: list[int],
    trigger_views: int,
    window_target_views: int,
    overlap_views: int,
    max_windows: int,
    enabled: bool,
) -> list[dict[str, Any]]:
    ordered_indices = sorted(int(index) for index in frame_indices)
    if not enabled or len(ordered_indices) <= max(1, trigger_views):
        return [
            {
                "window_id": "window_00",
                "selected_frame_indices": ordered_indices,
                "window_index": 0,
            }
        ]

    target = max(8, min(int(window_target_views), len(ordered_indices)))
    overlap = max(1, min(int(overlap_views), target - 1))
    stride = max(1, target - overlap)
    windows: list[list[int]] = []
    start = 0
    while start < len(ordered_indices):
        window = ordered_indices[start : start + target]
        if len(window) < max(8, target // 2):
            if windows:
                merged = sorted(set(windows[-1] + window))
                windows[-1] = merged[-target:]
            else:
                windows.append(ordered_indices[-target:])
            break
        windows.append(window)
        if start + target >= len(ordered_indices):
            break
        start += stride

    if len(windows) > max_windows:
        positions = np.linspace(0, len(windows) - 1, num=max_windows, dtype=np.float64)
        chosen = sorted({int(round(position)) for position in positions.tolist()})
        windows = [windows[index] for index in chosen]

    merged_windows: list[dict[str, Any]] = []
    for index, window_indices in enumerate(windows):
        merged_windows.append(
            {
                "window_id": f"window_{index:02d}",
                "selected_frame_indices": sorted(set(int(value) for value in window_indices)),
                "window_index": index,
            }
        )
    return merged_windows


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


def _estimate_selection_camera_matrices(
    *,
    registered_pcds: np.ndarray,
    mean_intrinsic: np.ndarray,
    recon_utils: Any,
) -> list[np.ndarray | None]:
    import torch

    camera_matrices: list[np.ndarray | None] = []
    for frame_idx in range(int(registered_pcds.shape[0])):
        try:
            registered_pts = torch.from_numpy(registered_pcds[frame_idx]).float()
            c2w, success = recon_utils.estimate_camera_pose(registered_pts, mean_intrinsic)
            if not success:
                camera_matrices.append(None)
                continue
            camera_matrices.append(np.linalg.inv(np.asarray(c2w, dtype=np.float64)))
        except Exception:
            camera_matrices.append(None)
    return camera_matrices


def _select_sparse2dgs_view_indices(
    frame_count: int,
    *,
    target_views: int,
    quality_weights: list[float] | None = None,
    camera_matrices: list[np.ndarray | None] | None = None,
    images: np.ndarray | None = None,
) -> list[int]:
    if frame_count < target_views:
        raise RuntimeError(
            f"sparse2dgs_requires_{target_views}_views_but_only_{frame_count}_available"
        )
    if frame_count == target_views:
        return list(range(frame_count))
    descriptors = _build_frame_selection_descriptors(
        frame_count=frame_count,
        camera_matrices=camera_matrices,
        images=images,
    )
    geometry_context = _build_frame_selection_geometry_context(
        frame_count=frame_count,
        camera_matrices=camera_matrices,
    )
    normalized_quality = _normalize_quality_weights(
        frame_count=frame_count,
        quality_weights=quality_weights,
    )
    return _greedy_diverse_selection(
        descriptors=descriptors,
        target_count=target_views,
        quality_scores=normalized_quality,
        geometry_context=geometry_context,
    )


def _build_frame_selection_descriptors(
    *,
    frame_count: int,
    camera_matrices: list[np.ndarray | None] | None,
    images: np.ndarray | None,
) -> np.ndarray:
    centers = np.zeros((frame_count, 3), dtype=np.float64)
    forwards = np.zeros((frame_count, 3), dtype=np.float64)
    valid_pose_mask = np.zeros(frame_count, dtype=bool)

    if camera_matrices and len(camera_matrices) == frame_count:
        for frame_idx, w2c in enumerate(camera_matrices):
            if w2c is None:
                continue
            rotation = np.asarray(w2c[:3, :3], dtype=np.float64)
            translation = np.asarray(w2c[:3, 3], dtype=np.float64)
            center = -(rotation.T @ translation)
            forward = rotation.T @ np.array([0.0, 0.0, 1.0], dtype=np.float64)
            norm = np.linalg.norm(forward)
            if norm > 1e-6:
                forward = forward / norm
            centers[frame_idx] = center
            forwards[frame_idx] = forward
            valid_pose_mask[frame_idx] = True

    if np.any(valid_pose_mask):
        valid_centers = centers[valid_pose_mask]
        center_mean = valid_centers.mean(axis=0)
        center_scale = np.maximum(valid_centers.std(axis=0), 1e-3)
        centers[valid_pose_mask] = (valid_centers - center_mean) / center_scale

    contour_signatures = np.zeros((frame_count, 16), dtype=np.float64)
    if images is not None and int(images.shape[0]) == frame_count:
        for frame_idx in range(frame_count):
            contour_signatures[frame_idx] = _frame_contour_signature(images[frame_idx])

    time_feature = np.linspace(0.0, 1.0, num=frame_count, dtype=np.float64)[:, None]
    return np.concatenate(
        [
            centers * 1.8,
            forwards * 1.2,
            contour_signatures * 0.9,
            time_feature * 0.6,
        ],
        axis=1,
    )


def _build_frame_selection_geometry_context(
    *,
    frame_count: int,
    camera_matrices: list[np.ndarray | None] | None,
) -> dict[str, np.ndarray]:
    center_dirs = np.zeros((frame_count, 3), dtype=np.float64)
    valid_mask = np.zeros(frame_count, dtype=bool)
    sector_ids = np.full(frame_count, -1, dtype=np.int64)

    if not camera_matrices or len(camera_matrices) != frame_count:
        return {
            "center_dirs": center_dirs,
            "valid_mask": valid_mask,
            "sector_ids": sector_ids,
        }

    centers = np.zeros((frame_count, 3), dtype=np.float64)
    for frame_idx, w2c in enumerate(camera_matrices):
        if w2c is None:
            continue
        rotation = np.asarray(w2c[:3, :3], dtype=np.float64)
        translation = np.asarray(w2c[:3, 3], dtype=np.float64)
        center = -(rotation.T @ translation)
        if not np.all(np.isfinite(center)):
            continue
        centers[frame_idx] = center
        valid_mask[frame_idx] = True

    if np.count_nonzero(valid_mask) < 2:
        return {
            "center_dirs": center_dirs,
            "valid_mask": valid_mask,
            "sector_ids": sector_ids,
        }

    valid_centers = centers[valid_mask]
    centered = valid_centers - valid_centers.mean(axis=0, keepdims=True)
    norms = np.linalg.norm(centered, axis=1)
    safe_norms = np.maximum(norms, 1e-6)
    center_dirs[valid_mask] = centered / safe_norms[:, None]

    try:
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
    except np.linalg.LinAlgError:
        return {
            "center_dirs": center_dirs,
            "valid_mask": valid_mask,
            "sector_ids": sector_ids,
        }
    if vh.shape[0] < 2:
        return {
            "center_dirs": center_dirs,
            "valid_mask": valid_mask,
            "sector_ids": sector_ids,
        }
    plane_basis = vh[:2]
    projected = centered @ plane_basis.T
    azimuth = np.arctan2(projected[:, 1], projected[:, 0])
    bins = np.floor(((azimuth + math.pi) / (2.0 * math.pi)) * 8.0).astype(np.int64)
    bins = np.clip(bins, 0, 7)
    sector_ids[np.flatnonzero(valid_mask)] = bins
    return {
        "center_dirs": center_dirs,
        "valid_mask": valid_mask,
        "sector_ids": sector_ids,
    }


def _frame_contour_signature(image: np.ndarray, *, grid_size: int = 4) -> np.ndarray:
    rgb = _normalize_image(image).astype(np.float64)
    grayscale = rgb.mean(axis=2) / 255.0
    grad_y, grad_x = np.gradient(grayscale)
    magnitude = np.hypot(grad_x, grad_y)
    rows = np.array_split(np.arange(magnitude.shape[0]), grid_size)
    cols = np.array_split(np.arange(magnitude.shape[1]), grid_size)
    signature: list[float] = []
    for row_indices in rows:
        for col_indices in cols:
            patch = magnitude[np.ix_(row_indices, col_indices)]
            signature.append(float(np.mean(patch)))
    descriptor = np.asarray(signature, dtype=np.float64)
    norm = float(np.linalg.norm(descriptor))
    if norm > 1e-8:
        descriptor /= norm
    return descriptor


def _normalize_quality_weights(
    *,
    frame_count: int,
    quality_weights: list[float] | None,
) -> np.ndarray:
    if not quality_weights or len(quality_weights) != frame_count:
        return np.ones(frame_count, dtype=np.float64)
    values = np.asarray(quality_weights, dtype=np.float64)
    finite_mask = np.isfinite(values)
    if not np.any(finite_mask):
        return np.ones(frame_count, dtype=np.float64)
    finite_values = values[finite_mask]
    min_value = float(finite_values.min())
    max_value = float(finite_values.max())
    if max_value - min_value < 1e-6:
        normalized = np.ones(frame_count, dtype=np.float64)
    else:
        normalized = (values - min_value) / max(max_value - min_value, 1e-6)
        normalized[~finite_mask] = 0.0
    return np.clip(normalized, 0.0, 1.0)


def _greedy_diverse_selection(
    *,
    descriptors: np.ndarray,
    target_count: int,
    quality_scores: np.ndarray,
    geometry_context: dict[str, np.ndarray] | None = None,
) -> list[int]:
    frame_count = int(descriptors.shape[0])
    if frame_count <= target_count:
        return list(range(frame_count))

    center_dirs = np.zeros((frame_count, 3), dtype=np.float64)
    valid_mask = np.zeros(frame_count, dtype=bool)
    sector_ids = np.full(frame_count, -1, dtype=np.int64)
    target_sector_coverage = 0
    if geometry_context is not None:
        center_dirs = np.asarray(geometry_context.get("center_dirs"), dtype=np.float64)
        valid_mask = np.asarray(geometry_context.get("valid_mask"), dtype=bool)
        sector_ids = np.asarray(geometry_context.get("sector_ids"), dtype=np.int64)
        if (
            valid_mask.shape[0] == frame_count
            and center_dirs.shape == (frame_count, 3)
            and sector_ids.shape[0] == frame_count
        ):
            available_sector_count = len(
                {
                    int(sector_id)
                    for sector_id in sector_ids[valid_mask].tolist()
                    if int(sector_id) >= 0
                }
            )
            target_sector_coverage = min(
                target_count,
                available_sector_count,
                max(0, int(config.geometry_hq_min_coverage_sectors)),
            )
        else:
            center_dirs = np.zeros((frame_count, 3), dtype=np.float64)
            valid_mask = np.zeros(frame_count, dtype=bool)
            sector_ids = np.full(frame_count, -1, dtype=np.int64)

    def _selected_sector_set(selected_indices: list[int]) -> set[int]:
        if target_sector_coverage <= 0:
            return set()
        return {
            int(sector_ids[index])
            for index in selected_indices
            if 0 <= index < frame_count and valid_mask[index] and int(sector_ids[index]) >= 0
        }

    def _min_angle_deg(candidate: int, selected_indices: list[int]) -> float:
        if not valid_mask[candidate]:
            return 0.0
        candidate_dir = center_dirs[candidate]
        selected_dirs = [center_dirs[index] for index in selected_indices if valid_mask[index]]
        if not selected_dirs:
            return 180.0
        cosines = [
            float(np.clip(np.dot(candidate_dir, selected_dir), -1.0, 1.0))
            for selected_dir in selected_dirs
        ]
        return float(np.degrees(np.arccos(max(cosines))))

    selected: list[int] = []
    used: set[int] = set()
    seed_candidates = [int(np.argmax(quality_scores))]
    if np.any(valid_mask):
        first_seed = seed_candidates[0]
        if 0 <= first_seed < frame_count:
            first_dir = center_dirs[first_seed]
            farthest_index: int | None = None
            farthest_score = float("-inf")
            for candidate in range(frame_count):
                if candidate == first_seed or not valid_mask[candidate]:
                    continue
                cosine = float(np.clip(np.dot(center_dirs[candidate], first_dir), -1.0, 1.0))
                angle_score = (1.0 - cosine) * 0.5
                score = angle_score + float(quality_scores[candidate]) * 0.15
                if score > farthest_score:
                    farthest_score = score
                    farthest_index = candidate
            if farthest_index is not None:
                seed_candidates.append(int(farthest_index))
    seed_candidates.extend(
        [
            int(np.argmax(quality_scores[: max(1, frame_count // 4)])),
            int(
                frame_count
                - max(1, frame_count // 4)
                + np.argmax(quality_scores[max(0, frame_count - max(1, frame_count // 4)) :])
            ),
        ]
    )
    for seed in seed_candidates:
        if seed in used:
            continue
        used.add(seed)
        selected.append(seed)
        if len(selected) >= target_count:
            return sorted(selected)

    if target_sector_coverage > 0:
        while len(selected) < min(target_count, target_sector_coverage):
            selected_sectors = _selected_sector_set(selected)
            best_index: int | None = None
            best_score = float("-inf")
            for candidate in range(frame_count):
                if candidate in used or not valid_mask[candidate]:
                    continue
                candidate_sector = int(sector_ids[candidate])
                if candidate_sector < 0 or candidate_sector in selected_sectors:
                    continue
                temporal_gap = (
                    min(abs(candidate - selected_index) for selected_index in selected) / max(frame_count - 1, 1)
                    if selected
                    else 1.0
                )
                min_angle_deg = _min_angle_deg(candidate, selected)
                score = (
                    min(1.5, min_angle_deg / max(float(config.geometry_hq_min_baseline_median_deg), 1e-6)) * 1.2
                    + float(quality_scores[candidate]) * 0.35
                    + temporal_gap * 0.10
                )
                if score > best_score:
                    best_score = score
                    best_index = candidate
            if best_index is None:
                break
            used.add(best_index)
            selected.append(best_index)

    while len(selected) < target_count:
        best_index: int | None = None
        best_score = float("-inf")
        selected_sectors = _selected_sector_set(selected)
        for candidate in range(frame_count):
            if candidate in used:
                continue
            novelty = min(
                float(np.linalg.norm(descriptors[candidate] - descriptors[selected_index]))
                for selected_index in selected
            )
            temporal_gap = min(abs(candidate - selected_index) for selected_index in selected) / max(frame_count - 1, 1)
            angle_novelty = 0.0
            sector_bonus = 0.0
            low_angle_penalty = 0.0
            if valid_mask[candidate]:
                min_angle_deg = _min_angle_deg(candidate, selected)
                target_angle_deg = max(6.0, float(config.geometry_hq_min_baseline_median_deg))
                angle_novelty = min(1.4, min_angle_deg / max(target_angle_deg, 1e-6))
                safe_angle_floor = max(target_angle_deg * 0.75, 4.0)
                if min_angle_deg < safe_angle_floor:
                    low_angle_penalty = min(
                        1.0,
                        (safe_angle_floor - min_angle_deg) / max(safe_angle_floor, 1e-6),
                    )
                candidate_sector = int(sector_ids[candidate])
                if candidate_sector >= 0:
                    if candidate_sector not in selected_sectors:
                        sector_bonus = 0.18
                        if len(selected_sectors) < target_sector_coverage:
                            sector_bonus = 0.72
            score = (
                novelty * 0.50
                + angle_novelty * 1.15
                + sector_bonus
                + temporal_gap * 0.08
                + float(quality_scores[candidate]) * 0.16
                - low_angle_penalty * 0.90
            )
            if score > best_score:
                best_score = score
                best_index = candidate
        if best_index is None:
            raise RuntimeError("sparse2dgs_view_selection_failed")
        used.add(best_index)
        selected.append(best_index)

    selected.sort()
    return selected


def _resize_image_if_needed(image: np.ndarray, *, width: int, height: int) -> np.ndarray:
    if image.shape[1] == width and image.shape[0] == height:
        return image
    from PIL import Image

    pil_image = Image.fromarray(image, mode="RGB")
    return np.asarray(pil_image.resize((width, height), Image.BILINEAR), dtype=np.uint8)


def _load_bridge_export_image(
    *,
    curated_path: Path,
    width: int,
    height: int,
) -> np.ndarray:
    from PIL import Image

    if not curated_path.exists():
        raise RuntimeError(f"curated_bridge_frame_missing:{curated_path}")
    with Image.open(curated_path) as image:
        return np.asarray(
            image.convert("RGB").resize((width, height), Image.BILINEAR),
            dtype=np.uint8,
        )


def _resolve_bridge_source_dimensions(
    *,
    curated_frame_paths: list[Path],
    fallback_width: int,
    fallback_height: int,
) -> tuple[int, int]:
    if not curated_frame_paths:
        raise RuntimeError("curated_frames_missing")
    source_width = fallback_width
    source_height = fallback_height
    for curated_path in curated_frame_paths:
        image_size = _read_image_size(curated_path)
        if image_size is None:
            continue
        source_width, source_height = image_size
        break
    return source_width, source_height


def _resolve_bridge_export_plan(
    *,
    frame_count: int,
    requested_target_views: int,
    source_width: int,
    source_height: int,
    max_image_size_override: int | None = None,
    total_pixel_budget_override: int | None = None,
) -> tuple[int, int, int, dict[str, int | float | bool]]:
    max_dim = max(
        256,
        int(
            max_image_size_override
            if max_image_size_override is not None
            else config.sparse2dgs_contract_max_image_size
        ),
    )
    min_dim = min(max_dim, max(256, int(config.sparse2dgs_contract_min_image_size)))
    total_pixel_budget = max(
        1_000_000,
        int(
            total_pixel_budget_override
            if total_pixel_budget_override is not None
            else config.sparse2dgs_contract_total_pixel_budget
        ),
    )
    min_views = max(8, int(config.sparse2dgs_min_views))
    target_views = min(frame_count, max(min_views, requested_target_views))
    source_area = max(int(source_width) * int(source_height), 1)
    source_short_side = max(1, min(int(source_width), int(source_height)))
    source_long_side = max(1, max(int(source_width), int(source_height)))
    desired_short_side = min(
        source_short_side,
        max(min_dim, int(config.geometry_hq_min_exported_short_side)),
    )
    desired_long_side = _round_down_to_multiple(
        int(round(source_long_side * (float(desired_short_side) / float(source_short_side)))),
        64,
    )
    desired_width = max(64, _round_down_to_multiple(int(round(source_width * (float(desired_short_side) / float(source_short_side)))), 64))
    desired_height = max(64, _round_down_to_multiple(int(round(source_height * (float(desired_short_side) / float(source_short_side)))), 64))
    desired_area = max(desired_width * desired_height, 1)
    hq_resolution_preserved = False

    if desired_long_side <= max_dim:
        while target_views > min_views and desired_area * target_views > total_pixel_budget:
            target_views -= 1

    while True:
        max_area_per_view = max(64 * 64, total_pixel_budget // max(target_views, 1))
        longest_side = max(source_width, source_height)
        scale_by_dim = min(1.0, float(max_dim) / float(longest_side)) if longest_side > 0 else 1.0
        scale_by_budget = min(1.0, math.sqrt(float(max_area_per_view) / float(source_area)))
        scale = min(scale_by_dim, scale_by_budget)
        export_width = max(64, _round_down_to_multiple(int(round(source_width * scale)), 64))
        export_height = max(64, _round_down_to_multiple(int(round(source_height * scale)), 64))
        while (
            export_width * export_height * target_views > total_pixel_budget
            and max(export_width, export_height) > min_dim
        ):
            aspect = float(source_width) / float(max(source_height, 1))
            next_longest_side = max(min_dim, _round_down_to_multiple(max(export_width, export_height) - 64, 64))
            if source_width >= source_height:
                export_width = next_longest_side
                export_height = max(64, _round_down_to_multiple(int(round(export_width / max(aspect, 1e-6))), 64))
            else:
                export_height = next_longest_side
                export_width = max(64, _round_down_to_multiple(int(round(export_height * aspect)), 64))
        export_longest_side = max(export_width, export_height)
        export_short_side = min(export_width, export_height)
        if desired_long_side <= max_dim and export_short_side < desired_short_side and target_views > min_views:
            target_views -= 1
            continue
        if export_longest_side >= min_dim or target_views <= min_views:
            hq_resolution_preserved = export_short_side >= desired_short_side
            budget_info = {
                "requested_target_views": int(requested_target_views),
                "resolved_target_views": int(target_views),
                "total_pixel_budget": int(total_pixel_budget),
                "max_image_size": int(max_dim),
                "min_image_size": int(min_dim),
                "hq_target_short_side": int(desired_short_side),
                "hq_resolution_preserved": bool(hq_resolution_preserved),
                "exported_pixel_count_per_view": int(export_width * export_height),
                "exported_total_pixel_count": int(export_width * export_height * target_views),
                "target_views_reduced": bool(target_views != requested_target_views),
            }
            return target_views, export_width, export_height, budget_info
        target_views -= 1


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


def _round_down_to_multiple(value: int, multiple: int) -> int:
    if multiple <= 0:
        return value
    return max(multiple, int(math.floor(float(value) / float(multiple)) * multiple))


def _write_geometry_hq_report(
    *,
    ctx: JobContext,
    support_frame_indices: list[int],
    selected_frame_indices: list[int],
    curated_frame_paths: list[Path],
    camera_matrices: list[np.ndarray | None],
    exported_width: int,
    exported_height: int,
    pose_failed_indices: list[int],
) -> None:
    support_camera_centers = _selected_camera_centers(
        selected_frame_indices=support_frame_indices,
        camera_matrices=camera_matrices,
    )
    camera_centers = _selected_camera_centers(
        selected_frame_indices=selected_frame_indices,
        camera_matrices=camera_matrices,
    )
    coverage_sector_count = _coverage_sector_count(camera_centers, sector_count=8)
    baseline_median_deg = _baseline_median_deg(camera_centers)
    metrics = {
        "selected_view_count": int(len(selected_frame_indices)),
        "valid_pose_view_count": int(len(camera_centers)),
        "pose_failed_count": int(len(pose_failed_indices)),
        "exported_width": int(exported_width),
        "exported_height": int(exported_height),
        "exported_short_side": int(min(exported_width, exported_height)),
        "coverage_sector_count": int(coverage_sector_count),
        "baseline_median_deg": round(float(baseline_median_deg), 3),
    }
    thresholds = {
        "min_selected_view_count": int(config.geometry_hq_min_selected_views),
        "max_pose_failed_count": 0,
        "min_exported_short_side": int(config.geometry_hq_min_exported_short_side),
        "min_coverage_sector_count": int(config.geometry_hq_min_coverage_sectors),
        "min_baseline_median_deg": float(config.geometry_hq_min_baseline_median_deg),
    }
    failed_metrics: list[str] = []
    if metrics["selected_view_count"] < thresholds["min_selected_view_count"]:
        failed_metrics.append("selected_view_count")
    if metrics["pose_failed_count"] > thresholds["max_pose_failed_count"]:
        failed_metrics.append("pose_failed_count")
    if metrics["exported_short_side"] < thresholds["min_exported_short_side"]:
        failed_metrics.append("exported_short_side")
    if metrics["coverage_sector_count"] < thresholds["min_coverage_sector_count"]:
        failed_metrics.append("coverage_sector_count")
    if metrics["baseline_median_deg"] < thresholds["min_baseline_median_deg"]:
        failed_metrics.append("baseline_median_deg")
    update_quality_card(
        ctx,
        card_id="geometry_hq",
        title="Geometry HQ",
        metrics=metrics,
        thresholds=thresholds,
        failed_metrics=failed_metrics,
        notes=[
            "HQ-only gate at bridge stage.",
            "Uses selected views, pose health, coverage sectors, and baseline spread.",
        ],
    )
    coverage_debug = {
        "support": _coverage_debug_payload(
            frame_indices=support_frame_indices,
            camera_centers=support_camera_centers,
            curated_frame_paths=curated_frame_paths,
            sector_count=8,
        ),
        "selected": _coverage_debug_payload(
            frame_indices=selected_frame_indices,
            camera_centers=camera_centers,
            curated_frame_paths=curated_frame_paths,
            sector_count=8,
        ),
        "pose_failed_indices": [int(index) for index in pose_failed_indices],
    }
    quality_dir = ctx.output_dir
    quality_dir.mkdir(parents=True, exist_ok=True)
    (quality_dir / config.geometry_coverage_debug_filename).write_text(
        json.dumps(coverage_debug, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _selected_camera_centers(
    *,
    selected_frame_indices: list[int],
    camera_matrices: list[np.ndarray | None],
) -> np.ndarray:
    centers: list[np.ndarray] = []
    for frame_idx in selected_frame_indices:
        if frame_idx < 0 or frame_idx >= len(camera_matrices):
            continue
        w2c = camera_matrices[frame_idx]
        if w2c is None:
            continue
        rotation = np.asarray(w2c[:3, :3], dtype=np.float64)
        translation = np.asarray(w2c[:3, 3], dtype=np.float64)
        center = -(rotation.T @ translation)
        if np.all(np.isfinite(center)):
            centers.append(center)
    if not centers:
        return np.zeros((0, 3), dtype=np.float64)
    return np.asarray(centers, dtype=np.float64)


def _coverage_sector_count(centers: np.ndarray, *, sector_count: int) -> int:
    if centers.shape[0] < 3:
        return int(centers.shape[0])
    centered = centers - centers.mean(axis=0, keepdims=True)
    try:
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
    except np.linalg.LinAlgError:
        return 0
    plane_basis = vh[:2]
    if plane_basis.shape[0] < 2:
        return 0
    projected = centered @ plane_basis.T
    if projected.shape[0] == 0:
        return 0
    radii = np.linalg.norm(projected, axis=1)
    valid = radii > 1e-6
    if not np.any(valid):
        return 0
    azimuth = np.arctan2(projected[valid, 1], projected[valid, 0])
    bins = np.floor(((azimuth + math.pi) / (2.0 * math.pi)) * float(sector_count)).astype(np.int64)
    bins = np.clip(bins, 0, sector_count - 1)
    return int(len(set(int(value) for value in bins.tolist())))


def _baseline_median_deg(centers: np.ndarray) -> float:
    if centers.shape[0] < 2:
        return 0.0
    centered = centers - centers.mean(axis=0, keepdims=True)
    norms = np.linalg.norm(centered, axis=1)
    valid = norms > 1e-6
    if np.count_nonzero(valid) < 2:
        return 0.0
    directions = centered[valid] / norms[valid, None]
    nearest_neighbor_angles: list[float] = []
    for index, direction in enumerate(directions):
        cosine = np.clip(directions @ direction, -1.0, 1.0)
        angles = np.degrees(np.arccos(cosine))
        candidate_angles = [float(value) for neighbor_index, value in enumerate(angles.tolist()) if neighbor_index != index]
        if not candidate_angles:
            continue
        nearest_neighbor_angles.append(min(candidate_angles))
    if not nearest_neighbor_angles:
        return 0.0
    return float(np.median(np.asarray(nearest_neighbor_angles, dtype=np.float64)))


def _coverage_debug_payload(
    *,
    frame_indices: list[int],
    camera_centers: np.ndarray,
    curated_frame_paths: list[Path],
    sector_count: int,
) -> dict[str, Any]:
    if camera_centers.shape[0] == 0:
        return {
            "frame_count": 0,
            "coverage_sector_count": 0,
            "baseline_median_deg": 0.0,
            "sector_histogram": {str(index): 0 for index in range(sector_count)},
            "empty_sectors": list(range(sector_count)),
            "frames": [],
        }

    centered = camera_centers - camera_centers.mean(axis=0, keepdims=True)
    try:
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        plane_basis = vh[:2]
    except np.linalg.LinAlgError:
        plane_basis = np.asarray([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64)
    projected = centered @ plane_basis.T
    radii = np.linalg.norm(projected, axis=1)
    azimuth = np.degrees(np.arctan2(projected[:, 1], projected[:, 0]))
    bins = np.floor(((np.radians(azimuth) + math.pi) / (2.0 * math.pi)) * float(sector_count)).astype(np.int64)
    bins = np.clip(bins, 0, sector_count - 1)
    histogram = {str(index): 0 for index in range(sector_count)}
    frames_payload: list[dict[str, Any]] = []
    for local_index, frame_index in enumerate(frame_indices[: len(camera_centers)]):
        sector_id = int(bins[local_index])
        histogram[str(sector_id)] += 1
        filename = ""
        if 0 <= frame_index < len(curated_frame_paths):
            filename = curated_frame_paths[frame_index].name
        frames_payload.append(
            {
                "frame_index": int(frame_index),
                "filename": filename,
                "sector_id": sector_id,
                "azimuth_deg": round(float(azimuth[local_index]), 3),
                "radius": round(float(radii[local_index]), 6),
            }
        )
    empty_sectors = [index for index in range(sector_count) if histogram[str(index)] == 0]
    return {
        "frame_count": int(len(frames_payload)),
        "coverage_sector_count": int(len(set(int(frame["sector_id"]) for frame in frames_payload))),
        "baseline_median_deg": round(float(_baseline_median_deg(camera_centers)), 3),
        "sector_histogram": histogram,
        "empty_sectors": empty_sectors,
        "frames": frames_payload,
    }


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
