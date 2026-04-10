from __future__ import annotations

from .context import JobContext


def ensure_job_layout(ctx: JobContext) -> None:
    ctx.frames_dir = ctx.output_dir / "frames"
    ctx.curated_dir = ctx.output_dir / "curated"
    ctx.slam3r_dir = ctx.output_dir / "slam3r"
    ctx.sparse2dgs_dir = ctx.output_dir / "sparse2dgs"
    ctx.sugar_dir = ctx.output_dir / "sugar"
    ctx.default_publish_dir = ctx.output_dir / "default"
    ctx.hq_dir = ctx.output_dir / "hq"

    for path in (
        ctx.frames_dir,
        ctx.curated_dir,
        ctx.slam3r_dir,
        ctx.sparse2dgs_dir,
        ctx.sugar_dir,
        ctx.default_publish_dir,
        ctx.hq_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
