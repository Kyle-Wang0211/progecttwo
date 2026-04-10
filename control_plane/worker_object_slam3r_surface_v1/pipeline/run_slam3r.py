from __future__ import annotations

from typing import Any, Callable

from ..adapters.slam3r_adapter import run_slam3r_reconstruction
from ..context import JobContext


def run_slam3r(
    ctx: JobContext,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    ctx.current_stage = "slam3r_reconstruct"
    run_slam3r_reconstruction(ctx, progress_callback=progress_callback)
