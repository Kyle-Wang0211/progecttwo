from __future__ import annotations

from ..artifacts import (
    build_default_artifact_manifest,
    ensure_default_publish_files,
    write_viewer_manifest,
)
from ..context import JobContext
from ..runtime import ControlPlaneClient, push_runtime
from ..storage_client import ObjectStorageClient


def publish_default(
    ctx: JobContext,
    client: ControlPlaneClient,
    storage: ObjectStorageClient,
) -> dict[str, dict[str, object]]:
    ctx.current_stage = "publish_default"
    files = ensure_default_publish_files(ctx)
    write_viewer_manifest(ctx, hq_ready=False)

    default_asset = storage.upload_file(
        storage_key=f"{ctx.output_prefix.rstrip('/')}/default/default_object.glb",
        local_path=files["default_glb"],
        artifact_type="default_object_glb",
    )
    preview_asset = storage.upload_file(
        storage_key=f"{ctx.output_prefix.rstrip('/')}/default/poster.png",
        local_path=files["poster"],
        artifact_type="poster_png",
    )
    viewer_manifest_asset = storage.upload_file(
        storage_key=f"{ctx.output_prefix.rstrip('/')}/default/viewer_manifest.json",
        local_path=files["viewer_manifest"],
        artifact_type="viewer_manifest_json",
    )

    push_runtime(
        client,
        job_id=ctx.job_id,
        worker_id=ctx.worker_id,
        state="exporting",
        stage=ctx.current_stage,
        title="正在发布默认成品",
        detail="textured mesh/surface first result 已生成。",
        progress_fraction=0.92,
    )
    return build_default_artifact_manifest(
        default_asset=default_asset,
        preview_asset=preview_asset,
        viewer_manifest_asset=viewer_manifest_asset,
    )
