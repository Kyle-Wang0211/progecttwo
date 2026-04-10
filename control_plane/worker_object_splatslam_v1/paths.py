from __future__ import annotations

from .context import JobContext


def ensure_job_layout(ctx: JobContext) -> None:
    ctx.frames_dir = ctx.output_dir / "frames"
    ctx.curated_dir = ctx.output_dir / "curated"
    ctx.masks_dir = ctx.output_dir / "masks"
    ctx.splatslam_dir = ctx.output_dir / "splatslam"
    ctx.support_dir = ctx.output_dir / "support"
    ctx.splat_dir = ctx.output_dir / "splat"
    ctx.mesh_dir = ctx.output_dir / "mesh"
    ctx.default_publish_dir = ctx.output_dir / "default"
    ctx.hq_dir = ctx.output_dir / "hq"

    for path in (
        ctx.frames_dir,
        ctx.curated_dir,
        ctx.masks_dir,
        ctx.splatslam_dir,
        ctx.support_dir,
        ctx.splat_dir,
        ctx.mesh_dir,
        ctx.default_publish_dir,
        ctx.hq_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
