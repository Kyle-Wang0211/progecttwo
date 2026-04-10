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

    summary = {
        "paper": "Sparse2DGS",
        "paper_url": SPARSE2DGS_PAPER_URL,
        "repo": config.sparse2dgs_repo,
        "command": command,
        "slam3r_summary": str(ctx.slam3r_dir / config.slam3r_summary_filename),
        "output_dir": str(ctx.sparse2dgs_dir),
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
        sparse2dgs_dir=str(ctx.sparse2dgs_dir),
        output_dir=str(ctx.sparse2dgs_dir),
        repo_dir=str(repo_dir),
    )
    return shlex.split(rendered)
