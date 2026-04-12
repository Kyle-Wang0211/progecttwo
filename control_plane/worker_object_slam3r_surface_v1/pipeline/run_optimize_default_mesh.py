from __future__ import annotations

from ..adapters.default_delivery_adapter import optimize_default_mesh_delivery
from ..context import JobContext


def run_optimize_default_mesh(ctx: JobContext, *, progress_callback=None) -> None:
    ctx.current_stage = "optimize_default_mesh"
    optimize_default_mesh_delivery(ctx, progress_callback=progress_callback)
