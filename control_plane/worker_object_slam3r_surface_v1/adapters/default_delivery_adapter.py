from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..config import config
from ..context import JobContext
from ..quality_gate import update_quality_card


@dataclass(frozen=True)
class _Camera:
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float


@dataclass(frozen=True)
class _ImagePose:
    camera_id: int
    name: str
    qvec: tuple[float, float, float, float]
    tvec: tuple[float, float, float]


@dataclass(frozen=True)
class _ProjectionView:
    pose: _ImagePose
    camera: _Camera
    image: np.ndarray
    rotation: np.ndarray
    translation: np.ndarray
    center: np.ndarray


OptimizeProgressCallback = Callable[[dict[str, Any]], None]


def optimize_default_mesh_delivery(
    ctx: JobContext,
    *,
    progress_callback: OptimizeProgressCallback | None = None,
) -> Path:
    assert ctx.matcha_dir is not None
    assert ctx.delivery_dir is not None

    _emit_optimize_progress(
        progress_callback,
        progress=0.02,
        title="正在读取 HQ 网格",
        detail="正在载入 MAtCha HQ 网格，并检查原始拓扑规模。",
        metrics={"optimize_phase": "load_matcha_mesh"},
    )
    raw_mesh = _resolve_matcha_mesh_asset(ctx.matcha_dir)
    mesh = _load_mesh(raw_mesh)
    initial_faces = int(len(mesh.faces))
    initial_vertices = int(len(mesh.vertices))
    initial_area = float(getattr(mesh, "area", 0.0))

    _emit_optimize_progress(
        progress_callback,
        progress=0.08,
        title="正在清理无效顶点",
        detail=f"已读取原始网格，当前 {initial_faces:,} 面，正在清理无引用顶点并合并重复顶点。",
        metrics={
            "optimize_phase": "cleanup_vertices",
            "initial_faces": str(initial_faces),
            "initial_vertices": str(initial_vertices),
        },
    )
    mesh.remove_unreferenced_vertices()
    try:
        mesh.merge_vertices()
    except Exception:
        pass

    raw_reference_mesh = _copy_mesh_geometry(mesh)
    raw_reference_faces = int(len(raw_reference_mesh.faces))
    raw_reference_vertices = int(len(raw_reference_mesh.vertices))
    raw_reference_area = float(getattr(raw_reference_mesh, "area", 0.0))
    stage_quality: dict[str, dict[str, float | int]] = {
        "after_vertex_cleanup": _stage_quality_snapshot(
            reference_mesh=raw_reference_mesh,
            candidate_mesh=mesh,
        ),
    }

    mesh, working_summary = _prepare_working_mesh(
        mesh,
        progress_callback=progress_callback,
        progress_start=0.12,
        progress_end=0.22,
    )
    stage_quality["after_working_mesh"] = _stage_quality_snapshot(
        reference_mesh=raw_reference_mesh,
        candidate_mesh=mesh,
    )
    mesh, multilayer_summary = _prune_multilayer_faces(
        mesh,
        progress_callback=progress_callback,
        progress_start=0.22,
        progress_end=0.26,
    )
    stage_quality["after_multilayer_prune"] = _stage_quality_snapshot(
        reference_mesh=raw_reference_mesh,
        candidate_mesh=mesh,
    )

    _emit_optimize_progress(
        progress_callback,
        progress=0.27,
        title="正在清理碎片",
        detail="正在去掉极小碎片，只保留主连通表面。",
        metrics={"optimize_phase": "drop_small_components"},
    )
    mesh = _drop_small_components(mesh)
    stage_quality["after_drop_small_components"] = _stage_quality_snapshot(
        reference_mesh=raw_reference_mesh,
        candidate_mesh=mesh,
    )
    _emit_optimize_progress(
        progress_callback,
        progress=0.31,
        title="正在清理退化三角面",
        detail="正在清理退化面和重复面，避免后续修补时把坏拓扑继续放大。",
        metrics={"optimize_phase": "drop_degenerate_faces"},
    )
    mesh = _drop_degenerate_faces(mesh)
    stage_quality["after_drop_degenerate_faces"] = _stage_quality_snapshot(
        reference_mesh=raw_reference_mesh,
        candidate_mesh=mesh,
    )
    reference_mesh = _copy_mesh_geometry(mesh)
    reference_faces = int(len(reference_mesh.faces))
    reference_vertices = int(len(reference_mesh.vertices))
    reference_area = float(getattr(reference_mesh, "area", 0.0))
    reference_extents = _mesh_extents(reference_mesh)
    mesh, pre_self_intersection_summary = _reduce_self_intersections(
        mesh,
        progress_callback=progress_callback,
        progress_start=0.33,
        progress_end=0.36,
    )
    stage_quality["after_pre_self_intersection_cleanup"] = _stage_quality_snapshot(
        reference_mesh=raw_reference_mesh,
        candidate_mesh=mesh,
    )
    mesh, topology_summary = _repair_mesh_topology(
        mesh,
        progress_callback=progress_callback,
        progress_start=0.36,
        progress_end=0.50,
        phase_prefix="first_topology_repair",
    )
    stage_quality["after_first_topology_repair"] = _stage_quality_snapshot(
        reference_mesh=raw_reference_mesh,
        candidate_mesh=mesh,
    )
    mesh = _smooth_mesh(
        mesh,
        progress_callback=progress_callback,
        progress_start=0.50,
        progress_end=0.70,
    )
    stage_quality["after_smooth_mesh"] = _stage_quality_snapshot(
        reference_mesh=raw_reference_mesh,
        candidate_mesh=mesh,
    )

    _emit_optimize_progress(
        progress_callback,
        progress=0.72,
        title="正在修复法线",
        detail="正在统一法线方向，减少移动端 viewer 里的阴影破碎感。",
        metrics={"optimize_phase": "fix_normals"},
    )
    try:
        mesh.fix_normals(multibody=True)
    except Exception:
        mesh.fix_normals()

    simplification_applied = False
    simplify_reason = "preserve_geometry"
    target_faces = max(1024, config.delivery_target_face_count)
    pre_budget_faces = int(len(mesh.faces))
    _emit_optimize_progress(
        progress_callback,
        progress=0.78,
        title="正在评估默认预算",
        detail="正在判断是否需要为了 HQ-only 成品做受控降面，优先保几何覆盖。",
        metrics={
            "optimize_phase": "evaluate_simplification",
            "target_faces": str(target_faces),
            "current_faces": str(len(mesh.faces)),
        },
    )
    if _should_simplify_mesh(mesh, target_faces=target_faces):
        _emit_optimize_progress(
            progress_callback,
            progress=0.82,
            title="正在收敛网格预算",
            detail="当前网格超出默认预算，正在做保形降面并尽量保住主表面。",
            metrics={"optimize_phase": "simplify_mesh"},
        )
        mesh, simplify_guard_summary = _shape_preserving_simplify_mesh(
            mesh,
            reference_mesh=raw_reference_mesh,
            target_faces=_simplify_target_faces(pre_budget_faces, target_faces=target_faces),
            min_extent_retention_floor=float(config.mesh_fidelity_hq_min_extent_retention),
            min_surface_area_retention_floor=float(config.mesh_fidelity_hq_min_surface_area_retention),
        )
        simplification_applied = bool(simplify_guard_summary.get("accepted", False)) and len(mesh.faces) < pre_budget_faces
        simplify_reason = str(simplify_guard_summary.get("reason", "target_budget"))
    else:
        _emit_optimize_progress(
            progress_callback,
            progress=0.82,
            title="默认预算无需降面",
            detail="当前网格仍在默认预算内，继续保几何并进入最终拓扑修整。",
            metrics={"optimize_phase": "preserve_geometry_budget"},
        )
        simplify_guard_summary = {
            "accepted": False,
            "reason": "preserve_geometry_budget",
            "attempted_targets": [],
            "accepted_target": pre_budget_faces,
        }
    stage_quality["after_budget_simplification"] = _stage_quality_snapshot(
        reference_mesh=raw_reference_mesh,
        candidate_mesh=mesh,
    )

    mesh, final_multilayer_summary = _prune_multilayer_faces(
        mesh,
        progress_callback=progress_callback,
        progress_start=0.84,
        progress_end=0.87,
    )
    stage_quality["after_final_multilayer_prune"] = _stage_quality_snapshot(
        reference_mesh=raw_reference_mesh,
        candidate_mesh=mesh,
    )
    mesh, self_intersection_summary = _reduce_self_intersections(
        mesh,
        progress_callback=progress_callback,
        progress_start=0.87,
        progress_end=0.91,
    )
    stage_quality["after_final_self_intersection_cleanup"] = _stage_quality_snapshot(
        reference_mesh=raw_reference_mesh,
        candidate_mesh=mesh,
    )
    mesh, final_topology_summary = _repair_mesh_topology(
        mesh,
        progress_callback=progress_callback,
        progress_start=0.91,
        progress_end=0.94,
        phase_prefix="final_topology_repair",
    )
    stage_quality["after_final_topology_repair"] = _stage_quality_snapshot(
        reference_mesh=raw_reference_mesh,
        candidate_mesh=mesh,
    )
    topology_summary = {
        **topology_summary,
        "watertight_after": bool(final_topology_summary["watertight_after"]),
        "hole_fill_iterations": int(topology_summary["hole_fill_iterations"]) + int(final_topology_summary["hole_fill_iterations"]),
        "holes_repaired": bool(topology_summary["holes_repaired"] or final_topology_summary["holes_repaired"]),
        "aggressive_repair_allowed": bool(
            topology_summary.get("aggressive_repair_allowed", True)
            and final_topology_summary.get("aggressive_repair_allowed", True)
        ),
        "closure_strategy": str(final_topology_summary.get("closure_strategy", topology_summary.get("closure_strategy", "native_repair"))),
        "small_hole_patches": int(topology_summary.get("small_hole_patches", 0)) + int(final_topology_summary.get("small_hole_patches", 0)),
        "small_holes_filled_count": int(topology_summary.get("small_holes_filled_count", 0)) + int(final_topology_summary.get("small_holes_filled_count", 0)),
        "faces_added_by_hole_fill": int(topology_summary.get("faces_added_by_hole_fill", 0)) + int(final_topology_summary.get("faces_added_by_hole_fill", 0)),
        "max_filled_hole_perimeter_ratio": max(
            float(topology_summary.get("max_filled_hole_perimeter_ratio", 0.0)),
            float(final_topology_summary.get("max_filled_hole_perimeter_ratio", 0.0)),
        ),
        "boundary_length_before": float(topology_summary.get("boundary_length_before", 0.0)),
        "boundary_length_after": float(final_topology_summary.get("boundary_length_after", 0.0)),
        "boundary_edge_ratio_after": float(final_topology_summary.get("boundary_edge_ratio_after", 0.0)),
        "small_hole_candidates_before": int(topology_summary.get("small_hole_candidates_before", 0)),
        "large_hole_candidates_before": int(topology_summary.get("large_hole_candidates_before", 0)),
        "small_hole_candidates_after": int(final_topology_summary.get("small_hole_candidates_after", 0)),
        "large_hole_candidates_after": int(final_topology_summary.get("large_hole_candidates_after", 0)),
        "pre_self_intersection_cleanup_applied": bool(pre_self_intersection_summary.get("applied", False)),
        "pre_self_intersection_cleanup_removed_faces": int(pre_self_intersection_summary.get("removed_faces", 0)),
        "pre_self_intersection_cleanup_initial_ratio": float(pre_self_intersection_summary.get("initial_ratio", 0.0)),
        "pre_self_intersection_cleanup_final_ratio": float(pre_self_intersection_summary.get("final_ratio", 0.0)),
        "final_multilayer_prune_applied": bool(final_multilayer_summary.get("applied", False)),
        "final_multilayer_prune_removed_faces": int(final_multilayer_summary.get("removed_faces", 0)),
        "final_multilayer_prune_initial_ratio": float(final_multilayer_summary.get("initial_ratio", 0.0)),
        "final_multilayer_prune_final_ratio": float(final_multilayer_summary.get("final_ratio", 0.0)),
        "self_intersection_cleanup_applied": bool(self_intersection_summary.get("applied", False)),
        "self_intersection_cleanup_removed_faces": int(self_intersection_summary.get("removed_faces", 0)),
        "self_intersection_cleanup_initial_ratio": float(self_intersection_summary.get("initial_ratio", 0.0)),
        "self_intersection_cleanup_final_ratio": float(self_intersection_summary.get("final_ratio", 0.0)),
    }

    destination = ctx.delivery_dir / "optimized_mesh.ply"
    destination.parent.mkdir(parents=True, exist_ok=True)
    _emit_optimize_progress(
        progress_callback,
        progress=0.97,
        title="正在写出优化网格",
        detail="正在把优化后的 HQ 网格写盘，并生成交付摘要。",
        metrics={"optimize_phase": "export_optimized_mesh"},
    )
    mesh.export(destination)

    summary = {
        "source_mesh": str(raw_mesh),
        "optimized_mesh": str(destination),
        "initial_faces": initial_faces,
        "initial_vertices": initial_vertices,
        "raw_reference_faces": raw_reference_faces,
        "raw_reference_vertices": raw_reference_vertices,
        "reference_faces": reference_faces,
        "reference_vertices": reference_vertices,
        "optimized_faces": int(len(mesh.faces)),
        "optimized_vertices": int(len(mesh.vertices)),
        "initial_surface_area": float(initial_area),
        "raw_reference_surface_area": float(raw_reference_area),
        "reference_surface_area": float(reference_area),
        "preserve_geometry": bool(config.delivery_preserve_geometry_default),
        "simplification_applied": simplification_applied,
        "simplify_reason": simplify_reason,
        "target_face_count": int(config.delivery_target_face_count),
        "hard_max_face_count": int(config.delivery_hard_max_face_count),
        "component_min_faces": int(config.delivery_component_min_faces),
        "component_ratio_floor": float(config.delivery_component_ratio_floor),
        "watertight_before": bool(topology_summary["watertight_before"]),
        "watertight_after": bool(topology_summary["watertight_after"]),
        "hole_fill_iterations": int(topology_summary["hole_fill_iterations"]),
        "holes_repaired": bool(topology_summary["holes_repaired"]),
        "aggressive_repair_allowed": bool(topology_summary.get("aggressive_repair_allowed", True)),
        "open_surface_ready": bool(len(mesh.faces) > 0),
        "closure_strategy": str(topology_summary.get("closure_strategy", "native_repair")),
        "working_mesh_applied": bool(working_summary["applied"]),
        "working_mesh_reason": str(working_summary["reason"]),
        "working_target_faces": int(working_summary["target_faces"]),
        "working_faces": int(working_summary["working_faces"]),
        "working_vertices": int(working_summary["working_vertices"]),
        "working_surface_area_retention": float(working_summary["surface_area_retention"]),
        "working_min_extent_retention": float(working_summary["min_extent_retention"]),
        "working_attempted_targets": list(working_summary.get("attempted_targets", [])),
        "working_accepted_target": int(working_summary.get("accepted_target", working_summary["working_faces"])),
        "budget_simplify_attempted_targets": list(simplify_guard_summary.get("attempted_targets", [])),
        "budget_simplify_accepted_target": int(simplify_guard_summary.get("accepted_target", pre_budget_faces)),
        "final_multilayer_prune_applied": bool(final_multilayer_summary.get("applied", False)),
        "final_multilayer_prune_removed_faces": int(final_multilayer_summary.get("removed_faces", 0)),
        "final_multilayer_prune_initial_ratio": float(final_multilayer_summary.get("initial_ratio", 0.0)),
        "final_multilayer_prune_final_ratio": float(final_multilayer_summary.get("final_ratio", 0.0)),
        "self_intersection_cleanup_applied": bool(self_intersection_summary.get("applied", False)),
        "self_intersection_cleanup_removed_faces": int(self_intersection_summary.get("removed_faces", 0)),
        "self_intersection_cleanup_initial_ratio": float(self_intersection_summary.get("initial_ratio", 0.0)),
        "self_intersection_cleanup_final_ratio": float(self_intersection_summary.get("final_ratio", 0.0)),
        "optimize_stage_quality": stage_quality,
    }
    _write_open_surface_hq_report(
        ctx=ctx,
        mesh=mesh,
        initial_area=reference_area,
        initial_extents=reference_extents,
    )
    sheetness_metrics = _write_sheetness_hq_report(
        ctx=ctx,
        mesh=mesh,
        initial_area=reference_area,
        initial_extents=reference_extents,
    )
    _write_hole_fill_hq_report(
        ctx=ctx,
        initial_faces=reference_faces,
        topology_summary=topology_summary,
    )
    mesh_fidelity_metrics = _write_mesh_fidelity_hq_report(
        ctx=ctx,
        reference_mesh=raw_reference_mesh,
        working_summary=working_summary,
        optimized_mesh=mesh,
    )
    summary.update(
        {
            "mesh_fidelity_surface_area_retention": float(mesh_fidelity_metrics["surface_area_retention"]),
            "mesh_fidelity_min_extent_retention": float(mesh_fidelity_metrics["min_extent_retention"]),
            "mesh_fidelity_vertex_distance_mean_ratio": float(mesh_fidelity_metrics["vertex_distance_mean_ratio"]),
            "mesh_fidelity_vertex_distance_p95_ratio": float(mesh_fidelity_metrics["vertex_distance_p95_ratio"]),
            "sheetness_balling_score": float(sheetness_metrics["balling_score"]),
            "sheetness_self_intersection_ratio": float(sheetness_metrics["self_intersection_ratio"]),
            "sheetness_local_sheet_branch_count_mean": float(sheetness_metrics["local_sheet_branch_count_mean"]),
            "sheetness_local_sheet_branch_count_p95": float(sheetness_metrics["local_sheet_branch_count_p95"]),
        }
    )
    (ctx.delivery_dir / config.delivery_mesh_summary_filename).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _emit_optimize_progress(
        progress_callback,
        progress=1.0,
        title="HQ 网格优化完成",
        detail=(
            f"HQ 网格优化完成，当前 {len(mesh.faces):,} 面、{len(mesh.vertices):,} 顶点，"
            "即将进入照片纹理投影。"
        ),
        metrics={
            "optimize_phase": "done",
            "optimized_faces": str(len(mesh.faces)),
            "optimized_vertices": str(len(mesh.vertices)),
            "holes_repaired": str(bool(topology_summary["holes_repaired"])).lower(),
            "watertight_after": str(bool(topology_summary["watertight_after"])).lower(),
        },
    )
    return destination


def bake_default_texture_delivery(
    ctx: JobContext,
    *,
    progress_callback: OptimizeProgressCallback | None = None,
) -> Path:
    assert ctx.delivery_dir is not None
    assert ctx.sparse2dgs_scene_dir is not None

    _emit_optimize_progress(
        progress_callback,
        progress=0.05,
        title="正在读取优化后的 HQ 网格",
        detail="正在载入优化后的网格结果，准备投影视角和纹理 atlas。",
        metrics={"texture_phase": "load_optimized_mesh"},
    )
    optimized_mesh = _resolve_optimized_mesh_asset(ctx.delivery_dir)
    mesh = _load_mesh(optimized_mesh)

    cameras = _read_cameras(ctx.sparse2dgs_scene_dir / "sparse" / "0" / "cameras.txt")
    poses = _read_images(ctx.sparse2dgs_scene_dir / "sparse" / "0" / "images.txt")
    if not cameras or not poses:
        raise RuntimeError("delivery_texture_contract_missing")

    _emit_optimize_progress(
        progress_callback,
        progress=0.18,
        title="正在筛选 HQ 贴图视角",
        detail="正在从全量高分辨率支持集中挑选最适合纹理投影的视角。",
        metrics={
            "texture_phase": "select_projection_views",
            "texture_max_views": str(max(1, int(config.delivery_texture_max_views))),
        },
    )
    projected_views = _sample_projected_views(
        ctx=ctx,
        poses=poses,
        max_views=max(1, int(config.delivery_texture_max_views)),
    )
    _emit_optimize_progress(
        progress_callback,
        progress=0.32,
        title="正在载入高分辨率投影视图",
        detail="正在读取相机参数和高分辨率照片，建立可见性投影视图。",
        metrics={
            "texture_phase": "load_projection_views",
            "texture_projected_view_count": str(len(projected_views)),
            "texture_projection_image_size": str(max(256, int(config.delivery_projection_image_size))),
        },
    )
    loaded_views = _load_delivery_projection_views(
        ctx=ctx,
        images_dir=ctx.sparse2dgs_scene_dir / "images",
        cameras=cameras,
        poses=projected_views,
    )
    _emit_optimize_progress(
        progress_callback,
        progress=0.52,
        title="正在投影多视图顶点颜色",
        detail="正在把可见照片颜色投到网格顶点上，建立稳定的纹理先验。",
        metrics={
            "texture_phase": "project_vertex_colors",
            "texture_loaded_view_count": str(len(loaded_views)),
        },
    )
    vertex_colors, observed_vertex_count = _project_vertex_colors(
        mesh=mesh,
        projection_views=loaded_views,
    )
    _emit_optimize_progress(
        progress_callback,
        progress=0.72,
        title="正在烘焙 HQ 纹理 atlas",
        detail="正在按可见性和连续性把多视图照片投影到 UV atlas 上。",
        metrics={
            "texture_phase": "bake_uv_atlas",
            "texture_atlas_size": str(max(256, int(config.delivery_texture_atlas_size))),
        },
    )
    textured_mesh, atlas_summary = _bake_uv_textured_mesh(
        mesh=mesh,
        vertex_colors=vertex_colors,
        projection_views=loaded_views,
        atlas_size=max(256, int(config.delivery_texture_atlas_size)),
        progress_callback=progress_callback,
    )
    _emit_optimize_progress(
        progress_callback,
        progress=0.90,
        title="正在导出 HQ GLB",
        detail="纹理 atlas 已完成，正在写出最终 HQ GLB 文件。",
        metrics={"texture_phase": "export_glb"},
    )
    glb_path = ctx.delivery_dir / "default_mesh.glb"
    glb_path.parent.mkdir(parents=True, exist_ok=True)
    textured_mesh.export(glb_path)
    glb_size_bytes = glb_path.stat().st_size if glb_path.exists() else 0

    summary = {
        "optimized_mesh": str(optimized_mesh),
        "default_asset": str(glb_path),
        "projection_mode": "per_texel_best_view_photo_projection",
        "texture_bake_mode": "uv_atlas_from_visible_photo_projection",
        "projection_source": "curated_resized_highres",
        "projected_view_count": len(loaded_views),
        "observed_vertex_count": int(observed_vertex_count),
        "face_count": int(len(textured_mesh.faces)),
        "vertex_count": int(len(textured_mesh.vertices)),
        "atlas_size": int(atlas_summary["atlas_size"]),
        "atlas_observed_pixel_count": int(atlas_summary["observed_pixel_count"]),
        "atlas_coverage_ratio": float(atlas_summary["coverage_ratio"]),
        "atlas_photo_projected_pixel_count": int(atlas_summary["photo_projected_pixel_count"]),
        "atlas_photo_projected_face_count": int(atlas_summary["photo_projected_face_count"]),
        "atlas_fallback_face_count": int(atlas_summary["fallback_face_count"]),
        "neighbor_view_disagreement": float(atlas_summary["neighbor_view_disagreement"]),
        "low_saturation_texel_ratio": float(atlas_summary["low_saturation_texel_ratio"]),
        "texture_max_views": max(1, int(config.delivery_texture_max_views)),
        "projection_image_size": max(256, int(config.delivery_projection_image_size)),
        "texture_atlas_size_config": max(256, int(config.delivery_texture_atlas_size)),
        "glb_size_bytes": int(glb_size_bytes),
        "glb_size_mb": round(glb_size_bytes / (1024 * 1024), 3) if glb_size_bytes > 0 else 0.0,
    }
    (ctx.delivery_dir / config.delivery_texture_summary_filename).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_texture_hq_report(ctx=ctx, summary=summary)
    _emit_optimize_progress(
        progress_callback,
        progress=1.0,
        title="HQ 纹理投影完成",
        detail="最终 HQ GLB 和纹理质检摘要已写出。",
        metrics={
            "texture_phase": "done",
            "texture_glb_size_mb": f"{summary['glb_size_mb']:.3f}",
        },
    )
    return glb_path


def _resolve_matcha_mesh_asset(matcha_dir: Path) -> Path:
    summary = _read_json(matcha_dir / config.matcha_summary_filename) or {}
    candidate = summary.get("mesh_asset")
    if isinstance(candidate, str) and candidate.strip():
        path = Path(candidate).expanduser()
        if path.exists():
            return path
    matches = sorted(matcha_dir.glob("tetra_mesh_binary_search_*.ply"))
    if matches:
        return matches[-1]
    matches = sorted(matcha_dir.glob("*.ply"))
    if matches:
        return matches[-1]
    raise RuntimeError(f"matcha_mesh_asset_missing:{matcha_dir}")


def _resolve_optimized_mesh_asset(delivery_dir: Path) -> Path:
    summary = _read_json(delivery_dir / config.delivery_mesh_summary_filename) or {}
    candidate = summary.get("optimized_mesh")
    if isinstance(candidate, str) and candidate.strip():
        path = Path(candidate).expanduser()
        if path.exists():
            return path
    fallback = delivery_dir / "optimized_mesh.ply"
    if fallback.exists():
        return fallback
    raise RuntimeError(f"optimized_mesh_missing:{delivery_dir}")


def _load_mesh(source: Path):
    try:
        import trimesh
    except Exception as exc:
        raise RuntimeError(f"delivery_mesh_runtime_missing:{exc}") from exc

    mesh_or_scene = trimesh.load(source, force="scene")
    if isinstance(mesh_or_scene, trimesh.Scene):
        geometries = [geometry for geometry in mesh_or_scene.geometry.values() if len(geometry.faces) > 0]
        if not geometries:
            raise RuntimeError(f"delivery_mesh_empty_scene:{source}")
        mesh = trimesh.util.concatenate(geometries)
    else:
        mesh = mesh_or_scene
    if not hasattr(mesh, "faces") or len(mesh.faces) == 0:
        raise RuntimeError(f"delivery_mesh_missing_faces:{source}")
    return mesh


def _drop_small_components(mesh):
    try:
        import trimesh
    except Exception:
        return mesh

    components = list(mesh.split(only_watertight=False))
    if len(components) <= 1:
        return mesh
    largest_faces = max(len(component.faces) for component in components)
    min_faces = max(config.delivery_component_min_faces, int(largest_faces * config.delivery_component_ratio_floor))
    kept = [component for component in components if len(component.faces) >= min_faces]
    discarded = [component for component in components if len(component.faces) < min_faces]
    if not kept:
        kept = [max(components, key=lambda component: len(component.faces))]
        discarded = [component for component in components if component is not kept[0]]

    if discarded:
        reference_bounds = _mesh_bounds(mesh)
        kept_bounds = _mesh_bounds(kept[0]) if len(kept) == 1 else _mesh_bounds(trimesh.util.concatenate(kept))
        target_retention = max(0.55, float(config.mesh_fidelity_hq_min_extent_retention) * 0.95)
        current_retention = _bounds_extent_retention(
            reference_bounds=reference_bounds,
            candidate_bounds=kept_bounds,
        )
        if float(np.min(current_retention)) < target_retention:
            for component in sorted(discarded, key=lambda candidate: len(candidate.faces), reverse=True):
                component_bounds = _mesh_bounds(component)
                trial_bounds = np.asarray(
                    [
                        np.minimum(kept_bounds[0], component_bounds[0]),
                        np.maximum(kept_bounds[1], component_bounds[1]),
                    ],
                    dtype=np.float64,
                )
                trial_retention = _bounds_extent_retention(
                    reference_bounds=reference_bounds,
                    candidate_bounds=trial_bounds,
                )
                weak_axes = current_retention < target_retention
                if (
                    float(np.min(trial_retention)) > float(np.min(current_retention)) + 1e-6
                    or np.any(trial_retention[weak_axes] >= target_retention)
                ):
                    kept.append(component)
                    kept_bounds = trial_bounds
                    current_retention = trial_retention
                if float(np.min(current_retention)) >= target_retention:
                    break

    if len(kept) == 1:
        return kept[0]
    return trimesh.util.concatenate(kept)


def _drop_degenerate_faces(mesh):
    if hasattr(mesh, "nondegenerate_faces"):
        try:
            mask = mesh.nondegenerate_faces()
            mesh.update_faces(mask)
            mesh.remove_unreferenced_vertices()
        except Exception:
            pass
    if hasattr(mesh, "unique_faces"):
        try:
            mask = mesh.unique_faces()
            mesh.update_faces(mask)
            mesh.remove_unreferenced_vertices()
        except Exception:
            pass
    return mesh


def _prepare_working_mesh(
    mesh,
    *,
    progress_callback: OptimizeProgressCallback | None = None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
):
    face_count = int(len(mesh.faces))
    summary = {
        "applied": False,
        "reason": "below_trigger",
        "target_faces": face_count,
        "working_faces": face_count,
        "working_vertices": int(len(mesh.vertices)),
        "surface_area_retention": 1.0,
        "min_extent_retention": 1.0,
        "vertex_distance_mean_ratio": 0.0,
        "vertex_distance_p95_ratio": 0.0,
    }
    trigger_faces = max(1, int(config.delivery_working_mesh_trigger_faces))
    if face_count <= trigger_faces:
        _emit_optimize_progress(
            progress_callback,
            progress=progress_end,
            title="无需构建工作网格",
            detail="当前原始 HQ 网格规模仍在工作预算内，直接进入拓扑修补。",
            metrics={
                "optimize_phase": "prepare_working_mesh_skip",
                "working_mesh_applied": "false",
                "current_faces": str(face_count),
            },
        )
        return mesh, summary

    target_faces = _working_mesh_target_faces(face_count)
    summary["target_faces"] = int(target_faces)
    _emit_optimize_progress(
        progress_callback,
        progress=progress_start,
        title="正在构建工作网格",
        detail=(
            f"原始网格达到 {face_count:,} 面，先生成保边界/保轮廓/保尺度的工作网格，"
            "避免超大 raw mesh 在 CPU 拓扑修补阶段拖死 worker。"
        ),
        metrics={
            "optimize_phase": "prepare_working_mesh",
            "working_mesh_applied": "true",
            "raw_faces": str(face_count),
            "target_faces": str(target_faces),
        },
    )
    guard_extent_floor = max(0.55, float(config.mesh_fidelity_hq_min_extent_retention) * 0.95)
    guard_area_floor = max(0.55, float(config.mesh_fidelity_hq_min_surface_area_retention) * 0.95)
    working_mesh, simplify_guard_summary = _shape_preserving_simplify_mesh(
        mesh,
        reference_mesh=mesh,
        target_faces=target_faces,
        min_extent_retention_floor=guard_extent_floor,
        min_surface_area_retention_floor=guard_area_floor,
    )
    if len(working_mesh.faces) <= 0 or len(working_mesh.faces) >= face_count:
        summary["reason"] = str(simplify_guard_summary.get("reason", "simplify_noop"))
        _emit_optimize_progress(
            progress_callback,
            progress=progress_end,
            title="工作网格构建未收敛",
            detail="降面器没有生成更小的工作网格，本轮继续沿用原始网格进入拓扑修补。",
            metrics={
                "optimize_phase": "prepare_working_mesh_noop",
                "working_mesh_applied": "false",
                "raw_faces": str(face_count),
            },
        )
        return mesh, summary

    try:
        working_mesh.remove_unreferenced_vertices()
    except Exception:
        pass
    try:
        working_mesh.merge_vertices()
    except Exception:
        pass
    working_mesh = _drop_small_components(working_mesh)
    working_mesh = _drop_degenerate_faces(working_mesh)

    fidelity = _compute_mesh_fidelity_metrics(reference_mesh=mesh, candidate_mesh=working_mesh)
    summary.update(
        {
            "applied": True,
            "reason": "controlled_working_mesh",
            "working_faces": int(len(working_mesh.faces)),
            "working_vertices": int(len(working_mesh.vertices)),
            "surface_area_retention": float(fidelity["surface_area_retention"]),
            "min_extent_retention": float(fidelity["min_extent_retention"]),
            "vertex_distance_mean_ratio": float(fidelity["vertex_distance_mean_ratio"]),
            "vertex_distance_p95_ratio": float(fidelity["vertex_distance_p95_ratio"]),
            "attempted_targets": list(simplify_guard_summary.get("attempted_targets", [])),
            "accepted_target": int(simplify_guard_summary.get("accepted_target", len(working_mesh.faces))),
        }
    )
    _emit_optimize_progress(
        progress_callback,
        progress=progress_end,
        title="工作网格构建完成",
        detail=(
            f"已将工作网格收敛到 {len(working_mesh.faces):,} 面，"
            f"面积保留 {fidelity['surface_area_retention']:.3f}，最小尺度保留 {fidelity['min_extent_retention']:.3f}。"
        ),
        metrics={
            "optimize_phase": "prepare_working_mesh_done",
            "working_mesh_applied": "true",
            "working_faces": str(len(working_mesh.faces)),
            "working_vertices": str(len(working_mesh.vertices)),
            "surface_area_retention": f"{fidelity['surface_area_retention']:.5f}",
            "min_extent_retention": f"{fidelity['min_extent_retention']:.5f}",
        },
    )
    return working_mesh, summary


def _working_mesh_target_faces(face_count: int) -> int:
    ratio_target = int(round(face_count * max(0.05, float(config.delivery_working_mesh_target_ratio))))
    target = max(int(config.delivery_working_mesh_min_faces), ratio_target)
    target = min(target, int(config.delivery_working_mesh_face_cap))
    target = min(target, max(face_count - 1, 1))
    return max(1024, int(target))


def _shape_preserving_simplify_mesh(
    mesh,
    *,
    reference_mesh,
    target_faces: int,
    min_extent_retention_floor: float,
    min_surface_area_retention_floor: float,
) -> tuple[Any, dict[str, Any]]:
    face_count = int(len(mesh.faces))
    if face_count <= target_faces:
        return mesh, {
            "accepted": False,
            "reason": "below_target",
            "attempted_targets": [],
            "accepted_target": face_count,
        }

    gap = max(face_count - int(target_faces), 1)
    attempted_targets: list[int] = []
    for backoff_ratio in (0.0, 0.20, 0.40, 0.60):
        trial_target = min(
            face_count - 1,
            max(
                int(target_faces),
                int(round(target_faces + gap * backoff_ratio)),
            ),
        )
        if trial_target in attempted_targets:
            continue
        attempted_targets.append(trial_target)
        candidate = _simplify_mesh(mesh, target_faces=trial_target)
        if len(candidate.faces) <= 0:
            continue
        try:
            candidate.remove_unreferenced_vertices()
        except Exception:
            pass
        candidate = _drop_degenerate_faces(candidate)
        fidelity = _compute_mesh_fidelity_metrics(reference_mesh=reference_mesh, candidate_mesh=candidate)
        if (
            float(fidelity["min_extent_retention"]) >= float(min_extent_retention_floor)
            and float(fidelity["surface_area_retention"]) >= float(min_surface_area_retention_floor)
        ):
            return candidate, {
                "accepted": True,
                "reason": "shape_guard_pass",
                "attempted_targets": attempted_targets,
                "accepted_target": int(trial_target),
                "surface_area_retention": float(fidelity["surface_area_retention"]),
                "min_extent_retention": float(fidelity["min_extent_retention"]),
            }

    return mesh, {
        "accepted": False,
        "reason": "shape_guard_rejected",
        "attempted_targets": attempted_targets,
        "accepted_target": face_count,
    }


def _prune_multilayer_faces(
    mesh,
    *,
    progress_callback: OptimizeProgressCallback | None = None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
):
    summary = {
        "applied": False,
        "removed_faces": 0,
        "initial_ratio": 0.0,
        "final_ratio": 0.0,
        "reverted": False,
    }
    if not bool(config.delivery_multilayer_prune_enabled):
        return mesh, summary

    current_mesh = mesh
    original_mesh = _copy_mesh_geometry(mesh)
    initial_ratio = float(_estimate_self_intersection_ratio(current_mesh))
    summary["initial_ratio"] = initial_ratio
    if initial_ratio <= float(config.delivery_self_intersection_cleanup_trigger_ratio):
        summary["final_ratio"] = initial_ratio
        return current_mesh, summary

    _emit_optimize_progress(
        progress_callback,
        progress=progress_start,
        title="正在裁掉局部多层表面",
        detail="检测到工作网格在局部像多层薄片叠在一起，先去掉明显内部层，再进入碎片和拓扑修补。",
        metrics={
            "optimize_phase": "prune_multilayer_faces",
            "self_intersection_ratio": f"{initial_ratio:.5f}",
        },
    )

    candidate_indices = _estimate_multilayer_prune_candidates(current_mesh)
    if not candidate_indices:
        summary["final_ratio"] = initial_ratio
        _emit_optimize_progress(
            progress_callback,
            progress=progress_end,
            title="未检测到可裁掉的多层面",
            detail="当前工作网格虽然有穿插，但没有找到稳定的内部层候选，继续进入后续修补。",
            metrics={
                "optimize_phase": "prune_multilayer_faces_skip",
                "self_intersection_ratio": f"{initial_ratio:.5f}",
            },
        )
        return current_mesh, summary

    max_drop_ratio = max(0.0, float(config.delivery_multilayer_prune_max_face_drop_ratio))
    face_cap = max(32, int(round(len(current_mesh.faces) * max_drop_ratio)))
    if len(candidate_indices) > face_cap:
        face_areas = np.asarray(getattr(current_mesh, "area_faces", np.zeros(len(current_mesh.faces))), dtype=np.float64)
        boundary_vertices = _boundary_vertex_mask(
            np.asarray(current_mesh.faces, dtype=np.int64),
            vertex_count=len(current_mesh.vertices),
        )
        boundary_faces = np.any(boundary_vertices[np.asarray(current_mesh.faces, dtype=np.int64)], axis=1)
        candidate_indices = sorted(
            candidate_indices,
            key=lambda index: (
                bool(boundary_faces[index]),
                float(face_areas[index]) if 0 <= index < len(face_areas) else math.inf,
            ),
        )[:face_cap]

    mask = np.ones(len(current_mesh.faces), dtype=bool)
    mask[np.asarray(sorted(candidate_indices), dtype=np.int64)] = False
    current_mesh.update_faces(mask)
    try:
        current_mesh.remove_unreferenced_vertices()
    except Exception:
        pass
    try:
        current_mesh.merge_vertices()
    except Exception:
        pass
    current_mesh = _drop_small_components(current_mesh)
    current_mesh = _drop_degenerate_faces(current_mesh)
    final_ratio = float(_estimate_self_intersection_ratio(current_mesh))
    if final_ratio >= initial_ratio:
        summary.update(
            {
                "applied": False,
                "removed_faces": 0,
                "final_ratio": initial_ratio,
                "reverted": True,
            }
        )
        _emit_optimize_progress(
            progress_callback,
            progress=progress_end,
            title="局部多层面裁剪已回退",
            detail=(
                f"候选裁剪没有降低自相交（{initial_ratio:.5f} -> {final_ratio:.5f}），"
                "本轮保留原始工作网格，避免越修越缠。"
            ),
            metrics={
                "optimize_phase": "prune_multilayer_faces_reverted",
                "self_intersection_ratio": f"{initial_ratio:.5f}",
            },
        )
        return original_mesh, summary
    summary.update(
        {
            "applied": True,
            "removed_faces": int(len(candidate_indices)),
            "final_ratio": final_ratio,
        }
    )
    _emit_optimize_progress(
        progress_callback,
        progress=progress_end,
        title="局部多层面裁剪完成",
        detail=(
            f"已去掉 {len(candidate_indices):,} 个疑似内部层三角面，"
            f"自相交估计从 {initial_ratio:.5f} 降到 {final_ratio:.5f}。"
        ),
        metrics={
            "optimize_phase": "prune_multilayer_faces_done",
            "removed_faces": str(len(candidate_indices)),
            "self_intersection_ratio": f"{final_ratio:.5f}",
        },
    )
    return current_mesh, summary


def _estimate_multilayer_prune_candidates(mesh) -> set[int]:
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or faces.ndim != 2 or faces.shape[1] != 3 or len(faces) < 2:
        return set()

    base_sample_cap = max(64, int(config.delivery_multilayer_prune_sample_cap))
    scaled_sample_cap = int(max(base_sample_cap, min(len(faces), max(4096, len(faces) * 0.08))))
    sample_cap = min(len(faces), min(max(scaled_sample_cap, base_sample_cap), 32768))
    if len(faces) <= sample_cap:
        sample_indices = np.arange(len(faces), dtype=np.int64)
    else:
        sample_indices = np.linspace(0, len(faces) - 1, num=sample_cap, dtype=np.int64)

    sampled_faces = faces[sample_indices]
    triangles = vertices[sampled_faces]
    centroids = np.mean(triangles, axis=1)
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normal_norms = np.linalg.norm(normals, axis=1)
    valid_normals = normal_norms > 1e-8
    normals[valid_normals] /= normal_norms[valid_normals][:, None]
    aabb_min = np.min(triangles, axis=1)
    aabb_max = np.max(triangles, axis=1)
    face_areas = np.asarray(getattr(mesh, "area_faces", np.zeros(len(faces))), dtype=np.float64)
    boundary_vertices = _boundary_vertex_mask(faces, vertex_count=len(vertices))
    boundary_faces = np.any(boundary_vertices[faces], axis=1)

    extents = _mesh_extents(mesh)
    diagonal = max(float(np.linalg.norm(extents)), 1e-6)
    plane_tolerance = max(diagonal * 0.0012, 1e-5)
    query_radius = max(diagonal * 0.025, plane_tolerance * 25.0)
    min_normal_cosine = max(0.0, min(1.0, float(config.delivery_multilayer_prune_normal_cosine)))

    try:
        from scipy.spatial import cKDTree

        tree = cKDTree(centroids)
        neighborhoods = [tree.query_ball_point(centroids[index], query_radius, workers=1) for index in range(len(sample_indices))]
    except Exception:
        neighborhoods = []
        for index in range(len(sample_indices)):
            delta = centroids - centroids[index]
            distances = np.linalg.norm(delta, axis=1)
            neighborhoods.append(np.flatnonzero(distances <= query_radius).tolist())

    drop_faces: set[int] = set()
    for local_index, candidate_local_indices in enumerate(neighborhoods):
        if not valid_normals[local_index]:
            continue
        face_i = sampled_faces[local_index]
        tri_i = triangles[local_index]
        area_i = float(face_areas[int(sample_indices[local_index])]) if len(face_areas) > int(sample_indices[local_index]) else 0.0
        boundary_i = bool(boundary_faces[int(sample_indices[local_index])]) if len(boundary_faces) > int(sample_indices[local_index]) else False
        for other_local_index in candidate_local_indices:
            if other_local_index <= local_index or not valid_normals[other_local_index]:
                continue
            face_j = sampled_faces[other_local_index]
            if np.intersect1d(face_i, face_j).size > 0:
                continue
            cosine = abs(float(np.dot(normals[local_index], normals[other_local_index])))
            if cosine < min_normal_cosine:
                continue
            if not _aabb_overlap(
                aabb_min[local_index],
                aabb_max[local_index],
                aabb_min[other_local_index],
                aabb_max[other_local_index],
                pad=plane_tolerance,
            ):
                continue
            tri_j = triangles[other_local_index]
            centroid_i = centroids[local_index]
            centroid_j = centroids[other_local_index]
            intersects = _point_near_triangle(centroid_i, tri_j, plane_tolerance) or _point_near_triangle(
                centroid_j,
                tri_i,
                plane_tolerance,
            )
            if not intersects:
                continue

            sample_i = int(sample_indices[local_index])
            sample_j = int(sample_indices[other_local_index])
            area_j = float(face_areas[sample_j]) if len(face_areas) > sample_j else 0.0
            boundary_j = bool(boundary_faces[sample_j]) if len(boundary_faces) > sample_j else False
            drop_faces.add(
                _choose_multilayer_drop_face(
                    sample_i=sample_i,
                    sample_j=sample_j,
                    area_i=area_i,
                    area_j=area_j,
                    boundary_i=boundary_i,
                    boundary_j=boundary_j,
                )
            )
    return drop_faces


def _choose_multilayer_drop_face(
    *,
    sample_i: int,
    sample_j: int,
    area_i: float,
    area_j: float,
    boundary_i: bool,
    boundary_j: bool,
) -> int:
    score_i = (1.0 if boundary_i else 0.0, area_i)
    score_j = (1.0 if boundary_j else 0.0, area_j)
    return int(sample_i if score_i < score_j else sample_j)


def _repair_mesh_topology(
    mesh,
    *,
    progress_callback: OptimizeProgressCallback | None = None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
    phase_prefix: str = "topology_repair",
):
    aggressive_repair_allowed = len(mesh.faces) <= int(config.delivery_aggressive_repair_face_cap)
    summary = {
        "watertight_before": bool(getattr(mesh, "is_watertight", False)),
        "watertight_after": bool(getattr(mesh, "is_watertight", False)),
        "hole_fill_iterations": 0,
        "holes_repaired": False,
        "closure_strategy": "boundary_preserving_native_repair" if aggressive_repair_allowed else "boundary_preserving_open_mesh",
        "aggressive_repair_allowed": bool(aggressive_repair_allowed),
        "small_hole_patches": 0,
        "small_holes_filled_count": 0,
        "faces_added_by_hole_fill": 0,
        "max_filled_hole_perimeter_ratio": 0.0,
    }
    try:
        import trimesh
    except Exception:
        return mesh, summary

    repair = getattr(trimesh, "repair", None)
    if repair is None:
        return mesh, summary

    _emit_optimize_progress(
        progress_callback,
        progress=progress_start,
        title="正在修补拓扑边界",
        detail="正在分析边界环和小孔，准备做边界保持的小洞修补。",
        metrics={
            "optimize_phase": phase_prefix,
            "repair_mode": summary["closure_strategy"],
            "aggressive_repair_allowed": str(bool(aggressive_repair_allowed)).lower(),
        },
    )
    initial_boundary_stats = _analyze_boundary_topology(vertices=np.asarray(mesh.vertices, dtype=np.float64), faces=np.asarray(mesh.faces, dtype=np.int64))
    summary["boundary_length_before"] = float(initial_boundary_stats["boundary_length"])
    summary["boundary_edge_ratio_before"] = float(initial_boundary_stats["boundary_edge_ratio"])
    summary["small_hole_candidates_before"] = int(initial_boundary_stats["small_loop_count"])
    summary["large_hole_candidates_before"] = int(initial_boundary_stats["large_loop_count"])

    if not aggressive_repair_allowed:
        try:
            if hasattr(repair, "fix_winding"):
                repair.fix_winding(mesh)
        except Exception:
            pass
        try:
            if hasattr(repair, "fix_inversion"):
                try:
                    repair.fix_inversion(mesh, multibody=True)
                except TypeError:
                    repair.fix_inversion(mesh)
        except Exception:
            pass
        try:
            mesh.remove_unreferenced_vertices()
        except Exception:
            pass
        final_boundary_stats = _analyze_boundary_topology(vertices=np.asarray(mesh.vertices, dtype=np.float64), faces=np.asarray(mesh.faces, dtype=np.int64))
        summary["watertight_after"] = bool(getattr(mesh, "is_watertight", False))
        summary["boundary_length_after"] = float(final_boundary_stats["boundary_length"])
        summary["boundary_edge_ratio_after"] = float(final_boundary_stats["boundary_edge_ratio"])
        summary["small_hole_candidates_after"] = int(final_boundary_stats["small_loop_count"])
        summary["large_hole_candidates_after"] = int(final_boundary_stats["large_loop_count"])
        if not summary["watertight_after"]:
            summary["closure_strategy"] = "boundary_preserving_open_mesh"
        _emit_optimize_progress(
            progress_callback,
            progress=progress_end,
            title="已完成开放表面修补",
            detail="当前网格过大，已跳过激进补洞，只做边界保持的开放表面修补。",
            metrics={
                "optimize_phase": phase_prefix,
                "watertight_after": str(bool(summary["watertight_after"])).lower(),
                "closure_strategy": summary["closure_strategy"],
            },
        )
        return mesh, summary

    total_iterations = max(1, int(config.delivery_hole_fill_iterations))
    for iteration_index in range(total_iterations):
        iteration_ratio = iteration_index / max(total_iterations, 1)
        _emit_optimize_progress(
            progress_callback,
            progress=progress_start + (progress_end - progress_start) * iteration_ratio,
            title=f"正在修补小洞 {iteration_index + 1}/{total_iterations}",
            detail=f"正在做第 {iteration_index + 1} 轮边界保持的小洞修补与拓扑清理。",
            metrics={
                "optimize_phase": phase_prefix,
                "repair_iteration": str(iteration_index + 1),
                "repair_total_iterations": str(total_iterations),
            },
        )
        summary["hole_fill_iterations"] += 1
        try:
            if hasattr(mesh, "process"):
                mesh.process(validate=True)
        except Exception:
            pass
        try:
            if hasattr(repair, "fix_winding"):
                repair.fix_winding(mesh)
        except Exception:
            pass
        try:
            if hasattr(repair, "fix_inversion"):
                try:
                    repair.fix_inversion(mesh, multibody=True)
                except TypeError:
                    repair.fix_inversion(mesh)
        except Exception:
            pass
        if bool(config.delivery_native_fill_holes_enabled):
            try:
                if hasattr(repair, "fill_holes"):
                    repaired = repair.fill_holes(mesh)
                    summary["holes_repaired"] = bool(summary["holes_repaired"] or repaired)
            except Exception:
                pass
        patched_mesh, patch_summary = _patch_small_boundary_loops(mesh)
        if int(patch_summary["patched_count"]) > 0:
            mesh = patched_mesh
            summary["small_hole_patches"] += int(patch_summary["patched_count"])
            summary["small_holes_filled_count"] += int(patch_summary["patched_count"])
            summary["faces_added_by_hole_fill"] += int(patch_summary["faces_added"])
            summary["max_filled_hole_perimeter_ratio"] = max(
                float(summary["max_filled_hole_perimeter_ratio"]),
                float(patch_summary["max_perimeter_ratio"]),
            )
            summary["holes_repaired"] = True
        try:
            mesh.remove_unreferenced_vertices()
        except Exception:
            pass
        if bool(getattr(mesh, "is_watertight", False)):
            break

    summary["watertight_after"] = bool(getattr(mesh, "is_watertight", False))
    final_boundary_stats = _analyze_boundary_topology(vertices=np.asarray(mesh.vertices, dtype=np.float64), faces=np.asarray(mesh.faces, dtype=np.int64))
    summary["boundary_length_after"] = float(final_boundary_stats["boundary_length"])
    summary["boundary_edge_ratio_after"] = float(final_boundary_stats["boundary_edge_ratio"])
    summary["small_hole_candidates_after"] = int(final_boundary_stats["small_loop_count"])
    summary["large_hole_candidates_after"] = int(final_boundary_stats["large_loop_count"])
    if not summary["watertight_after"]:
        summary["closure_strategy"] = "boundary_preserving_open_mesh"
    _emit_optimize_progress(
        progress_callback,
        progress=progress_end,
        title="拓扑修补完成",
        detail=(
            "当前轮拓扑修补已结束，"
            f"小洞补面 {summary['small_hole_patches']} 处，"
            f"watertight={summary['watertight_after']}。"
        ),
        metrics={
            "optimize_phase": phase_prefix,
            "small_hole_patches": str(summary["small_hole_patches"]),
            "watertight_after": str(bool(summary["watertight_after"])).lower(),
            "closure_strategy": summary["closure_strategy"],
        },
    )
    return mesh, summary


def _patch_small_boundary_loops(mesh) -> tuple[Any, dict[str, float | int]]:
    try:
        import trimesh
    except Exception:
        return mesh, {"patched_count": 0, "faces_added": 0, "max_perimeter_ratio": 0.0}

    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if len(vertices) == 0 or len(faces) == 0:
        return mesh, {"patched_count": 0, "faces_added": 0, "max_perimeter_ratio": 0.0}

    loops = _extract_small_boundary_loops(
        vertices=vertices,
        faces=faces,
        max_edges=max(3, int(config.delivery_small_hole_max_edges)),
        max_perimeter_ratio=max(
            0.0,
            min(
                float(config.delivery_small_hole_max_perimeter_ratio),
                float(config.hole_fill_hq_max_filled_hole_perimeter_ratio) * float(config.delivery_small_hole_hq_safety_margin),
            ),
        ),
    )
    if not loops:
        return mesh, {"patched_count": 0, "faces_added": 0, "max_perimeter_ratio": 0.0}

    new_vertices = vertices.tolist()
    new_faces = faces.tolist()
    patched_count = 0
    faces_added = 0
    max_perimeter_ratio = 0.0
    for loop in loops:
        loop_indices = np.asarray(loop["vertices"], dtype=np.int64)
        loop_points = vertices[loop_indices]
        centroid = loop_points.mean(axis=0)
        if not np.all(np.isfinite(centroid)):
            continue
        centroid_index = len(new_vertices)
        new_vertices.append(centroid.tolist())
        loop_vertices = loop["vertices"]
        for edge_index in range(len(loop_vertices)):
            a = int(loop_vertices[edge_index])
            b = int(loop_vertices[(edge_index + 1) % len(loop_vertices)])
            new_faces.append([a, b, centroid_index])
            faces_added += 1
        patched_count += 1
        max_perimeter_ratio = max(max_perimeter_ratio, float(loop["perimeter_ratio"]))

    repaired = trimesh.Trimesh(
        vertices=np.asarray(new_vertices, dtype=np.float64),
        faces=np.asarray(new_faces, dtype=np.int64),
        process=False,
    )
    try:
        repaired.remove_unreferenced_vertices()
    except Exception:
        pass
    return repaired, {
        "patched_count": patched_count,
        "faces_added": faces_added,
        "max_perimeter_ratio": max_perimeter_ratio,
    }


def _extract_small_boundary_loops(
    *,
    vertices: np.ndarray,
    faces: np.ndarray,
    max_edges: int,
    max_perimeter_ratio: float,
) -> list[dict[str, Any]]:
    edge_pairs = np.sort(
        np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]),
        axis=1,
    )
    unique_edges, counts = np.unique(edge_pairs, axis=0, return_counts=True)
    boundary_edges = unique_edges[counts == 1]
    if boundary_edges.size == 0:
        return []

    adjacency: dict[int, list[int]] = {}
    for start, end in boundary_edges.tolist():
        adjacency.setdefault(int(start), []).append(int(end))
        adjacency.setdefault(int(end), []).append(int(start))

    bounds = np.asarray([vertices.min(axis=0), vertices.max(axis=0)], dtype=np.float64)
    bbox_diag = float(np.linalg.norm(bounds[1] - bounds[0]))
    perimeter_limit = bbox_diag * max_perimeter_ratio if bbox_diag > 1e-6 else math.inf

    loops: list[dict[str, Any]] = []
    used_edges: set[tuple[int, int]] = set()
    for start, neighbors in adjacency.items():
        if len(neighbors) != 2:
            continue
        for neighbor in neighbors:
            edge_key = tuple(sorted((start, neighbor)))
            if edge_key in used_edges:
                continue
            loop = _trace_boundary_loop(start, neighbor, adjacency, used_edges)
            if len(loop) < 3 or len(loop) > max_edges:
                continue
            perimeter = _boundary_loop_perimeter(vertices, loop)
            if perimeter_limit < math.inf and perimeter > perimeter_limit:
                continue
            planarity_ratio = _boundary_loop_planarity_ratio(vertices, loop)
            if planarity_ratio > float(config.delivery_small_hole_max_planarity_ratio):
                continue
            perimeter_ratio = perimeter / max(bbox_diag, 1e-6) if bbox_diag > 1e-6 else 0.0
            loops.append(
                {
                    "vertices": loop,
                    "edge_count": len(loop),
                    "perimeter": float(perimeter),
                    "perimeter_ratio": float(perimeter_ratio),
                    "planarity_ratio": float(planarity_ratio),
                }
            )
    return loops


def _boundary_loop_planarity_ratio(vertices: np.ndarray, loop: list[int]) -> float:
    if len(loop) < 3:
        return math.inf
    points = vertices[np.asarray(loop, dtype=np.int64)]
    if points.ndim != 2 or points.shape[1] != 3:
        return math.inf
    centered = points - points.mean(axis=0, keepdims=True)
    try:
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
    except np.linalg.LinAlgError:
        return math.inf
    if vh.shape[0] < 3:
        return math.inf
    normal = vh[-1]
    distances = np.abs(centered @ normal)
    loop_diag = max(float(np.linalg.norm(points.max(axis=0) - points.min(axis=0))), 1e-6)
    return float(np.sqrt(np.mean(np.square(distances))) / loop_diag)


def _extract_boundary_loops(vertices: np.ndarray, faces: np.ndarray) -> list[dict[str, Any]]:
    edge_pairs = np.sort(
        np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]),
        axis=1,
    )
    unique_edges, counts = np.unique(edge_pairs, axis=0, return_counts=True)
    boundary_edges = unique_edges[counts == 1]
    if boundary_edges.size == 0:
        return []

    adjacency: dict[int, list[int]] = {}
    for start, end in boundary_edges.tolist():
        adjacency.setdefault(int(start), []).append(int(end))
        adjacency.setdefault(int(end), []).append(int(start))

    bounds = np.asarray([vertices.min(axis=0), vertices.max(axis=0)], dtype=np.float64)
    bbox_diag = float(np.linalg.norm(bounds[1] - bounds[0]))
    loops: list[dict[str, Any]] = []
    used_edges: set[tuple[int, int]] = set()
    for start, neighbors in adjacency.items():
        if len(neighbors) != 2:
            continue
        for neighbor in neighbors:
            edge_key = tuple(sorted((start, neighbor)))
            if edge_key in used_edges:
                continue
            loop = _trace_boundary_loop(start, neighbor, adjacency, used_edges)
            if len(loop) < 3:
                continue
            perimeter = _boundary_loop_perimeter(vertices, loop)
            perimeter_ratio = perimeter / max(bbox_diag, 1e-6) if bbox_diag > 1e-6 else 0.0
            loops.append(
                {
                    "vertices": loop,
                    "edge_count": len(loop),
                    "perimeter": float(perimeter),
                    "perimeter_ratio": float(perimeter_ratio),
                }
            )
    return loops


def _analyze_boundary_topology(*, vertices: np.ndarray, faces: np.ndarray) -> dict[str, float | int]:
    if len(vertices) == 0 or len(faces) == 0:
        return {
            "boundary_edge_count": 0,
            "unique_edge_count": 0,
            "boundary_edge_ratio": 0.0,
            "boundary_length": 0.0,
            "small_loop_count": 0,
            "large_loop_count": 0,
        }

    edge_pairs = np.sort(
        np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]),
        axis=1,
    )
    unique_edges, counts = np.unique(edge_pairs, axis=0, return_counts=True)
    boundary_edges = unique_edges[counts == 1]
    boundary_length = 0.0
    if boundary_edges.size > 0:
        edge_vertices = vertices[boundary_edges]
        boundary_length = float(np.linalg.norm(edge_vertices[:, 0] - edge_vertices[:, 1], axis=1).sum())
    loops = _extract_boundary_loops(vertices, faces)
    small_loop_count = 0
    large_loop_count = 0
    max_edges = max(3, int(config.delivery_small_hole_max_edges))
    max_perimeter_ratio = max(0.0, float(config.delivery_small_hole_max_perimeter_ratio))
    for loop in loops:
        if int(loop["edge_count"]) <= max_edges and float(loop["perimeter_ratio"]) <= max_perimeter_ratio:
            small_loop_count += 1
        else:
            large_loop_count += 1
    return {
        "boundary_edge_count": int(len(boundary_edges)),
        "unique_edge_count": int(len(unique_edges)),
        "boundary_edge_ratio": float(len(boundary_edges) / max(len(unique_edges), 1)),
        "boundary_length": float(boundary_length),
        "small_loop_count": int(small_loop_count),
        "large_loop_count": int(large_loop_count),
    }


def _trace_boundary_loop(
    start: int,
    next_vertex: int,
    adjacency: dict[int, list[int]],
    used_edges: set[tuple[int, int]],
) -> list[int]:
    loop = [start]
    previous = start
    current = next_vertex
    used_edges.add(tuple(sorted((start, next_vertex))))
    while True:
        if current == start:
            break
        if current in loop:
            return []
        loop.append(current)
        neighbors = adjacency.get(current, [])
        if len(neighbors) != 2:
            return []
        candidate = neighbors[0] if neighbors[1] == previous else neighbors[1]
        edge_key = tuple(sorted((current, candidate)))
        if edge_key in used_edges and candidate != start:
            return []
        used_edges.add(edge_key)
        previous, current = current, candidate
    return loop


def _boundary_loop_perimeter(vertices: np.ndarray, loop: list[int]) -> float:
    if len(loop) < 2:
        return 0.0
    perimeter = 0.0
    for index, vertex_index in enumerate(loop):
        next_index = loop[(index + 1) % len(loop)]
        perimeter += float(np.linalg.norm(vertices[vertex_index] - vertices[next_index]))
    return perimeter


def _smooth_mesh(
    mesh,
    *,
    progress_callback: OptimizeProgressCallback | None = None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
):
    iterations = max(0, int(config.delivery_taubin_iterations))
    if iterations <= 0:
        return mesh
    faces = np.asarray(mesh.faces, dtype=np.int64)
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    if faces.ndim != 2 or faces.shape[1] != 3 or len(vertices) == 0:
        return mesh
    boundary_mask = _boundary_vertex_mask(faces, vertex_count=len(vertices))
    feature_mask = _feature_vertex_mask(
        vertices=vertices,
        faces=faces,
        feature_angle_deg=float(config.delivery_feature_preserve_angle_deg),
    )
    movable_indices = np.flatnonzero(~boundary_mask & ~feature_mask)
    if movable_indices.size == 0:
        return mesh
    neighbors = _vertex_neighbors(faces, vertex_count=len(vertices))
    current = vertices.copy()
    lamb = float(config.delivery_taubin_lambda)
    nu = float(config.delivery_taubin_nu)
    try:
        for iteration_index in range(iterations):
            iteration_ratio = iteration_index / max(iterations, 1)
            _emit_optimize_progress(
                progress_callback,
                progress=progress_start + (progress_end - progress_start) * iteration_ratio,
                title=f"正在保边平滑表面 {iteration_index + 1}/{iterations}",
                detail=f"正在做第 {iteration_index + 1} 轮边界/特征保持平滑，尽量减弱毛刺又不把表面抹圆。",
                metrics={
                    "optimize_phase": "smooth_mesh",
                    "smooth_iteration": str(iteration_index + 1),
                    "smooth_total_iterations": str(iterations),
                },
            )
            current = _boundary_preserving_laplacian_pass(
                current,
                neighbors=neighbors,
                movable_indices=movable_indices,
                factor=lamb,
            )
            current = _boundary_preserving_laplacian_pass(
                current,
                neighbors=neighbors,
                movable_indices=movable_indices,
                factor=nu,
            )
    except Exception:
        return mesh
    mesh.vertices = current
    _emit_optimize_progress(
        progress_callback,
        progress=progress_end,
        title="保边平滑完成",
        detail="已完成边界和特征保持平滑，继续进入法线修复与预算收敛。",
        metrics={"optimize_phase": "smooth_mesh_done"},
    )
    return mesh


def _emit_optimize_progress(
    callback: OptimizeProgressCallback | None,
    *,
    progress: float,
    title: str,
    detail: str,
    metrics: dict[str, Any] | None = None,
) -> None:
    if callback is None:
        return
    callback(
        {
            "progress": max(0.0, min(1.0, float(progress))),
            "title": title,
            "detail": detail,
            "metrics": dict(metrics or {}),
        }
    )


def _make_throttled_subprogress_emitter(
    callback: OptimizeProgressCallback | None,
    *,
    progress_start: float,
    progress_end: float,
) -> Callable[..., None]:
    last_emit_time = 0.0
    last_progress = -1.0

    def emit(
        local_progress: float,
        *,
        title: str,
        detail: str,
        metrics: dict[str, Any] | None = None,
        force: bool = False,
    ) -> None:
        nonlocal last_emit_time, last_progress
        if callback is None:
            return
        clamped = max(0.0, min(1.0, float(local_progress)))
        now = time.time()
        interval = float(config.delivery_bake_progress_emit_interval_sec)
        if not force:
            if clamped <= last_progress + 1e-6 and now - last_emit_time < interval:
                return
            if now - last_emit_time < interval and clamped < 1.0:
                return
        last_progress = clamped
        last_emit_time = now
        mapped = progress_start + (progress_end - progress_start) * clamped
        _emit_optimize_progress(
            callback,
            progress=mapped,
            title=title,
            detail=detail,
            metrics=metrics,
        )

    return emit


def _boundary_vertex_mask(faces: np.ndarray, *, vertex_count: int) -> np.ndarray:
    edges = np.sort(
        np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]),
        axis=1,
    )
    unique_edges, counts = np.unique(edges, axis=0, return_counts=True)
    boundary_edges = unique_edges[counts == 1]
    mask = np.zeros(vertex_count, dtype=bool)
    if boundary_edges.size > 0:
        mask[np.unique(boundary_edges.reshape(-1))] = True
    return mask


def _vertex_neighbors(faces: np.ndarray, *, vertex_count: int) -> list[np.ndarray]:
    adjacency: list[set[int]] = [set() for _ in range(vertex_count)]
    for face in faces.tolist():
        a, b, c = int(face[0]), int(face[1]), int(face[2])
        adjacency[a].update((b, c))
        adjacency[b].update((a, c))
        adjacency[c].update((a, b))
    return [np.asarray(sorted(neighbors), dtype=np.int64) for neighbors in adjacency]


def _boundary_preserving_laplacian_pass(
    vertices: np.ndarray,
    *,
    neighbors: list[np.ndarray],
    movable_indices: np.ndarray,
    factor: float,
) -> np.ndarray:
    updated = vertices.copy()
    for index in movable_indices.tolist():
        neighbor_indices = neighbors[index]
        if neighbor_indices.size < 2:
            continue
        centroid = vertices[neighbor_indices].mean(axis=0)
        updated[index] = vertices[index] + factor * (centroid - vertices[index])
    return updated


def _feature_vertex_mask(
    *,
    vertices: np.ndarray,
    faces: np.ndarray,
    feature_angle_deg: float,
) -> np.ndarray:
    if len(vertices) == 0 or len(faces) == 0:
        return np.zeros(len(vertices), dtype=bool)
    threshold_cosine = math.cos(math.radians(max(0.0, feature_angle_deg)))
    face_normals = _face_normals(vertices, faces)
    edge_to_faces: dict[tuple[int, int], list[int]] = {}
    for face_index, face in enumerate(faces.tolist()):
        a, b, c = int(face[0]), int(face[1]), int(face[2])
        for start, end in ((a, b), (b, c), (c, a)):
            edge_to_faces.setdefault(tuple(sorted((start, end))), []).append(face_index)
    mask = np.zeros(len(vertices), dtype=bool)
    for edge, face_indices in edge_to_faces.items():
        mark_feature = len(face_indices) != 2
        if not mark_feature and len(face_indices) == 2:
            normal_a = face_normals[face_indices[0]]
            normal_b = face_normals[face_indices[1]]
            cosine = float(np.clip(np.dot(normal_a, normal_b), -1.0, 1.0))
            mark_feature = cosine <= threshold_cosine
        if mark_feature:
            mask[list(edge)] = True
    return mask


def _face_normals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    tri_vertices = vertices[faces]
    normals = np.cross(
        tri_vertices[:, 1] - tri_vertices[:, 0],
        tri_vertices[:, 2] - tri_vertices[:, 0],
    )
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    lengths[lengths == 0] = 1.0
    return normals / lengths


def _simplify_mesh(mesh, *, target_faces: int):
    if len(mesh.faces) <= target_faces:
        return mesh
    simplify = getattr(mesh, "simplify_quadric_decimation", None)
    if callable(simplify):
        for kwargs in (
            {"face_count": target_faces},
            {"faces": target_faces},
            {"percent": max(0.01, min(0.99, target_faces / max(1, len(mesh.faces))))},
        ):
            try:
                simplified = simplify(**kwargs)
                if simplified is not None and len(simplified.faces) > 0:
                    return simplified
            except Exception:
                continue
    return mesh


def _should_simplify_mesh(mesh, *, target_faces: int) -> bool:
    face_count = len(mesh.faces)
    if face_count <= target_faces:
        return False
    if not config.delivery_preserve_geometry_default:
        return True
    return face_count > config.delivery_hard_max_face_count


def _simplify_target_faces(face_count: int, *, target_faces: int) -> int:
    if not config.delivery_preserve_geometry_default:
        return target_faces
    if face_count > config.delivery_hard_max_face_count:
        return max(target_faces, int(config.delivery_hard_max_face_count))
    return face_count


def _project_vertex_colors(
    *,
    mesh,
    projection_views: list[_ProjectionView],
) -> tuple[np.ndarray, int]:
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    vertex_count = len(vertices)
    if vertex_count == 0:
        raise RuntimeError("delivery_texture_empty_mesh")

    normals = np.asarray(mesh.vertex_normals, dtype=np.float64)
    if normals.shape != vertices.shape:
        normals = np.zeros_like(vertices)

    fallback_colors = np.full((vertex_count, 3), 180.0, dtype=np.float64)
    existing_colors = _existing_vertex_colors(mesh)
    if existing_colors is not None:
        fallback_colors = existing_colors[:, :3].astype(np.float64)

    color_accum = np.zeros((vertex_count, 3), dtype=np.float64)
    weight_accum = np.zeros(vertex_count, dtype=np.float64)

    for view in projection_views:
        camera = view.camera
        image = view.image
        height, width = image.shape[:2]
        rotation = view.rotation
        translation = view.translation
        camera_points = (rotation @ vertices.T).T + translation
        depth = camera_points[:, 2]
        valid = depth > 1e-6
        if not np.any(valid):
            continue

        u = camera.fx * (camera_points[:, 0] / depth) + camera.cx
        v = camera.fy * (camera_points[:, 1] / depth) + camera.cy
        valid &= np.isfinite(u) & np.isfinite(v)
        valid &= (u >= 0.0) & (u <= (width - 1)) & (v >= 0.0) & (v <= (height - 1))
        if np.any(valid):
            view_dirs = view.center[None, :] - vertices
            view_norm = np.linalg.norm(view_dirs, axis=1, keepdims=True)
            view_norm[view_norm == 0] = 1.0
            view_dirs /= view_norm
            cosine = np.sum(normals * view_dirs, axis=1)
            valid &= cosine > 0.05
        valid_indices = np.where(valid)[0]
        if valid_indices.size == 0:
            continue

        pixel_u = np.clip(np.rint(u[valid_indices]).astype(np.int32), 0, width - 1)
        pixel_v = np.clip(np.rint(v[valid_indices]).astype(np.int32), 0, height - 1)
        sampled = image[pixel_v, pixel_u]
        cosine = np.maximum(0.05, np.sum(normals[valid_indices] * view_dirs[valid_indices], axis=1))
        weights = cosine / np.maximum(depth[valid_indices], 1e-3)
        color_accum[valid_indices] += sampled * weights[:, None]
        weight_accum[valid_indices] += weights

    resolved = fallback_colors.copy()
    observed = weight_accum > 0
    resolved[observed] = color_accum[observed] / weight_accum[observed, None]
    rgba = np.concatenate(
        [
            np.clip(np.rint(resolved), 0, 255).astype(np.uint8),
            np.full((vertex_count, 1), 255, dtype=np.uint8),
        ],
        axis=1,
    )
    return rgba, int(np.count_nonzero(observed))


def _sample_projected_views(ctx: JobContext, poses: list[_ImagePose], *, max_views: int) -> list[_ImagePose]:
    if not poses:
        return []
    if len(poses) <= max_views:
        return poses
    if max_views <= 1:
        return [poses[len(poses) // 2]]
    quality_scores = _projection_pose_quality_scores(ctx, poses)
    descriptors = _projection_pose_descriptors(poses)
    selected_indices = _greedy_pose_selection(
        descriptors=descriptors,
        quality_scores=quality_scores,
        target_count=max_views,
    )
    return [poses[index] for index in selected_indices]


def _projection_pose_quality_scores(ctx: JobContext, poses: list[_ImagePose]) -> np.ndarray:
    summary = _read_json(ctx.output_dir / "curate_frames.json") or {}
    frames = summary.get("frames")
    if not isinstance(frames, list):
        return np.ones(len(poses), dtype=np.float64)
    by_stem: dict[str, float] = {}
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        name = str(frame.get("name") or "").strip()
        if not name:
            continue
        stem = Path(name).stem
        target_signal = float(frame.get("target_signal") or 0.0)
        orb_feature_count = float(frame.get("orb_feature_count") or 0.0)
        hard_penalty = len(frame.get("hard_reject_reasons") or [])
        soft_penalty = len(frame.get("soft_downgrade_reasons") or [])
        score = 1.0
        score += min(target_signal, 1.0) * 2.0
        score += min(orb_feature_count / 1200.0, 1.5)
        score -= hard_penalty * 0.50
        score -= soft_penalty * 0.20
        by_stem[stem] = max(score, 0.05)
    raw_scores = np.asarray([by_stem.get(Path(pose.name).stem, 1.0) for pose in poses], dtype=np.float64)
    min_value = float(raw_scores.min(initial=1.0))
    max_value = float(raw_scores.max(initial=1.0))
    if max_value - min_value < 1e-6:
        return np.ones(len(poses), dtype=np.float64)
    return np.clip((raw_scores - min_value) / max(max_value - min_value, 1e-6), 0.0, 1.0)


def _projection_pose_descriptors(poses: list[_ImagePose]) -> np.ndarray:
    if not poses:
        return np.zeros((0, 7), dtype=np.float64)
    centers = np.zeros((len(poses), 3), dtype=np.float64)
    forwards = np.zeros((len(poses), 3), dtype=np.float64)
    for index, pose in enumerate(poses):
        rotation = _qvec_to_rotmat(pose.qvec)
        translation = np.asarray(pose.tvec, dtype=np.float64)
        center = -(rotation.T @ translation)
        forward = rotation.T @ np.array([0.0, 0.0, 1.0], dtype=np.float64)
        norm = float(np.linalg.norm(forward))
        if norm > 1e-6:
            forward = forward / norm
        centers[index] = center
        forwards[index] = forward
    center_mean = centers.mean(axis=0)
    center_scale = np.maximum(centers.std(axis=0), 1e-3)
    centers = (centers - center_mean) / center_scale
    time_feature = np.linspace(0.0, 1.0, num=len(poses), dtype=np.float64)[:, None]
    return np.concatenate([centers * 1.8, forwards * 1.2, time_feature * 0.6], axis=1)


def _greedy_pose_selection(
    *,
    descriptors: np.ndarray,
    quality_scores: np.ndarray,
    target_count: int,
) -> list[int]:
    count = int(descriptors.shape[0])
    if count <= target_count:
        return list(range(count))
    selected: list[int] = []
    used: set[int] = set()
    seed_candidates = [
        int(np.argmax(quality_scores)),
        0,
        count - 1,
    ]
    for seed in seed_candidates:
        if seed in used:
            continue
        used.add(seed)
        selected.append(seed)
        if len(selected) >= target_count:
            return sorted(selected)
    while len(selected) < target_count:
        best_index: int | None = None
        best_score = float("-inf")
        for candidate in range(count):
            if candidate in used:
                continue
            novelty = min(
                float(np.linalg.norm(descriptors[candidate] - descriptors[selected_index]))
                for selected_index in selected
            )
            temporal_gap = min(abs(candidate - selected_index) for selected_index in selected) / max(count - 1, 1)
            score = novelty + temporal_gap * 0.35 + float(quality_scores[candidate]) * 0.20
            if score > best_score:
                best_score = score
                best_index = candidate
        if best_index is None:
            break
        used.add(best_index)
        selected.append(best_index)
    selected.sort()
    return selected


def _load_delivery_projection_views(
    *,
    ctx: JobContext,
    images_dir: Path,
    cameras: dict[int, _Camera],
    poses: list[_ImagePose],
) -> list[_ProjectionView]:
    projection_views: list[_ProjectionView] = []
    loaded_pose_names: set[str] = set()
    curated_paths = _sorted_curated_frame_paths(ctx)

    for pose in poses:
        camera = cameras.get(pose.camera_id)
        if camera is None:
            continue
        curated_path = _resolve_curated_projection_path(curated_paths, pose.name)
        if curated_path is None:
            continue
        projection_view = _load_curated_projection_view(
            curated_path=curated_path,
            camera=camera,
            pose=pose,
        )
        if projection_view is None:
            continue
        projection_views.append(projection_view)
        loaded_pose_names.add(pose.name)

    missing_poses = [pose for pose in poses if pose.name not in loaded_pose_names]
    if missing_poses:
        projection_views.extend(
            _load_projection_views(
                images_dir=images_dir,
                cameras=cameras,
                poses=missing_poses,
            )
        )
    return projection_views


def _load_projection_views(
    *,
    images_dir: Path,
    cameras: dict[int, _Camera],
    poses: list[_ImagePose],
) -> list[_ProjectionView]:
    from PIL import Image

    projection_views: list[_ProjectionView] = []
    for pose in poses:
        camera = cameras.get(pose.camera_id)
        if camera is None:
            continue
        image_path = images_dir / pose.name
        if not image_path.exists():
            continue
        image = np.asarray(Image.open(image_path).convert("RGB"), dtype=np.float64)
        rotation = _qvec_to_rotmat(pose.qvec)
        translation = np.asarray(pose.tvec, dtype=np.float64)
        center = -(rotation.T @ translation)
        projection_views.append(
            _ProjectionView(
                pose=pose,
                camera=camera,
                image=image,
                rotation=rotation,
                translation=translation,
                center=center,
            )
        )
    return projection_views


def _sorted_curated_frame_paths(ctx: JobContext) -> list[Path]:
    if ctx.curated_dir is None or not ctx.curated_dir.exists():
        return []
    return sorted(
        path
        for path in ctx.curated_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )


def _resolve_curated_projection_path(curated_paths: list[Path], pose_name: str) -> Path | None:
    stem = Path(pose_name).stem
    try:
        frame_index = int(stem)
    except ValueError:
        return None
    if frame_index < 0 or frame_index >= len(curated_paths):
        return None
    return curated_paths[frame_index]


def _load_curated_projection_view(
    *,
    curated_path: Path,
    camera: _Camera,
    pose: _ImagePose,
) -> _ProjectionView | None:
    from PIL import Image

    if not curated_path.exists():
        return None

    image = np.asarray(Image.open(curated_path).convert("RGB"), dtype=np.float64)
    target_size = max(256, int(config.delivery_projection_image_size))
    output_width, output_height = _resolve_projection_dimensions(
        source_width=int(camera.width),
        source_height=int(camera.height),
        target_longest_side=target_size,
    )
    if output_width <= 0 or output_height <= 0:
        return None
    resized_image = np.asarray(
        Image.fromarray(image.astype(np.uint8), mode="RGB").resize((output_width, output_height), Image.BILINEAR),
        dtype=np.float64,
    )

    scale_x = float(output_width) / max(float(camera.width), 1.0)
    scale_y = float(output_height) / max(float(camera.height), 1.0)
    scaled_camera = _Camera(
        width=int(output_width),
        height=int(output_height),
        fx=float(camera.fx * scale_x),
        fy=float(camera.fy * scale_y),
        cx=float(camera.cx * scale_x),
        cy=float(camera.cy * scale_y),
    )
    rotation = _qvec_to_rotmat(pose.qvec)
    translation = np.asarray(pose.tvec, dtype=np.float64)
    center = -(rotation.T @ translation)
    return _ProjectionView(
        pose=pose,
        camera=scaled_camera,
        image=resized_image,
        rotation=rotation,
        translation=translation,
        center=center,
    )


def _resolve_projection_dimensions(
    *,
    source_width: int,
    source_height: int,
    target_longest_side: int,
) -> tuple[int, int]:
    longest_side = max(source_width, source_height)
    if longest_side <= 0:
        return 0, 0
    scale = float(target_longest_side) / float(longest_side)
    return max(64, int(round(source_width * scale))), max(64, int(round(source_height * scale)))


def _bake_uv_textured_mesh(
    *,
    mesh,
    vertex_colors: np.ndarray,
    projection_views: list[_ProjectionView],
    atlas_size: int,
    progress_callback: OptimizeProgressCallback | None = None,
):
    try:
        import trimesh
        import xatlas
        from PIL import Image
        from trimesh.visual.material import SimpleMaterial
        from trimesh.visual.texture import TextureVisuals
    except Exception as exc:
        raise RuntimeError(f"delivery_texture_runtime_missing:{exc}") from exc

    subprogress = _make_throttled_subprogress_emitter(
        progress_callback,
        progress_start=0.72,
        progress_end=0.89,
    )
    bake_mesh, bake_vertex_colors, bake_proxy_summary = _prepare_bake_mesh_proxy(
        mesh,
        vertex_colors=vertex_colors,
        progress_emitter=subprogress,
    )

    vertices = np.asarray(bake_mesh.vertices, dtype=np.float32)
    faces = np.asarray(bake_mesh.faces, dtype=np.uint32)
    if len(vertices) == 0 or len(faces) == 0:
        raise RuntimeError("delivery_texture_empty_mesh")

    subprogress(
        0.02,
        title="正在展开 HQ UV 图集",
        detail="正在为优化后的网格建立 UV chart，这一步会按网格复杂度消耗较多 CPU。",
        metrics={"texture_phase": "parameterize_uv_atlas"},
        force=True,
    )
    vmapping, remapped_faces, uvs = xatlas.parametrize(vertices, faces)
    remapped_faces = np.asarray(remapped_faces, dtype=np.int64)
    uvs = np.asarray(uvs, dtype=np.float32)
    vmapping = np.asarray(vmapping, dtype=np.int64)
    subprogress(
        0.16,
        title="正在完成 HQ UV 图集展开",
        detail="UV chart 已建立，正在准备顶点颜色 fallback atlas。",
        metrics={"texture_phase": "parameterize_uv_atlas_done"},
        force=True,
    )

    baked_vertices = vertices[vmapping]
    baked_normals = np.asarray(bake_mesh.vertex_normals, dtype=np.float32)
    if baked_normals.shape == vertices.shape:
        baked_normals = baked_normals[vmapping]
    else:
        baked_normals = None
    baked_colors = np.asarray(bake_vertex_colors[:, :3], dtype=np.float32)[vmapping]

    fallback_rgb, fallback_coverage = _rasterize_vertex_color_atlas(
        uvs=uvs,
        faces=remapped_faces,
        colors=baked_colors,
        atlas_size=atlas_size,
        progress_emitter=subprogress,
    )
    texture_rgb, projection_summary = _rasterize_photo_projection_atlas(
        vertices=baked_vertices.astype(np.float64),
        uvs=uvs,
        faces=remapped_faces,
        projection_views=projection_views,
        atlas_size=atlas_size,
        fallback_rgb=fallback_rgb,
        fallback_coverage=fallback_coverage,
        progress_emitter=subprogress,
    )
    subprogress(
        0.96,
        title="正在统计 HQ 纹理质量",
        detail="Atlas 已烘焙完成，正在汇总覆盖率、回退比例和邻接视角一致性。",
        metrics={"texture_phase": "summarize_texture_quality"},
        force=True,
    )

    pil_image = Image.fromarray(texture_rgb, mode="RGB")
    material = SimpleMaterial(image=pil_image)
    visual = TextureVisuals(uv=uvs.astype(np.float64), image=pil_image, material=material)
    textured_mesh = trimesh.Trimesh(
        vertices=baked_vertices,
        faces=remapped_faces,
        vertex_normals=baked_normals,
        visual=visual,
        process=False,
    )
    atlas_summary = {
        "atlas_size": atlas_size,
        "bake_proxy_applied": bool(bake_proxy_summary["applied"]),
        "bake_proxy_faces": int(bake_proxy_summary["faces"]),
        "bake_proxy_vertices": int(bake_proxy_summary["vertices"]),
        "bake_proxy_surface_area_retention": float(bake_proxy_summary["surface_area_retention"]),
        "bake_proxy_min_extent_retention": float(bake_proxy_summary["min_extent_retention"]),
        "observed_pixel_count": int(projection_summary["observed_pixel_count"]),
        "coverage_ratio": float(projection_summary["coverage_ratio"]),
        "photo_projected_pixel_count": int(projection_summary["photo_projected_pixel_count"]),
        "photo_projected_face_count": int(projection_summary["photo_projected_face_count"]),
        "fallback_face_count": int(projection_summary["fallback_face_count"]),
        "neighbor_view_disagreement": float(projection_summary["neighbor_view_disagreement"]),
        "low_saturation_texel_ratio": float(projection_summary["low_saturation_texel_ratio"]),
    }
    subprogress(
        1.0,
        title="HQ 纹理 atlas 完成",
        detail="纹理 atlas 烘焙完成，正在返回给导出阶段写出最终 HQ GLB。",
        metrics={"texture_phase": "bake_uv_atlas_done"},
        force=True,
    )
    return textured_mesh, atlas_summary


def _prepare_bake_mesh_proxy(
    mesh,
    *,
    vertex_colors: np.ndarray,
    progress_emitter: Callable[..., None] | None = None,
) -> tuple[Any, np.ndarray, dict[str, Any]]:
    face_count = int(len(mesh.faces))
    summary = {
        "applied": False,
        "faces": face_count,
        "vertices": int(len(mesh.vertices)),
        "surface_area_retention": 1.0,
        "min_extent_retention": 1.0,
    }
    trigger_faces = max(1, int(config.delivery_bake_mesh_proxy_trigger_faces))
    if face_count <= trigger_faces:
        return mesh, np.asarray(vertex_colors, dtype=np.uint8), summary

    target_faces = _bake_mesh_proxy_target_faces(face_count)
    if target_faces >= face_count:
        return mesh, np.asarray(vertex_colors, dtype=np.uint8), summary

    if progress_emitter is not None:
        progress_emitter(
            0.01,
            title="正在收敛用于 UV 展开的工作网格",
            detail="当前 HQ 网格太大，先生成保形的 UV 工作网格，避免 UV 展开阶段假活着卡死。",
            metrics={
                "texture_phase": "prepare_bake_mesh_proxy",
                "bake_proxy_target_faces": str(target_faces),
            },
            force=True,
        )

    proxy_mesh, simplify_summary = _shape_preserving_simplify_mesh(
        mesh,
        reference_mesh=mesh,
        target_faces=target_faces,
        min_extent_retention_floor=max(0.70, float(config.mesh_fidelity_hq_min_extent_retention) * 0.98),
        min_surface_area_retention_floor=max(0.75, float(config.mesh_fidelity_hq_min_surface_area_retention) * 0.98),
    )
    if len(proxy_mesh.faces) <= 0 or len(proxy_mesh.faces) >= face_count:
        return mesh, np.asarray(vertex_colors, dtype=np.uint8), summary

    try:
        proxy_mesh.remove_unreferenced_vertices()
    except Exception:
        pass
    try:
        proxy_mesh.merge_vertices()
    except Exception:
        pass
    proxy_mesh = _drop_degenerate_faces(proxy_mesh)
    fidelity = _compute_mesh_fidelity_metrics(reference_mesh=mesh, candidate_mesh=proxy_mesh)
    proxy_colors = _remap_vertex_colors_nearest(
        reference_vertices=np.asarray(mesh.vertices, dtype=np.float64),
        reference_colors=np.asarray(vertex_colors, dtype=np.uint8),
        candidate_vertices=np.asarray(proxy_mesh.vertices, dtype=np.float64),
    )
    summary = {
        "applied": True,
        "faces": int(len(proxy_mesh.faces)),
        "vertices": int(len(proxy_mesh.vertices)),
        "surface_area_retention": float(fidelity["surface_area_retention"]),
        "min_extent_retention": float(fidelity["min_extent_retention"]),
        "accepted_target": int(simplify_summary.get("accepted_target", len(proxy_mesh.faces))),
    }
    if progress_emitter is not None:
        progress_emitter(
            0.015,
            title="UV 工作网格已收敛",
            detail=(
                f"已把 UV 工作网格收敛到 {len(proxy_mesh.faces):,} 面，"
                f"面积保留 {fidelity['surface_area_retention']:.3f}，最小尺度保留 {fidelity['min_extent_retention']:.3f}。"
            ),
            metrics={
                "texture_phase": "prepare_bake_mesh_proxy_done",
                "bake_proxy_faces": str(len(proxy_mesh.faces)),
                "bake_proxy_vertices": str(len(proxy_mesh.vertices)),
                "bake_proxy_surface_area_retention": f"{fidelity['surface_area_retention']:.5f}",
                "bake_proxy_min_extent_retention": f"{fidelity['min_extent_retention']:.5f}",
            },
            force=True,
        )
    return proxy_mesh, proxy_colors, summary


def _bake_mesh_proxy_target_faces(face_count: int) -> int:
    ratio_target = int(round(face_count * max(0.10, float(config.delivery_bake_mesh_proxy_target_ratio))))
    target = max(int(config.delivery_bake_mesh_proxy_min_faces), ratio_target)
    target = min(target, int(config.delivery_bake_mesh_proxy_face_cap))
    target = max(8192, int(target))
    return min(target, max(face_count - 1, 1))


def _remap_vertex_colors_nearest(
    *,
    reference_vertices: np.ndarray,
    reference_colors: np.ndarray,
    candidate_vertices: np.ndarray,
) -> np.ndarray:
    if len(reference_vertices) == 0 or len(candidate_vertices) == 0:
        return np.zeros((len(candidate_vertices), 4), dtype=np.uint8)
    if reference_colors.ndim != 2 or reference_colors.shape[0] != len(reference_vertices):
        return np.zeros((len(candidate_vertices), 4), dtype=np.uint8)
    if reference_colors.shape[1] == 3:
        alpha = np.full((len(reference_colors), 1), 255, dtype=np.uint8)
        reference_colors = np.concatenate([reference_colors, alpha], axis=1)
    try:
        from scipy.spatial import cKDTree

        tree = cKDTree(reference_vertices)
        _, indices = tree.query(candidate_vertices, k=1, workers=1)
        indices = np.asarray(indices, dtype=np.int64)
    except Exception:
        indices = []
        for vertex in candidate_vertices:
            deltas = reference_vertices - vertex[None, :]
            distances = np.linalg.norm(deltas, axis=1)
            indices.append(int(np.argmin(distances)))
        indices = np.asarray(indices, dtype=np.int64)
    indices = np.clip(indices, 0, len(reference_vertices) - 1)
    return np.asarray(reference_colors[indices], dtype=np.uint8)


def _rasterize_vertex_color_atlas(
    *,
    uvs: np.ndarray,
    faces: np.ndarray,
    colors: np.ndarray,
    atlas_size: int,
    progress_emitter: Callable[..., None] | None = None,
) -> tuple[np.ndarray, int]:
    import cv2

    atlas = np.zeros((atlas_size, atlas_size, 3), dtype=np.uint8)
    coverage = np.zeros((atlas_size, atlas_size), dtype=np.uint8)

    uv_pixels = np.empty_like(uvs, dtype=np.float32)
    uv_pixels[:, 0] = np.clip(uvs[:, 0], 0.0, 1.0) * float(atlas_size - 1)
    uv_pixels[:, 1] = (1.0 - np.clip(uvs[:, 1], 0.0, 1.0)) * float(atlas_size - 1)

    total_faces = int(len(faces))
    for face_index, face in enumerate(faces):
        tri_uv = uv_pixels[face]
        tri_colors = colors[face]
        min_x = max(0, int(np.floor(np.min(tri_uv[:, 0]))))
        max_x = min(atlas_size - 1, int(np.ceil(np.max(tri_uv[:, 0]))))
        min_y = max(0, int(np.floor(np.min(tri_uv[:, 1]))))
        max_y = min(atlas_size - 1, int(np.ceil(np.max(tri_uv[:, 1]))))
        if min_x >= max_x or min_y >= max_y:
            continue
        triangle_area = _edge_function(tri_uv[0], tri_uv[1], tri_uv[2])
        if abs(triangle_area) < 1e-5:
            continue

        xs = np.arange(min_x, max_x + 1, dtype=np.float32) + 0.5
        ys = np.arange(min_y, max_y + 1, dtype=np.float32) + 0.5
        grid_x, grid_y = np.meshgrid(xs, ys)
        points = np.stack([grid_x, grid_y], axis=-1)

        w0 = _edge_function(tri_uv[1], tri_uv[2], points)
        w1 = _edge_function(tri_uv[2], tri_uv[0], points)
        w2 = _edge_function(tri_uv[0], tri_uv[1], points)
        if triangle_area < 0:
            inside = (w0 <= 0) & (w1 <= 0) & (w2 <= 0)
        else:
            inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not np.any(inside):
            continue

        w0 = w0 / triangle_area
        w1 = w1 / triangle_area
        w2 = w2 / triangle_area
        interpolated = (
            w0[..., None] * tri_colors[0][None, None, :]
            + w1[..., None] * tri_colors[1][None, None, :]
            + w2[..., None] * tri_colors[2][None, None, :]
        )
        patch = atlas[min_y : max_y + 1, min_x : max_x + 1]
        patch_coverage = coverage[min_y : max_y + 1, min_x : max_x + 1]
        patch[inside] = np.clip(np.rint(interpolated[inside]), 0, 255).astype(np.uint8)
        patch_coverage[inside] = 255
        if progress_emitter is not None:
            progress_emitter(
                0.16 + 0.12 * ((face_index + 1) / max(total_faces, 1)),
                title="正在写入顶点颜色 fallback atlas",
                detail="正在把稳定的顶点颜色先写入 atlas，为后面的照片投影提供回退纹理。",
                metrics={
                    "texture_phase": "rasterize_vertex_fallback",
                    "texture_face_index": str(face_index + 1),
                    "texture_face_total": str(total_faces),
                },
            )

    if np.any(coverage):
        kernel_size = max(1, int(config.delivery_texture_fill_kernel))
        kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
        dilated = cv2.dilate(atlas, kernel, iterations=1)
        mask = coverage == 0
        atlas[mask] = dilated[mask]

    return atlas, coverage


def _rasterize_photo_projection_atlas(
    *,
    vertices: np.ndarray,
    uvs: np.ndarray,
    faces: np.ndarray,
    projection_views: list[_ProjectionView],
    atlas_size: int,
    fallback_rgb: np.ndarray,
    fallback_coverage: np.ndarray,
    progress_emitter: Callable[..., None] | None = None,
) -> tuple[np.ndarray, dict[str, float | int]]:
    import cv2

    atlas = fallback_rgb.copy()
    coverage = fallback_coverage.copy()
    uv_pixels = np.empty_like(uvs, dtype=np.float64)
    uv_pixels[:, 0] = np.clip(uvs[:, 0], 0.0, 1.0) * float(atlas_size - 1)
    uv_pixels[:, 1] = (1.0 - np.clip(uvs[:, 1], 0.0, 1.0)) * float(atlas_size - 1)

    photo_projected_pixel_count = 0
    projected_face_count = 0
    ranked_candidates = []
    total_faces = int(len(faces))
    for face_index, face in enumerate(faces):
        ranked_candidates.append(_rank_projection_views(vertices[face], projection_views))
        if progress_emitter is not None:
            progress_emitter(
                0.30 + 0.18 * ((face_index + 1) / max(total_faces, 1)),
                title="正在排序贴图候选视角",
                detail="正在为每个三角面筛选最合适的投影视角，优先保证可见性和连续性。",
                metrics={
                    "texture_phase": "rank_projection_views",
                    "texture_face_index": str(face_index + 1),
                    "texture_face_total": str(total_faces),
                },
            )
    preferred_views = _smooth_face_view_assignments(
        faces=faces,
        ranked_candidates=ranked_candidates,
    )

    for face_index, face in enumerate(faces):
        tri_vertices = vertices[face]
        tri_uv = uv_pixels[face]
        ordered_candidates = _ordered_projection_candidates(
            ranked_candidates[face_index],
            preferred_view_index=preferred_views[face_index],
        )
        if not ordered_candidates:
            continue
        projected = 0
        for view_index, _ in ordered_candidates:
            projected = _project_face_to_photo(
                tri_vertices=tri_vertices,
                tri_uv=tri_uv,
                view=projection_views[view_index],
                atlas=atlas,
                coverage=coverage,
            )
            if projected > 0:
                break
        if projected > 0:
            projected_face_count += 1
            photo_projected_pixel_count += projected
        if progress_emitter is not None:
            progress_emitter(
                0.48 + 0.40 * ((face_index + 1) / max(total_faces, 1)),
                title="正在投影多视图照片到 HQ atlas",
                detail="正在逐面把可见照片投到 UV atlas，上一步越大，这一步就越吃 CPU。",
                metrics={
                    "texture_phase": "project_photo_to_atlas",
                    "texture_face_index": str(face_index + 1),
                    "texture_face_total": str(total_faces),
                    "texture_projected_face_count": str(projected_face_count),
                },
            )

    if np.any(coverage):
        if progress_emitter is not None:
            progress_emitter(
                0.92,
                title="正在填补 atlas 缝隙",
                detail="正在对 atlas 做一次保守扩张，减少未覆盖 texel 的裂缝感。",
                metrics={"texture_phase": "dilate_texture_atlas"},
                force=True,
            )
        kernel_size = max(1, int(config.delivery_texture_fill_kernel))
        kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
        dilated = cv2.dilate(atlas, kernel, iterations=1)
        mask = coverage == 0
        atlas[mask] = dilated[mask]

    observed_pixel_count = int(np.count_nonzero(coverage))
    fallback_face_count = int(max(len(faces) - projected_face_count, 0))
    neighbor_view_disagreement = _neighbor_view_disagreement_ratio(
        faces=faces,
        preferred_views=preferred_views,
    )
    low_saturation_texel_ratio = _low_saturation_texel_ratio(
        atlas=atlas,
        coverage=coverage,
    )
    return atlas, {
        "observed_pixel_count": int(observed_pixel_count),
        "coverage_ratio": float(observed_pixel_count / max(1, atlas_size * atlas_size)),
        "photo_projected_pixel_count": int(photo_projected_pixel_count),
        "photo_projected_face_count": int(projected_face_count),
        "fallback_face_count": int(fallback_face_count),
        "neighbor_view_disagreement": float(neighbor_view_disagreement),
        "low_saturation_texel_ratio": float(low_saturation_texel_ratio),
    }


def _rank_projection_views(
    tri_vertices: np.ndarray,
    projection_views: list[_ProjectionView],
) -> list[tuple[int, float]]:
    if not projection_views:
        return []
    normal = np.cross(tri_vertices[1] - tri_vertices[0], tri_vertices[2] - tri_vertices[0])
    normal_norm = np.linalg.norm(normal)
    if normal_norm < 1e-8:
        return []
    normal = normal / normal_norm
    centroid = np.mean(tri_vertices, axis=0)

    ranked = _rank_projection_views_with_min_cosine(
        tri_vertices=tri_vertices,
        projection_views=projection_views,
        centroid=centroid,
        normal=normal,
        min_cosine=float(config.delivery_texture_min_view_cosine),
    )
    if ranked:
        return ranked
    return _rank_projection_views_with_min_cosine(
        tri_vertices=tri_vertices,
        projection_views=projection_views,
        centroid=centroid,
        normal=normal,
        min_cosine=-1.0,
    )


def _rank_projection_views_with_min_cosine(
    *,
    tri_vertices: np.ndarray,
    projection_views: list[_ProjectionView],
    centroid: np.ndarray,
    normal: np.ndarray,
    min_cosine: float,
) -> list[tuple[int, float]]:
    ranked: list[tuple[int, float]] = []
    for view_index, view in enumerate(projection_views):
        camera_points = (view.rotation @ tri_vertices.T).T + view.translation
        depth = camera_points[:, 2]
        if np.any(depth <= 1e-5):
            continue
        u = view.camera.fx * (camera_points[:, 0] / depth) + view.camera.cx
        v = view.camera.fy * (camera_points[:, 1] / depth) + view.camera.cy
        width = view.camera.width
        height = view.camera.height
        if np.any(~np.isfinite(u)) or np.any(~np.isfinite(v)):
            continue
        if np.any(u < 0.0) or np.any(u > (width - 1)) or np.any(v < 0.0) or np.any(v > (height - 1)):
            continue

        view_dir = view.center - centroid
        view_norm = np.linalg.norm(view_dir)
        if view_norm <= 1e-8:
            continue
        view_dir = view_dir / view_norm
        cosine = float(np.dot(normal, view_dir))
        if cosine <= min_cosine:
            continue

        area = abs(
            (u[1] - u[0]) * (v[2] - v[0])
            - (v[1] - v[0]) * (u[2] - u[0])
        ) * 0.5
        if area <= 1e-4:
            continue

        score = max(cosine, 0.05) * math.sqrt(area) / max(float(np.mean(depth)), 1e-3)
        ranked.append((view_index, float(score)))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def _face_adjacency(faces: np.ndarray) -> list[list[int]]:
    adjacency: list[set[int]] = [set() for _ in range(len(faces))]
    edge_to_faces: dict[tuple[int, int], list[int]] = {}
    for face_index, face in enumerate(faces.tolist()):
        a, b, c = int(face[0]), int(face[1]), int(face[2])
        for start, end in ((a, b), (b, c), (c, a)):
            edge_to_faces.setdefault(tuple(sorted((start, end))), []).append(face_index)
    for face_indices in edge_to_faces.values():
        if len(face_indices) != 2:
            continue
        first, second = face_indices
        adjacency[first].add(second)
        adjacency[second].add(first)
    return [sorted(neighbors) for neighbors in adjacency]


def _smooth_face_view_assignments(
    *,
    faces: np.ndarray,
    ranked_candidates: list[list[tuple[int, float]]],
) -> list[int | None]:
    assignments: list[int | None] = [
        candidates[0][0] if candidates else None
        for candidates in ranked_candidates
    ]
    adjacency = _face_adjacency(faces)
    margin = max(0.0, float(config.delivery_texture_view_consistency_margin))
    rounds = max(0, int(config.delivery_texture_view_smoothing_rounds))
    for _ in range(rounds):
        updated = assignments.copy()
        for face_index, neighbors in enumerate(adjacency):
            candidates = ranked_candidates[face_index]
            if not candidates or not neighbors:
                continue
            score_by_view = {view_index: score for view_index, score in candidates}
            current_view = assignments[face_index]
            neighbor_counts: dict[int, int] = {}
            for neighbor_index in neighbors:
                neighbor_view = assignments[neighbor_index]
                if neighbor_view is None:
                    continue
                neighbor_counts[neighbor_view] = neighbor_counts.get(neighbor_view, 0) + 1
            if not neighbor_counts:
                continue
            dominant_view = max(
                neighbor_counts,
                key=lambda view_index: (neighbor_counts[view_index], score_by_view.get(view_index, -math.inf)),
            )
            if dominant_view == current_view or dominant_view not in score_by_view:
                continue
            current_score = score_by_view.get(current_view, candidates[0][1]) if current_view is not None else -math.inf
            dominant_score = score_by_view[dominant_view]
            if current_score <= 0.0:
                if dominant_score > current_score:
                    updated[face_index] = dominant_view
                continue
            if dominant_score >= current_score * (1.0 - margin):
                updated[face_index] = dominant_view
        assignments = updated
    return assignments


def _ordered_projection_candidates(
    candidates: list[tuple[int, float]],
    *,
    preferred_view_index: int | None,
) -> list[tuple[int, float]]:
    if preferred_view_index is None or not candidates:
        return candidates
    preferred = [candidate for candidate in candidates if candidate[0] == preferred_view_index]
    if not preferred:
        return candidates
    return preferred + [candidate for candidate in candidates if candidate[0] != preferred_view_index]


def _neighbor_view_disagreement_ratio(*, faces: np.ndarray, preferred_views: list[int | None]) -> float:
    adjacency = _face_adjacency(faces)
    total_pairs = 0
    disagreement_pairs = 0
    for face_index, neighbors in enumerate(adjacency):
        current = preferred_views[face_index]
        if current is None:
            continue
        for neighbor in neighbors:
            if neighbor <= face_index:
                continue
            other = preferred_views[neighbor]
            if other is None:
                continue
            total_pairs += 1
            if other != current:
                disagreement_pairs += 1
    if total_pairs <= 0:
        return 0.0
    return float(disagreement_pairs / total_pairs)


def _low_saturation_texel_ratio(*, atlas: np.ndarray, coverage: np.ndarray) -> float:
    observed = coverage > 0
    if not np.any(observed):
        return 1.0
    rgb = atlas[observed].astype(np.float32) / 255.0
    max_rgb = np.max(rgb, axis=1)
    min_rgb = np.min(rgb, axis=1)
    saturation = np.zeros_like(max_rgb)
    valid = max_rgb > 1e-6
    saturation[valid] = (max_rgb[valid] - min_rgb[valid]) / max_rgb[valid]
    return float(np.count_nonzero(saturation <= 0.12) / max(len(saturation), 1))


def _project_face_to_photo(
    *,
    tri_vertices: np.ndarray,
    tri_uv: np.ndarray,
    view: _ProjectionView,
    atlas: np.ndarray,
    coverage: np.ndarray,
) -> int:
    triangle_area = _edge_function(tri_uv[0], tri_uv[1], tri_uv[2])
    if abs(triangle_area) < 1e-6:
        return 0

    atlas_size = atlas.shape[0]
    min_x = max(0, int(np.floor(np.min(tri_uv[:, 0]))))
    max_x = min(atlas_size - 1, int(np.ceil(np.max(tri_uv[:, 0]))))
    min_y = max(0, int(np.floor(np.min(tri_uv[:, 1]))))
    max_y = min(atlas_size - 1, int(np.ceil(np.max(tri_uv[:, 1]))))
    if min_x >= max_x or min_y >= max_y:
        return 0

    xs = np.arange(min_x, max_x + 1, dtype=np.float64) + 0.5
    ys = np.arange(min_y, max_y + 1, dtype=np.float64) + 0.5
    grid_x, grid_y = np.meshgrid(xs, ys)
    points = np.stack([grid_x, grid_y], axis=-1)

    w0 = _edge_function(tri_uv[1], tri_uv[2], points)
    w1 = _edge_function(tri_uv[2], tri_uv[0], points)
    w2 = _edge_function(tri_uv[0], tri_uv[1], points)
    if triangle_area < 0:
        inside = (w0 <= 0) & (w1 <= 0) & (w2 <= 0)
    else:
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
    if not np.any(inside):
        return 0

    w0 = w0 / triangle_area
    w1 = w1 / triangle_area
    w2 = w2 / triangle_area
    positions = (
        w0[..., None] * tri_vertices[0][None, None, :]
        + w1[..., None] * tri_vertices[1][None, None, :]
        + w2[..., None] * tri_vertices[2][None, None, :]
    )
    flat_positions = positions[inside]
    camera_points = (view.rotation @ flat_positions.T).T + view.translation
    depth = camera_points[:, 2]
    valid = depth > 1e-6
    if not np.any(valid):
        return 0

    u = view.camera.fx * (camera_points[:, 0] / depth) + view.camera.cx
    v = view.camera.fy * (camera_points[:, 1] / depth) + view.camera.cy
    valid &= np.isfinite(u) & np.isfinite(v)
    valid &= (u >= 0.0) & (u <= (view.camera.width - 1)) & (v >= 0.0) & (v <= (view.camera.height - 1))
    if not np.any(valid):
        return 0

    sampled = _sample_bilinear_rgb(view.image, u[valid], v[valid])
    patch = atlas[min_y : max_y + 1, min_x : max_x + 1]
    patch_coverage = coverage[min_y : max_y + 1, min_x : max_x + 1]
    inside_indices = np.argwhere(inside)
    valid_inside_indices = inside_indices[valid]
    patch[valid_inside_indices[:, 0], valid_inside_indices[:, 1]] = sampled
    patch_coverage[valid_inside_indices[:, 0], valid_inside_indices[:, 1]] = 255
    return int(len(valid_inside_indices))


def _sample_bilinear_rgb(image: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    u0 = np.floor(u).astype(np.int64)
    v0 = np.floor(v).astype(np.int64)
    u1 = np.clip(u0 + 1, 0, width - 1)
    v1 = np.clip(v0 + 1, 0, height - 1)
    u0 = np.clip(u0, 0, width - 1)
    v0 = np.clip(v0, 0, height - 1)

    du = (u - u0).astype(np.float64)
    dv = (v - v0).astype(np.float64)

    top_left = image[v0, u0]
    top_right = image[v0, u1]
    bottom_left = image[v1, u0]
    bottom_right = image[v1, u1]

    top = top_left * (1.0 - du[:, None]) + top_right * du[:, None]
    bottom = bottom_left * (1.0 - du[:, None]) + bottom_right * du[:, None]
    interpolated = top * (1.0 - dv[:, None]) + bottom * dv[:, None]
    return np.clip(np.rint(interpolated), 0, 255).astype(np.uint8)


def _edge_function(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    return (c[..., 0] - a[0]) * (b[1] - a[1]) - (c[..., 1] - a[1]) * (b[0] - a[0])


def _existing_vertex_colors(mesh) -> np.ndarray | None:
    visual = getattr(mesh, "visual", None)
    if visual is None:
        return None
    colors = getattr(visual, "vertex_colors", None)
    if colors is None:
        return None
    colors = np.asarray(colors)
    if colors.ndim != 2 or colors.shape[0] != len(mesh.vertices):
        return None
    if colors.shape[1] == 3:
        colors = np.concatenate([colors, np.full((colors.shape[0], 1), 255, dtype=colors.dtype)], axis=1)
    return colors


def _read_cameras(path: Path) -> dict[int, _Camera]:
    cameras: dict[int, _Camera] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 8 or parts[1] != "PINHOLE":
            continue
        camera_id = int(parts[0])
        cameras[camera_id] = _Camera(
            width=int(parts[2]),
            height=int(parts[3]),
            fx=float(parts[4]),
            fy=float(parts[5]),
            cx=float(parts[6]),
            cy=float(parts[7]),
        )
    return cameras


def _read_images(path: Path) -> list[_ImagePose]:
    poses: list[_ImagePose] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 10:
            continue
        poses.append(
            _ImagePose(
                camera_id=int(parts[8]),
                name=parts[9],
                qvec=(float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])),
                tvec=(float(parts[5]), float(parts[6]), float(parts[7])),
            )
        )
        if index < len(lines):
            index += 1
    return poses


def _qvec_to_rotmat(qvec: tuple[float, float, float, float]) -> np.ndarray:
    qw, qx, qy, qz = qvec
    return np.array(
        [
            [
                1 - 2 * (qy * qy + qz * qz),
                2 * (qx * qy - qw * qz),
                2 * (qx * qz + qw * qy),
            ],
            [
                2 * (qx * qy + qw * qz),
                1 - 2 * (qx * qx + qz * qz),
                2 * (qy * qz - qw * qx),
            ],
            [
                2 * (qx * qz - qw * qy),
                2 * (qy * qz + qw * qx),
                1 - 2 * (qx * qx + qy * qy),
            ],
        ],
        dtype=np.float64,
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _mesh_extents(mesh) -> np.ndarray:
    bounds = getattr(mesh, "bounds", None)
    if bounds is None:
        return np.ones(3, dtype=np.float64)
    bounds = np.asarray(bounds, dtype=np.float64)
    if bounds.shape != (2, 3):
        return np.ones(3, dtype=np.float64)
    extents = np.maximum(bounds[1] - bounds[0], 1e-6)
    return extents.astype(np.float64)


def _mesh_bounds(mesh) -> np.ndarray:
    bounds = getattr(mesh, "bounds", None)
    if bounds is None:
        return np.asarray([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], dtype=np.float64)
    bounds = np.asarray(bounds, dtype=np.float64)
    if bounds.shape != (2, 3):
        return np.asarray([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], dtype=np.float64)
    return bounds


def _bounds_extent_retention(*, reference_bounds: np.ndarray, candidate_bounds: np.ndarray) -> np.ndarray:
    reference_extents = np.maximum(reference_bounds[1] - reference_bounds[0], 1e-6)
    candidate_extents = np.maximum(candidate_bounds[1] - candidate_bounds[0], 0.0)
    return np.clip(candidate_extents / reference_extents, 0.0, 10.0)


def _compute_mesh_fidelity_metrics(*, reference_mesh, candidate_mesh) -> dict[str, float]:
    reference_area = float(getattr(reference_mesh, "area", 0.0))
    candidate_area = float(getattr(candidate_mesh, "area", 0.0))
    reference_extents = _mesh_extents(reference_mesh)
    candidate_extents = _mesh_extents(candidate_mesh)
    extent_retention = np.clip(candidate_extents / np.maximum(reference_extents, 1e-6), 0.0, 10.0)
    min_extent_retention = float(np.min(extent_retention)) if extent_retention.size > 0 else 0.0
    reference_diag = float(np.linalg.norm(reference_extents))
    normalization = max(reference_diag, 1e-6)

    reference_vertices = _sample_vertex_cloud(np.asarray(reference_mesh.vertices, dtype=np.float64))
    candidate_vertices = _sample_vertex_cloud(np.asarray(candidate_mesh.vertices, dtype=np.float64))
    ref_to_candidate = _nearest_neighbor_distances(reference_vertices, candidate_vertices)
    candidate_to_ref = _nearest_neighbor_distances(candidate_vertices, reference_vertices)
    if ref_to_candidate.size == 0 and candidate_to_ref.size == 0:
        bidirectional = np.zeros(1, dtype=np.float64)
    elif ref_to_candidate.size == 0:
        bidirectional = candidate_to_ref
    elif candidate_to_ref.size == 0:
        bidirectional = ref_to_candidate
    else:
        bidirectional = np.concatenate([ref_to_candidate, candidate_to_ref], axis=0)

    return {
        "surface_area_retention": float(candidate_area / max(reference_area, 1e-6)) if reference_area > 1e-6 else 1.0,
        "extent_retention_x": float(extent_retention[0]),
        "extent_retention_y": float(extent_retention[1]),
        "extent_retention_z": float(extent_retention[2]),
        "min_extent_retention": float(min_extent_retention),
        "vertex_distance_mean_ratio": float(np.mean(bidirectional) / normalization),
        "vertex_distance_p95_ratio": float(np.percentile(bidirectional, 95) / normalization),
    }


def _stage_quality_snapshot(*, reference_mesh, candidate_mesh) -> dict[str, float | int]:
    fidelity = _compute_mesh_fidelity_metrics(reference_mesh=reference_mesh, candidate_mesh=candidate_mesh)
    boundary_stats = _analyze_boundary_topology(
        vertices=np.asarray(candidate_mesh.vertices, dtype=np.float64),
        faces=np.asarray(candidate_mesh.faces, dtype=np.int64),
    )
    return {
        "faces": int(len(candidate_mesh.faces)),
        "vertices": int(len(candidate_mesh.vertices)),
        "surface_area_retention": round(float(fidelity["surface_area_retention"]), 5),
        "extent_retention_x": round(float(fidelity["extent_retention_x"]), 5),
        "extent_retention_y": round(float(fidelity["extent_retention_y"]), 5),
        "extent_retention_z": round(float(fidelity["extent_retention_z"]), 5),
        "min_extent_retention": round(float(fidelity["min_extent_retention"]), 5),
        "vertex_distance_mean_ratio": round(float(fidelity["vertex_distance_mean_ratio"]), 5),
        "vertex_distance_p95_ratio": round(float(fidelity["vertex_distance_p95_ratio"]), 5),
        "boundary_edge_ratio": round(float(boundary_stats["boundary_edge_ratio"]), 5),
        "self_intersection_ratio": round(float(_estimate_self_intersection_ratio(candidate_mesh)), 5),
    }


def _sample_vertex_cloud(vertices: np.ndarray, *, sample_cap: int = 20000) -> np.ndarray:
    if vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) == 0:
        return np.zeros((0, 3), dtype=np.float64)
    if len(vertices) <= sample_cap:
        return vertices.astype(np.float64, copy=False)
    sample_indices = np.linspace(0, len(vertices) - 1, num=sample_cap, dtype=np.int64)
    return vertices[sample_indices].astype(np.float64, copy=False)


def _nearest_neighbor_distances(source_vertices: np.ndarray, target_vertices: np.ndarray) -> np.ndarray:
    if len(source_vertices) == 0 or len(target_vertices) == 0:
        return np.zeros(0, dtype=np.float64)
    try:
        from scipy.spatial import cKDTree

        tree = cKDTree(target_vertices)
        distances, _ = tree.query(source_vertices, workers=1)
        return np.asarray(distances, dtype=np.float64)
    except Exception:
        reduced_target = target_vertices
        if len(reduced_target) > 4096:
            reduced_target = _sample_vertex_cloud(reduced_target, sample_cap=4096)
        distances: list[np.ndarray] = []
        chunk_size = 256
        for chunk_start in range(0, len(source_vertices), chunk_size):
            chunk = source_vertices[chunk_start : chunk_start + chunk_size]
            delta = chunk[:, None, :] - reduced_target[None, :, :]
            chunk_distances = np.linalg.norm(delta, axis=2).min(axis=1)
            distances.append(chunk_distances.astype(np.float64))
        return np.concatenate(distances, axis=0) if distances else np.zeros(0, dtype=np.float64)


def _largest_component_ratio(mesh) -> float:
    try:
        components = list(mesh.split(only_watertight=False))
    except Exception:
        return 1.0
    if not components:
        return 0.0
    total_faces = max(sum(len(component.faces) for component in components), 1)
    largest_faces = max(len(component.faces) for component in components)
    return float(largest_faces / total_faces)


def _compute_open_surface_metrics(
    *,
    mesh,
    initial_area: float,
    initial_extents: np.ndarray,
) -> tuple[dict[str, float], dict[str, float | int]]:
    final_area = float(getattr(mesh, "area", 0.0))
    final_extents = _mesh_extents(mesh)
    largest_component_ratio = _largest_component_ratio(mesh)
    surface_area_retention = float(final_area / max(initial_area, 1e-6)) if initial_area > 1e-6 else 1.0
    extent_retention = np.clip(final_extents / np.maximum(initial_extents, 1e-6), 0.0, 10.0)
    boundary_stats = _analyze_boundary_topology(
        vertices=np.asarray(mesh.vertices, dtype=np.float64),
        faces=np.asarray(mesh.faces, dtype=np.int64),
    )
    min_extent_retention = float(np.min(extent_retention)) if extent_retention.size > 0 else 0.0
    balling_score = float(
        max(0.0, 1.0 - min_extent_retention) * 0.7
        + max(0.0, 1.0 - largest_component_ratio) * 0.3
    )
    metrics = {
        "largest_component_ratio": round(largest_component_ratio, 5),
        "surface_area_retention": round(surface_area_retention, 5),
        "extent_retention_x": round(float(extent_retention[0]), 5),
        "extent_retention_y": round(float(extent_retention[1]), 5),
        "extent_retention_z": round(float(extent_retention[2]), 5),
        "min_extent_retention": round(min_extent_retention, 5),
        "boundary_edge_ratio": round(float(boundary_stats["boundary_edge_ratio"]), 5),
        "balling_score": round(balling_score, 5),
    }
    return metrics, boundary_stats


def _write_open_surface_hq_report(
    *,
    ctx: JobContext,
    mesh,
    initial_area: float,
    initial_extents: np.ndarray,
) -> None:
    metrics, _boundary_stats = _compute_open_surface_metrics(
        mesh=mesh,
        initial_area=initial_area,
        initial_extents=initial_extents,
    )
    thresholds = {
        "min_largest_component_ratio": float(config.open_surface_hq_min_largest_component_ratio),
        "min_surface_area_retention": float(config.open_surface_hq_min_surface_area_retention),
        "max_surface_area_retention": float(config.open_surface_hq_max_surface_area_retention),
        "min_extent_retention": float(config.open_surface_hq_min_extent_retention),
        "max_boundary_edge_ratio": float(config.open_surface_hq_max_boundary_edge_ratio),
        "max_balling_score": float(config.open_surface_hq_max_balling_score),
    }
    failed_metrics: list[str] = []
    if metrics["largest_component_ratio"] < thresholds["min_largest_component_ratio"]:
        failed_metrics.append("largest_component_ratio")
    if metrics["surface_area_retention"] < thresholds["min_surface_area_retention"]:
        failed_metrics.append("surface_area_retention_low")
    if metrics["surface_area_retention"] > thresholds["max_surface_area_retention"]:
        failed_metrics.append("surface_area_retention_high")
    if metrics["min_extent_retention"] < thresholds["min_extent_retention"]:
        failed_metrics.append("min_extent_retention")
    if metrics["boundary_edge_ratio"] > thresholds["max_boundary_edge_ratio"]:
        failed_metrics.append("boundary_edge_ratio")
    if metrics["balling_score"] > thresholds["max_balling_score"]:
        failed_metrics.append("balling_score")
    update_quality_card(
        ctx,
        card_id="open_surface_hq",
        title="Open Surface HQ",
        metrics=metrics,
        thresholds=thresholds,
        failed_metrics=failed_metrics,
        notes=[
            "HQ-only gate for open-surface retention.",
            "Protects against collapse, over-smoothing, and balling.",
        ],
    )


def _write_sheetness_hq_report(
    *,
    ctx: JobContext,
    mesh,
    initial_area: float,
    initial_extents: np.ndarray,
) -> dict[str, float]:
    open_surface_metrics, _boundary_stats = _compute_open_surface_metrics(
        mesh=mesh,
        initial_area=initial_area,
        initial_extents=initial_extents,
    )
    balling_score = float(open_surface_metrics["balling_score"])
    self_intersection_ratio = float(_estimate_self_intersection_ratio(mesh))
    local_sheet_branch_stats = _estimate_local_sheet_branch_stats(mesh)
    metrics = {
        "balling_score": round(balling_score, 5),
        "self_intersection_ratio": round(self_intersection_ratio, 5),
        "local_sheet_branch_count_mean": round(float(local_sheet_branch_stats["mean"]), 5),
        "local_sheet_branch_count_p95": round(float(local_sheet_branch_stats["p95"]), 5),
        "local_sheet_branch_sample_count": int(local_sheet_branch_stats["sample_count"]),
    }
    thresholds = {
        "max_balling_score": float(config.open_surface_hq_max_balling_score),
        "max_self_intersection_ratio": float(config.sheetness_hq_max_self_intersection_ratio),
        "warn_local_sheet_branch_count_p95": float(config.sheetness_hq_warn_local_sheet_branch_count_p95),
    }
    failed_metrics: list[str] = []
    if metrics["balling_score"] > thresholds["max_balling_score"]:
        failed_metrics.append("balling_score")
    if metrics["self_intersection_ratio"] > thresholds["max_self_intersection_ratio"]:
        failed_metrics.append("self_intersection_ratio")
    notes = [
        "HQ-only gate for sheet-like open-surface behavior.",
        "Hard-gates against balling and self-intersection; local multi-layer tendency is logged first as an observation signal.",
    ]
    if metrics["local_sheet_branch_count_p95"] > thresholds["warn_local_sheet_branch_count_p95"]:
        notes.append(
            "Observation warning: local_sheet_branch_count_p95 exceeds the current calibration band and should be reviewed against the candidate artifact."
        )
    update_quality_card(
        ctx,
        card_id="sheetness_hq",
        title="Sheetness HQ",
        metrics=metrics,
        thresholds=thresholds,
        failed_metrics=failed_metrics,
        notes=notes,
    )
    return {
        "balling_score": float(metrics["balling_score"]),
        "self_intersection_ratio": float(metrics["self_intersection_ratio"]),
        "local_sheet_branch_count_mean": float(metrics["local_sheet_branch_count_mean"]),
        "local_sheet_branch_count_p95": float(metrics["local_sheet_branch_count_p95"]),
    }


def _write_mesh_fidelity_hq_report(
    *,
    ctx: JobContext,
    reference_mesh,
    working_summary: dict[str, Any],
    optimized_mesh,
) -> dict[str, float]:
    metrics = _compute_mesh_fidelity_metrics(reference_mesh=reference_mesh, candidate_mesh=optimized_mesh)
    metrics = {
        "reference_faces": int(len(reference_mesh.faces)),
        "working_faces": int(working_summary.get("working_faces", len(reference_mesh.faces))),
        "optimized_faces": int(len(optimized_mesh.faces)),
        "working_mesh_applied": bool(working_summary.get("applied", False)),
        "working_surface_area_retention": round(float(working_summary.get("surface_area_retention", 1.0)), 5),
        "working_min_extent_retention": round(float(working_summary.get("min_extent_retention", 1.0)), 5),
        "surface_area_retention": round(float(metrics["surface_area_retention"]), 5),
        "extent_retention_x": round(float(metrics["extent_retention_x"]), 5),
        "extent_retention_y": round(float(metrics["extent_retention_y"]), 5),
        "extent_retention_z": round(float(metrics["extent_retention_z"]), 5),
        "min_extent_retention": round(float(metrics["min_extent_retention"]), 5),
        "vertex_distance_mean_ratio": round(float(metrics["vertex_distance_mean_ratio"]), 5),
        "vertex_distance_p95_ratio": round(float(metrics["vertex_distance_p95_ratio"]), 5),
    }
    thresholds = {
        "min_surface_area_retention": float(config.mesh_fidelity_hq_min_surface_area_retention),
        "max_surface_area_retention": float(config.mesh_fidelity_hq_max_surface_area_retention),
        "min_extent_retention": float(config.mesh_fidelity_hq_min_extent_retention),
        "max_vertex_distance_mean_ratio": float(config.mesh_fidelity_hq_max_vertex_distance_mean_ratio),
        "max_vertex_distance_p95_ratio": float(config.mesh_fidelity_hq_max_vertex_distance_p95_ratio),
    }
    failed_metrics: list[str] = []
    if metrics["surface_area_retention"] < thresholds["min_surface_area_retention"]:
        failed_metrics.append("surface_area_retention_low")
    if metrics["surface_area_retention"] > thresholds["max_surface_area_retention"]:
        failed_metrics.append("surface_area_retention_high")
    if metrics["min_extent_retention"] < thresholds["min_extent_retention"]:
        failed_metrics.append("min_extent_retention")
    if metrics["vertex_distance_mean_ratio"] > thresholds["max_vertex_distance_mean_ratio"]:
        failed_metrics.append("vertex_distance_mean_ratio")
    if metrics["vertex_distance_p95_ratio"] > thresholds["max_vertex_distance_p95_ratio"]:
        failed_metrics.append("vertex_distance_p95_ratio")
    update_quality_card(
        ctx,
        card_id="mesh_fidelity_hq",
        title="Mesh Fidelity HQ",
        metrics=metrics,
        thresholds=thresholds,
        failed_metrics=failed_metrics,
        notes=[
            "HQ-only gate for raw-vs-optimized mesh fidelity.",
            "Rejects aggressive simplification or optimization that damages scale, silhouette, or surface support.",
        ],
    )
    return {
        "surface_area_retention": float(metrics["surface_area_retention"]),
        "min_extent_retention": float(metrics["min_extent_retention"]),
        "vertex_distance_mean_ratio": float(metrics["vertex_distance_mean_ratio"]),
        "vertex_distance_p95_ratio": float(metrics["vertex_distance_p95_ratio"]),
    }


def _write_hole_fill_hq_report(
    *,
    ctx: JobContext,
    initial_faces: int,
    topology_summary: dict[str, Any],
) -> None:
    boundary_before = float(topology_summary.get("boundary_length_before", 0.0))
    boundary_after = float(topology_summary.get("boundary_length_after", 0.0))
    boundary_length_drop_ratio = (
        max(0.0, (boundary_before - boundary_after) / max(boundary_before, 1e-6))
        if boundary_before > 1e-6
        else 0.0
    )
    faces_added = int(topology_summary.get("faces_added_by_hole_fill", 0))
    metrics = {
        "small_holes_filled_count": int(topology_summary.get("small_holes_filled_count", 0)),
        "large_holes_skipped_count": int(topology_summary.get("large_hole_candidates_before", 0)),
        "max_filled_hole_perimeter_ratio": round(float(topology_summary.get("max_filled_hole_perimeter_ratio", 0.0)), 5),
        "fill_added_face_ratio": round(float(faces_added / max(initial_faces, 1)), 5),
        "boundary_length_drop_ratio": round(float(boundary_length_drop_ratio), 5),
    }
    thresholds = {
        "max_filled_hole_perimeter_ratio": float(config.hole_fill_hq_max_filled_hole_perimeter_ratio),
        "max_fill_added_face_ratio": float(config.hole_fill_hq_max_fill_added_face_ratio),
        "max_boundary_length_drop_ratio": float(config.hole_fill_hq_max_boundary_length_drop_ratio),
    }
    failed_metrics: list[str] = []
    if metrics["max_filled_hole_perimeter_ratio"] > thresholds["max_filled_hole_perimeter_ratio"]:
        failed_metrics.append("max_filled_hole_perimeter_ratio")
    if metrics["fill_added_face_ratio"] > thresholds["max_fill_added_face_ratio"]:
        failed_metrics.append("fill_added_face_ratio")
    if metrics["boundary_length_drop_ratio"] > thresholds["max_boundary_length_drop_ratio"]:
        failed_metrics.append("boundary_length_drop_ratio")
    update_quality_card(
        ctx,
        card_id="hole_fill_hq",
        title="Hole Fill HQ",
        metrics=metrics,
        thresholds=thresholds,
        failed_metrics=failed_metrics,
        notes=[
            "HQ-only gate for restrained hole filling.",
            "Allows skipping large holes but rejects aggressive closure.",
        ],
    )


def _write_texture_hq_report(*, ctx: JobContext, summary: dict[str, Any]) -> None:
    face_count = max(int(summary.get("face_count", 0)), 1)
    photo_projected_face_ratio = float(summary.get("atlas_photo_projected_face_count", 0)) / face_count
    fallback_face_ratio = float(summary.get("atlas_fallback_face_count", 0)) / face_count
    metrics = {
        "projected_view_count": int(summary.get("projected_view_count", 0)),
        "atlas_coverage_ratio": round(float(summary.get("atlas_coverage_ratio", 0.0)), 5),
        "photo_projected_face_ratio": round(photo_projected_face_ratio, 5),
        "fallback_face_ratio": round(fallback_face_ratio, 5),
        "neighbor_view_disagreement": round(float(summary.get("neighbor_view_disagreement", 0.0)), 5),
        "low_saturation_texel_ratio": round(float(summary.get("low_saturation_texel_ratio", 0.0)), 5),
    }
    thresholds = {
        "min_projected_view_count": int(config.texture_hq_min_projected_views),
        "min_atlas_coverage_ratio": float(config.texture_hq_min_atlas_coverage_ratio),
        "min_photo_projected_face_ratio": float(config.texture_hq_min_photo_projected_face_ratio),
        "max_fallback_face_ratio": float(config.texture_hq_max_fallback_face_ratio),
        "max_neighbor_view_disagreement": float(config.texture_hq_max_neighbor_view_disagreement),
        "max_low_saturation_texel_ratio": float(config.texture_hq_max_low_saturation_texel_ratio),
    }
    failed_metrics: list[str] = []
    if metrics["projected_view_count"] < thresholds["min_projected_view_count"]:
        failed_metrics.append("projected_view_count")
    if metrics["atlas_coverage_ratio"] < thresholds["min_atlas_coverage_ratio"]:
        failed_metrics.append("atlas_coverage_ratio")
    if metrics["photo_projected_face_ratio"] < thresholds["min_photo_projected_face_ratio"]:
        failed_metrics.append("photo_projected_face_ratio")
    if metrics["fallback_face_ratio"] > thresholds["max_fallback_face_ratio"]:
        failed_metrics.append("fallback_face_ratio")
    if metrics["neighbor_view_disagreement"] > thresholds["max_neighbor_view_disagreement"]:
        failed_metrics.append("neighbor_view_disagreement")
    if metrics["low_saturation_texel_ratio"] > thresholds["max_low_saturation_texel_ratio"]:
        failed_metrics.append("low_saturation_texel_ratio")
    update_quality_card(
        ctx,
        card_id="texture_hq",
        title="Texture HQ",
        metrics=metrics,
        thresholds=thresholds,
        failed_metrics=failed_metrics,
        notes=[
            "HQ-only gate for visible photo projection stability.",
            "Rejects low photo coverage, excessive fallback, and unstable seam assignment.",
        ],
    )


def _reduce_self_intersections(
    mesh,
    *,
    progress_callback: OptimizeProgressCallback | None = None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
):
    summary = {
        "applied": False,
        "removed_faces": 0,
        "added_faces": 0,
        "patched_hotspots": 0,
        "initial_ratio": 0.0,
        "final_ratio": 0.0,
        "reverted": False,
    }
    if not bool(config.delivery_self_intersection_cleanup_enabled):
        return mesh, summary

    current_mesh = mesh
    best_mesh = _copy_mesh_geometry(mesh)
    initial_ratio, initial_face_indices = _estimate_self_intersection_faces(best_mesh)
    best_ratio = float(initial_ratio)
    base_max_passes = max(1, int(config.delivery_self_intersection_cleanup_max_passes))
    trigger_ratio = max(0.0, float(config.delivery_self_intersection_cleanup_trigger_ratio))
    extra_passes = 0
    if initial_ratio >= max(trigger_ratio * 8.0, 0.45):
        extra_passes = 2
    elif initial_ratio >= max(trigger_ratio * 4.0, 0.20):
        extra_passes = 1
    max_passes = base_max_passes + extra_passes
    summary["initial_ratio"] = float(initial_ratio)

    ratio = float(initial_ratio)
    face_indices = set(initial_face_indices)
    for pass_index in range(max_passes):
        if pass_index > 0:
            ratio, face_indices = _estimate_self_intersection_faces(current_mesh)
        if ratio <= trigger_ratio or not face_indices:
            summary["final_ratio"] = float(ratio)
            if not summary["applied"]:
                _emit_optimize_progress(
                    progress_callback,
                    progress=progress_end,
                    title="表面穿插检查通过",
                    detail="当前网格没有明显自相交，继续进入最终拓扑修补。",
                    metrics={
                        "optimize_phase": "reduce_self_intersections_skip",
                        "self_intersection_ratio": f"{ratio:.5f}",
                },
            )
            break

        if not bool(config.delivery_local_patch_surgery_enabled):
            summary["final_ratio"] = best_ratio
            break

        _emit_optimize_progress(
            progress_callback,
            progress=progress_start + (progress_end - progress_start) * (pass_index / max(max_passes, 1)),
            title=f"正在重建局部缠绕热点 {pass_index + 1}/{max_passes}",
            detail="检测到持续性的薄层重叠热点，正在只对局部 patch 做 split / retriangulate / refine。",
            metrics={
                "optimize_phase": "reduce_self_intersections",
                "self_intersection_ratio": f"{ratio:.5f}",
                "cleanup_pass": str(pass_index + 1),
                "cleanup_total_passes": str(max_passes),
            },
        )

        hotspots = _identify_self_intersection_hotspots(current_mesh)
        if not hotspots:
            summary["final_ratio"] = best_ratio
            break

        improved = False
        current_best_mesh = _copy_mesh_geometry(current_mesh)
        current_best_ratio = float(best_ratio)
        for hotspot in hotspots[: max(1, int(config.delivery_local_patch_surgery_max_hotspots))]:
            trial_mesh, patch_summary = _repair_self_intersection_hotspot_patch(
                current_best_mesh,
                hotspot=hotspot,
            )
            if not bool(patch_summary.get("applied", False)):
                continue
            face_delta = int(patch_summary.get("face_delta", 0))
            if face_delta < 0:
                continue
            fidelity = _compute_mesh_fidelity_metrics(
                reference_mesh=current_best_mesh,
                candidate_mesh=trial_mesh,
            )
            current_open_metrics, _ = _compute_open_surface_metrics(
                mesh=current_best_mesh,
                initial_area=float(getattr(current_best_mesh, "area", 0.0)),
                initial_extents=_mesh_extents(current_best_mesh),
            )
            trial_open_metrics, _ = _compute_open_surface_metrics(
                mesh=trial_mesh,
                initial_area=float(getattr(current_best_mesh, "area", 0.0)),
                initial_extents=_mesh_extents(current_best_mesh),
            )
            surface_area_retention = float(fidelity["surface_area_retention"])
            min_extent_retention = float(fidelity["min_extent_retention"])
            if (
                surface_area_retention < float(config.delivery_local_patch_surgery_min_surface_area_retention)
                or surface_area_retention > float(config.delivery_local_patch_surgery_max_surface_area_retention)
                or min_extent_retention < float(config.delivery_local_patch_surgery_min_extent_retention)
            ):
                continue
            if (
                float(trial_open_metrics["largest_component_ratio"]) + 1e-6
                < float(current_open_metrics["largest_component_ratio"]) - 0.01
            ):
                continue
            if float(trial_open_metrics["boundary_edge_ratio"]) > max(
                float(config.open_surface_hq_max_boundary_edge_ratio),
                float(current_open_metrics["boundary_edge_ratio"]) + 0.01,
            ):
                continue
            if float(trial_open_metrics["balling_score"]) > max(
                float(config.open_surface_hq_max_balling_score),
                float(current_open_metrics["balling_score"]) + 0.01,
            ):
                continue
            trial_ratio = float(_estimate_self_intersection_ratio(trial_mesh))
            min_improvement = max(1e-4, float(config.delivery_local_patch_surgery_min_ratio_improvement))
            if trial_ratio > current_best_ratio - min_improvement:
                continue
            current_best_mesh = _copy_mesh_geometry(trial_mesh)
            current_best_ratio = trial_ratio
            summary["applied"] = True
            summary["added_faces"] += int(patch_summary.get("added_patch_faces", max(face_delta, 0)))
            summary["removed_faces"] += int(patch_summary.get("removed_patch_faces", 0))
            summary["patched_hotspots"] += 1
            summary["final_ratio"] = trial_ratio
            improved = True

        if improved:
            current_mesh = current_best_mesh
            best_mesh = _copy_mesh_geometry(current_best_mesh)
            best_ratio = current_best_ratio
            continue

        summary["reverted"] = True
        current_mesh = _copy_mesh_geometry(best_mesh)
        summary["final_ratio"] = best_ratio
        break

    _emit_optimize_progress(
        progress_callback,
        progress=progress_end,
        title="表面穿插修正完成",
        detail=(
            f"已完成局部热点重建，当前估计自相交比 {summary['final_ratio']:.5f}，"
            f"累计重建 {summary['patched_hotspots']} 个热点 patch，新增 {summary['added_faces']} 个局部三角面。"
        ),
        metrics={
            "optimize_phase": "reduce_self_intersections_done",
            "self_intersection_ratio": f"{summary['final_ratio']:.5f}",
            "removed_faces": str(summary["removed_faces"]),
            "added_faces": str(summary["added_faces"]),
            "patched_hotspots": str(summary["patched_hotspots"]),
            "cleanup_applied": str(bool(summary["applied"])).lower(),
        },
    )
    return current_mesh, summary


def _collect_self_intersection_pair_samples(mesh, *, sample_cap: int | None = None) -> dict[str, Any]:
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or faces.ndim != 2 or faces.shape[1] != 3 or len(faces) < 2:
        return {
            "diagonal": 0.0,
            "pairs": [],
            "sample_face_count": int(len(faces)),
        }

    extents = _mesh_extents(mesh)
    diagonal = max(float(np.linalg.norm(extents)), 1e-6)
    requested_cap = int(sample_cap or config.delivery_local_patch_surgery_sample_cap)
    requested_cap = max(256, requested_cap)
    if len(faces) <= requested_cap:
        sample_face_indices = np.arange(len(faces), dtype=np.int64)
    else:
        sample_face_indices = np.linspace(0, len(faces) - 1, num=requested_cap, dtype=np.int64)

    sampled_faces = faces[sample_face_indices]
    triangles = vertices[sampled_faces]
    centroids = triangles.mean(axis=1)
    aabb_min = triangles.min(axis=1)
    aabb_max = triangles.max(axis=1)
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normal_norms = np.linalg.norm(normals, axis=1)
    valid_normals = normal_norms > 1e-8
    normals[valid_normals] /= normal_norms[valid_normals][:, None]
    plane_tolerance = max(diagonal * 0.0015, 1e-5)
    radii = np.max(np.linalg.norm(triangles - centroids[:, None, :], axis=2), axis=1)
    query_radius = max(float(np.median(radii) * 2.5), diagonal * 0.03)
    boundary_vertices = _boundary_vertex_mask(faces, vertex_count=len(vertices))
    sampled_boundary = np.any(boundary_vertices[sampled_faces], axis=1)
    face_areas = np.asarray(getattr(mesh, "area_faces", np.zeros(len(faces))), dtype=np.float64)
    area_ratios = face_areas[sample_face_indices] / max(diagonal * diagonal, 1e-8)

    try:
        from scipy.spatial import cKDTree

        tree = cKDTree(centroids)
        neighborhoods = tree.query_ball_point(centroids, query_radius, workers=1)
    except Exception:
        neighborhoods = []
        for index in range(len(sample_face_indices)):
            delta = centroids - centroids[index]
            distances = np.linalg.norm(delta, axis=1)
            neighborhoods.append(np.flatnonzero(distances <= query_radius).tolist())

    pair_samples: list[dict[str, Any]] = []
    for local_i, candidate_indices in enumerate(neighborhoods):
        if not bool(valid_normals[local_i]):
            continue
        face_i = sampled_faces[local_i]
        tri_i = triangles[local_i]
        centroid_i = centroids[local_i]
        normal_i = normals[local_i]
        for local_j in candidate_indices:
            if local_j <= local_i or not bool(valid_normals[local_j]):
                continue
            face_j = sampled_faces[local_j]
            if np.intersect1d(face_i, face_j).size > 0:
                continue
            if not _aabb_overlap(
                aabb_min[local_i],
                aabb_max[local_i],
                aabb_min[local_j],
                aabb_max[local_j],
                pad=plane_tolerance,
            ):
                continue
            tri_j = triangles[local_j]
            centroid_j = centroids[local_j]
            if not (
                _point_near_triangle(centroid_i, tri_j, plane_tolerance)
                or _point_near_triangle(centroid_j, tri_i, plane_tolerance)
            ):
                continue
            normal_j = normals[local_j]
            mean_normal = normal_i + normal_j
            mean_normal_norm = float(np.linalg.norm(mean_normal))
            if mean_normal_norm <= 1e-8:
                mean_normal = normal_i
                mean_normal_norm = max(float(np.linalg.norm(mean_normal)), 1e-8)
            mean_normal = mean_normal / mean_normal_norm
            centroid_delta = centroid_j - centroid_i
            pair_samples.append(
                {
                    "face_i": int(sample_face_indices[local_i]),
                    "face_j": int(sample_face_indices[local_j]),
                    "midpoint": ((centroid_i + centroid_j) * 0.5).astype(np.float64),
                    "normal_cosine": float(np.dot(normal_i, normal_j)),
                    "plane_gap_ratio": float(abs(np.dot(centroid_delta, mean_normal)) / diagonal),
                    "centroid_distance_ratio": float(np.linalg.norm(centroid_delta) / diagonal),
                    "area_ratio_i": float(area_ratios[local_i]),
                    "area_ratio_j": float(area_ratios[local_j]),
                    "boundary_i": bool(sampled_boundary[local_i]),
                    "boundary_j": bool(sampled_boundary[local_j]),
                }
            )

    return {
        "diagonal": float(diagonal),
        "pairs": pair_samples,
        "sample_face_count": int(len(sample_face_indices)),
    }


def _identify_self_intersection_hotspots(mesh) -> list[dict[str, Any]]:
    pair_data = _collect_self_intersection_pair_samples(
        mesh,
        sample_cap=int(config.delivery_local_patch_surgery_sample_cap),
    )
    pairs = list(pair_data.get("pairs", []))
    diagonal = max(float(pair_data.get("diagonal", 0.0)), 1e-6)
    if not pairs:
        return []

    points = np.vstack([np.asarray(pair["midpoint"], dtype=np.float64) for pair in pairs])
    cluster_radius = max(
        diagonal * max(float(config.delivery_local_patch_surgery_cluster_radius_ratio), 1e-4),
        1e-5,
    )
    try:
        from scipy.spatial import cKDTree

        tree = cKDTree(points)
        neighborhoods = tree.query_ball_point(points, cluster_radius, workers=1)
    except Exception:
        neighborhoods = []
        for index in range(len(points)):
            delta = points - points[index]
            distances = np.linalg.norm(delta, axis=1)
            neighborhoods.append(np.flatnonzero(distances <= cluster_radius).tolist())

    visited = np.zeros(len(points), dtype=bool)
    clusters: list[list[int]] = []
    for start in range(len(points)):
        if visited[start]:
            continue
        frontier = [int(start)]
        visited[start] = True
        members: list[int] = []
        while frontier:
            index = frontier.pop()
            members.append(index)
            for neighbor in neighborhoods[index]:
                if not visited[neighbor]:
                    visited[neighbor] = True
                    frontier.append(int(neighbor))
        clusters.append(members)

    hotspots: list[dict[str, Any]] = []
    min_pair_count = max(1, int(config.delivery_local_patch_surgery_min_pair_count))
    max_boundary_ratio = max(0.0, float(config.delivery_local_patch_surgery_max_boundary_ratio))
    max_plane_gap_ratio = max(0.0, float(config.delivery_local_patch_surgery_max_plane_gap_ratio))
    for cluster_id, members in enumerate(sorted(clusters, key=len, reverse=True)):
        cluster_pairs = [pairs[index] for index in members]
        pair_count = len(cluster_pairs)
        if pair_count < min_pair_count:
            continue
        cluster_points = np.vstack([np.asarray(pair["midpoint"], dtype=np.float64) for pair in cluster_pairs])
        unique_face_indices = sorted(
            {
                int(face_index)
                for pair in cluster_pairs
                for face_index in (int(pair["face_i"]), int(pair["face_j"]))
            }
        )
        boundary_flags = np.asarray(
            [
                bool(flag)
                for pair in cluster_pairs
                for flag in (pair["boundary_i"], pair["boundary_j"])
            ],
            dtype=bool,
        )
        normal_cosines = np.asarray([float(pair["normal_cosine"]) for pair in cluster_pairs], dtype=np.float64)
        plane_gaps = np.asarray([float(pair["plane_gap_ratio"]) for pair in cluster_pairs], dtype=np.float64)
        centroid_distances = np.asarray(
            [float(pair["centroid_distance_ratio"]) for pair in cluster_pairs],
            dtype=np.float64,
        )
        boundary_ratio = float(np.mean(boundary_flags)) if boundary_flags.size else 0.0
        plane_gap_p95 = float(np.percentile(plane_gaps, 95)) if plane_gaps.size else 0.0
        if boundary_ratio > max_boundary_ratio or plane_gap_p95 > max_plane_gap_ratio:
            continue
        hotspots.append(
            {
                "cluster_id": int(cluster_id),
                "pair_count": int(pair_count),
                "unique_face_count": int(len(unique_face_indices)),
                "centroid_world": cluster_points.mean(axis=0).astype(np.float64),
                "seed_face_indices": unique_face_indices,
                "pair_samples": cluster_pairs,
                "boundary_face_ratio": float(boundary_ratio),
                "parallel_pair_ratio": float(np.mean(np.abs(normal_cosines) >= 0.85)) if normal_cosines.size else 0.0,
                "plane_gap_ratio_p95": float(plane_gap_p95),
                "centroid_distance_ratio_p95": float(np.percentile(centroid_distances, 95))
                if centroid_distances.size
                else 0.0,
            }
        )
        if len(hotspots) >= max(1, int(config.delivery_local_patch_surgery_max_hotspots)):
            break
    return hotspots


def _expand_face_patch(
    seed_face_indices: list[int],
    *,
    adjacency: list[list[int]],
    max_rings: int,
    max_faces: int,
) -> list[int]:
    if not seed_face_indices:
        return []
    patch: set[int] = {int(index) for index in seed_face_indices if 0 <= int(index) < len(adjacency)}
    frontier = list(sorted(patch))
    if len(patch) >= max_faces:
        return sorted(list(patch))[:max_faces]
    for _ in range(max(0, max_rings)):
        if not frontier or len(patch) >= max_faces:
            break
        next_frontier: list[int] = []
        for face_index in frontier:
            for neighbor in adjacency[face_index]:
                if neighbor in patch:
                    continue
                patch.add(int(neighbor))
                next_frontier.append(int(neighbor))
                if len(patch) >= max_faces:
                    return sorted(patch)
        frontier = next_frontier
    return sorted(patch)


def _select_connected_patch_component(
    face_indices: list[int],
    *,
    adjacency: list[list[int]],
    anchor_face_index: int,
) -> list[int]:
    patch_set = {int(index) for index in face_indices}
    if not patch_set or int(anchor_face_index) not in patch_set:
        return []
    visited: set[int] = set()
    frontier = [int(anchor_face_index)]
    visited.add(int(anchor_face_index))
    component: list[int] = []
    while frontier:
        face_index = frontier.pop()
        component.append(int(face_index))
        for neighbor in adjacency[face_index]:
            if neighbor in patch_set and neighbor not in visited:
                visited.add(int(neighbor))
                frontier.append(int(neighbor))
    return sorted(component)


def _pair_anchor_seed_faces(
    *,
    hotspot: dict[str, Any],
    anchor_face_index: int,
    seed_face_set: set[int],
    max_pairs: int = 6,
) -> list[int]:
    hotspot_centroid = np.asarray(
        hotspot.get("centroid_world", np.zeros(3, dtype=np.float64)),
        dtype=np.float64,
    )
    seeds: set[int] = {int(anchor_face_index)}
    ranked_pairs = sorted(
        list(hotspot.get("pair_samples", [])),
        key=lambda pair: (
            float(np.linalg.norm(np.asarray(pair["midpoint"], dtype=np.float64) - hotspot_centroid)),
            float(pair.get("plane_gap_ratio", 0.0)),
            float(pair.get("centroid_distance_ratio", 0.0)),
        ),
    )
    for pair in ranked_pairs[: max(1, int(max_pairs))]:
        face_i = int(pair["face_i"])
        face_j = int(pair["face_j"])
        if face_i in seed_face_set:
            seeds.add(face_i)
        if face_j in seed_face_set:
            seeds.add(face_j)
    return sorted(seeds)


def _signed_area_2d(points_2d: np.ndarray) -> float:
    if len(points_2d) < 3:
        return 0.0
    x = points_2d[:, 0]
    y = points_2d[:, 1]
    return float(0.5 * np.sum(x * np.roll(y, -1) - y * np.roll(x, -1)))


def _project_points_to_plane(
    points: np.ndarray,
    *,
    hint_normal: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if points.ndim != 2 or points.shape[1] != 3 or len(points) == 0:
        raise RuntimeError("local_patch_empty_points")
    origin = points.mean(axis=0)
    centered = points - origin
    try:
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        basis_u = vh[0]
        basis_v = vh[1]
        normal = vh[-1]
    except np.linalg.LinAlgError as exc:
        raise RuntimeError("local_patch_plane_fit_failed") from exc
    if hint_normal is not None:
        hint = np.asarray(hint_normal, dtype=np.float64)
        hint_norm = float(np.linalg.norm(hint))
        if hint_norm > 1e-8:
            hint = hint / hint_norm
            if float(np.dot(normal, hint)) < 0.0:
                normal = -normal
    normal_norm = float(np.linalg.norm(normal))
    if normal_norm <= 1e-8:
        raise RuntimeError("local_patch_plane_normal_invalid")
    normal = normal / normal_norm
    basis_u = basis_u - normal * float(np.dot(basis_u, normal))
    basis_u_norm = float(np.linalg.norm(basis_u))
    if basis_u_norm <= 1e-8:
        helper = np.asarray([1.0, 0.0, 0.0], dtype=np.float64)
        if abs(float(np.dot(helper, normal))) > 0.9:
            helper = np.asarray([0.0, 1.0, 0.0], dtype=np.float64)
        basis_u = helper - normal * float(np.dot(helper, normal))
        basis_u_norm = max(float(np.linalg.norm(basis_u)), 1e-8)
    basis_u = basis_u / basis_u_norm
    basis_v = np.cross(normal, basis_u)
    basis_v_norm = max(float(np.linalg.norm(basis_v)), 1e-8)
    basis_v = basis_v / basis_v_norm
    points_2d = np.column_stack([centered @ basis_u, centered @ basis_v]).astype(np.float64)
    return origin, basis_u, basis_v, normal, points_2d


def _polygon_from_boundary_loops_2d(
    *,
    loop_vertex_ids: list[list[int]],
    projected_vertices_2d: np.ndarray,
) -> tuple[Any | None, set[int]]:
    try:
        from shapely.geometry import Point, Polygon
    except Exception:
        return None, set()

    loop_payloads: list[dict[str, Any]] = []
    boundary_ids: set[int] = set()
    for loop in loop_vertex_ids:
        local_ids = [int(index) for index in loop]
        if len(local_ids) < 3:
            continue
        coords = projected_vertices_2d[np.asarray(local_ids, dtype=np.int64)]
        dedup_coords: list[np.ndarray] = []
        dedup_ids: list[int] = []
        for vertex_id, coord in zip(local_ids, coords):
            if dedup_coords and np.linalg.norm(coord - dedup_coords[-1]) <= 1e-8:
                continue
            dedup_coords.append(np.asarray(coord, dtype=np.float64))
            dedup_ids.append(int(vertex_id))
        if len(dedup_coords) >= 2 and np.linalg.norm(dedup_coords[0] - dedup_coords[-1]) <= 1e-8:
            dedup_coords = dedup_coords[:-1]
            dedup_ids = dedup_ids[:-1]
        if len(dedup_coords) < 3:
            continue
        coords_array = np.asarray(dedup_coords, dtype=np.float64)
        signed_area = _signed_area_2d(coords_array)
        if abs(signed_area) <= 1e-10:
            continue
        boundary_ids.update(dedup_ids)
        loop_payloads.append(
            {
                "vertex_ids": dedup_ids,
                "coords": coords_array,
                "abs_area": abs(float(signed_area)),
            }
        )

    if not loop_payloads:
        return None, set()

    loop_payloads.sort(key=lambda item: item["abs_area"], reverse=True)
    shell = loop_payloads[0]
    polygon = Polygon(shell["coords"])
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.is_empty:
        return None, boundary_ids
    if getattr(polygon, "geom_type", "") == "MultiPolygon":
        polygon = max(list(polygon.geoms), key=lambda geom: float(geom.area))

    holes: list[np.ndarray] = []
    shell_probe = polygon.buffer(max(float(np.linalg.norm(np.ptp(projected_vertices_2d, axis=0))) * 1e-6, 1e-8))
    for payload in loop_payloads[1:]:
        probe = Point(payload["coords"][0])
        if shell_probe.contains(probe):
            holes.append(payload["coords"])
    candidate = Polygon(shell["coords"], holes=holes)
    if not candidate.is_valid:
        candidate = candidate.buffer(0)
    if candidate.is_empty:
        return None, boundary_ids
    if getattr(candidate, "geom_type", "") == "MultiPolygon":
        candidate = max(list(candidate.geoms), key=lambda geom: float(geom.area))
    return candidate, boundary_ids


def _deduplicate_patch_point_records(
    point_records: list[dict[str, Any]],
    *,
    min_spacing: float,
) -> list[dict[str, Any]]:
    if not point_records:
        return []
    sorted_records = sorted(
        point_records,
        key=lambda record: (
            0 if record.get("boundary", False) else 1,
            0 if record.get("fixed", False) else 1,
            0 if record.get("global_id") is not None else 1,
        ),
    )
    accepted: list[dict[str, Any]] = []
    for record in sorted_records:
        coord = np.asarray(record["coord_2d"], dtype=np.float64)
        duplicate = False
        for existing in accepted:
            if np.linalg.norm(coord - np.asarray(existing["coord_2d"], dtype=np.float64)) <= min_spacing:
                duplicate = True
                break
        if not duplicate:
            accepted.append(record)
    return accepted


def _triangulate_patch_points_2d(
    *,
    point_records: list[dict[str, Any]],
    polygon,
    tolerance: float,
) -> list[tuple[int, int, int]]:
    try:
        from scipy.spatial import Delaunay
        from shapely.geometry import Polygon as _Polygon
    except Exception:
        return []

    if len(point_records) < 3 or polygon is None:
        return []
    coords_2d = np.vstack([np.asarray(record["coord_2d"], dtype=np.float64) for record in point_records])
    try:
        delaunay = Delaunay(coords_2d)
    except Exception:
        return []

    inflated_polygon = polygon.buffer(max(float(tolerance), 1e-8))
    faces: list[tuple[int, int, int]] = []
    seen: set[tuple[int, int, int]] = set()
    for simplex in np.asarray(delaunay.simplices, dtype=np.int64):
        triangle = coords_2d[simplex]
        area_twice = abs(
            float(
                (triangle[1, 0] - triangle[0, 0]) * (triangle[2, 1] - triangle[0, 1])
                - (triangle[1, 1] - triangle[0, 1]) * (triangle[2, 0] - triangle[0, 0])
            )
        )
        if area_twice <= max(float(tolerance), 1e-12):
            continue
        tri_poly = _Polygon(triangle)
        if tri_poly.is_empty or tri_poly.area <= max(float(tolerance), 1e-12):
            continue
        if not inflated_polygon.covers(tri_poly):
            continue
        key = tuple(sorted(int(index) for index in simplex.tolist()))
        if key in seen:
            continue
        seen.add(key)
        faces.append(tuple(int(index) for index in simplex.tolist()))
    return faces


def _solve_patch_point_heights(
    *,
    point_records: list[dict[str, Any]],
    patch_faces_local: list[tuple[int, int, int]],
) -> np.ndarray:
    point_count = len(point_records)
    heights = np.asarray(
        [float(record.get("height", 0.0)) for record in point_records],
        dtype=np.float64,
    )
    if point_count == 0:
        return heights

    adjacency: list[set[int]] = [set() for _ in range(point_count)]
    for face in patch_faces_local:
        a, b, c = (int(face[0]), int(face[1]), int(face[2]))
        adjacency[a].update((b, c))
        adjacency[b].update((a, c))
        adjacency[c].update((a, b))

    fixed_indices = [index for index, record in enumerate(point_records) if bool(record.get("fixed", False))]
    fixed_index_set = set(fixed_indices)
    unknown_indices = [index for index in range(point_count) if index not in fixed_index_set]
    if not unknown_indices:
        return heights

    index_map = {old_index: new_index for new_index, old_index in enumerate(unknown_indices)}
    system_size = len(unknown_indices)
    matrix = np.zeros((system_size, system_size), dtype=np.float64)
    rhs = np.zeros(system_size, dtype=np.float64)
    for old_index in unknown_indices:
        row = index_map[old_index]
        neighbors = sorted(adjacency[old_index])
        if not neighbors:
            matrix[row, row] = 1.0
            rhs[row] = heights[old_index]
            continue
        matrix[row, row] = float(len(neighbors))
        for neighbor in neighbors:
            if neighbor in index_map:
                matrix[row, index_map[neighbor]] -= 1.0
            else:
                rhs[row] += heights[neighbor]
    try:
        solved = np.linalg.solve(matrix, rhs)
    except np.linalg.LinAlgError:
        solved, *_ = np.linalg.lstsq(matrix, rhs, rcond=None)
    for old_index, value in zip(unknown_indices, solved.tolist()):
        heights[old_index] = float(value)
    return heights


def _assign_patch_face_branches(
    *,
    hotspot_pair_samples: list[dict[str, Any]],
    patch_face_lookup: dict[int, int],
    patch_face_count: int,
    patch_adjacency: list[list[int]],
    face_centroids: np.ndarray,
    hotspot_centroid: np.ndarray,
) -> np.ndarray:
    branch_signs = np.zeros(patch_face_count, dtype=np.float64)
    pair_graph: dict[int, set[int]] = {}
    touched_faces: set[int] = set()
    for pair in hotspot_pair_samples:
        face_i = patch_face_lookup.get(int(pair["face_i"]))
        face_j = patch_face_lookup.get(int(pair["face_j"]))
        if face_i is None or face_j is None or face_i == face_j:
            continue
        pair_graph.setdefault(face_i, set()).add(face_j)
        pair_graph.setdefault(face_j, set()).add(face_i)
        touched_faces.add(face_i)
        touched_faces.add(face_j)

    visited: set[int] = set()
    ordered_roots = sorted(
        touched_faces,
        key=lambda index: float(np.linalg.norm(face_centroids[index] - hotspot_centroid)),
    )
    for root in ordered_roots:
        if root in visited:
            continue
        queue = [int(root)]
        visited.add(int(root))
        branch_signs[int(root)] = 1.0
        while queue:
            face_index = queue.pop(0)
            current_sign = branch_signs[face_index] if abs(branch_signs[face_index]) > 1e-8 else 1.0
            for neighbor in sorted(pair_graph.get(face_index, set())):
                expected = -current_sign
                if abs(branch_signs[neighbor]) <= 1e-8:
                    branch_signs[neighbor] = expected
                elif np.sign(branch_signs[neighbor]) != np.sign(expected):
                    branch_signs[neighbor] = expected
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(int(neighbor))

    frontier = [index for index in range(patch_face_count) if abs(branch_signs[index]) > 1e-8]
    frontier_set = set(frontier)
    while frontier:
        face_index = frontier.pop(0)
        current_sign = branch_signs[face_index]
        for neighbor in patch_adjacency[face_index]:
            if abs(branch_signs[neighbor]) > 1e-8:
                continue
            branch_signs[neighbor] = current_sign
            if neighbor not in frontier_set:
                frontier.append(int(neighbor))
                frontier_set.add(int(neighbor))
    return branch_signs


def _smooth_patch_displacements(
    *,
    seed_displacements: np.ndarray,
    vertex_neighbors: list[np.ndarray],
    movable_mask: np.ndarray,
    preserve_mask: np.ndarray,
    iterations: int = 6,
) -> np.ndarray:
    current = np.asarray(seed_displacements, dtype=np.float64).copy()
    for _ in range(max(0, int(iterations))):
        updated = current.copy()
        for vertex_index in np.flatnonzero(movable_mask).tolist():
            if bool(preserve_mask[vertex_index]):
                continue
            neighbor_indices = vertex_neighbors[vertex_index]
            if neighbor_indices.size == 0:
                continue
            neighbor_mean = current[neighbor_indices].mean(axis=0)
            updated[vertex_index] = 0.55 * current[vertex_index] + 0.45 * neighbor_mean
        current = updated
    return current


def _untangle_hotspot_patch(
    *,
    mesh,
    hotspot: dict[str, Any],
    vertices: np.ndarray,
    faces: np.ndarray,
    patch_face_indices_array: np.ndarray,
    patch_vertex_ids: np.ndarray,
    local_faces: np.ndarray,
    boundary_vertex_mask: np.ndarray,
    kept_vertex_ids: set[int],
) -> tuple[Any | None, dict[str, Any]]:
    summary = {
        "applied": False,
        "reason": "untangle_noop",
        "face_delta": 0,
        "removed_patch_faces": 0,
        "added_patch_faces": 0,
        "added_vertices": 0,
        "moved_vertices": 0,
        "patch_face_count": int(len(patch_face_indices_array)),
        "new_patch_face_count": int(len(patch_face_indices_array)),
        "hotspot_pair_count": int(hotspot.get("pair_count", 0)),
        "seed_face_count": int(len(hotspot.get("seed_face_indices", []))),
        "patch_vertex_count": int(len(patch_vertex_ids)),
        "boundary_loop_count": 0,
        "boundary_vertex_count": int(np.count_nonzero(boundary_vertex_mask)),
        "point_record_count": int(len(patch_vertex_ids)),
        "polygon_area": 0.0,
        "untangle_offset": 0.0,
        "positive_face_count": 0,
        "negative_face_count": 0,
    }
    if len(patch_face_indices_array) <= 0 or len(patch_vertex_ids) <= 0:
        summary["reason"] = "empty_patch"
        return None, summary

    try:
        import trimesh
    except Exception:
        summary["reason"] = "trimesh_missing"
        return None, summary

    patch_vertices = vertices[patch_vertex_ids]
    patch_face_lookup = {int(global_face): local_index for local_index, global_face in enumerate(patch_face_indices_array.tolist())}
    patch_face_normals = _face_normals(patch_vertices, local_faces)
    patch_face_centroids = np.mean(patch_vertices[local_faces], axis=1)
    patch_face_areas = np.linalg.norm(
        np.cross(
            patch_vertices[local_faces][:, 1] - patch_vertices[local_faces][:, 0],
            patch_vertices[local_faces][:, 2] - patch_vertices[local_faces][:, 0],
        ),
        axis=1,
    ) * 0.5
    patch_adjacency = _face_adjacency(local_faces)
    hotspot_centroid = np.asarray(hotspot.get("centroid_world", np.mean(patch_face_centroids, axis=0)), dtype=np.float64)
    branch_signs = _assign_patch_face_branches(
        hotspot_pair_samples=list(hotspot.get("pair_samples", [])),
        patch_face_lookup=patch_face_lookup,
        patch_face_count=len(local_faces),
        patch_adjacency=patch_adjacency,
        face_centroids=patch_face_centroids,
        hotspot_centroid=hotspot_centroid,
    )
    positive_face_count = int(np.count_nonzero(branch_signs > 0))
    negative_face_count = int(np.count_nonzero(branch_signs < 0))
    summary["positive_face_count"] = positive_face_count
    summary["negative_face_count"] = negative_face_count
    if positive_face_count <= 0 or negative_face_count <= 0:
        summary["reason"] = "missing_branch_split"
        return None, summary

    movable_mask = ~boundary_vertex_mask
    if kept_vertex_ids:
        shared_with_outer = np.asarray([int(global_id) in kept_vertex_ids for global_id in patch_vertex_ids.tolist()], dtype=bool)
        movable_mask &= ~shared_with_outer
    if not np.any(movable_mask):
        summary["reason"] = "no_movable_vertices"
        return None, summary

    incident_faces: list[list[int]] = [[] for _ in range(len(patch_vertex_ids))]
    for face_index, face in enumerate(local_faces.tolist()):
        for vertex_index in face:
            incident_faces[int(vertex_index)].append(int(face_index))

    vertex_seed_displacements = np.zeros((len(patch_vertex_ids), 3), dtype=np.float64)
    vertex_preserve_mask = np.zeros(len(patch_vertex_ids), dtype=bool)
    vertex_normals = np.zeros((len(patch_vertex_ids), 3), dtype=np.float64)
    pair_force_accum = np.zeros((len(patch_vertex_ids), 3), dtype=np.float64)
    pair_force_weight = np.zeros(len(patch_vertex_ids), dtype=np.float64)
    for pair in hotspot.get("pair_samples", []):
        face_i = patch_face_lookup.get(int(pair["face_i"]))
        face_j = patch_face_lookup.get(int(pair["face_j"]))
        if face_i is None or face_j is None or face_i == face_j:
            continue
        centroid_i = patch_face_centroids[face_i]
        centroid_j = patch_face_centroids[face_j]
        normal_i = patch_face_normals[face_i]
        normal_j = patch_face_normals[face_j]
        dir_i = normal_i.copy()
        dir_j = normal_j.copy()
        if float(np.dot(dir_i, centroid_j - centroid_i)) > 0.0:
            dir_i = -dir_i
        if float(np.dot(dir_j, centroid_i - centroid_j)) > 0.0:
            dir_j = -dir_j
        if float(np.linalg.norm(dir_i)) <= 1e-8 or float(np.linalg.norm(dir_j)) <= 1e-8:
            continue
        pair_weight = max(
            0.25,
            1.0 - float(pair.get("plane_gap_ratio", hotspot.get("plane_gap_ratio_p95", 0.0)))
            / max(float(hotspot.get("plane_gap_ratio_p95", 0.0)), 1e-6),
        )
        for vertex_index in local_faces[face_i].tolist():
            pair_force_accum[int(vertex_index)] += dir_i * pair_weight
            pair_force_weight[int(vertex_index)] += pair_weight
        for vertex_index in local_faces[face_j].tolist():
            pair_force_accum[int(vertex_index)] += dir_j * pair_weight
            pair_force_weight[int(vertex_index)] += pair_weight
    for local_vertex_index, face_indices in enumerate(incident_faces):
        if not face_indices:
            continue
        local_normals = patch_face_normals[np.asarray(face_indices, dtype=np.int64)]
        local_areas = patch_face_areas[np.asarray(face_indices, dtype=np.int64)]
        averaged_normal = np.sum(local_normals * local_areas[:, None], axis=0)
        averaged_normal_norm = float(np.linalg.norm(averaged_normal))
        if averaged_normal_norm > 1e-8:
            vertex_normals[local_vertex_index] = averaged_normal / averaged_normal_norm
        local_branch_values = branch_signs[np.asarray(face_indices, dtype=np.int64)]
        branch_scalar = float(np.mean(local_branch_values)) if np.count_nonzero(local_branch_values) > 0 else 0.0
        pair_force = pair_force_accum[local_vertex_index]
        pair_force_norm = float(np.linalg.norm(pair_force))
        if pair_force_weight[local_vertex_index] > 0.0 and pair_force_norm > 1e-8:
            direction = pair_force / pair_force_norm
            strength = min(1.0, float(pair_force_weight[local_vertex_index]) / 3.0)
            vertex_seed_displacements[local_vertex_index] = direction * strength
            continue
        if abs(branch_scalar) < 0.10:
            continue
        signed_normal = np.sum(local_normals * local_areas[:, None] * local_branch_values[:, None], axis=0)
        signed_norm = float(np.linalg.norm(signed_normal))
        if signed_norm > 1e-8:
            direction = signed_normal / signed_norm
        elif float(np.linalg.norm(vertex_normals[local_vertex_index])) > 1e-8:
            direction = vertex_normals[local_vertex_index] * (1.0 if branch_scalar >= 0.0 else -1.0)
        else:
            continue
        vertex_seed_displacements[local_vertex_index] = direction * abs(branch_scalar)

    seeded_vertex_count = int(np.count_nonzero(np.linalg.norm(vertex_seed_displacements, axis=1) > 1e-8))
    if seeded_vertex_count <= 0:
        summary["reason"] = "no_seed_displacements"
        return None, summary

    vertex_neighbors = _vertex_neighbors(local_faces, vertex_count=len(patch_vertex_ids))
    smoothed_displacements = _smooth_patch_displacements(
        seed_displacements=vertex_seed_displacements,
        vertex_neighbors=vertex_neighbors,
        movable_mask=movable_mask,
        preserve_mask=vertex_preserve_mask | (~movable_mask),
        iterations=6,
    )
    smoothed_norms = np.linalg.norm(smoothed_displacements, axis=1)
    valid_norms = smoothed_norms[np.flatnonzero(smoothed_norms > 1e-8)]
    if valid_norms.size <= 0:
        summary["reason"] = "untangle_displacement_zero"
        return None, summary

    mesh_diagonal = max(float(np.linalg.norm(_mesh_extents(mesh))), 1e-6)
    base_offset = max(
        float(hotspot.get("plane_gap_ratio_p95", 0.0)) * mesh_diagonal * 0.85,
        mesh_diagonal * 1e-5,
    )
    summary["untangle_offset"] = float(base_offset)
    displacement_scale = base_offset / max(float(np.percentile(valid_norms, 75)), 1e-8)
    updated_patch_vertices = patch_vertices.copy()
    moved_vertices = 0
    max_offset = base_offset * 1.25
    for local_vertex_index in np.flatnonzero(movable_mask).tolist():
        direction = smoothed_displacements[local_vertex_index]
        direction_norm = float(np.linalg.norm(direction))
        if direction_norm <= 1e-8:
            continue
        offset = direction * displacement_scale
        offset_norm = float(np.linalg.norm(offset))
        if offset_norm > max_offset:
            offset = offset / offset_norm * max_offset
        updated_patch_vertices[local_vertex_index] = patch_vertices[local_vertex_index] + offset
        moved_vertices += 1

    if moved_vertices <= 0:
        summary["reason"] = "no_vertices_moved"
        return None, summary

    updated_vertices = vertices.copy()
    updated_vertices[patch_vertex_ids] = updated_patch_vertices
    updated_mesh = trimesh.Trimesh(
        vertices=updated_vertices,
        faces=faces.copy(),
        process=False,
    )
    try:
        updated_mesh.remove_unreferenced_vertices()
    except Exception:
        pass
    updated_mesh = _drop_degenerate_faces(updated_mesh)
    summary.update(
        {
            "applied": True,
            "reason": "collision_driven_untangle",
            "moved_vertices": int(moved_vertices),
        }
    )
    return updated_mesh, summary


def _repair_self_intersection_hotspot_patch(mesh, *, hotspot: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    summary = {
        "applied": False,
        "reason": "noop",
        "face_delta": 0,
        "removed_patch_faces": 0,
        "added_patch_faces": 0,
        "added_vertices": 0,
        "moved_vertices": 0,
        "patch_face_count": 0,
        "new_patch_face_count": 0,
        "hotspot_pair_count": int(hotspot.get("pair_count", 0)),
        "seed_face_count": 0,
        "patch_vertex_count": 0,
        "boundary_loop_count": 0,
        "boundary_vertex_count": 0,
        "point_record_count": 0,
        "polygon_area": 0.0,
        "untangle_offset": 0.0,
    }
    try:
        import trimesh
    except Exception:
        summary["reason"] = "trimesh_missing"
        return mesh, summary

    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    seed_face_indices = [int(index) for index in hotspot.get("seed_face_indices", []) if 0 <= int(index) < len(faces)]
    if len(seed_face_indices) < 3:
        summary["reason"] = "insufficient_seed_faces"
        return mesh, summary
    summary["seed_face_count"] = int(len(seed_face_indices))

    adjacency = _face_adjacency(faces)
    hotspot_centroid = np.asarray(
        hotspot.get("centroid_world", np.zeros(3, dtype=np.float64)),
        dtype=np.float64,
    )
    face_centroids = np.mean(vertices[faces[np.asarray(seed_face_indices, dtype=np.int64)]], axis=1)
    anchor_seed_local_index = int(
        np.argmin(np.linalg.norm(face_centroids - hotspot_centroid[None, :], axis=1))
    )
    anchor_face_index = int(seed_face_indices[anchor_seed_local_index])
    seed_face_set = {int(index) for index in seed_face_indices}
    connected_seed_component = _select_connected_patch_component(
        seed_face_indices,
        adjacency=adjacency,
        anchor_face_index=anchor_face_index,
    )
    pair_anchor_faces = _pair_anchor_seed_faces(
        hotspot=hotspot,
        anchor_face_index=anchor_face_index,
        seed_face_set=seed_face_set,
        max_pairs=6,
    )
    patch_face_indices = _expand_face_patch(
        sorted(set(connected_seed_component or [anchor_face_index]) | set(pair_anchor_faces)),
        adjacency=adjacency,
        max_rings=max(0, int(config.delivery_local_patch_surgery_patch_expansion_rings)),
        max_faces=max(32, int(config.delivery_local_patch_surgery_max_patch_faces)),
    )
    if len(patch_face_indices) < 8:
        summary["reason"] = "patch_too_small"
        return mesh, summary

    patch_face_indices_array = np.asarray(sorted(patch_face_indices), dtype=np.int64)
    patch_faces = faces[patch_face_indices_array]
    patch_vertex_ids = np.unique(patch_faces.reshape(-1))
    global_to_local = {int(global_id): local_id for local_id, global_id in enumerate(patch_vertex_ids.tolist())}
    local_faces = np.asarray(
        [[global_to_local[int(vertex_id)] for vertex_id in face] for face in patch_faces.tolist()],
        dtype=np.int64,
    )
    local_vertices = vertices[patch_vertex_ids]
    local_boundary_mask = _boundary_vertex_mask(local_faces, vertex_count=len(patch_vertex_ids))
    kept_face_mask = np.ones(len(faces), dtype=bool)
    kept_face_mask[patch_face_indices_array] = False
    kept_vertex_ids = set(np.unique(faces[kept_face_mask].reshape(-1)).tolist()) if np.any(kept_face_mask) else set()
    untangle_mesh, untangle_summary = _untangle_hotspot_patch(
        mesh=mesh,
        hotspot=hotspot,
        vertices=vertices,
        faces=faces,
        patch_face_indices_array=patch_face_indices_array,
        patch_vertex_ids=patch_vertex_ids,
        local_faces=local_faces,
        boundary_vertex_mask=local_boundary_mask,
        kept_vertex_ids=kept_vertex_ids,
    )
    if untangle_mesh is not None and bool(untangle_summary.get("applied", False)):
        return untangle_mesh, untangle_summary

    boundary_loops = _extract_boundary_loops(local_vertices, local_faces)
    summary["patch_face_count"] = int(len(patch_face_indices_array))
    summary["patch_vertex_count"] = int(len(patch_vertex_ids))
    summary["boundary_loop_count"] = int(len(boundary_loops))
    if not boundary_loops:
        summary["reason"] = "patch_missing_boundary"
        return mesh, summary

    patch_face_normals = _face_normals(local_vertices, local_faces)
    patch_face_areas = np.asarray(getattr(mesh, "area_faces", np.zeros(len(faces))), dtype=np.float64)[patch_face_indices_array]
    weighted_normal = np.sum(patch_face_normals * patch_face_areas[:, None], axis=0)
    if float(np.linalg.norm(weighted_normal)) <= 1e-8:
        weighted_normal = patch_face_normals.mean(axis=0)
    plane_origin, basis_u, basis_v, plane_normal, patch_vertices_2d = _project_points_to_plane(
        local_vertices,
        hint_normal=weighted_normal,
    )

    polygon, boundary_local_id_set = _polygon_from_boundary_loops_2d(
        loop_vertex_ids=[list(loop["vertices"]) for loop in boundary_loops],
        projected_vertices_2d=patch_vertices_2d,
    )
    if polygon is None or float(getattr(polygon, "area", 0.0)) <= 1e-10:
        summary["reason"] = "patch_polygon_invalid"
        return mesh, summary
    summary["boundary_vertex_count"] = int(len(boundary_local_id_set))
    summary["polygon_area"] = float(getattr(polygon, "area", 0.0))

    point_records: list[dict[str, Any]] = []
    moved_global_ids: set[int] = set()
    for local_id, global_id in enumerate(patch_vertex_ids.tolist()):
        global_id = int(global_id)
        is_boundary = int(local_id) in boundary_local_id_set
        is_fixed = bool(is_boundary or global_id in kept_vertex_ids)
        original_position = vertices[global_id]
        original_height = float(np.dot(original_position - plane_origin, plane_normal))
        point_records.append(
            {
                "coord_2d": patch_vertices_2d[local_id],
                "coord_3d": np.asarray(original_position, dtype=np.float64),
                "height": float(original_height),
                "global_id": global_id,
                "boundary": bool(is_boundary),
                "fixed": bool(is_fixed),
            }
        )
        if not is_fixed:
            moved_global_ids.add(global_id)

    hotspot_midpoints = [
        np.asarray(pair["midpoint"], dtype=np.float64)
        for pair in hotspot.get("pair_samples", [])
    ]
    refine_points_world = hotspot_midpoints + [patch_faces_vertices.mean(axis=0) for patch_faces_vertices in vertices[patch_faces]]
    projected_refine_points: list[np.ndarray] = []
    for point in refine_points_world:
        projected = np.asarray(
            [
                float(np.dot(point - plane_origin, basis_u)),
                float(np.dot(point - plane_origin, basis_v)),
            ],
            dtype=np.float64,
        )
        projected_refine_points.append(projected)

    min_spacing = max(float(np.linalg.norm(_mesh_extents(mesh))) * 1e-5, 1e-6)
    try:
        from shapely.geometry import Point
    except Exception:
        Point = None  # type: ignore[assignment]
    if Point is not None:
        for projected in projected_refine_points:
            if not polygon.buffer(max(min_spacing * 4.0, 1e-8)).contains(Point(projected)):
                continue
            point_records.append(
                {
                    "coord_2d": projected,
                    "coord_3d": plane_origin + basis_u * projected[0] + basis_v * projected[1],
                    "height": 0.0,
                    "global_id": None,
                    "boundary": False,
                    "fixed": False,
                }
            )

    point_records = _deduplicate_patch_point_records(point_records, min_spacing=min_spacing)
    summary["point_record_count"] = int(len(point_records))
    new_patch_faces_local = _triangulate_patch_points_2d(
        point_records=point_records,
        polygon=polygon,
        tolerance=max(min_spacing * 0.1, 1e-8),
    )
    if len(new_patch_faces_local) < 3:
        summary["reason"] = "patch_triangulation_failed"
        return mesh, summary

    updated_vertices = vertices.copy()
    new_vertices: list[np.ndarray] = []
    point_global_ids: list[int] = []
    mesh_diagonal = max(float(np.linalg.norm(_mesh_extents(mesh))), 1e-6)
    nonfixed_heights = [
        float(record.get("height", 0.0))
        for record in point_records
        if not bool(record.get("fixed", False)) and record.get("global_id") is not None
    ]
    branch_sign = 1.0
    if nonfixed_heights:
        median_height = float(np.median(np.asarray(nonfixed_heights, dtype=np.float64)))
        if abs(median_height) > 1e-8:
            branch_sign = 1.0 if median_height >= 0.0 else -1.0
    untangle_offset = (
        branch_sign
        * max(float(hotspot.get("plane_gap_ratio_p95", 0.0)) * mesh_diagonal * 0.5, mesh_diagonal * 1e-5)
    )
    summary["untangle_offset"] = float(untangle_offset)
    solved_heights = _solve_patch_point_heights(
        point_records=point_records,
        patch_faces_local=new_patch_faces_local,
    )
    next_global_id = int(len(updated_vertices))
    for point_index, record in enumerate(point_records):
        global_id = record.get("global_id")
        if bool(record.get("fixed", False)) and global_id is not None:
            coord_3d = np.asarray(record["coord_3d"], dtype=np.float64)
        else:
            coord_2d = np.asarray(record["coord_2d"], dtype=np.float64)
            coord_3d = (
                plane_origin
                + basis_u * coord_2d[0]
                + basis_v * coord_2d[1]
                + plane_normal * float(solved_heights[point_index] + untangle_offset)
            )
        if global_id is None:
            new_vertices.append(coord_3d)
            point_global_ids.append(int(next_global_id))
            next_global_id += 1
            continue
        global_id = int(global_id)
        if global_id in moved_global_ids:
            updated_vertices[global_id] = coord_3d
        point_global_ids.append(global_id)
    if new_vertices:
        updated_vertices = np.vstack([updated_vertices, np.asarray(new_vertices, dtype=np.float64)])

    patch_normal_reference = weighted_normal
    if float(np.linalg.norm(patch_normal_reference)) <= 1e-8:
        patch_normal_reference = plane_normal
    patch_normal_reference = patch_normal_reference / max(float(np.linalg.norm(patch_normal_reference)), 1e-8)

    rebuilt_patch_faces: list[list[int]] = []
    for tri_local in new_patch_faces_local:
        tri_global = [int(point_global_ids[int(index)]) for index in tri_local]
        if len(set(tri_global)) < 3:
            continue
        tri_vertices = updated_vertices[np.asarray(tri_global, dtype=np.int64)]
        tri_normal = np.cross(tri_vertices[1] - tri_vertices[0], tri_vertices[2] - tri_vertices[0])
        if float(np.linalg.norm(tri_normal)) <= 1e-10:
            continue
        if float(np.dot(tri_normal, patch_normal_reference)) < 0.0:
            tri_global = [tri_global[0], tri_global[2], tri_global[1]]
        rebuilt_patch_faces.append(tri_global)
    if len(rebuilt_patch_faces) < 3:
        summary["reason"] = "patch_faces_degenerate"
        return mesh, summary

    kept_faces = faces[kept_face_mask]
    rebuilt_faces = np.vstack([kept_faces, np.asarray(rebuilt_patch_faces, dtype=np.int64)])
    rebuilt_mesh = trimesh.Trimesh(
        vertices=updated_vertices,
        faces=rebuilt_faces,
        process=False,
    )
    try:
        rebuilt_mesh.remove_unreferenced_vertices()
    except Exception:
        pass
    rebuilt_mesh = _drop_degenerate_faces(rebuilt_mesh)
    if len(rebuilt_mesh.faces) <= 0:
        summary["reason"] = "rebuilt_mesh_empty"
        return mesh, summary

    added_patch_faces = int(len(rebuilt_patch_faces))
    removed_patch_faces = int(len(patch_face_indices_array))
    summary.update(
        {
            "applied": True,
            "reason": "patch_rebuilt",
            "face_delta": int(added_patch_faces - removed_patch_faces),
            "removed_patch_faces": removed_patch_faces,
            "added_patch_faces": added_patch_faces,
            "added_vertices": int(len(new_vertices)),
            "moved_vertices": int(len(moved_global_ids)),
            "patch_face_count": removed_patch_faces,
            "new_patch_face_count": added_patch_faces,
        }
    )
    return rebuilt_mesh, summary


def _estimate_local_sheet_branch_stats(mesh) -> dict[str, float]:
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) == 0:
        return {"mean": 0.0, "p95": 0.0, "sample_count": 0.0}

    normals = np.array(getattr(mesh, "vertex_normals", np.zeros_like(vertices)), dtype=np.float64, copy=True)
    if normals.shape != vertices.shape:
        normals = np.zeros_like(vertices, dtype=np.float64)
    normal_norms = np.linalg.norm(normals, axis=1)
    valid_normals = normal_norms > 1e-8
    normals[valid_normals] /= normal_norms[valid_normals][:, None]

    extents = _mesh_extents(mesh)
    diagonal = max(float(np.linalg.norm(extents)), 1e-6)
    radius = max(diagonal * 0.02, 1e-4)
    band = max(radius * 0.35, diagonal * 0.003)
    support_floor = 3

    sample_cap = 2048
    if len(vertices) <= sample_cap:
        sample_indices = np.arange(len(vertices), dtype=np.int64)
    else:
        sample_indices = np.linspace(0, len(vertices) - 1, num=sample_cap, dtype=np.int64)

    try:
        from scipy.spatial import cKDTree

        tree = cKDTree(vertices)
        neighborhoods = [tree.query_ball_point(vertices[index], radius, workers=1) for index in sample_indices]
    except Exception:
        neighborhoods = []
        reduced = vertices
        for index in sample_indices:
            delta = reduced - vertices[index]
            distances = np.linalg.norm(delta, axis=1)
            neighborhoods.append(np.flatnonzero(distances <= radius).tolist())

    branch_counts: list[float] = []
    for sample_index, neighbor_indices in zip(sample_indices, neighborhoods):
        if len(neighbor_indices) < 6 or not valid_normals[sample_index]:
            branch_counts.append(1.0)
            continue
        offsets = np.dot(vertices[np.asarray(neighbor_indices, dtype=np.int64)] - vertices[sample_index], normals[sample_index])
        offsets = np.sort(offsets.astype(np.float64))
        if offsets.size == 0:
            branch_counts.append(1.0)
            continue
        clusters: list[list[float]] = [[float(offsets[0])]]
        for value in offsets[1:]:
            if abs(float(value) - float(np.mean(clusters[-1]))) <= band:
                clusters[-1].append(float(value))
            else:
                clusters.append([float(value)])
        min_support = max(support_floor, int(len(neighbor_indices) * 0.08))
        supported_clusters = [cluster for cluster in clusters if len(cluster) >= min_support]
        branch_counts.append(float(max(1, len(supported_clusters))))

    if not branch_counts:
        return {"mean": 0.0, "p95": 0.0, "sample_count": 0.0}
    counts = np.asarray(branch_counts, dtype=np.float64)
    return {
        "mean": float(np.mean(counts)),
        "p95": float(np.percentile(counts, 95)),
        "sample_count": float(len(counts)),
    }


def _copy_mesh_geometry(mesh):
    try:
        return mesh.copy()
    except Exception:
        try:
            import trimesh

            return trimesh.Trimesh(
                vertices=np.asarray(mesh.vertices, dtype=np.float64).copy(),
                faces=np.asarray(mesh.faces, dtype=np.int64).copy(),
                process=False,
            )
        except Exception:
            return mesh


def _estimate_self_intersection_ratio(mesh) -> float:
    ratio, _ = _estimate_self_intersection_faces(mesh)
    return float(ratio)


def _estimate_self_intersection_faces(mesh) -> tuple[float, set[int]]:
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or faces.ndim != 2 or faces.shape[1] != 3 or len(faces) < 2:
        return 0.0, set()

    base_sample_cap = max(4096, int(config.delivery_multilayer_prune_sample_cap))
    scaled_sample_cap = int(max(base_sample_cap, min(len(faces), max(8192, len(faces) * 0.08))))
    sample_cap = min(len(faces), min(max(scaled_sample_cap, base_sample_cap), 32768))
    if len(faces) <= sample_cap:
        sample_indices = np.arange(len(faces), dtype=np.int64)
    else:
        sample_indices = np.linspace(0, len(faces) - 1, num=sample_cap, dtype=np.int64)

    sampled_faces = faces[sample_indices]
    triangles = vertices[sampled_faces]
    centroids = np.mean(triangles, axis=1)
    aabb_min = np.min(triangles, axis=1)
    aabb_max = np.max(triangles, axis=1)
    extents = _mesh_extents(mesh)
    diagonal = max(float(np.linalg.norm(extents)), 1e-6)
    plane_tolerance = max(diagonal * 0.0015, 1e-5)
    radii = np.max(np.linalg.norm(triangles - centroids[:, None, :], axis=2), axis=1)
    query_radius = max(float(np.median(radii) * 2.5), diagonal * 0.03)

    try:
        from scipy.spatial import cKDTree

        tree = cKDTree(centroids)
        neighborhoods = [tree.query_ball_point(centroids[index], query_radius, workers=1) for index in range(len(sample_indices))]
    except Exception:
        neighborhoods = []
        for index in range(len(sample_indices)):
            delta = centroids - centroids[index]
            distances = np.linalg.norm(delta, axis=1)
            neighborhoods.append(np.flatnonzero(distances <= query_radius).tolist())

    intersecting_faces: set[int] = set()
    for local_index, candidate_indices in enumerate(neighborhoods):
        face_i = sampled_faces[local_index]
        tri_i = triangles[local_index]
        centroid_i = centroids[local_index]
        for other_local_index in candidate_indices:
            if other_local_index <= local_index:
                continue
            face_j = sampled_faces[other_local_index]
            if np.intersect1d(face_i, face_j).size > 0:
                continue
            if not _aabb_overlap(aabb_min[local_index], aabb_max[local_index], aabb_min[other_local_index], aabb_max[other_local_index], pad=plane_tolerance):
                continue
            tri_j = triangles[other_local_index]
            centroid_j = centroids[other_local_index]
            if _point_near_triangle(centroid_i, tri_j, plane_tolerance) or _point_near_triangle(centroid_j, tri_i, plane_tolerance):
                intersecting_faces.add(local_index)
                intersecting_faces.add(other_local_index)
    actual_face_indices = {int(sample_indices[index]) for index in intersecting_faces}
    ratio = float(len(intersecting_faces) / max(len(sample_indices), 1))
    return ratio, actual_face_indices


def _aabb_overlap(
    a_min: np.ndarray,
    a_max: np.ndarray,
    b_min: np.ndarray,
    b_max: np.ndarray,
    *,
    pad: float,
) -> bool:
    return bool(np.all((a_min - pad) <= (b_max + pad)) and np.all((b_min - pad) <= (a_max + pad)))


def _point_near_triangle(point: np.ndarray, triangle: np.ndarray, plane_tolerance: float) -> bool:
    a = triangle[0]
    b = triangle[1]
    c = triangle[2]
    normal = np.cross(b - a, c - a)
    normal_norm = float(np.linalg.norm(normal))
    if normal_norm <= 1e-10:
        return False
    unit_normal = normal / normal_norm
    signed_distance = float(np.dot(point - a, unit_normal))
    if abs(signed_distance) > plane_tolerance:
        return False
    projected = point - signed_distance * unit_normal
    edge_ab = np.dot(np.cross(b - a, projected - a), unit_normal)
    edge_bc = np.dot(np.cross(c - b, projected - b), unit_normal)
    edge_ca = np.dot(np.cross(a - c, projected - c), unit_normal)
    tolerance = max(plane_tolerance, 1e-8)
    return bool(edge_ab >= -tolerance and edge_bc >= -tolerance and edge_ca >= -tolerance)
