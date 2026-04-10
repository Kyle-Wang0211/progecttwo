from __future__ import annotations

from ..adapters.hq_refine_adapter import run_hq_refine_backend
from ..context import JobContext


def run_hq_refine(ctx: JobContext) -> None:
    ctx.current_stage = "hq_refine"
    run_hq_refine_backend(ctx)
