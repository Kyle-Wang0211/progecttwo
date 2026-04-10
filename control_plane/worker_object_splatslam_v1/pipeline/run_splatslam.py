from __future__ import annotations

from typing import Any, Callable

from ..adapters.splatslam_adapter import run_splatslam_pipeline
from ..context import JobContext


def run_splatslam(
    ctx: JobContext,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    ctx.current_stage = "splatslam"
    run_splatslam_pipeline(ctx, progress_callback=progress_callback)
