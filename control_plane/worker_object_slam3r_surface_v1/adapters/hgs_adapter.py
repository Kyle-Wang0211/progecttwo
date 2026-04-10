from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from ..config import config
from ..context import JobContext

HGS_PAPER_URL = (
    "https://openaccess.thecvf.com/content/CVPR2025/html/"
    "Li_3D-HGS_3D_Half-Gaussian_Splatting_CVPR_2025_paper.html"
)


def run_3dhgs_refine_backend(ctx: JobContext) -> None:
    assert ctx.sugar_dir is not None
    assert ctx.hq_dir is not None

    command = _render_command(
        template=config.hgs_command_template,
        ctx=ctx,
        repo_dir=Path(config.hgs_repo),
    )
    if not command:
        raise RuntimeError("3dhgs_command_not_configured")

    subprocess.run(
        command,
        cwd=str(Path(config.hgs_repo)),
        check=True,
        text=True,
        timeout=config.hgs_stage_timeout_sec,
    )

    summary = {
        "paper": "3D-HGS",
        "paper_url": HGS_PAPER_URL,
        "repo": config.hgs_repo,
        "command": command,
        "surface_summary": str(ctx.sugar_dir / config.sugar_summary_filename),
        "output_dir": str(ctx.hq_dir),
    }
    (ctx.hq_dir / config.hgs_summary_filename).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _render_command(*, template: str, ctx: JobContext, repo_dir: Path) -> list[str]:
    if not template.strip():
        return []
    rendered = template.format(
        sugar_dir=str(ctx.sugar_dir),
        hq_dir=str(ctx.hq_dir),
        output_dir=str(ctx.hq_dir),
        repo_dir=str(repo_dir),
    )
    return shlex.split(rendered)
