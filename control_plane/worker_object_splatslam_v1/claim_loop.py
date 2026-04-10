from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .config import config
from .context import JobContext
from .paths import ensure_job_layout
from .pipeline.curate_frames import curate_frames
from .pipeline.download_input import download_input
from .pipeline.extract_frames import extract_frames
from .pipeline.publish_default_splat import publish_default_splat
from .pipeline.publish_hq import publish_hq
from .pipeline.run_hq_refine import run_hq_refine
from .pipeline.run_masks_lite import run_masks_lite
from .pipeline.run_optional_mesh_export import run_optional_mesh_export
from .pipeline.run_splat_cleanup import run_splat_cleanup
from .pipeline.run_splatslam import run_splatslam
from .pipeline.run_support_plane import run_support_plane
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
    if stage in {"publish_default_splat", "publish_hq", "optional_mesh_export", "artifact_upload"}:
        return "exporting"
    if stage == "hq_refine":
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
    tracker = _RuntimeTracker(
        state=_step_state(stage),
        stage=stage,
        title=title,
        detail=detail,
        progress_fraction=progress_fraction,
    )
    print(
        f"[object_splatslam_v1] job={ctx.job_id} stage={stage} start title={title}",
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
        f"[object_splatslam_v1] job={ctx.job_id} stage={stage} done",
        flush=True,
    )


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
        heartbeat_interval = max(5.0, float(config.heartbeat_interval_sec) / 2.0)
        runtime_interval = max(10.0, float(config.heartbeat_interval_sec))
        last_runtime = time.time()
        while not stop_event.wait(heartbeat_interval):
            try:
                client.heartbeat(ctx.worker_id, state="busy", current_job_id=ctx.job_id)
            except Exception as exc:
                payload = tracker.snapshot()
                print(
                    f"[object_splatslam_v1] job={ctx.job_id} stage={payload['stage']} keepalive heartbeat failed error={exc}",
                    flush=True,
                )
            now = time.time()
            if now - last_runtime >= runtime_interval:
                try:
                    payload = tracker.snapshot()
                    payload_metrics = dict(payload["metrics"])
                    payload_metrics["keepalive"] = True
                    push_runtime(
                        client,
                        job_id=ctx.job_id,
                        worker_id=ctx.worker_id,
                        state=payload["state"],
                        stage=payload["stage"],
                        title=payload["title"],
                        detail=payload["detail"],
                        progress_fraction=payload["progress_fraction"],
                        metrics=payload_metrics,
                    )
                    last_runtime = now
                except Exception as exc:
                    payload = tracker.snapshot()
                    print(
                        f"[object_splatslam_v1] job={ctx.job_id} stage={payload['stage']} keepalive runtime failed error={exc}",
                        flush=True,
                    )

    keepalive_thread = threading.Thread(
        target=keepalive_loop,
        name=f"splatslam-keepalive-{ctx.job_id[:8]}-{tracker.snapshot()['stage']}",
        daemon=True,
    )
    keepalive_thread.start()
    try:
        return action()
    finally:
        stop_event.set()
        keepalive_thread.join(timeout=1.0)


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
        f"[object_splatslam_v1] claimed job={ctx.job_id} worker={worker_id}",
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
        print(
            f"[object_splatslam_v1] job={ctx.job_id} input downloaded",
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
            stage="object_mask",
            title="正在估计主体区域",
            detail="正在执行轻量 object mask，帮助后续 SLAM 聚焦主体。",
            progress_fraction=0.38,
            action=run_masks_lite,
        )
        ctx.current_stage = "splatslam_prepare"
        tracker = _RuntimeTracker(
            state="reconstructing",
            stage="splatslam_prepare",
            title="正在准备 Splat-SLAM",
            detail="正在准备 object-first 输入并启动 Splat-SLAM。",
            progress_fraction=0.46,
        )
        print(
            f"[object_splatslam_v1] job={ctx.job_id} stage=splatslam_prepare start title=正在准备 Splat-SLAM",
            flush=True,
        )
        _push_tracker_runtime(client, ctx, tracker)
        _run_action_with_keepalive(
            ctx=ctx,
            client=client,
            tracker=tracker,
            action=lambda: run_splatslam(
                ctx,
                progress_callback=lambda update: _update_tracker_runtime(
                    client,
                    ctx,
                    tracker,
                    stage=str(update.get("stage") or tracker.snapshot()["stage"]),
                    title=str(update.get("title") or tracker.snapshot()["title"]),
                    detail=str(update.get("detail") or tracker.snapshot()["detail"]),
                    progress_fraction=float(update["progress_fraction"]) if update.get("progress_fraction") is not None else None,
                    metrics=dict(update.get("metrics") or {}),
                ),
            ),
        )
        print(
            f"[object_splatslam_v1] job={ctx.job_id} stage=splatslam done",
            flush=True,
        )
        if config.splatslam_enable_product_cleanup:
            _run_step(
                ctx=ctx,
                client=client,
                stage="support_plane",
                title="正在计算 canonical pose",
                detail="正在估计支撑面、up 方向和默认观察姿态。",
                progress_fraction=0.76,
                action=run_support_plane,
            )
            _run_step(
                ctx=ctx,
                client=client,
                stage="splat_cleanup",
                title="正在整理对象 splat",
                detail="正在裁除背景并保留合理支撑面 patch。",
                progress_fraction=0.84,
                action=run_splat_cleanup,
            )
        default_manifest_holder: dict[str, dict[str, dict[str, object]]] = {}
        _run_step(
            ctx=ctx,
            client=client,
            stage="publish_default_splat",
            title="正在整理默认对象 splat",
            detail="正在写出 raw native splat、cleaned 对比资产、海报和 viewer manifest。",
            progress_fraction=0.82,
            action=lambda current_ctx: default_manifest_holder.setdefault(
                "manifest",
                publish_default_splat(current_ctx, client, storage),
            ),
        )
        default_manifest = default_manifest_holder["manifest"]
        _run_step(
            ctx=ctx,
            client=client,
            stage="artifact_upload",
            title="正在回传默认对象成品",
            detail="正在上传默认成品清单并通知手机准备下载。",
            progress_fraction=0.88,
            action=lambda current_ctx: client.upload_artifact_manifest(
                current_ctx.job_id,
                {
                    "worker_id": worker_id,
                    "manifest": default_manifest,
                },
            ),
        )

        if ctx.should_run_optional_mesh_export:
            _run_step(
                ctx=ctx,
                client=client,
                stage="optional_mesh_export",
                title="正在导出可选 mesh",
                detail="正在生成可选 mesh 资产，供兼容查看和导出。",
                progress_fraction=0.92,
                action=run_optional_mesh_export,
            )

        if ctx.should_run_hq_refine:
            _run_step(
                ctx=ctx,
                client=client,
                stage="hq_refine",
                title="正在执行 HQ refine",
                detail="正在基于默认对象 splat 继续生成高清增强结果。",
                progress_fraction=0.96,
                action=lambda current_ctx: run_hq_refine(current_ctx),
            )
            hq_manifest_holder: dict[str, dict[str, dict[str, object]]] = {}
            _run_step(
                ctx=ctx,
                client=client,
                stage="publish_hq",
                title="正在整理高清增强结果",
                detail="正在写出 HQ splat 并更新 viewer manifest。",
                progress_fraction=0.98,
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
                title="正在回传高清增强结果",
                detail="正在上传更新后的结果清单并准备回到手机。",
                progress_fraction=0.99,
                action=lambda current_ctx: client.upload_artifact_manifest(
                    current_ctx.job_id,
                    {
                        "worker_id": worker_id,
                        "manifest": hq_manifest,
                    },
                ),
            )

        client.complete(ctx.job_id, worker_id, "已完成", "对象成品已准备好")
        print(
            f"[object_splatslam_v1] job={ctx.job_id} completed",
            flush=True,
        )
        return True
    except Exception as exc:
        print(
            f"[object_splatslam_v1] job={ctx.job_id} failed stage={ctx.current_stage} error={exc}",
            flush=True,
        )
        client.fail(
            ctx.job_id,
            worker_id,
            reason="object_splatslam_failed",
            detail=str(exc),
            stage=ctx.current_stage,
        )
        return True
