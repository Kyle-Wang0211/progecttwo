from __future__ import annotations

from ..adapters.splat_cleanup_adapter import cleanup_splat
from ..context import JobContext


def run_splat_cleanup(ctx: JobContext) -> None:
    ctx.current_stage = "splat_cleanup"
    cleanup_splat(ctx)
