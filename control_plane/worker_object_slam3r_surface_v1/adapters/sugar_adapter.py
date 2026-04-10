from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from ..config import config
from ..context import JobContext

SUGAR_PAPER_URL = (
    "https://openaccess.thecvf.com/content/CVPR2024/html/"
    "Guedon_SuGaR_Surface-Aligned_Gaussian_Splatting_for_Efficient_3D_Mesh_Reconstruction_and_CVPR_2024_paper.html"
)


def run_sugar_mesh_export(ctx: JobContext) -> None:
    assert ctx.sparse2dgs_dir is not None
    assert ctx.sugar_dir is not None

    command = _render_command(
        template=config.sugar_command_template,
        ctx=ctx,
        repo_dir=Path(config.sugar_repo),
    )
    if not command:
        raise RuntimeError("sugar_command_not_configured")

    subprocess.run(
        command,
        cwd=str(Path(config.sugar_repo)),
        check=True,
        text=True,
        timeout=config.sugar_stage_timeout_sec,
    )

    summary = {
        "paper": "SuGaR",
        "paper_url": SUGAR_PAPER_URL,
        "repo": config.sugar_repo,
        "command": command,
        "sparse2dgs_summary": str(ctx.sparse2dgs_dir / config.sparse2dgs_summary_filename),
        "output_dir": str(ctx.sugar_dir),
    }
    (ctx.sugar_dir / config.sugar_summary_filename).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _render_command(*, template: str, ctx: JobContext, repo_dir: Path) -> list[str]:
    if not template.strip():
        return []
    rendered = template.format(
        sparse2dgs_dir=str(ctx.sparse2dgs_dir),
        sugar_dir=str(ctx.sugar_dir),
        output_dir=str(ctx.sugar_dir),
        repo_dir=str(repo_dir),
    )
    return shlex.split(rendered)
