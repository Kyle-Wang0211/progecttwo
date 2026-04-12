from __future__ import annotations

import threading
import time
from typing import Any

from .context import JobContext
from .paths import ensure_job_layout
from .pipeline.bridge_slam3r_scene import bridge_slam3r_scene
from .pipeline.curate_frames import curate_frames
from .pipeline.download_input import download_input
from .pipeline.extract_frames import extract_frames
from .pipeline.run_bake_default_texture import run_bake_default_texture
from .pipeline.publish_default_mesh import publish_default_mesh
from .pipeline.run_matcha_mesh import run_matcha_mesh
from .pipeline.run_optimize_default_mesh import run_optimize_default_mesh
from .pipeline.run_slam3r import run_slam3r
from .pipeline.run_sparse2dgs_surface import run_sparse2dgs_surface
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
    if stage in {"optimize_default_mesh", "bake_default_texture", "publish_default_mesh", "artifact_upload"}:
        return "exporting"
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
        action=lambda: action(ctx, tracker),
    )
    print(
        f"[object_slam3r_surface_v1] job={ctx.job_id} stage={stage} done",
        flush=True,
    )


def _map_sparse2dgs_training_progress(step: int, total: int) -> float:
    clamped_total = max(total, 1)
    ratio = min(max(step / clamped_total, 0.0), 1.0)
    return 0.68 + (0.82 - 0.68) * ratio


def _make_sparse2dgs_progress_callback(
    *,
    client: ControlPlaneClient,
    ctx: JobContext,
    tracker: _RuntimeTracker,
):
    def callback(step: int, total: int) -> None:
        progress_fraction = _map_sparse2dgs_training_progress(step, total)
        detail = (
            "正在按 CVPR 2025 Sparse2DGS 生成稳定、完整、准确的表面。"
            f" 当前训练 {step}/{total}。"
        )
        _update_tracker_runtime(
            client,
            ctx,
            tracker,
            detail=detail,
            progress_fraction=progress_fraction,
            metrics={
                "training_step": str(step),
                "training_total_steps": str(total),
                "training_progress_percent": f"{(step / max(total, 1)) * 100:.1f}",
            },
        )

    return callback


def _map_optimize_default_mesh_progress(local_progress: float) -> float:
    ratio = min(max(local_progress, 0.0), 1.0)
    return 0.88 + (0.94 - 0.88) * ratio


def _make_optimize_default_mesh_progress_callback(
    *,
    client: ControlPlaneClient,
    ctx: JobContext,
    tracker: _RuntimeTracker,
):
    def callback(payload: dict[str, Any]) -> None:
        local_progress = float(payload.get("progress", 0.0))
        title = str(payload.get("title") or "正在优化默认网格")
        detail = str(payload.get("detail") or "正在清理碎片、修法线并收敛到移动端友好的默认 mesh 预算。")
        metrics = {
            "optimize_local_progress_percent": f"{min(max(local_progress, 0.0), 1.0) * 100:.1f}",
        }
        extra_metrics = payload.get("metrics")
        if isinstance(extra_metrics, dict):
            metrics.update({str(key): str(value) for key, value in extra_metrics.items()})
        _update_tracker_runtime(
            client,
            ctx,
            tracker,
            title=title,
            detail=detail,
            progress_fraction=_map_optimize_default_mesh_progress(local_progress),
            metrics=metrics,
        )

    return callback


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
            action=lambda current_ctx, _tracker: download_input(current_ctx, client=client, storage=storage),
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="curate",
            title="正在抽取候选帧",
            detail="正在从录制素材中提取用于论文主链的候选帧。",
            progress_fraction=0.20,
            action=lambda current_ctx, _tracker: extract_frames(current_ctx),
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="curate",
            title="正在筛选有效关键帧",
            detail="正在按统一标准筛选可供 SLAM3R 使用的关键帧。",
            progress_fraction=0.28,
            action=lambda current_ctx, _tracker: curate_frames(current_ctx),
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="slam3r_reconstruct",
            title="正在执行 SLAM3R 主干重建",
            detail="正在按 CVPR 2025 SLAM3R 生成单目视频的稠密几何主干。",
            progress_fraction=0.48,
            action=lambda current_ctx, _tracker: run_slam3r(current_ctx),
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="slam3r_scene_contract",
            title="正在整理官方场景契约",
            detail="正在把 SLAM3R 官方输出转换成 Sparse2DGS 官方可读取的 COLMAP 场景。",
            progress_fraction=0.58,
            action=lambda current_ctx, _tracker: bridge_slam3r_scene(current_ctx),
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="sparse2dgs_surface",
            title="正在执行 Sparse2DGS 表面重建",
            detail="正在按 CVPR 2025 Sparse2DGS 生成稳定、完整、准确的表面。",
            progress_fraction=0.68,
            action=lambda current_ctx, tracker: run_sparse2dgs_surface(
                current_ctx,
                progress_callback=_make_sparse2dgs_progress_callback(
                    client=client,
                    ctx=current_ctx,
                    tracker=tracker,
                ),
            ),
        )

        _run_step(
            ctx=ctx,
            client=client,
            stage="matcha_mesh_extract",
            title="正在执行 MAtCha 网格提取",
            detail="正在按 CVPR 2025 MAtCha 从稳定 surface 中提取默认 mesh。",
            progress_fraction=0.82,
            action=lambda current_ctx, _tracker: run_matcha_mesh(current_ctx),
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="optimize_default_mesh",
            title="正在优化默认网格",
            detail="正在清理碎片、修法线并收敛到移动端友好的默认 mesh 预算。",
            progress_fraction=0.88,
            action=lambda current_ctx, tracker: run_optimize_default_mesh(
                current_ctx,
                progress_callback=_make_optimize_default_mesh_progress_callback(
                    client=client,
                    ctx=current_ctx,
                    tracker=tracker,
                ),
            ),
        )
        _run_step(
            ctx=ctx,
            client=client,
            stage="bake_default_texture",
            title="正在投影照片纹理",
            detail="正在把多视图照片信息投影到默认 mesh，并写出 GLB 成品。",
            progress_fraction=0.94,
            action=lambda current_ctx, _tracker: run_bake_default_texture(current_ctx),
        )

        default_manifest_holder: dict[str, dict[str, dict[str, object]]] = {}
        _run_step(
            ctx=ctx,
            client=client,
            stage="publish_default_mesh",
            title="正在整理默认网格成品",
            detail="正在写出默认 mesh、海报和 viewer manifest。",
            progress_fraction=0.97,
            action=lambda current_ctx, _tracker: default_manifest_holder.setdefault(
                "manifest",
                publish_default_mesh(current_ctx, client, storage),
            ),
        )
        default_manifest = default_manifest_holder["manifest"]
        _run_step(
            ctx=ctx,
            client=client,
            stage="artifact_upload",
            title="正在回传默认网格成品",
            detail="正在上传默认 mesh 成品清单并通知手机准备下载。",
            progress_fraction=0.99,
            action=lambda current_ctx, _tracker: client.upload_artifact_manifest(
                current_ctx.job_id,
                default_manifest,
                worker_id=current_ctx.worker_id,
            ),
        )

        client.complete(ctx.job_id, worker_id, "已完成", "默认 mesh 成品已准备好")
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
