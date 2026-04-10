from __future__ import annotations

from typing import Optional

from ..context import JobContext
from ..runtime import ControlPlaneClient, push_runtime
from ..storage_client import ObjectStorageClient


def download_input(
    ctx: JobContext,
    *,
    client: ControlPlaneClient,
    storage: ObjectStorageClient,
) -> None:
    input_payload = ctx.assignment.get("input") or {}
    input_mode = str(input_payload.get("mode") or "").strip().lower()
    download_url = input_payload.get("download_url")
    manifest_url = input_payload.get("manifest_url")
    total_bytes = int(input_payload.get("size_bytes") or 0) or None

    ctx.current_stage = "downloading_input"
    last_progress_report = -1

    def on_progress(bytes_written: int, expected_total_bytes: Optional[int]) -> None:
        nonlocal last_progress_report
        resolved_total = expected_total_bytes or total_bytes
        fraction = None
        if resolved_total and resolved_total > 0:
            fraction = max(0.0, min(0.18, (bytes_written / resolved_total) * 0.18))
        progress_bucket = bytes_written // (4 * 1024 * 1024)
        if progress_bucket == last_progress_report:
            return
        last_progress_report = progress_bucket
        push_runtime(
            client,
            job_id=ctx.job_id,
            worker_id=ctx.worker_id,
            state="reconstructing",
            stage=ctx.current_stage,
            title="正在拉取对象素材",
            detail="新远端 worker 正在下载 guided capture 输入。",
            progress_fraction=fraction,
            progress_basis="input_download",
            metrics={"downloaded_bytes": bytes_written},
        )

    if input_mode == "chunked_stream":
        if not manifest_url:
            raise RuntimeError("chunk_manifest_url_missing")
        storage.download_chunk_stream_to_path(
            manifest_url,
            ctx.input_video,
            progress_callback=on_progress,
        )
        return

    if not download_url:
        raise RuntimeError("input_download_url_missing")
    storage.download_to_path(download_url, ctx.input_video, progress_callback=on_progress)
