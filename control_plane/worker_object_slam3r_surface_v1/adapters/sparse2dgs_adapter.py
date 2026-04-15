from __future__ import annotations

import json
import math
import os
import re
import selectors
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .contract_bridge_adapter import (
    _load_slam3r_scene_inputs,
    _select_sparse2dgs_view_indices,
    build_sparse2dgs_window_scene_contract,
)
from ..config import config
from ..context import JobContext

SPARSE2DGS_PAPER_URL = (
    "https://openaccess.thecvf.com/content/CVPR2025/html/"
    "Wu_Sparse2DGS_Geometry-Prioritized_Gaussian_Splatting_for_Surface_Reconstruction_from_Sparse_Views_CVPR_2025_paper.html"
)
SPARSE2DGS_TRAINING_STEP_PATTERN = re.compile(r"Training progress:.*?\|\s*(\d+)/(\d+)")
_PLY_TYPE_TO_NUMPY = {
    "char": "i1",
    "uchar": "u1",
    "int8": "i1",
    "uint8": "u1",
    "short": "i2",
    "ushort": "u2",
    "int16": "i2",
    "uint16": "u2",
    "int": "i4",
    "uint": "u4",
    "int32": "i4",
    "uint32": "u4",
    "float": "f4",
    "float32": "f4",
    "double": "f8",
    "float64": "f8",
}
_NUMPY_TYPE_TO_PLY = {
    "i1": "char",
    "u1": "uchar",
    "i2": "short",
    "u2": "ushort",
    "i4": "int",
    "u4": "uint",
    "f4": "float",
    "f8": "double",
}


@dataclass(frozen=True)
class Sparse2DGSAttemptPlan:
    attempt_index: int
    target_views: int
    max_image_size: int
    total_pixel_budget: int
    reason: str


@dataclass(frozen=True)
class Sparse2DGSWindow:
    window_id: str
    frame_indices: list[int]
    window_index: int


def run_sparse2dgs_surface_reconstruction(
    ctx: JobContext,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> None:
    assert ctx.slam3r_dir is not None
    assert ctx.sparse2dgs_dir is not None

    initial_contract = _load_sparse2dgs_scene_contract(ctx)
    geometry_windows = _resolve_geometry_windows(initial_contract)
    use_windowed_geometry = (
        len(geometry_windows) > 1
        or int(initial_contract.get("support_frame_count") or 0) > int(initial_contract.get("selected_frame_count") or 0)
    )
    if not use_windowed_geometry:
        summary = _run_sparse2dgs_single_contract(
            ctx=ctx,
            contract=initial_contract,
            output_dir=ctx.sparse2dgs_dir,
            progress_callback=progress_callback,
        )
        (ctx.sparse2dgs_dir / config.sparse2dgs_summary_filename).write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return

    windows_root = ctx.sparse2dgs_dir / "_windows"
    windows_root.mkdir(parents=True, exist_ok=True)
    budget_info = initial_contract.get("bridge_budget") if isinstance(initial_contract.get("bridge_budget"), dict) else {}
    max_image_size_override = (
        int(budget_info.get("max_image_size"))
        if isinstance(budget_info, dict) and budget_info.get("max_image_size") is not None
        else None
    )
    total_pixel_budget_override = (
        int(budget_info.get("total_pixel_budget"))
        if isinstance(budget_info, dict) and budget_info.get("total_pixel_budget") is not None
        else None
    )

    window_summaries: list[dict[str, Any]] = []
    total_windows = max(len(geometry_windows), 1)
    for window in geometry_windows:
        window_contract = build_sparse2dgs_window_scene_contract(
            ctx,
            frame_indices=list(window.frame_indices),
            scene_label=window.window_id,
            max_image_size_override=max_image_size_override,
            total_pixel_budget_override=total_pixel_budget_override,
        )
        window_output_dir = windows_root / window.window_id
        window_progress = _make_window_progress_callback(
            progress_callback=progress_callback,
            window_index=window.window_index,
            total_windows=total_windows,
        )
        window_summary = _run_sparse2dgs_single_contract(
            ctx=ctx,
            contract=window_contract,
            output_dir=window_output_dir,
            progress_callback=window_progress,
        )
        window_summary["window_id"] = window.window_id
        window_summary["window_index"] = window.window_index
        window_summary["selected_frame_indices"] = list(window.frame_indices)
        window_summaries.append(window_summary)

    if not window_summaries:
        raise RuntimeError("sparse2dgs_window_geometry_empty")

    fused_summary = _materialize_fused_sparse2dgs_output(
        ctx=ctx,
        base_contract=initial_contract,
        window_summaries=window_summaries,
    )
    if progress_callback is not None:
        progress_callback(7000, 7000)
    summary = {
        "paper": "Sparse2DGS",
        "paper_url": SPARSE2DGS_PAPER_URL,
        "repo": config.sparse2dgs_repo,
        "mode": "windowed_highres_geometry",
        "scene_dir": str(ctx.sparse2dgs_scene_dir or ""),
        "slam3r_summary": str(ctx.slam3r_dir / config.slam3r_summary_filename),
        "output_dir": str(ctx.sparse2dgs_dir),
        "default_asset": str(fused_summary["default_asset"]),
        "train_log": str(fused_summary["train_log"]),
        "support_frame_count": int(initial_contract.get("support_frame_count") or 0),
        "selected_frame_count": int(initial_contract.get("selected_frame_count") or 0),
        "geometry_window_count": len(window_summaries),
        "windows": window_summaries,
        "fused_model": fused_summary,
    }
    (ctx.sparse2dgs_dir / config.sparse2dgs_summary_filename).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _run_sparse2dgs_single_contract(
    *,
    ctx: JobContext,
    contract: dict[str, Any],
    output_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    base_frame_indices = [int(value) for value in (contract.get("selected_frame_indices") or [])]
    if not base_frame_indices:
        raise RuntimeError("sparse2dgs_selected_frames_empty")

    scene_inputs = _load_slam3r_scene_inputs(ctx)
    attempt_logs_dir = output_dir / "_attempt_logs"
    attempt_logs_dir.mkdir(parents=True, exist_ok=True)
    attempt_plans = _build_sparse2dgs_attempt_plans(contract)
    attempts: list[dict[str, Any]] = []
    last_error: str | None = None
    active_contract: dict[str, Any] | None = None
    active_scene_dir: Path | None = None
    output_dir.mkdir(parents=True, exist_ok=True)

    original_scene_dir = ctx.sparse2dgs_scene_dir
    try:
        for attempt_plan in attempt_plans:
            attempt_contract = _build_attempt_scene_contract(
                ctx=ctx,
                base_contract=contract,
                base_frame_indices=base_frame_indices,
                attempt_plan=attempt_plan,
                scene_inputs=scene_inputs,
            )
            active_contract = attempt_contract
            active_scene_dir = Path(str(attempt_contract["scene_dir"]))
            ctx.sparse2dgs_scene_dir = active_scene_dir
            checkpoint_iterations = _build_sparse2dgs_checkpoint_iterations()
            max_resume_retries = max(0, int(config.sparse2dgs_max_checkpoint_resume_retries))
            resume_state = _match_sparse2dgs_resume_state(
                output_dir=output_dir,
                attempt_plan=attempt_plan,
                attempt_contract=attempt_contract,
            )
            if resume_state is not None:
                resume_attempt_index = max(1, int(resume_state.get("resume_attempt_index") or 0) + 1)
                resume_checkpoint = Path(str(resume_state["resume_checkpoint"]))
            else:
                _clear_sparse2dgs_resume_state(output_dir)
                _reset_sparse2dgs_output_dir(output_dir, preserve={"_attempt_logs"})
                resume_attempt_index = 0
                resume_checkpoint = None
            continue_with_next_budget = False

            while True:
                command = _render_command(
                    template=config.sparse2dgs_command_template,
                    ctx=ctx,
                    repo_dir=Path(config.sparse2dgs_repo),
                    output_dir=output_dir,
                )
                if not command:
                    raise RuntimeError("sparse2dgs_command_not_configured")
                command = _augment_sparse2dgs_command(
                    command,
                    checkpoint_iterations=checkpoint_iterations,
                    start_checkpoint=resume_checkpoint,
                )

                attempt_log_path = _sparse2dgs_attempt_log_path(
                    attempt_logs_dir,
                    attempt_index=attempt_plan.attempt_index,
                    resume_attempt_index=resume_attempt_index,
                )
                attempt_record: dict[str, Any] = {
                    "attempt_index": int(attempt_plan.attempt_index),
                    "resume_attempt_index": int(resume_attempt_index),
                    "reason": attempt_plan.reason if resume_attempt_index == 0 else "resume_from_checkpoint",
                    "target_views": int(attempt_plan.target_views),
                    "max_image_size": int(attempt_plan.max_image_size),
                    "total_pixel_budget": int(attempt_plan.total_pixel_budget),
                    "scene_dir": str(active_scene_dir),
                    "selected_frame_count": int(attempt_contract.get("selected_frame_count") or len(base_frame_indices)),
                    "exported_image_size": list(attempt_contract.get("exported_image_size") or []),
                    "log_path": str(attempt_log_path),
                    "start_checkpoint": str(resume_checkpoint) if resume_checkpoint is not None else None,
                }
                _write_sparse2dgs_resume_state(
                    output_dir,
                    {
                        "status": "running",
                        "attempt_index": int(attempt_plan.attempt_index),
                        "resume_attempt_index": int(resume_attempt_index),
                        "target_views": int(attempt_plan.target_views),
                        "max_image_size": int(attempt_plan.max_image_size),
                        "total_pixel_budget": int(attempt_plan.total_pixel_budget),
                        "scene_dir": str(active_scene_dir),
                        "selected_frame_indices": [
                            int(value)
                            for value in (attempt_contract.get("selected_frame_indices") or base_frame_indices)
                        ],
                        "exported_image_size": list(attempt_contract.get("exported_image_size") or []),
                        "resume_checkpoint": str(resume_checkpoint) if resume_checkpoint is not None else None,
                        "updated_at_epoch_sec": time.time(),
                    },
                )
                try:
                    _run_sparse2dgs_command(
                        command,
                        cwd=str(Path(config.sparse2dgs_repo)),
                        timeout=config.sparse2dgs_stage_timeout_sec,
                        log_path=attempt_log_path,
                        progress_callback=progress_callback,
                    )
                    attempt_record["status"] = "ok"
                    attempts.append(attempt_record)
                    _clear_sparse2dgs_resume_state(output_dir)
                    shutil.copy2(attempt_log_path, output_dir / "sparse2dgs_train.log")
                    default_asset = _resolve_sparse2dgs_default_asset(output_dir)
                    summary = {
                        "paper": "Sparse2DGS",
                        "paper_url": SPARSE2DGS_PAPER_URL,
                        "repo": config.sparse2dgs_repo,
                        "scene_dir": str(active_scene_dir),
                        "output_dir": str(output_dir),
                        "default_asset": str(default_asset),
                        "train_log": str(output_dir / "sparse2dgs_train.log"),
                        "selected_frame_count": int(attempt_contract.get("selected_frame_count") or len(base_frame_indices)),
                        "selected_frame_indices": [
                            int(value)
                            for value in (attempt_contract.get("selected_frame_indices") or base_frame_indices)
                        ],
                        "exported_image_size": list(attempt_contract.get("exported_image_size") or []),
                        "bridge_budget": attempt_contract.get("bridge_budget") or {},
                        "attempts": attempts,
                    }
                    return summary
                except subprocess.TimeoutExpired:
                    latest_checkpoint = _resolve_latest_sparse2dgs_checkpoint(output_dir)
                    attempt_record["status"] = "timeout"
                    attempt_record["failure_reason"] = "timeout"
                    attempt_record["resume_checkpoint"] = str(latest_checkpoint) if latest_checkpoint else None
                    attempts.append(attempt_record)
                    if latest_checkpoint is not None and resume_attempt_index < max_resume_retries:
                        _write_sparse2dgs_resume_state(
                            output_dir,
                            {
                                "status": "checkpoint_ready",
                                "attempt_index": int(attempt_plan.attempt_index),
                                "resume_attempt_index": int(resume_attempt_index),
                                "target_views": int(attempt_plan.target_views),
                                "max_image_size": int(attempt_plan.max_image_size),
                                "total_pixel_budget": int(attempt_plan.total_pixel_budget),
                                "scene_dir": str(active_scene_dir),
                                "selected_frame_indices": [
                                    int(value)
                                    for value in (attempt_contract.get("selected_frame_indices") or base_frame_indices)
                                ],
                                "exported_image_size": list(attempt_contract.get("exported_image_size") or []),
                                "resume_checkpoint": str(latest_checkpoint),
                                "updated_at_epoch_sec": time.time(),
                            },
                        )
                        resume_attempt_index += 1
                        resume_checkpoint = latest_checkpoint
                        last_error = (
                            "sparse2dgs_timeout_resume:"
                            f"attempt_index={attempt_plan.attempt_index}:"
                            f"resume_attempt_index={resume_attempt_index}:"
                            f"checkpoint={latest_checkpoint}"
                        )
                        continue
                    if latest_checkpoint is None:
                        _clear_sparse2dgs_resume_state(output_dir)
                    last_error = (
                        "sparse2dgs_timeout:"
                        f"attempt_index={attempt_plan.attempt_index}:"
                        f"target_views={attempt_plan.target_views}:"
                        f"log={attempt_log_path}"
                    )
                    break
                except subprocess.CalledProcessError as exc:
                    oom_reason = _parse_sparse2dgs_oom_reason(attempt_log_path, ctx)
                    latest_checkpoint = _resolve_latest_sparse2dgs_checkpoint(output_dir)
                    attempt_record["status"] = "failed"
                    attempt_record["return_code"] = int(exc.returncode)
                    attempt_record["failure_reason"] = oom_reason or "subprocess_failed"
                    attempt_record["resume_checkpoint"] = str(latest_checkpoint) if latest_checkpoint else None
                    attempts.append(attempt_record)
                    if oom_reason and attempt_plan != attempt_plans[-1]:
                        _clear_sparse2dgs_resume_state(output_dir)
                        last_error = oom_reason
                        continue_with_next_budget = True
                        break
                    if oom_reason is None and latest_checkpoint is not None and resume_attempt_index < max_resume_retries:
                        _write_sparse2dgs_resume_state(
                            output_dir,
                            {
                                "status": "checkpoint_ready",
                                "attempt_index": int(attempt_plan.attempt_index),
                                "resume_attempt_index": int(resume_attempt_index),
                                "target_views": int(attempt_plan.target_views),
                                "max_image_size": int(attempt_plan.max_image_size),
                                "total_pixel_budget": int(attempt_plan.total_pixel_budget),
                                "scene_dir": str(active_scene_dir),
                                "selected_frame_indices": [
                                    int(value)
                                    for value in (attempt_contract.get("selected_frame_indices") or base_frame_indices)
                                ],
                                "exported_image_size": list(attempt_contract.get("exported_image_size") or []),
                                "resume_checkpoint": str(latest_checkpoint),
                                "updated_at_epoch_sec": time.time(),
                            },
                        )
                        resume_attempt_index += 1
                        resume_checkpoint = latest_checkpoint
                        last_error = (
                            "sparse2dgs_resume_after_failure:"
                            f"attempt_index={attempt_plan.attempt_index}:"
                            f"resume_attempt_index={resume_attempt_index}:"
                            f"checkpoint={latest_checkpoint}:"
                            f"return_code={exc.returncode}"
                        )
                        continue
                    if latest_checkpoint is None or oom_reason is not None:
                        _clear_sparse2dgs_resume_state(output_dir)
                    last_error = oom_reason or (
                        "sparse2dgs_subprocess_failed:"
                        f"return_code={exc.returncode}:"
                        f"log={attempt_log_path}"
                    )
                    break

            if continue_with_next_budget:
                continue
            break
    finally:
        ctx.sparse2dgs_scene_dir = original_scene_dir

    raise RuntimeError(last_error or "sparse2dgs_failed")


def _build_attempt_scene_contract(
    *,
    ctx: JobContext,
    base_contract: dict[str, Any],
    base_frame_indices: list[int],
    attempt_plan: Sparse2DGSAttemptPlan,
    scene_inputs: dict[str, Any],
) -> dict[str, Any]:
    attempt_frame_indices = _select_attempt_frame_indices(
        base_frame_indices=base_frame_indices,
        target_views=int(attempt_plan.target_views),
        scene_inputs=scene_inputs,
    )
    scene_label_base = str(base_contract.get("scene_label") or base_contract.get("window_id") or "single")
    scene_label = f"{scene_label_base}_attempt_{attempt_plan.attempt_index:02d}"
    return build_sparse2dgs_window_scene_contract(
        ctx,
        frame_indices=attempt_frame_indices,
        scene_label=scene_label,
        max_image_size_override=attempt_plan.max_image_size,
        total_pixel_budget_override=attempt_plan.total_pixel_budget,
    )


def _select_attempt_frame_indices(
    *,
    base_frame_indices: list[int],
    target_views: int,
    scene_inputs: dict[str, Any],
) -> list[int]:
    ordered_indices = [int(value) for value in base_frame_indices]
    if target_views >= len(ordered_indices):
        return ordered_indices
    camera_matrices = [scene_inputs["selection_camera_matrices"][index] for index in ordered_indices]
    quality_weights = [scene_inputs["frame_quality_weights"][index] for index in ordered_indices]
    images = np.asarray(scene_inputs["rgb_imgs"])[ordered_indices]
    relative_indices = _select_sparse2dgs_view_indices(
        len(ordered_indices),
        target_views=target_views,
        quality_weights=quality_weights,
        camera_matrices=camera_matrices,
        images=images,
    )
    return [ordered_indices[index] for index in relative_indices]


def _resolve_geometry_windows(contract: dict[str, Any]) -> list[Sparse2DGSWindow]:
    raw_windows = contract.get("geometry_windows")
    resolved: list[Sparse2DGSWindow] = []
    if isinstance(raw_windows, list):
        for fallback_index, raw_window in enumerate(raw_windows):
            if not isinstance(raw_window, dict):
                continue
            frame_indices = sorted(
                {
                    int(value)
                    for value in (raw_window.get("selected_frame_indices") or [])
                }
            )
            if not frame_indices:
                continue
            resolved.append(
                Sparse2DGSWindow(
                    window_id=str(raw_window.get("window_id") or f"window_{fallback_index:02d}"),
                    frame_indices=frame_indices,
                    window_index=int(raw_window.get("window_index") or fallback_index),
                )
            )
    if resolved:
        return sorted(resolved, key=lambda item: item.window_index)
    fallback_indices = [int(value) for value in (contract.get("selected_frame_indices") or [])]
    return [
        Sparse2DGSWindow(
            window_id="window_00",
            frame_indices=fallback_indices,
            window_index=0,
        )
    ]


def _make_window_progress_callback(
    *,
    progress_callback: Callable[[int, int], None] | None,
    window_index: int,
    total_windows: int,
) -> Callable[[int, int], None] | None:
    if progress_callback is None:
        return None

    def callback(step: int, total: int) -> None:
        local_total = max(int(total), 1)
        global_total = local_total * max(int(total_windows), 1)
        global_step = max(0, int(window_index)) * local_total + min(max(int(step), 0), local_total)
        progress_callback(global_step, global_total)

    return callback


def _materialize_fused_sparse2dgs_output(
    *,
    ctx: JobContext,
    base_contract: dict[str, Any],
    window_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    output_dir = ctx.sparse2dgs_dir
    assert output_dir is not None
    _reset_sparse2dgs_output_dir(output_dir, preserve={"_windows"})

    primary_summary = window_summaries[0]
    primary_output_dir = Path(str(primary_summary["output_dir"]))
    for name in ("cameras.json", "cfg_args", "input.ply"):
        source_path = primary_output_dir / name
        if source_path.exists():
            shutil.copy2(source_path, output_dir / name)

    window_assets: list[Path] = []
    max_iteration = 0
    for summary in window_summaries:
        default_asset = Path(str(summary["default_asset"]))
        if not default_asset.exists():
            raise RuntimeError(f"sparse2dgs_window_default_asset_missing:{default_asset}")
        window_assets.append(default_asset)
        max_iteration = max(max_iteration, _iteration_from_point_cloud_path(default_asset))

    fused_vertices, properties, fusion_stats = _fuse_sparse2dgs_vertices(window_assets)
    iteration = max(max_iteration, 7000)
    fused_point_dir = output_dir / "point_cloud" / f"iteration_{iteration}"
    fused_point_dir.mkdir(parents=True, exist_ok=True)
    fused_asset = fused_point_dir / "point_cloud.ply"
    _write_binary_ply_vertices(fused_asset, vertices=fused_vertices, properties=properties)

    fused_train_log = output_dir / "sparse2dgs_train.log"
    fused_train_log.write_text(
        json.dumps(
            {
                "mode": "windowed_highres_geometry",
                "window_logs": [summary.get("train_log") for summary in window_summaries],
                "fusion": fusion_stats,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return {
        "default_asset": str(fused_asset),
        "train_log": str(fused_train_log),
        "point_count": int(len(fused_vertices)),
        "iteration": int(iteration),
        "fusion": fusion_stats,
        "scene_dir": str(base_contract.get("scene_dir") or ""),
        "output_dir": str(output_dir),
    }


def _fuse_sparse2dgs_vertices(point_cloud_paths: list[Path]) -> tuple[np.ndarray, list[tuple[str, str]], dict[str, Any]]:
    all_vertices: list[np.ndarray] = []
    properties: list[tuple[str, str]] | None = None
    source_point_count = 0
    nonfinite_point_count = 0

    for path in point_cloud_paths:
        vertices, path_properties = _read_binary_ply_vertices(path)
        source_point_count += int(len(vertices))
        if properties is None:
            properties = path_properties
        elif properties != path_properties:
            raise RuntimeError(f"sparse2dgs_fusion_property_mismatch:{path}")
        filtered_vertices, filtered_count = _filter_nonfinite_sparse2dgs_vertices(vertices)
        nonfinite_point_count += int(filtered_count)
        if len(filtered_vertices) > 0:
            all_vertices.append(filtered_vertices)

    if not all_vertices or properties is None:
        raise RuntimeError("sparse2dgs_fusion_empty")

    merged = np.concatenate(all_vertices, axis=0)
    fused = merged
    voxel_size = 0.0
    deduped = False
    cap = max(1, int(config.sparse2dgs_fused_point_cap))
    if len(merged) > cap:
        fused, voxel_size, deduped = _voxel_deduplicate_sparse2dgs_vertices(merged, point_cap=cap)

    if len(fused) > cap:
        fused = _trim_sparse2dgs_vertices_by_opacity(fused, point_cap=cap)

    if len(fused) == 0:
        raise RuntimeError("sparse2dgs_fusion_all_points_filtered")

    return fused, properties, {
        "source_window_count": len(point_cloud_paths),
        "source_point_count": int(source_point_count),
        "nonfinite_point_count_filtered": int(nonfinite_point_count),
        "fused_point_count": int(len(fused)),
        "voxel_size": float(voxel_size),
        "voxel_deduplicated": bool(deduped),
        "point_cap": int(cap),
    }


def _voxel_deduplicate_sparse2dgs_vertices(
    vertices: np.ndarray,
    *,
    point_cap: int,
    voxel_divisor: float | None = None,
) -> tuple[np.ndarray, float, bool]:
    vertices, _ = _filter_nonfinite_sparse2dgs_vertices(vertices)
    xyz = _vertex_xyz(vertices)
    if xyz.shape[0] == 0:
        return vertices, 0.0, False

    bbox_min = xyz.min(axis=0)
    bbox_max = xyz.max(axis=0)
    diagonal = float(np.linalg.norm(bbox_max - bbox_min))
    if diagonal <= 1e-6:
        return _trim_sparse2dgs_vertices_by_opacity(vertices, point_cap=point_cap), 0.0, True

    divisor = float(voxel_divisor) if voxel_divisor is not None else float(config.sparse2dgs_fused_point_voxel_divisor)
    voxel_size = max(diagonal / max(divisor, 1.0), 1e-5)
    quantized = np.floor((xyz - bbox_min[None, :]) / voxel_size).astype(np.int64)
    key_dtype = np.dtype([("x", "<i8"), ("y", "<i8"), ("z", "<i8")])
    keys = np.empty(len(vertices), dtype=key_dtype)
    keys["x"] = quantized[:, 0]
    keys["y"] = quantized[:, 1]
    keys["z"] = quantized[:, 2]
    _, inverse = np.unique(keys, return_inverse=True)

    scores = _vertex_scores(vertices)
    order = np.lexsort((-scores, inverse))
    ordered_inverse = inverse[order]
    keep_mask = np.ones(len(order), dtype=bool)
    if len(order) > 1:
        keep_mask[1:] = ordered_inverse[1:] != ordered_inverse[:-1]
    selected_indices = order[keep_mask]
    deduped = vertices[selected_indices]
    if len(deduped) > point_cap:
        deduped = _trim_sparse2dgs_vertices_by_opacity(deduped, point_cap=point_cap)
    return deduped, voxel_size, True


def _trim_sparse2dgs_vertices_by_opacity(vertices: np.ndarray, *, point_cap: int) -> np.ndarray:
    if len(vertices) <= point_cap:
        return vertices
    scores = _vertex_scores(vertices)
    keep_indices = np.argsort(scores)[-point_cap:]
    keep_indices.sort()
    return vertices[keep_indices]


def _filter_nonfinite_sparse2dgs_vertices(vertices: np.ndarray) -> tuple[np.ndarray, int]:
    if len(vertices) == 0:
        return vertices, 0
    finite_mask = np.isfinite(_vertex_xyz(vertices)).all(axis=1)
    if bool(finite_mask.all()):
        return vertices, 0
    return vertices[finite_mask], int((~finite_mask).sum())


def _vertex_xyz(vertices: np.ndarray) -> np.ndarray:
    return np.column_stack(
        [
            np.asarray(vertices["x"], dtype=np.float64),
            np.asarray(vertices["y"], dtype=np.float64),
            np.asarray(vertices["z"], dtype=np.float64),
        ]
    )


def _vertex_scores(vertices: np.ndarray) -> np.ndarray:
    if "opacity" in vertices.dtype.names:
        return np.asarray(vertices["opacity"], dtype=np.float64)
    return np.ones(len(vertices), dtype=np.float64)


def _read_binary_ply_vertices(path: Path) -> tuple[np.ndarray, list[tuple[str, str]]]:
    with path.open("rb") as handle:
        header_lines: list[str] = []
        while True:
            line = handle.readline()
            if not line:
                raise RuntimeError(f"ply_header_truncated:{path}")
            decoded = line.decode("ascii", errors="strict").strip()
            header_lines.append(decoded)
            if decoded == "end_header":
                break

        if not header_lines or header_lines[0] != "ply":
            raise RuntimeError(f"ply_header_invalid:{path}")
        if len(header_lines) < 2 or "binary_little_endian" not in header_lines[1]:
            raise RuntimeError(f"ply_format_unsupported:{path}")

        vertex_count = 0
        current_element: str | None = None
        properties: list[tuple[str, str]] = []
        for line in header_lines[1:]:
            if not line:
                continue
            parts = line.split()
            if not parts:
                continue
            keyword = parts[0]
            if keyword == "element" and len(parts) >= 3:
                current_element = parts[1]
                if current_element == "vertex":
                    vertex_count = int(parts[2])
                continue
            if keyword == "property" and current_element == "vertex":
                if len(parts) != 3 or parts[1] == "list":
                    raise RuntimeError(f"ply_vertex_property_unsupported:{path}:{line}")
                properties.append((parts[1], parts[2]))

        if vertex_count <= 0 or not properties:
            raise RuntimeError(f"ply_vertex_data_missing:{path}")

        dtype = np.dtype(
            [
                (name, "<" + _PLY_TYPE_TO_NUMPY[ply_type])
                for ply_type, name in properties
            ]
        )
        vertices = np.fromfile(handle, dtype=dtype, count=vertex_count)
        if len(vertices) != vertex_count:
            raise RuntimeError(f"ply_vertex_count_mismatch:{path}")
        return vertices, properties


def _write_binary_ply_vertices(
    path: Path,
    *,
    vertices: np.ndarray,
    properties: list[tuple[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(b"ply\n")
        handle.write(b"format binary_little_endian 1.0\n")
        handle.write(f"element vertex {len(vertices)}\n".encode("ascii"))
        for ply_type, name in properties:
            handle.write(f"property {ply_type} {name}\n".encode("ascii"))
        handle.write(b"end_header\n")
        np.asarray(vertices).tofile(handle)


def _iteration_from_point_cloud_path(path: Path) -> int:
    parent_name = path.parent.name
    if parent_name.startswith("iteration_"):
        try:
            return int(parent_name.split("iteration_", 1)[1])
        except ValueError:
            return 7000
    return 7000


def _render_command(*, template: str, ctx: JobContext, repo_dir: Path, output_dir: Path) -> list[str]:
    if not template.strip():
        return []
    rendered = template.format(
        curated_dir=str(ctx.curated_dir),
        slam3r_dir=str(ctx.slam3r_dir),
        scene_dir=str(ctx.sparse2dgs_scene_dir or ""),
        sparse2dgs_dir=str(ctx.sparse2dgs_dir),
        output_dir=str(output_dir),
        repo_dir=str(repo_dir),
    )
    return shlex.split(rendered)


def _augment_sparse2dgs_command(
    command: list[str],
    *,
    checkpoint_iterations: list[int],
    start_checkpoint: Path | None,
) -> list[str]:
    augmented = list(command)
    if checkpoint_iterations:
        augmented.append("--checkpoint_iterations")
        augmented.extend(str(value) for value in checkpoint_iterations)
    if start_checkpoint is not None:
        augmented.extend(["--start_checkpoint", str(start_checkpoint)])
    return augmented


def _build_sparse2dgs_checkpoint_iterations() -> list[int]:
    interval = max(0, int(config.sparse2dgs_checkpoint_interval))
    if interval <= 0:
        return []
    total_iterations = 7000
    checkpoints: list[int] = []
    step = interval
    while step < total_iterations:
        checkpoints.append(int(step))
        step += interval
    return checkpoints


def _resolve_latest_sparse2dgs_checkpoint(output_dir: Path) -> Path | None:
    candidates: list[tuple[int, Path]] = []
    for path in output_dir.glob("chkpnt*.pth"):
        suffix = path.stem.replace("chkpnt", "", 1)
        try:
            iteration = int(suffix)
        except ValueError:
            continue
        candidates.append((iteration, path))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def _sparse2dgs_resume_state_path(output_dir: Path) -> Path:
    filename = getattr(config, "sparse2dgs_resume_state_filename", "sparse2dgs_resume_state.json")
    return output_dir / str(filename)


def _load_sparse2dgs_resume_state(output_dir: Path) -> dict[str, Any] | None:
    path = _sparse2dgs_resume_state_path(output_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    checkpoint_value = payload.get("resume_checkpoint")
    if checkpoint_value:
        checkpoint_path = Path(str(checkpoint_value))
        if not checkpoint_path.exists():
            return None
    return payload


def _write_sparse2dgs_resume_state(output_dir: Path, payload: dict[str, Any]) -> None:
    path = _sparse2dgs_resume_state_path(output_dir)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _clear_sparse2dgs_resume_state(output_dir: Path) -> None:
    _sparse2dgs_resume_state_path(output_dir).unlink(missing_ok=True)


def _match_sparse2dgs_resume_state(
    *,
    output_dir: Path,
    attempt_plan: Sparse2DGSAttemptPlan,
    attempt_contract: dict[str, Any],
) -> dict[str, Any] | None:
    payload = _load_sparse2dgs_resume_state(output_dir)
    if payload is None:
        return None
    if str(payload.get("status") or "") not in {"running", "checkpoint_ready"}:
        return None
    if int(payload.get("attempt_index") or -1) != int(attempt_plan.attempt_index):
        return None
    if int(payload.get("target_views") or -1) != int(attempt_plan.target_views):
        return None
    if int(payload.get("max_image_size") or -1) != int(attempt_plan.max_image_size):
        return None
    if int(payload.get("total_pixel_budget") or -1) != int(attempt_plan.total_pixel_budget):
        return None
    if str(payload.get("scene_dir") or "") != str(attempt_contract.get("scene_dir") or ""):
        return None
    payload_indices = [int(value) for value in (payload.get("selected_frame_indices") or [])]
    contract_indices = [int(value) for value in (attempt_contract.get("selected_frame_indices") or [])]
    if payload_indices != contract_indices:
        return None
    checkpoint_value = payload.get("resume_checkpoint")
    if not checkpoint_value:
        return None
    checkpoint_path = Path(str(checkpoint_value))
    if not checkpoint_path.exists():
        return None
    return payload


def _sparse2dgs_attempt_log_path(
    attempt_logs_dir: Path,
    *,
    attempt_index: int,
    resume_attempt_index: int,
) -> Path:
    base = f"sparse2dgs_train_attempt_{attempt_index + 1}"
    if resume_attempt_index <= 0:
        return attempt_logs_dir / f"{base}.log"
    return attempt_logs_dir / f"{base}_resume_{resume_attempt_index}.log"


def _resolve_sparse2dgs_default_asset(output_dir: Path) -> Path:
    point_cloud_root = output_dir / "point_cloud"
    if not point_cloud_root.exists():
        raise RuntimeError(f"sparse2dgs_point_cloud_missing:{point_cloud_root}")

    candidates: list[tuple[int, Path]] = []
    for child in point_cloud_root.iterdir():
        if not child.is_dir() or not child.name.startswith("iteration_"):
            continue
        try:
            iteration = int(child.name.split("iteration_", 1)[1])
        except ValueError:
            continue
        point_cloud = child / "point_cloud.ply"
        if point_cloud.is_file():
            candidates.append((iteration, point_cloud))

    if not candidates:
        raise RuntimeError(f"sparse2dgs_default_asset_missing:{point_cloud_root}")

    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def _load_sparse2dgs_scene_contract(ctx: JobContext) -> dict[str, object]:
    assert ctx.slam3r_dir is not None
    contract_path = ctx.slam3r_dir / "sparse2dgs_scene_contract.json"
    if not contract_path.exists():
        raise RuntimeError(f"sparse2dgs_scene_contract_missing:{contract_path}")
    try:
        return json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"sparse2dgs_scene_contract_invalid:{contract_path}") from exc


def _build_sparse2dgs_attempt_plans(contract: dict[str, object]) -> list[Sparse2DGSAttemptPlan]:
    selected_views = max(
        int(contract.get("selected_frame_count") or 0),
        min(
            int(contract.get("selected_frame_target") or 0),
            max(int(contract.get("frame_count") or 0), 0),
        ),
    )
    exported_size = contract.get("exported_image_size") or []
    if not isinstance(exported_size, list) or len(exported_size) != 2:
        raise RuntimeError("sparse2dgs_scene_contract_missing_export_size")
    current_longest_side = max(int(exported_size[0]), int(exported_size[1]))
    current_short_side = min(int(exported_size[0]), int(exported_size[1]))
    bridge_budget = contract.get("bridge_budget") or {}
    if not isinstance(bridge_budget, dict):
        bridge_budget = {}
    current_pixel_budget = max(
        1_000_000,
        int(bridge_budget.get("total_pixel_budget") or config.sparse2dgs_contract_total_pixel_budget),
    )
    min_image_size = max(256, int(config.sparse2dgs_contract_min_image_size))
    min_views = max(8, int(config.sparse2dgs_min_views))
    hq_short_side_floor = max(min_image_size, int(config.geometry_hq_min_exported_short_side))
    scale = min(max(float(config.sparse2dgs_oom_resolution_scale), 0.50), 0.95)
    view_drop_step = max(1, int(config.sparse2dgs_oom_view_drop_step))
    max_attempts = max(1, 1 + int(config.sparse2dgs_max_oom_retries))

    plans: list[Sparse2DGSAttemptPlan] = [
        Sparse2DGSAttemptPlan(
            attempt_index=0,
            target_views=max(selected_views, min_views),
            max_image_size=current_longest_side,
            total_pixel_budget=current_pixel_budget,
            reason="initial_budget",
        )
    ]

    current_views = plans[0].target_views
    current_max_image_size = plans[0].max_image_size
    current_budget = plans[0].total_pixel_budget
    current_short_side = current_short_side
    while len(plans) < max_attempts:
        next_views = current_views
        next_max_image_size = current_max_image_size
        next_budget = current_budget
        reason = ""

        if current_max_image_size > min_image_size:
            scaled_size = _round_down_to_multiple(int(math.floor(current_max_image_size * scale)), 64)
            scaled_short_side = _round_down_to_multiple(int(math.floor(current_short_side * scale)), 64)
            if current_views > min_views and scaled_short_side < hq_short_side_floor:
                next_views = max(min_views, current_views - view_drop_step)
                reason = "oom_reduce_views_preserve_short_side"
            else:
                next_max_image_size = max(min_image_size, scaled_size)
                next_budget = max(1_000_000, int(current_budget * scale * scale))
                current_short_side = max(min_image_size, scaled_short_side)
                reason = "oom_reduce_resolution"
        elif current_views > min_views:
            next_views = max(min_views, current_views - view_drop_step)
            reason = "oom_reduce_views"
        else:
            break

        if (
            next_views == current_views
            and next_max_image_size == current_max_image_size
            and next_budget == current_budget
        ):
            break

        current_views = next_views
        current_max_image_size = next_max_image_size
        current_budget = next_budget
        plans.append(
            Sparse2DGSAttemptPlan(
                attempt_index=len(plans),
                target_views=current_views,
                max_image_size=current_max_image_size,
                total_pixel_budget=current_budget,
                reason=reason,
            )
        )

    return plans


def _reset_sparse2dgs_output_dir(output_dir: Path, *, preserve: set[str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for child in output_dir.iterdir():
        if child.name in preserve:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
            continue
        child.unlink(missing_ok=True)


def _run_sparse2dgs_command(
    command: list[str],
    *,
    cwd: str,
    timeout: int,
    log_path: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> None:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("w", encoding="utf-8") as log_handle:
        _write_sparse2dgs_log_header(log_handle, command=command, cwd=cwd, timeout=timeout)

        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,
            bufsize=0,
            env=env,
        )

        assert process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        start_time = time.monotonic()
        trailing = ""
        last_reported: list[tuple[int, int] | None] = [None]

        try:
            while True:
                if time.monotonic() - start_time > timeout:
                    process.kill()
                    _write_sparse2dgs_log_footer(
                        log_handle,
                        status="timeout",
                        elapsed_sec=time.monotonic() - start_time,
                    )
                    raise subprocess.TimeoutExpired(command, timeout)

                events = selector.select(timeout=1.0)
                if not events:
                    if process.poll() is not None:
                        break
                    continue

                for key, _ in events:
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue

                    decoded = chunk.decode("utf-8", errors="replace")
                    sys.stdout.write(decoded)
                    sys.stdout.flush()
                    log_handle.write(decoded)
                    log_handle.flush()
                    trailing = _consume_sparse2dgs_output(
                        trailing + decoded,
                        progress_callback=progress_callback,
                        last_reported=last_reported,
                    )

                if process.poll() is not None and not selector.get_map():
                    break
        finally:
            selector.close()

        if trailing:
            _emit_sparse2dgs_progress(
                trailing,
                progress_callback=progress_callback,
                last_reported=last_reported,
            )

        return_code = process.wait()
        elapsed_sec = time.monotonic() - start_time
        _write_sparse2dgs_log_footer(
            log_handle,
            status="ok" if return_code == 0 else "failed",
            elapsed_sec=elapsed_sec,
            return_code=return_code,
        )
        if return_code != 0:
            raise subprocess.CalledProcessError(return_code, command)


def _parse_sparse2dgs_oom_reason(log_path: Path, ctx: JobContext) -> str | None:
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    if "OutOfMemoryError" not in content and "CUDA out of memory" not in content:
        return None

    detail = ""
    contract_path = ctx.slam3r_dir / "sparse2dgs_scene_contract.json"
    if contract_path.exists():
        try:
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            selected_views = contract.get("selected_frame_count")
            exported_size = contract.get("exported_image_size")
            detail = f":selected_views={selected_views}:exported_image_size={exported_size}"
        except (OSError, json.JSONDecodeError):
            detail = ""
    return f"sparse2dgs_cuda_oom{detail}"


def _write_sparse2dgs_log_header(
    log_handle,
    *,
    command: list[str],
    cwd: str,
    timeout: int,
) -> None:
    log_handle.write(f"[sparse2dgs] cwd={cwd}\n")
    log_handle.write(f"[sparse2dgs] timeout_sec={timeout}\n")
    log_handle.write(f"[sparse2dgs] command={shlex.join(command)}\n")
    log_handle.write("[sparse2dgs] --- begin combined stdout/stderr ---\n")
    log_handle.flush()


def _round_down_to_multiple(value: int, multiple: int) -> int:
    if multiple <= 0:
        return value
    return max(multiple, (value // multiple) * multiple)


def _write_sparse2dgs_log_footer(
    log_handle,
    *,
    status: str,
    elapsed_sec: float,
    return_code: int | None = None,
) -> None:
    log_handle.write("\n[sparse2dgs] --- end combined stdout/stderr ---\n")
    log_handle.write(f"[sparse2dgs] status={status}\n")
    if return_code is not None:
        log_handle.write(f"[sparse2dgs] return_code={return_code}\n")
    log_handle.write(f"[sparse2dgs] elapsed_sec={elapsed_sec:.2f}\n")
    log_handle.flush()


def _consume_sparse2dgs_output(
    buffered: str,
    *,
    progress_callback: Callable[[int, int], None] | None,
    last_reported: list[tuple[int, int] | None],
) -> str:
    lines = re.split(r"[\r\n]+", buffered)
    if buffered and buffered[-1] not in "\r\n":
        trailing = lines.pop()
    else:
        trailing = ""

    for line in lines:
        _emit_sparse2dgs_progress(
            line,
            progress_callback=progress_callback,
            last_reported=last_reported,
        )
    return trailing


def _emit_sparse2dgs_progress(
    line: str,
    *,
    progress_callback: Callable[[int, int], None] | None,
    last_reported: list[tuple[int, int] | None],
) -> None:
    if progress_callback is None:
        return

    match = SPARSE2DGS_TRAINING_STEP_PATTERN.search(line)
    if match is None:
        return

    step = int(match.group(1))
    total = max(int(match.group(2)), 1)
    signature = (step, total)
    if last_reported[0] == signature:
        return
    last_reported[0] = signature
    progress_callback(step, total)
