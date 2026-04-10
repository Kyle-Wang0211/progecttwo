from __future__ import annotations

from ..adapters.contract_bridge_adapter import bridge_sparse2dgs_to_sugar_inputs
from ..context import JobContext


def bridge_sparse2dgs_sugar(ctx: JobContext) -> None:
    ctx.current_stage = "sugar_contract"
    bridge_sparse2dgs_to_sugar_inputs(ctx)
