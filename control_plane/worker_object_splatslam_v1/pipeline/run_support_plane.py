from __future__ import annotations

from ..adapters.canonical_pose_adapter import estimate_canonical_pose
from ..context import JobContext


def run_support_plane(ctx: JobContext) -> None:
    ctx.current_stage = "support_plane"
    estimate_canonical_pose(ctx)
