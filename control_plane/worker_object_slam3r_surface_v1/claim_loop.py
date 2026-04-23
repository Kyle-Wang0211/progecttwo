from __future__ import annotations

import threading
import time
from typing import Any, Optional

from .config import config
from .context import JobContext
from .paths import ensure_job_layout
from .pipeline.bridge_slam3r_scene import bridge_slam3r_scene
from .pipeline.curate_frames import curate_frames
from .pipeline.curate_from_client import curate_from_client
from .pipeline.download_input import download_input
from .pipeline.extract_frames import extract_frames
from .pipeline.run_bake_default_texture import run_bake_default_texture
from .pipeline.run_poisson_mvs_mesh import run_poisson_mvs_mesh
from .pipeline.publish_default_mesh import publish_default_mesh
from .pipeline.run_matcha_mesh import run_matcha_mesh
from .pipeline.run_optimize_default_mesh import run_optimize_default_mesh
from .pipeline.run_depth_prediction import run_depth_prediction
from .pipeline.run_holdout_lpips_advisory import compute_holdout_lpips_advisory
from .pipeline.run_slam3r import run_slam3r
from .pipeline.run_sparse2dgs_surface import run_sparse2dgs_surface
from .pipeline.run_twodgs_direct import run_twodgs_direct
from .pipeline.run_vggt_geometry import run_vggt_geometry
from .quality_gate import hq_gate_failure_reason
from .runtime import ControlPlaneClient, push_runtime
from .stage_hygiene import StageTimingRecorder, release_parent_stage_memory
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
    recorder: Optional[StageTimingRecorder] = None,
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
    stage_t0 = time.monotonic()
    # try/finally：即便 action 抛异常，也要释放父堆 + 记录 stage_timings，
    # 这样我们能看清任务到底在哪一层死的、死前父进程占了多少内存。
    try:
        _run_action_with_keepalive(
            ctx=ctx,
            client=client,
            tracker=tracker,
            action=lambda: action(ctx, tracker),
        )
    finally:
        stage_elapsed_sec = time.monotonic() - stage_t0
        release_metrics = release_parent_stage_memory()
        if recorder is not None:
            recorder.record_stage(
                stage=stage,
                title=title,
                progress_fraction=progress_fraction,
                elapsed_sec=stage_elapsed_sec,
                release_metrics=release_metrics,
            )
        print(
            f"[object_slam3r_surface_v1] job={ctx.job_id} stage={stage} done "
            f"elapsed_sec={stage_elapsed_sec:.2f} "
            f"pre_release_rss_mb={release_metrics['pre_release_rss_mb']:.2f} "
            f"post_release_rss_mb={release_metrics['post_release_rss_mb']:.2f} "
            f"released_mb={release_metrics['released_mb']:.2f} "
            f"gc_collected={release_metrics['gc_collected_objects']} "
            f"malloc_trim={release_metrics['malloc_trim_called']}",
            flush=True,
        )


def _surface_stage_labels() -> tuple[str, str]:
    # Stage name/dir/json all hardcode "sparse2dgs" for historical reasons,
    # but the real trainer is chosen by config.gaussian_backend (dispatched
    # in sparse2dgs_adapter._active_gaussian_backend). UI must follow that.
    backend = str(getattr(config, "gaussian_backend", "twodgs") or "twodgs").lower()
    if backend == "sparse2dgs":
        return (
            "正在执行 Sparse2DGS 表面重建",
            "正在按 CVPR 2025 Sparse2DGS 生成稳定、完整、准确的表面。",
        )
    return (
        "正在执行 2DGS 表面重建",
        "正在按 SIGGRAPH 2024 2D Gaussian Splatting 生成稳定、完整、准确的表面(30-100 views 稠密重建)。",
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
        _, stage_detail = _surface_stage_labels()
        detail = f"{stage_detail} 当前训练 {step}/{total}。"
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


def _map_matcha_mesh_progress(local_progress: float) -> float:
    ratio = min(max(local_progress, 0.0), 1.0)
    return 0.82 + (0.88 - 0.82) * ratio


def _map_bake_default_texture_progress(local_progress: float) -> float:
    ratio = min(max(local_progress, 0.0), 1.0)
    return 0.94 + (0.97 - 0.94) * ratio


def _make_matcha_mesh_progress_callback(
    *,
    client: ControlPlaneClient,
    ctx: JobContext,
    tracker: _RuntimeTracker,
):
    def callback(payload: dict[str, Any]) -> None:
        local_progress = float(payload.get("progress", 0.0))
        title = str(payload.get("title") or "正在执行 MAtCha 网格提取")
        detail = str(payload.get("detail") or "正在从多窗口高分辨率表面里提取 HQ 主网格。")
        metrics = {
            "matcha_local_progress_percent": f"{min(max(local_progress, 0.0), 1.0) * 100:.1f}",
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
            progress_fraction=_map_matcha_mesh_progress(local_progress),
            metrics=metrics,
        )

    return callback


def _make_optimize_default_mesh_progress_callback(
    *,
    client: ControlPlaneClient,
    ctx: JobContext,
    tracker: _RuntimeTracker,
):
    def callback(payload: dict[str, Any]) -> None:
        local_progress = float(payload.get("progress", 0.0))
        title = str(payload.get("title") or "正在优化 HQ 网格")
        detail = str(payload.get("detail") or "正在清理碎片、修法线并收敛到 HQ-only 的开放表面质量门槛。")
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


def _make_bake_default_texture_progress_callback(
    *,
    client: ControlPlaneClient,
    ctx: JobContext,
    tracker: _RuntimeTracker,
):
    def callback(payload: dict[str, Any]) -> None:
        local_progress = float(payload.get("progress", 0.0))
        title = str(payload.get("title") or "正在投影 HQ 纹理")
        detail = str(payload.get("detail") or "正在把多视图高分辨率照片投影到 HQ 网格。")
        metrics = {
            "texture_local_progress_percent": f"{min(max(local_progress, 0.0), 1.0) * 100:.1f}",
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
            progress_fraction=_map_bake_default_texture_progress(local_progress),
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

    recorder = StageTimingRecorder(
        output_path=ctx.output_dir / "stage_timings.json",
        job_id=ctx.job_id,
    )

    try:
        _run_step(
            ctx=ctx,
            client=client,
            stage="download_input",
            title="正在准备输入素材",
            detail="正在下载录制素材并准备抽帧。",
            progress_fraction=0.12,
            action=lambda current_ctx, _tracker: download_input(current_ctx, client=client, storage=storage),
            recorder=recorder,
        )
        # C 架构 dispatch:如果 iOS 上传了 curated.json 到 input/,直接按它抽帧,
        # 跳过服务器端 extract_frames + curate_frames(az×el)。
        # 符合"质量 >> 灵活性,不做兜底"原则:curated.json 出错不降级回老路径。
        _curated_json_path = ctx.input_dir / "curated.json" if ctx.input_dir else None
        _has_client_curation = bool(_curated_json_path and _curated_json_path.exists())
        if _has_client_curation:
            _run_step(
                ctx=ctx,
                client=client,
                stage="curate",
                title="按客户端 curate 清单抽帧",
                detail="iOS 已在端上完成 dome curate,服务器跳过 az×el,直接按 timestamp 抽帧。",
                progress_fraction=0.24,
                action=lambda current_ctx, _tracker: curate_from_client(current_ctx, client=client),
                recorder=recorder,
            )
        else:
            _run_step(
                ctx=ctx,
                client=client,
                stage="curate",
                title="正在抽取候选帧",
                detail="正在从录制素材中提取用于论文主链的候选帧。",
                progress_fraction=0.20,
                action=lambda current_ctx, _tracker: extract_frames(current_ctx),
                recorder=recorder,
            )
            _run_step(
                ctx=ctx,
                client=client,
                stage="curate",
                title="正在整理关键帧",
                detail="正在把端上已接受素材映射成稳定、可复现的 HQ 关键帧集合。",
                progress_fraction=0.28,
                action=lambda current_ctx, _tracker: curate_frames(current_ctx),
                recorder=recorder,
            )
        # Geometry backend branch: slam3r (incremental, needs bridge) vs
        # vggt (feed-forward, writes contract directly). Selection via
        # config.geometry_backend env. Default slam3r for safety; flip to
        # vggt once production A/B confirms.
        _geometry_backend = str(getattr(config, "geometry_backend", "slam3r") or "slam3r").lower()
        if _geometry_backend == "vggt":
            _run_step(
                ctx=ctx,
                client=client,
                stage="vggt_geometry",
                title="正在执行 VGGT 几何感知",
                detail="正在按 CVPR 2025 Best Paper VGGT 一次前馈生成相机位姿 + 稠密初始点云。",
                progress_fraction=0.48,
                action=lambda current_ctx, tracker: run_vggt_geometry(
                    current_ctx,
                    progress_callback=lambda p: tracker.update(p) if hasattr(tracker, "update") else None,
                ),
                recorder=recorder,
            )
            # VGGT writes sparse2dgs_scene_contract.json itself; no bridge.
        else:
            _run_step(
                ctx=ctx,
                client=client,
                stage="slam3r_reconstruct",
                title="正在执行 SLAM3R 主干重建",
                detail="正在按 CVPR 2025 SLAM3R 生成单目视频的稠密几何主干。",
                progress_fraction=0.48,
                action=lambda current_ctx, _tracker: run_slam3r(current_ctx),
                recorder=recorder,
            )
            _run_step(
                ctx=ctx,
                client=client,
                stage="slam3r_scene_contract",
                title="正在整理官方场景契约",
                detail="正在把 SLAM3R 官方输出转换成 Sparse2DGS 官方可读取的 COLMAP 场景。",
                progress_fraction=0.58,
                action=lambda current_ctx, _tracker: bridge_slam3r_scene(current_ctx),
                recorder=recorder,
            )

        # Optional depth prior: DA V2 Small + Large with confidence map.
        # Only runs when explicitly enabled (default off until P3 consumes).
        if bool(getattr(config, "depth_prior_enabled", False)):
            _run_step(
                ctx=ctx,
                client=client,
                stage="depth_prediction",
                title="正在生成服务端深度先验",
                detail="正在跑 DA V2 Small + Large 并对齐到 VGGT 度量,生成 per-frame 深度+置信度。",
                progress_fraction=0.63,
                action=lambda current_ctx, tracker: run_depth_prediction(
                    current_ctx,
                    progress_callback=lambda p: tracker.update(p) if hasattr(tracker, "update") else None,
                ),
                recorder=recorder,
            )

        _mesh_backend = os.environ.get("AETHER_MESH_BACKEND", "matcha").lower()
        if _mesh_backend == "poisson_mvs":
            _run_step(
                ctx=ctx,
                client=client,
                stage="poisson_mvs_mesh",
                title="正在执行 Poisson + MVS Texturing",
                detail="Open3D Poisson 出几何 + MVS Texturing 投影多视图贴图。",
                progress_fraction=0.68,
                action=lambda current_ctx, tracker: run_poisson_mvs_mesh(
                    current_ctx,
                    progress_callback=lambda p: tracker.update(p) if hasattr(tracker, "update") else None,
                ),
                recorder=recorder,
            )
        else:
            _surface_title, _surface_detail = _surface_stage_labels()
            if _geometry_backend == "vggt":
                # VGGT path: sparse2dgs_adapter wants SLAM3R .npy preds which
                # VGGT doesn't produce. Use run_twodgs_direct, which invokes
                # run_2dgs_official.sh on the VGGT-built scene directly and
                # writes sparse2dgs_surface.json matcha will read.
                _train_action = lambda current_ctx, tracker: run_twodgs_direct(
                    current_ctx,
                    progress_callback=_make_sparse2dgs_progress_callback(
                        client=client,
                        ctx=current_ctx,
                        tracker=tracker,
                    ),
                )
            else:
                _train_action = lambda current_ctx, tracker: run_sparse2dgs_surface(
                    current_ctx,
                    progress_callback=_make_sparse2dgs_progress_callback(
                        client=client,
                        ctx=current_ctx,
                        tracker=tracker,
                    ),
                )
            _run_step(
                ctx=ctx,
                client=client,
                stage="sparse2dgs_surface",
                title=_surface_title,
                detail=_surface_detail,
                progress_fraction=0.68,
                action=_train_action,
                recorder=recorder,
            )

            _run_step(
                ctx=ctx,
                client=client,
                stage="matcha_mesh_extract",
                title="正在执行 MAtCha 网格提取",
                detail="正在按 CVPR 2025 MAtCha 从稳定 surface 中提取 HQ 网格主表面。",
                progress_fraction=0.82,
                action=lambda current_ctx, tracker: run_matcha_mesh(
                    current_ctx,
                    progress_callback=_make_matcha_mesh_progress_callback(
                        client=client,
                        ctx=current_ctx,
                        tracker=tracker,
                    ),
                ),
                recorder=recorder,
            )
            _run_step(
                ctx=ctx,
                client=client,
                stage="optimize_default_mesh",
                title="正在优化 HQ 网格",
                detail="正在清理碎片、修法线并收敛到 HQ-only 的开放表面质量门槛。",
                progress_fraction=0.88,
                action=lambda current_ctx, tracker: run_optimize_default_mesh(
                    current_ctx,
                    progress_callback=_make_optimize_default_mesh_progress_callback(
                        client=client,
                        ctx=current_ctx,
                        tracker=tracker,
                    ),
                ),
                recorder=recorder,
            )
            _run_step(
                ctx=ctx,
                client=client,
                stage="bake_default_texture",
                title="正在投影 HQ 纹理",
                detail="正在把多视图照片投影到 HQ 网格，并写出唯一的 HQ GLB 成品。",
                progress_fraction=0.94,
                action=lambda current_ctx, tracker: run_bake_default_texture(
                    current_ctx,
                    progress_callback=_make_bake_default_texture_progress_callback(
                        client=client,
                        ctx=current_ctx,
                        tracker=tracker,
                    ),
                ),
                recorder=recorder,
            )

        # Advisory: renders delivered mesh from non-training poses, records
        # LPIPS/SSIM to delivery/holdout_lpips_advisory.json. Pure diagnostic
        # — function catches all errors internally so it cannot fail the
        # stage. Calibrates topology-proxy gates against a perceptual signal.
        _run_step(
            ctx=ctx,
            client=client,
            stage="holdout_lpips_advisory",
            title="正在记录留一视图感知差异（仅诊断）",
            detail="从未参与训练的视角渲染成品，对比原图的 LPIPS/SSIM，仅记录不影响发布。",
            progress_fraction=0.955,
            action=lambda current_ctx, _tracker: compute_holdout_lpips_advisory(current_ctx),
            recorder=recorder,
        )

        publish_result_holder: dict[str, dict[str, object]] = {}
        _run_step(
            ctx=ctx,
            client=client,
            stage="publish_default_mesh",
            title="正在整理 HQ 成品",
            detail="正在写出 HQ GLB、海报、quality report 和 viewer manifest。",
            progress_fraction=0.97,
            action=lambda current_ctx, _tracker: publish_result_holder.setdefault(
                "result",
                publish_default_mesh(current_ctx, client, storage),
            ),
            recorder=recorder,
        )
        publish_result = publish_result_holder["result"]
        default_manifest = publish_result["artifact_manifest"]
        quality_report = publish_result["quality_report"]
        _run_step(
            ctx=ctx,
            client=client,
            stage="artifact_upload",
            title="正在回传 HQ 成品",
            detail="正在上传 HQ 成品清单并通知手机准备下载。",
            progress_fraction=0.99,
            action=lambda current_ctx, _tracker: client.upload_artifact_manifest(
                current_ctx.job_id,
                default_manifest,
                worker_id=current_ctx.worker_id,
            ),
            recorder=recorder,
        )

        failed_cards = [str(card) for card in (quality_report.get("failed_cards") or [])]
        publish_allowed = bool(quality_report.get("publish_allowed"))
        completion_detail = "HQ 成品已准备好"
        if not publish_allowed:
            completion_detail = "未达 HQ，仅供质检候选结果已上传"
            if failed_cards:
                completion_detail += f"。未通过：{', '.join(failed_cards)}。"

        client.complete(ctx.job_id, worker_id, "已完成", completion_detail)
        print(
            (
                f"[object_slam3r_surface_v1] job={ctx.job_id} completed publish_allowed={publish_allowed}"
                f" failed_cards={failed_cards}"
            ),
            flush=True,
        )
        recorder.finalize(status="ok", last_stage=ctx.current_stage)
        return True
    except Exception as exc:
        print(
            f"[object_slam3r_surface_v1] job={ctx.job_id} failed stage={ctx.current_stage} error={exc}",
            flush=True,
        )
        recorder.finalize(
            status="failed",
            error=str(exc),
            last_stage=ctx.current_stage,
        )
        client.fail(
            ctx.job_id,
            worker_id,
            reason="object_surface_failed",
            detail=str(exc),
            stage=ctx.current_stage,
        )
        return True
