from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable

from ..config import config
from ..context import JobContext

SLAM3R_PAPER_URL = (
    "https://openaccess.thecvf.com/content/CVPR2025/html/"
    "Liu_SLAM3R_Real-Time_Dense_Scene_Reconstruction_from_Monocular_RGB_Videos_CVPR_2025_paper.html"
)


def run_slam3r_reconstruction(
    ctx: JobContext,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    assert ctx.slam3r_dir is not None
    assert ctx.curated_dir is not None

    if progress_callback is not None:
        progress_callback(
            {
                "stage": "slam3r_reconstruct",
                "title": "正在执行 SLAM3R",
                "detail": "正在根据 CVPR 2025 SLAM3R 主干恢复单目视频的稠密几何先验。",
                "progress_fraction": 0.48,
            }
        )

    command = _render_command(
        template=config.slam3r_command_template,
        ctx=ctx,
        repo_dir=Path(config.slam3r_repo),
    )
    if not command:
        raise RuntimeError("slam3r_command_not_configured")

    subprocess.run(
        command,
        cwd=str(Path(config.slam3r_repo)),
        check=True,
        text=True,
        timeout=config.slam3r_stage_timeout_sec,
    )

    summary = {
        "paper": "SLAM3R",
        "paper_url": SLAM3R_PAPER_URL,
        "repo": config.slam3r_repo,
        "command": command,
        "curated_dir": str(ctx.curated_dir),
        "output_dir": str(ctx.slam3r_dir),
    }
    (ctx.slam3r_dir / config.slam3r_summary_filename).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _render_command(*, template: str, ctx: JobContext, repo_dir: Path) -> list[str]:
    if not template.strip():
        return []
    rendered = template.format(
        input_video=str(ctx.input_video),
        curated_dir=str(ctx.curated_dir),
        output_dir=str(ctx.slam3r_dir),
        slam3r_dir=str(ctx.slam3r_dir),
        repo_dir=str(repo_dir),
    )
    return shlex.split(rendered)
