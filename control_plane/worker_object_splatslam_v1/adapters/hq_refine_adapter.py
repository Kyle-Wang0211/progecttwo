from __future__ import annotations

from ..adapters.graphdeco_3dgs_adapter import run_graphdeco_hq_refine
from ..context import JobContext


def run_hq_refine_backend(ctx: JobContext) -> None:
    run_graphdeco_hq_refine(ctx)
