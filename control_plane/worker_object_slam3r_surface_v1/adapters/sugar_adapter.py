from __future__ import annotations

import json
import shlex
import subprocess
import time
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

    start_time = time.time()
    subprocess.run(
        command,
        cwd=str(Path(config.sugar_repo)),
        check=True,
        text=True,
        timeout=config.sugar_stage_timeout_sec,
    )

    default_asset = _resolve_sugar_default_asset(repo_dir=Path(config.sugar_repo), not_before=start_time)
    summary = {
        "paper": "SuGaR",
        "paper_url": SUGAR_PAPER_URL,
        "repo": config.sugar_repo,
        "command": command,
        "scene_dir": str(ctx.sugar_scene_dir or ""),
        "gs_output_dir": str(ctx.sugar_gs_output_dir or ""),
        "sparse2dgs_summary": str(ctx.sparse2dgs_dir / config.sparse2dgs_summary_filename),
        "output_dir": str(ctx.sugar_dir),
        "default_asset": str(default_asset),
    }
    (ctx.sugar_dir / config.sugar_summary_filename).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _render_command(*, template: str, ctx: JobContext, repo_dir: Path) -> list[str]:
    if not template.strip():
        return []
    rendered = template.format(
        scene_dir=str(ctx.sugar_scene_dir or ""),
        gs_output_dir=str(ctx.sugar_gs_output_dir or ""),
        sparse2dgs_dir=str(ctx.sparse2dgs_dir),
        sugar_dir=str(ctx.sugar_dir),
        output_dir=str(ctx.sugar_dir),
        repo_dir=str(repo_dir),
    )
    return shlex.split(rendered)


def _resolve_sugar_default_asset(*, repo_dir: Path, not_before: float) -> Path:
    output_root = repo_dir / "output"
    if not output_root.exists():
        raise RuntimeError(f"sugar_output_missing:{output_root}")

    candidates: list[Path] = []
    for pattern in ("**/sugarmesh*.ply", "**/sugarmesh*.obj", "**/*.ply", "**/*.obj"):
        for candidate in output_root.glob(pattern):
            if not candidate.is_file():
                continue
            if candidate.stat().st_mtime + 5 < not_before:
                continue
            candidates.append(candidate)

    if not candidates:
        raise RuntimeError("sugar_default_asset_missing")

    candidates.sort(
        key=lambda path: (
            0 if path.suffix.lower() == ".ply" else 1,
            -path.stat().st_mtime,
        )
    )
    return candidates[0]
