from __future__ import annotations

from ..adapters.open3d_adapter import cleanup_surface
from ..context import JobContext


def run_cleanup(ctx: JobContext) -> None:
    ctx.current_stage = "cleanup"
    cleanup_surface(ctx)

