from __future__ import annotations

from ..adapters.contract_bridge_adapter import bridge_slam3r_to_sparse2dgs_scene
from ..context import JobContext


def bridge_slam3r_scene(ctx: JobContext) -> None:
    ctx.current_stage = "slam3r_scene_contract"
    bridge_slam3r_to_sparse2dgs_scene(ctx)
