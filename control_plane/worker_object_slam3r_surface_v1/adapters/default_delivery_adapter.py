from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..config import config
from ..context import JobContext


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
        title="正在读取默认网格",
        detail="正在载入 MAtCha 默认 mesh，并检查原始拓扑规模。",
        metrics={"optimize_phase": "load_matcha_mesh"},
    )
    raw_mesh = _resolve_matcha_mesh_asset(ctx.matcha_dir)
    mesh = _load_mesh(raw_mesh)
    initial_faces = int(len(mesh.faces))
    initial_vertices = int(len(mesh.vertices))

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

    _emit_optimize_progress(
        progress_callback,
        progress=0.15,
        title="正在清理碎片",
        detail="正在去掉极小碎片，只保留主连通表面。",
        metrics={"optimize_phase": "drop_small_components"},
    )
    mesh = _drop_small_components(mesh)
    _emit_optimize_progress(
        progress_callback,
        progress=0.22,
        title="正在清理退化三角面",
        detail="正在清理退化面和重复面，避免后续修补时把坏拓扑继续放大。",
        metrics={"optimize_phase": "drop_degenerate_faces"},
    )
    mesh = _drop_degenerate_faces(mesh)
    mesh, topology_summary = _repair_mesh_topology(
        mesh,
        progress_callback=progress_callback,
        progress_start=0.24,
        progress_end=0.46,
        phase_prefix="first_topology_repair",
    )
    mesh = _smooth_mesh(
        mesh,
        progress_callback=progress_callback,
        progress_start=0.46,
        progress_end=0.70,
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
    _emit_optimize_progress(
        progress_callback,
        progress=0.78,
        title="正在评估默认预算",
        detail="正在判断是否需要为了移动端默认成品做受控降面，优先保几何覆盖。",
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
        mesh = _simplify_mesh(
            mesh,
            target_faces=_simplify_target_faces(initial_faces, target_faces=target_faces),
        )
        simplification_applied = len(mesh.faces) < initial_faces
        simplify_reason = "hard_face_cap" if initial_faces > config.delivery_hard_max_face_count else "target_budget"
    else:
        _emit_optimize_progress(
            progress_callback,
            progress=0.82,
            title="默认预算无需降面",
            detail="当前网格仍在默认预算内，继续保几何并进入最终拓扑修整。",
            metrics={"optimize_phase": "preserve_geometry_budget"},
        )

    mesh, final_topology_summary = _repair_mesh_topology(
        mesh,
        progress_callback=progress_callback,
        progress_start=0.84,
        progress_end=0.94,
        phase_prefix="final_topology_repair",
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
    }

    destination = ctx.delivery_dir / "optimized_mesh.ply"
    destination.parent.mkdir(parents=True, exist_ok=True)
    _emit_optimize_progress(
        progress_callback,
        progress=0.97,
        title="正在写出优化网格",
        detail="正在把优化后的默认 mesh 写盘，并生成交付摘要。",
        metrics={"optimize_phase": "export_optimized_mesh"},
    )
    mesh.export(destination)

    summary = {
        "source_mesh": str(raw_mesh),
        "optimized_mesh": str(destination),
        "initial_faces": initial_faces,
        "initial_vertices": initial_vertices,
        "optimized_faces": int(len(mesh.faces)),
        "optimized_vertices": int(len(mesh.vertices)),
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
    }
    (ctx.delivery_dir / config.delivery_mesh_summary_filename).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _emit_optimize_progress(
        progress_callback,
        progress=1.0,
        title="默认网格优化完成",
        detail=(
            f"默认网格优化完成，当前 {len(mesh.faces):,} 面、{len(mesh.vertices):,} 顶点，"
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


def bake_default_texture_delivery(ctx: JobContext) -> Path:
    assert ctx.delivery_dir is not None
    assert ctx.sparse2dgs_scene_dir is not None

    optimized_mesh = _resolve_optimized_mesh_asset(ctx.delivery_dir)
    mesh = _load_mesh(optimized_mesh)

    cameras = _read_cameras(ctx.sparse2dgs_scene_dir / "sparse" / "0" / "cameras.txt")
    poses = _read_images(ctx.sparse2dgs_scene_dir / "sparse" / "0" / "images.txt")
    if not cameras or not poses:
        raise RuntimeError("delivery_texture_contract_missing")

    projected_views = _sample_projected_views(
        ctx=ctx,
        poses=poses,
        max_views=max(1, int(config.delivery_texture_max_views)),
    )
    loaded_views = _load_delivery_projection_views(
        ctx=ctx,
        images_dir=ctx.sparse2dgs_scene_dir / "images",
        cameras=cameras,
        poses=projected_views,
    )
    vertex_colors, observed_vertex_count = _project_vertex_colors(
        mesh=mesh,
        projection_views=loaded_views,
    )
    textured_mesh, atlas_summary = _bake_uv_textured_mesh(
        mesh=mesh,
        vertex_colors=vertex_colors,
        projection_views=loaded_views,
        atlas_size=max(256, int(config.delivery_texture_atlas_size)),
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
        "glb_size_bytes": int(glb_size_bytes),
        "glb_size_mb": round(glb_size_bytes / (1024 * 1024), 3) if glb_size_bytes > 0 else 0.0,
    }
    (ctx.delivery_dir / config.delivery_texture_summary_filename).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
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
    if not kept:
        kept = [max(components, key=lambda component: len(component.faces))]
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
        summary["watertight_after"] = bool(getattr(mesh, "is_watertight", False))
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
        try:
            if hasattr(repair, "fill_holes"):
                repaired = repair.fill_holes(mesh)
                summary["holes_repaired"] = bool(summary["holes_repaired"] or repaired)
        except Exception:
            pass
        patched_mesh, patched_count = _patch_small_boundary_loops(mesh)
        if patched_count > 0:
            mesh = patched_mesh
            summary["small_hole_patches"] += int(patched_count)
            summary["holes_repaired"] = True
        try:
            mesh.remove_unreferenced_vertices()
        except Exception:
            pass
        if bool(getattr(mesh, "is_watertight", False)):
            break

    summary["watertight_after"] = bool(getattr(mesh, "is_watertight", False))
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


def _patch_small_boundary_loops(mesh) -> tuple[Any, int]:
    try:
        import trimesh
    except Exception:
        return mesh, 0

    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if len(vertices) == 0 or len(faces) == 0:
        return mesh, 0

    loops = _extract_small_boundary_loops(
        vertices=vertices,
        faces=faces,
        max_edges=max(3, int(config.delivery_small_hole_max_edges)),
        max_perimeter_ratio=max(0.0, float(config.delivery_small_hole_max_perimeter_ratio)),
    )
    if not loops:
        return mesh, 0

    new_vertices = vertices.tolist()
    new_faces = faces.tolist()
    patched_count = 0
    for loop in loops:
        loop_points = vertices[np.asarray(loop, dtype=np.int64)]
        centroid = loop_points.mean(axis=0)
        if not np.all(np.isfinite(centroid)):
            continue
        centroid_index = len(new_vertices)
        new_vertices.append(centroid.tolist())
        for edge_index in range(len(loop)):
            a = int(loop[edge_index])
            b = int(loop[(edge_index + 1) % len(loop)])
            new_faces.append([a, b, centroid_index])
        patched_count += 1

    repaired = trimesh.Trimesh(
        vertices=np.asarray(new_vertices, dtype=np.float64),
        faces=np.asarray(new_faces, dtype=np.int64),
        process=False,
    )
    try:
        repaired.remove_unreferenced_vertices()
    except Exception:
        pass
    return repaired, patched_count


def _extract_small_boundary_loops(
    *,
    vertices: np.ndarray,
    faces: np.ndarray,
    max_edges: int,
    max_perimeter_ratio: float,
) -> list[list[int]]:
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

    loops: list[list[int]] = []
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
            loops.append(loop)
    return loops


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


def _bake_uv_textured_mesh(*, mesh, vertex_colors: np.ndarray, projection_views: list[_ProjectionView], atlas_size: int):
    try:
        import trimesh
        import xatlas
        from PIL import Image
        from trimesh.visual.material import SimpleMaterial
        from trimesh.visual.texture import TextureVisuals
    except Exception as exc:
        raise RuntimeError(f"delivery_texture_runtime_missing:{exc}") from exc

    vertices = np.asarray(mesh.vertices, dtype=np.float32)
    faces = np.asarray(mesh.faces, dtype=np.uint32)
    if len(vertices) == 0 or len(faces) == 0:
        raise RuntimeError("delivery_texture_empty_mesh")

    vmapping, remapped_faces, uvs = xatlas.parametrize(vertices, faces)
    remapped_faces = np.asarray(remapped_faces, dtype=np.int64)
    uvs = np.asarray(uvs, dtype=np.float32)
    vmapping = np.asarray(vmapping, dtype=np.int64)

    baked_vertices = vertices[vmapping]
    baked_normals = np.asarray(mesh.vertex_normals, dtype=np.float32)
    if baked_normals.shape == vertices.shape:
        baked_normals = baked_normals[vmapping]
    else:
        baked_normals = None
    baked_colors = np.asarray(vertex_colors[:, :3], dtype=np.float32)[vmapping]

    fallback_rgb, fallback_coverage = _rasterize_vertex_color_atlas(
        uvs=uvs,
        faces=remapped_faces,
        colors=baked_colors,
        atlas_size=atlas_size,
    )
    texture_rgb, observed_pixel_count, projected_pixel_count, projected_face_count = _rasterize_photo_projection_atlas(
        vertices=baked_vertices.astype(np.float64),
        uvs=uvs,
        faces=remapped_faces,
        projection_views=projection_views,
        atlas_size=atlas_size,
        fallback_rgb=fallback_rgb,
        fallback_coverage=fallback_coverage,
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
        "observed_pixel_count": int(observed_pixel_count),
        "coverage_ratio": float(observed_pixel_count / max(1, atlas_size * atlas_size)),
        "photo_projected_pixel_count": int(projected_pixel_count),
        "photo_projected_face_count": int(projected_face_count),
    }
    return textured_mesh, atlas_summary


def _rasterize_vertex_color_atlas(
    *,
    uvs: np.ndarray,
    faces: np.ndarray,
    colors: np.ndarray,
    atlas_size: int,
) -> tuple[np.ndarray, int]:
    import cv2

    atlas = np.zeros((atlas_size, atlas_size, 3), dtype=np.uint8)
    coverage = np.zeros((atlas_size, atlas_size), dtype=np.uint8)

    uv_pixels = np.empty_like(uvs, dtype=np.float32)
    uv_pixels[:, 0] = np.clip(uvs[:, 0], 0.0, 1.0) * float(atlas_size - 1)
    uv_pixels[:, 1] = (1.0 - np.clip(uvs[:, 1], 0.0, 1.0)) * float(atlas_size - 1)

    for face in faces:
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
) -> tuple[np.ndarray, int, int, int]:
    import cv2

    atlas = fallback_rgb.copy()
    coverage = fallback_coverage.copy()
    uv_pixels = np.empty_like(uvs, dtype=np.float64)
    uv_pixels[:, 0] = np.clip(uvs[:, 0], 0.0, 1.0) * float(atlas_size - 1)
    uv_pixels[:, 1] = (1.0 - np.clip(uvs[:, 1], 0.0, 1.0)) * float(atlas_size - 1)

    photo_projected_pixel_count = 0
    projected_face_count = 0
    ranked_candidates = [
        _rank_projection_views(vertices[face], projection_views)
        for face in faces
    ]
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

    if np.any(coverage):
        kernel_size = max(1, int(config.delivery_texture_fill_kernel))
        kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
        dilated = cv2.dilate(atlas, kernel, iterations=1)
        mask = coverage == 0
        atlas[mask] = dilated[mask]

    observed_pixel_count = int(np.count_nonzero(coverage))
    return atlas, observed_pixel_count, int(photo_projected_pixel_count), int(projected_face_count)


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
