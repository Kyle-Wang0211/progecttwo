from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from ..config import config
from ..context import JobContext

MATCHA_PAPER_URL = "https://openaccess.thecvf.com/content/CVPR2025/html/Guedon_MAtCha_Gaussians_Atlas_of_Charts_for_High-Quality_Geometry_and_Photorealism_CVPR_2025_paper.html"


def run_matcha_mesh_extraction(ctx: JobContext) -> None:
    assert ctx.sparse2dgs_dir is not None
    assert ctx.sparse2dgs_scene_dir is not None
    assert ctx.matcha_dir is not None

    command = _render_command(
        template=config.matcha_command_template,
        ctx=ctx,
        repo_dir=Path(config.matcha_repo),
    )
    if not command:
        raise RuntimeError("matcha_command_not_configured")

    subprocess.run(
        command,
        cwd=str(Path(config.matcha_repo)),
        check=True,
        text=True,
        timeout=config.matcha_stage_timeout_sec,
    )

    mesh_asset = _resolve_matcha_mesh_asset(ctx.matcha_dir)
    glb_asset = _convert_mesh_to_glb(mesh_asset, ctx.matcha_dir / "default_mesh.glb")

    summary = {
        "paper": "MAtCha",
        "paper_url": MATCHA_PAPER_URL,
        "repo": config.matcha_repo,
        "command": command,
        "scene_dir": str(ctx.sparse2dgs_scene_dir),
        "sparse2dgs_output_dir": str(ctx.sparse2dgs_dir),
        "output_dir": str(ctx.matcha_dir),
        "mesh_asset": str(mesh_asset),
        "glb_asset": str(glb_asset),
        "default_asset": str(glb_asset),
        "mesh_extraction": "adaptive_tetrahedralization",
    }
    (ctx.matcha_dir / config.matcha_summary_filename).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _render_command(*, template: str, ctx: JobContext, repo_dir: Path) -> list[str]:
    if not template.strip():
        return []
    rendered = template.format(
        scene_dir=str(ctx.sparse2dgs_scene_dir or ""),
        sparse2dgs_dir=str(ctx.sparse2dgs_dir),
        matcha_dir=str(ctx.matcha_dir),
        output_dir=str(ctx.matcha_dir),
        repo_dir=str(repo_dir),
    )
    return shlex.split(rendered)


def _resolve_matcha_mesh_asset(output_dir: Path) -> Path:
    candidates = sorted(output_dir.glob("tetra_mesh_binary_search_*.ply"))
    if candidates:
        return candidates[-1]

    fallback_candidates = sorted(output_dir.glob("*.ply"))
    if fallback_candidates:
        return fallback_candidates[-1]

    raise RuntimeError(f"matcha_mesh_asset_missing:{output_dir}")


def _convert_mesh_to_glb(source_mesh: Path, destination_glb: Path) -> Path:
    try:
        import trimesh

        mesh_or_scene = trimesh.load(source_mesh, force="scene")
        destination_glb.parent.mkdir(parents=True, exist_ok=True)
        mesh_or_scene.export(destination_glb)
        return destination_glb
    except Exception as exc:
        raise RuntimeError(f"matcha_glb_export_failed:{exc}") from exc
