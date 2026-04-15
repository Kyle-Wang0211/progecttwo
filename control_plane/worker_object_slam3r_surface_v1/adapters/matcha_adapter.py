from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from ..config import config
from ..context import JobContext
from .sparse2dgs_adapter import (
    _iteration_from_point_cloud_path,
    _read_binary_ply_vertices,
    _resolve_sparse2dgs_default_asset,
    _trim_sparse2dgs_vertices_by_opacity,
    _voxel_deduplicate_sparse2dgs_vertices,
    _write_binary_ply_vertices,
)

MATCHA_PAPER_URL = "https://openaccess.thecvf.com/content/CVPR2025/html/Guedon_MAtCha_Gaussians_Atlas_of_Charts_for_High-Quality_Geometry_and_Photorealism_CVPR_2025_paper.html"
MatchaProgressCallback = Callable[[dict[str, Any]], None]
_LEGACY_MATCHA_TEMPLATE_MARKERS = (
    "extract_mesh_adaptive_tsdf.py",
    "2d-gaussian-splatting/extract_mesh_adaptive_tsdf.py",
    "python 2d-gaussian-splatting",
)


def run_matcha_mesh_extraction(
    ctx: JobContext,
    *,
    progress_callback: MatchaProgressCallback | None = None,
) -> None:
    assert ctx.sparse2dgs_dir is not None
    assert ctx.sparse2dgs_scene_dir is not None
    assert ctx.matcha_dir is not None

    _emit_matcha_progress(
        progress_callback,
        progress=0.04,
        title="正在准备 HQ 几何检查点",
        detail="正在检查 Sparse2DGS 输出，并决定是否先构建更稳的 working checkpoint。",
        metrics={"matcha_phase": "prepare_checkpoint"},
    )
    model_dir, model_summary = _prepare_matcha_model_dir(
        sparse2dgs_dir=ctx.sparse2dgs_dir,
        matcha_dir=ctx.matcha_dir,
        progress_callback=progress_callback,
    )

    command = _render_command(
        template=config.matcha_command_template,
        ctx=ctx,
        repo_dir=Path(config.matcha_repo),
        model_dir=model_dir,
    )
    if not command:
        raise RuntimeError("matcha_command_not_configured")

    _emit_matcha_progress(
        progress_callback,
        progress=0.30,
        title="正在执行 MAtCha 网格提取",
        detail="正在从当前高质量高斯检查点里提取 tetra mesh 主表面。",
        metrics={
            "matcha_phase": "extract_mesh",
            "matcha_model_dir": str(model_dir),
            "matcha_working_point_cap": str(model_summary["working_point_cap"]),
            "matcha_working_point_count": str(model_summary["working_point_count"]),
        },
    )
    subprocess.run(
        command,
        cwd=str(Path(config.matcha_repo)),
        check=True,
        text=True,
        timeout=config.matcha_stage_timeout_sec,
    )

    _emit_matcha_progress(
        progress_callback,
        progress=0.90,
        title="正在整理 MAtCha 网格产物",
        detail="官方提取已完成，正在定位主网格文件并写出摘要。",
        metrics={"matcha_phase": "resolve_mesh_asset"},
    )
    mesh_asset = _resolve_matcha_mesh_asset(ctx.matcha_dir)

    summary = {
        "paper": "MAtCha",
        "paper_url": MATCHA_PAPER_URL,
        "repo": config.matcha_repo,
        "command": command,
        "scene_dir": str(ctx.sparse2dgs_scene_dir),
        "sparse2dgs_output_dir": str(ctx.sparse2dgs_dir),
        "matcha_model_dir": str(model_dir),
        "output_dir": str(ctx.matcha_dir),
        "mesh_asset": str(mesh_asset),
        "default_asset": str(mesh_asset),
        "mesh_extraction": "adaptive_tetrahedralization",
        "working_model": model_summary,
    }
    (ctx.matcha_dir / config.matcha_summary_filename).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _emit_matcha_progress(
        progress_callback,
        progress=1.0,
        title="MAtCha 网格提取完成",
        detail="主网格已写出，正在进入 HQ 网格优化。",
        metrics={
            "matcha_phase": "done",
            "matcha_mesh_asset": str(mesh_asset),
        },
    )


def _prepare_matcha_model_dir(
    *,
    sparse2dgs_dir: Path,
    matcha_dir: Path,
    progress_callback: MatchaProgressCallback | None = None,
) -> tuple[Path, dict[str, Any]]:
    point_cloud_asset = _resolve_sparse2dgs_default_asset(sparse2dgs_dir)
    source_vertices, properties = _read_binary_ply_vertices(point_cloud_asset)
    source_point_count = int(len(source_vertices))
    trigger = max(1, int(config.matcha_working_point_trigger))
    point_cap = max(1, int(config.matcha_working_point_cap))
    attempt_index = max(0, int(os.environ.get("OBJECT_SLAM3R_SURFACE_MATCHA_ATTEMPT_INDEX", "0")))

    if source_point_count <= trigger or source_point_count <= point_cap:
        return sparse2dgs_dir, {
            "applied": False,
            "source_point_count": source_point_count,
            "working_point_count": source_point_count,
            "working_point_cap": point_cap,
            "working_model_dir": str(sparse2dgs_dir),
            "reason": "below_trigger",
            "voxel_deduplicated": False,
            "voxel_size": 0.0,
        }

    _emit_matcha_progress(
        progress_callback,
        progress=0.16,
        title="正在构建 working checkpoint",
        detail=(
            f"当前高斯点过多（{source_point_count:,}），先构建更稳的 working checkpoint，"
            "避免 MAtCha 在提取 tetra mesh 时被资源压力拖垮。"
        ),
        metrics={
            "matcha_phase": "build_working_checkpoint",
            "matcha_source_point_count": str(source_point_count),
            "matcha_working_point_cap": str(point_cap),
        },
    )
    trimmed_vertices, voxel_size, voxel_deduplicated = _voxel_deduplicate_sparse2dgs_vertices(
        source_vertices,
        point_cap=point_cap,
        voxel_divisor=float(config.matcha_working_point_voxel_divisor),
    )
    if len(trimmed_vertices) > point_cap:
        trimmed_vertices = _trim_sparse2dgs_vertices_by_opacity(trimmed_vertices, point_cap=point_cap)
    iteration = _iteration_from_point_cloud_path(point_cloud_asset)
    working_model_dir = matcha_dir / "_working_model" / f"attempt_{attempt_index:02d}"
    if working_model_dir.exists():
        shutil.rmtree(working_model_dir)
    working_model_dir.mkdir(parents=True, exist_ok=True)

    for name in ("cameras.json", "cfg_args", "input.ply"):
        source_path = sparse2dgs_dir / name
        if source_path.exists():
            shutil.copy2(source_path, working_model_dir / name)

    point_cloud_dir = working_model_dir / "point_cloud" / f"iteration_{iteration}"
    point_cloud_dir.mkdir(parents=True, exist_ok=True)
    _write_binary_ply_vertices(
        point_cloud_dir / "point_cloud.ply",
        vertices=trimmed_vertices,
        properties=properties,
    )
    return working_model_dir, {
        "applied": True,
        "source_point_count": source_point_count,
        "working_point_count": int(len(trimmed_vertices)),
        "working_point_cap": point_cap,
        "working_model_dir": str(working_model_dir),
        "reason": "voxel_cap_sparse2dgs_checkpoint_for_matcha",
        "voxel_deduplicated": bool(voxel_deduplicated),
        "voxel_size": float(voxel_size),
    }


def _render_command(*, template: str, ctx: JobContext, repo_dir: Path, model_dir: Path) -> list[str]:
    if not template.strip():
        return _default_matcha_wrapper_command(ctx=ctx, repo_dir=repo_dir, model_dir=model_dir)
    lowered = template.strip().lower()
    if any(marker in lowered for marker in _LEGACY_MATCHA_TEMPLATE_MARKERS):
        return _default_matcha_wrapper_command(ctx=ctx, repo_dir=repo_dir, model_dir=model_dir)
    rendered = template.format(
        scene_dir=str(ctx.sparse2dgs_scene_dir or ""),
        sparse2dgs_dir=str(ctx.sparse2dgs_dir),
        matcha_dir=str(ctx.matcha_dir),
        output_dir=str(ctx.matcha_dir),
        repo_dir=str(repo_dir),
        model_dir=str(model_dir),
    )
    return shlex.split(rendered)


def _default_matcha_wrapper_command(
    *,
    ctx: JobContext,
    repo_dir: Path,
    model_dir: Path,
) -> list[str]:
    assert ctx.sparse2dgs_scene_dir is not None
    assert ctx.matcha_dir is not None
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_matcha_official.sh"
    return [
        "bash",
        str(script_path),
        str(repo_dir),
        str(ctx.sparse2dgs_scene_dir),
        str(model_dir),
        str(ctx.matcha_dir),
    ]


def _resolve_matcha_mesh_asset(output_dir: Path) -> Path:
    candidates = sorted(output_dir.glob("tetra_mesh_binary_search_*.ply"))
    if candidates:
        return candidates[-1]

    fallback_candidates = sorted(output_dir.glob("*.ply"))
    if fallback_candidates:
        return fallback_candidates[-1]

    raise RuntimeError(f"matcha_mesh_asset_missing:{output_dir}")


def _emit_matcha_progress(
    callback: MatchaProgressCallback | None,
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
            "progress": float(min(max(progress, 0.0), 1.0)),
            "title": title,
            "detail": detail,
            "metrics": dict(metrics or {}),
        }
    )
