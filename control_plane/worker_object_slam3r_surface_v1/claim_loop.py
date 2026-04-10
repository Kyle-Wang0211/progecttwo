from __future__ import annotations

import threading
import time
from typing import Any

from .context import JobContext
from .paths import ensure_job_layout
from .pipeline.bridge_slam3r_scene import bridge_slam3r_scene
from .pipeline.bridge_sparse2dgs_sugar import bridge_sparse2dgs_sugar
from .pipeline.curate_frames import curate_frames
from .pipeline.download_input import download_input
from .pipeline.extract_frames import extract_frames
from .pipeline.publish_default_surface import publish_default_surface
from .pipeline.publish_hq import publish_hq
from .pipeline.run_3dhgs_refine import run_3dhgs_refine
from .pipeline.run_slam3r import run_slam3r
from .pipeline.run_sparse2dgs_surface import run_sparse2dgs_surface
from .pipeline.run_sugar_mesh import run_sugar_mesh
from .runtime import ControlPlaneClient, push_runtime
from .storage_client import ObjectStorageClient


class _RuntimeTracker:
    def __init__(
        self,
        *,
        state: str,
        stage: str,
        title: str,
        detail: str,
        progress_fraction: float,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._payload: dict[str, Any] = {
            "state": state,
            "stage": stage,
            "title": title,
            "detail": detail,
            "progress_fraction": progress_fraction,
            "metrics": dict(metrics or {}),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._payload["state"],
                "stage": self._payload["stage"],
                "title": self._payload["title"],
                "detail": self._payload["detail"],
                "progress_fraction": self._payload["progress_fraction"],
                "metrics": dict(self._payload["metrics"]),
            }

    def update(
        self,
        *,
        state: str | None = None,
        stage: str | None = None,
        title: str | None = None,
        detail: str | None = None,
        progress_fraction: float | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            if state is not None:
                self._payload["state"] = state
            if stage is not None:
                self._payload["stage"] = stage
            if title is not None:
                self._payload["title"] = title
            if detail is not None:
                self._payload["detail"] = detail
            if progress_fraction is not None:
                self._payload["progress_fraction"] = progress_fraction
            if metrics is not None:
                merged = dict(self._payload["metrics"])
                merged.update(metrics)
                self._payload["metrics"] = merged


def _step_state(stage: str) -> str:
    if stage in {"publish_default_surface", "publish_hq", "artifact_upload"}:
        return "exporting"
    if stage == "3dhgs_refine":
        return "training_full"
    return "reconstructing"


def _push_tracker_runtime(
    client: ControlPlaneClient,
    ctx: JobContext,
    tracker: _RuntimeTracker,
) -> None:
    payload = tracker.snapshot()
    push_runtime(
        client,
        job_id=ctx.job_id,
        worker_id=ctx.worker_id,
        state=payload["state"],
        stage=payload["stage"],
        title=payload["title"],
        detail=payload["detail"],
        progress_fraction=payload["progress_fraction"],
        metrics=payload["metrics"],
    )


def _update_tracker_runtime(
    client: ControlPlaneClient,
    ctx: JobContext,
    tracker: _RuntimeTracker,
    *,
    state: str | None = None,
    stage: str | None = None,
    title: str | None = None,
    detail: str | None = None,
    progress_fraction: float | None = None,
    metrics: dict[str, Any] | None = None,
) -> None:
    if stage is not None:
        ctx.current_stage = stage
    tracker.update(
        state=state,
        stage=stage,
        title=title,
        detail=detail,
        progress_fraction=progress_fraction,
        metrics=metrics,
    )
    _push_tracker_runtime(client, ctx, tracker)


def _run_action_with_keepalive(
    *,
    ctx: JobContext,
    client: ControlPlaneClient,
    tracker: _RuntimeTracker,
    action,
):
    stop_event = threading.Event()

    def keepalive_loop() -> None:
        heartbeat_interval = max(5.0, 7.5)
        runtime_interval = max(10.0, 15.0)
        last_runtime = time.time()
        while not stop_event.wait(heartbeat_interval):
            try:
                client.heartbeat(ctx.worker_id, state="busy", current_job_id=ctx.job_id)
            except Exception as exc:
                payload = tracker.snapshot()
                print(
                    f"[object_slam3r_surface_v1] job={ctx.job_id} stage={payload['stage']} keepalive heartbeat failed error={exc}",
                    flush=True,
                )
            now = time.time()
            if now - last_runtime >= runtime_interval:
                try:
                    payload = tracker.snapshot()
                    metrics = dict(payload["metrics"])
                    metrics["keepalive"] = True
                    push_runtime(
                        client,
                        job_id=ctx.job_id,
                        worker_id=ctx.worker_id,
                        state=payload["state"],
                        stage=payload["stage"],
                        title=payload["title"],
                        detail=payload["detail"],
                        progress_fraction=payload["progress_fraction"],
                        metrics=metrics,
                    )
                    last_runtime = now
                except Exception as exc:
                    payload = tracker.snapshot()
                    print(
                        f"[object_slam3r_surface_v1] job={ctx.job_id} stage={payload['stage']} keepalive runtime failed error={exc}",
                        flush=True,
                    )

    keepalive_thread = threading.Thread(
        target=keepalive_loop,
        name=f"surface-keepalive-{ctx.job_id[:8]}-{tracker.snapshot()['stage']}",
        daemon=True,
    )
    keepalive_thread.start()
    try:
        return action()
    finally:
        stop_event.set()
        keepalive_thread.join(timeout=1.0)


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
    tracker = _RuntimeTracker(
        state=_step_state(stage),
        stage=stage,
        title=title,
        detail=detail,
        progress_fraction=progress_fraction,
    )
    print(
        f"[object_slam3r_surface_v1] job={ctx.job_id} stage={stage} start title={title}",
        flush=True,
    )
    _push_tracker_runtime(client, ctx, tracker)
    _run_action_with_keepalive(
        ctx=ctx,
        client=client,
        tracker=tracker,
        action=lambda: action(ctx),
    )
    print(
        f"[object_slam3r_surface_v1] job={ctx.job_id} stage={stage} done",
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
        f"[object_slam3r_surface_v1] claimed job={ctx.job_id} worker={worker_id}",
        flush=True,
    )
    client.heartbeat(worker_id, state="busy", current_job_id=ctx.job_id)

    try:
        _run_step(
            ctx=ctx,
            client=client,
            stage="download_input",
            title="正在准备输入素材",
            detail="正在下载录制素材并准备抽帧。",
            progress_fraction=0.12,
            action=lambda current_ctx: download_input(current_ctx, client=client, storage=storage),
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="curate",
            title="正在抽取候选帧",
            detail="正在从录制素材中提取用于论文主链的候选帧。",
            progress_fraction=0.20,
            action=extract_frames,
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="curate",
            title="正在筛选有效关键帧",
            detail="正在按统一标准筛选可供 SLAM3R 使用的关键帧。",
            progress_fraction=0.28,
            action=curate_frames,
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="slam3r_reconstruct",
            title="正在执行 SLAM3R 主干重建",
            detail="正在按 CVPR 2025 SLAM3R 生成单目视频的稠密几何主干。",
            progress_fraction=0.48,
            action=lambda current_ctx: run_slam3r(current_ctx),
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="slam3r_scene_contract",
            title="正在整理官方场景契约",
            detail="正在把 SLAM3R 官方输出转换成 Sparse2DGS 官方可读取的 COLMAP 场景。",
            progress_fraction=0.58,
            action=bridge_slam3r_scene,
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="sparse2dgs_surface",
            title="正在执行 Sparse2DGS 表面重建",
            detail="正在按 CVPR 2025 Sparse2DGS 生成稳定、完整、准确的表面。",
            progress_fraction=0.68,
            action=run_sparse2dgs_surface,
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="sugar_contract",
            title="正在整理 SuGaR 官方输入契约",
            detail="正在把 Sparse2DGS 官方输出对齐为 SuGaR 所需的 scene 与 checkpoint 契约。",
            progress_fraction=0.74,
            action=bridge_sparse2dgs_sugar,
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="sugar_mesh",
            title="正在执行 SuGaR mesh 提取",
            detail="正在按 CVPR 2024 SuGaR 导出可交付的 mesh/surface 默认成品。",
            progress_fraction=0.82,
            action=run_sugar_mesh,
        )

        default_manifest_holder: dict[str, dict[str, dict[str, object]]] = {}
        _run_step(
            ctx=ctx,
            client=client,
            stage="publish_default_surface",
            title="正在整理默认表面成品",
            detail="正在写出默认 mesh/surface、海报和 viewer manifest。",
            progress_fraction=0.90,
            action=lambda current_ctx: default_manifest_holder.setdefault(
                "manifest",
                publish_default_surface(current_ctx, client, storage),
            ),
        )
        default_manifest = default_manifest_holder["manifest"]
        _run_step(
            ctx=ctx,
            client=client,
            stage="artifact_upload",
            title="正在回传默认表面成品",
            detail="正在上传默认成品清单并通知手机准备下载。",
            progress_fraction=0.94,
            action=lambda current_ctx: client.upload_artifact_manifest(
                current_ctx.job_id,
                {
                    "worker_id": worker_id,
                    "manifest": default_manifest,
                },
            ),
        )

        if ctx.should_run_hq_refine:
            _run_step(
                ctx=ctx,
                client=client,
                stage="3dhgs_refine",
                title="正在执行 3D-HGS 可选增强",
                detail="正在按 CVPR 2025 3D-HGS 生成可选高斯增强层。",
                progress_fraction=0.97,
                action=run_3dhgs_refine,
            )
            hq_manifest_holder: dict[str, dict[str, dict[str, object]]] = {}
            _run_step(
                ctx=ctx,
                client=client,
                stage="publish_hq",
                title="正在整理可选高斯增强结果",
                detail="正在更新 HQ viewer manifest 并回传可选增强资产。",
                progress_fraction=0.99,
                action=lambda current_ctx: hq_manifest_holder.setdefault(
                    "manifest",
                    publish_hq(current_ctx, client, storage, default_manifest),
                ),
            )
            hq_manifest = hq_manifest_holder["manifest"]
            _run_step(
                ctx=ctx,
                client=client,
                stage="artifact_upload",
                title="正在回传可选高斯增强结果",
                detail="正在上传 HQ 清单。",
                progress_fraction=0.995,
                action=lambda current_ctx: client.upload_artifact_manifest(
                    current_ctx.job_id,
                    {
                        "worker_id": worker_id,
                        "manifest": hq_manifest,
                    },
                ),
            )

        client.complete(ctx.job_id, worker_id, "已完成", "默认 mesh/surface 成品已准备好")
        print(
            f"[object_slam3r_surface_v1] job={ctx.job_id} completed",
            flush=True,
        )
        return True
    except Exception as exc:
        print(
            f"[object_slam3r_surface_v1] job={ctx.job_id} failed stage={ctx.current_stage} error={exc}",
            flush=True,
        )
        client.fail(
            ctx.job_id,
            worker_id,
            reason="object_surface_failed",
            detail=str(exc),
            stage=ctx.current_stage,
        )
        return True
