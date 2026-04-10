from __future__ import annotations

from .context import JobContext
from .paths import ensure_job_layout
from .pipeline.curate_frames import curate_frames
from .pipeline.download_input import download_input
from .pipeline.extract_frames import extract_frames
from .pipeline.publish_default import publish_default
from .pipeline.publish_hq import publish_hq
from .pipeline.run_cleanup import run_cleanup
from .pipeline.run_hq_3dgs import run_hq_3dgs
from .pipeline.run_masks import run_masks
from .pipeline.run_openmvs import run_openmvs
from .pipeline.run_sfm import run_sfm
from .pipeline.run_support_plane import run_support_plane
from .runtime import ControlPlaneClient, push_runtime
from .storage_client import ObjectStorageClient


def _step_state(stage: str) -> str:
    if stage in {"publish_default", "publish_hq"}:
        return "exporting"
    if stage == "refine_3dgs":
        return "training_full"
    return "reconstructing"


def _run_step(
    *,
    ctx: JobContext,
    client: ControlPlaneClient,
    stage: str,
    title: str,
    detail: str,
    progress_fraction: float,
    action,
) -> None:
    ctx.current_stage = stage
    print(
        f"[object_fast_publish_v1] job={ctx.job_id} stage={stage} start title={title}",
        flush=True,
    )
    push_runtime(
        client,
        job_id=ctx.job_id,
        worker_id=ctx.worker_id,
        state=_step_state(stage),
        stage=stage,
        title=title,
        detail=detail,
        progress_fraction=progress_fraction,
    )
    action(ctx)
    print(
        f"[object_fast_publish_v1] job={ctx.job_id} stage={stage} done",
        flush=True,
    )


def run_once(
    client: ControlPlaneClient,
    storage: ObjectStorageClient,
    worker_id: str,
) -> bool:
    assignment = client.claim_next(worker_id)
    if not assignment:
        return False

    ctx = JobContext.from_assignment(assignment, worker_id=worker_id)
    ensure_job_layout(ctx)
    print(
        f"[object_fast_publish_v1] claimed job={ctx.job_id} worker={worker_id}",
        flush=True,
    )

    try:
        ctx.current_stage = "download_input"
        push_runtime(
            client,
            job_id=ctx.job_id,
            worker_id=ctx.worker_id,
            state="reconstructing",
            stage="download_input",
            title="正在准备输入素材",
            detail="正在下载录制素材并准备抽帧。",
            progress_fraction=0.12,
        )
        download_input(ctx, client=client, storage=storage)
        print(
            f"[object_fast_publish_v1] job={ctx.job_id} input downloaded",
            flush=True,
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="curate",
            title="正在抽取关键画面",
            detail="正在从录制素材中抽取候选帧。",
            progress_fraction=0.20,
            action=extract_frames,
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="curate",
            title="正在筛选有效关键帧",
            detail="正在按前后端统一标准筛选有效帧。",
            progress_fraction=0.28,
            action=curate_frames,
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="sfm_fast",
            title="正在恢复相机位姿",
            detail="正在执行 fast SfM 估计相机位姿。",
            progress_fraction=0.42,
            action=run_sfm,
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="object_mask",
            title="正在分离主体",
            detail="正在估计 object mask 并裁除背景。",
            progress_fraction=0.56,
            action=run_masks,
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="surface_fast",
            title="正在生成默认成品",
            detail="正在生成 textured mesh/surface 首成品。",
            progress_fraction=0.72,
            action=run_openmvs,
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="support_plane",
            title="正在估计支撑面",
            detail="正在计算桌面 patch、up 方向与 canonical pose。",
            progress_fraction=0.82,
            action=run_support_plane,
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="cleanup",
            title="正在整理对象成品",
            detail="正在收紧边界并保留合理支撑面 patch。",
            progress_fraction=0.88,
            action=run_cleanup,
        )
        default_manifest = publish_default(ctx, client, storage)
        client.upload_artifact_manifest(
            ctx.job_id,
            {
                "worker_id": worker_id,
                "manifest": default_manifest,
            },
        )

        if ctx.should_run_hq_refine:
            print(
                f"[object_fast_publish_v1] job={ctx.job_id} stage=refine_3dgs start",
                flush=True,
            )
            run_hq_3dgs(ctx)
            hq_manifest = publish_hq(ctx, client, storage, default_manifest)
            client.upload_artifact_manifest(
                ctx.job_id,
                {
                    "worker_id": worker_id,
                    "manifest": hq_manifest,
                },
            )

        client.complete(ctx.job_id, worker_id, "已完成", "对象成品已准备好")
        print(
            f"[object_fast_publish_v1] job={ctx.job_id} completed",
            flush=True,
        )
        return True
    except Exception as exc:
        print(
            f"[object_fast_publish_v1] job={ctx.job_id} failed stage={ctx.current_stage} error={exc}",
            flush=True,
        )
        client.fail(
            ctx.job_id,
            worker_id,
            reason="object_fast_publish_failed",
            detail=str(exc),
            stage=ctx.current_stage,
        )
        return True
