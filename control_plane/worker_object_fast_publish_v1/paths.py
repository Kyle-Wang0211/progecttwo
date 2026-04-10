from __future__ import annotations

from .context import JobContext


def ensure_job_layout(ctx: JobContext) -> None:
    ctx.frames_dir = ctx.output_dir / "frames"
    ctx.curated_dir = ctx.output_dir / "curated"
    ctx.sfm_dir = ctx.output_dir / "sfm"
    ctx.masks_dir = ctx.output_dir / "masks"
    ctx.support_dir = ctx.output_dir / "support"
    ctx.surface_dir = ctx.output_dir / "surface"
    ctx.default_publish_dir = ctx.output_dir / "default"
    ctx.hq_dir = ctx.output_dir / "hq"

    for path in (
        ctx.frames_dir,
        ctx.curated_dir,
        ctx.sfm_dir,
        ctx.masks_dir,
        ctx.support_dir,
        ctx.surface_dir,
        ctx.default_publish_dir,
        ctx.hq_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

