from __future__ import annotations

from ..adapters.graphdeco_3dgs_adapter import run_graphdeco_hq_refine
from ..context import JobContext


def run_hq_3dgs(ctx: JobContext) -> None:
    ctx.current_stage = "refine_3dgs"
    run_graphdeco_hq_refine(ctx)

