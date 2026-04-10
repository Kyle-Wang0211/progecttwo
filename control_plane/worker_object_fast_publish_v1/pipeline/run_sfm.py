from __future__ import annotations

from ..adapters.hloc_adapter import maybe_run_hloc
from ..adapters.pycolmap_adapter import run_pycolmap
from ..context import JobContext


def run_sfm(ctx: JobContext) -> None:
    ctx.current_stage = "sfm_fast"
    maybe_run_hloc(ctx)
    run_pycolmap(ctx)

