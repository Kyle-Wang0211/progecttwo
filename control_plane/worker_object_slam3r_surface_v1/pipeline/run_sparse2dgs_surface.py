from __future__ import annotations

from ..adapters.sparse2dgs_adapter import run_sparse2dgs_surface_reconstruction
from ..context import JobContext


def run_sparse2dgs_surface(ctx: JobContext) -> None:
    ctx.current_stage = "sparse2dgs_surface"
    run_sparse2dgs_surface_reconstruction(ctx)
