from __future__ import annotations

from ..adapters.open3d_adapter import estimate_support_plane
from ..context import JobContext


def run_support_plane(ctx: JobContext) -> None:
    ctx.current_stage = "support_plane"
    estimate_support_plane(ctx)

