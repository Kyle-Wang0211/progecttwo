from __future__ import annotations

from ..adapters.openmvs_adapter import run_openmvs_reconstruct
from ..context import JobContext


def run_openmvs(ctx: JobContext) -> None:
    ctx.current_stage = "surface_fast"
    run_openmvs_reconstruct(ctx)

