from __future__ import annotations

from ..adapters.hgs_adapter import run_3dhgs_refine_backend
from ..context import JobContext


def run_3dhgs_refine(ctx: JobContext) -> None:
    ctx.current_stage = "3dhgs_refine"
    run_3dhgs_refine_backend(ctx)
