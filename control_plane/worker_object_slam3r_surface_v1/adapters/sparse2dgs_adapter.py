from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from ..config import config
from ..context import JobContext

SPARSE2DGS_PAPER_URL = (
    "https://openaccess.thecvf.com/content/CVPR2025/html/"
    "Wu_Sparse2DGS_Geometry-Prioritized_Gaussian_Splatting_for_Surface_Reconstruction_from_Sparse_Views_CVPR_2025_paper.html"
)


def run_sparse2dgs_surface_reconstruction(ctx: JobContext) -> None:
    assert ctx.slam3r_dir is not None
    assert ctx.sparse2dgs_dir is not None

    command = _render_command(
        template=config.sparse2dgs_command_template,
        ctx=ctx,
        repo_dir=Path(config.sparse2dgs_repo),
    )
    if not command:
        raise RuntimeError("sparse2dgs_command_not_configured")

    subprocess.run(
        command,
        cwd=str(Path(config.sparse2dgs_repo)),
        check=True,
        text=True,
        timeout=config.sparse2dgs_stage_timeout_sec,
    )

    default_asset = _resolve_sparse2dgs_default_asset(ctx.sparse2dgs_dir)

    summary = {
        "paper": "Sparse2DGS",
        "paper_url": SPARSE2DGS_PAPER_URL,
        "repo": config.sparse2dgs_repo,
        "command": command,
        "scene_dir": str(ctx.sparse2dgs_scene_dir or ""),
        "slam3r_summary": str(ctx.slam3r_dir / config.slam3r_summary_filename),
        "output_dir": str(ctx.sparse2dgs_dir),
        "default_asset": str(default_asset),
    }
    (ctx.sparse2dgs_dir / config.sparse2dgs_summary_filename).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _render_command(*, template: str, ctx: JobContext, repo_dir: Path) -> list[str]:
    if not template.strip():
        return []
    rendered = template.format(
        curated_dir=str(ctx.curated_dir),
        slam3r_dir=str(ctx.slam3r_dir),
        scene_dir=str(ctx.sparse2dgs_scene_dir or ""),
        sparse2dgs_dir=str(ctx.sparse2dgs_dir),
        output_dir=str(ctx.sparse2dgs_dir),
        repo_dir=str(repo_dir),
    )
    return shlex.split(rendered)


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
