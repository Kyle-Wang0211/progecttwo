from __future__ import annotations

from ..artifacts import build_hq_artifact_manifest, ensure_hq_publish_files, write_viewer_manifest
from ..context import JobContext
from ..runtime import ControlPlaneClient
from ..storage_client import ObjectStorageClient


def publish_hq(
    ctx: JobContext,
    client: ControlPlaneClient,
    storage: ObjectStorageClient,
    default_manifest: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    ctx.current_stage = "publish_hq"
    files = ensure_hq_publish_files(ctx)
    write_viewer_manifest(ctx, hq_ready=True)
    hq_asset = storage.upload_file(
        storage_key=f"{ctx.output_prefix.rstrip('/')}/hq/hq.splat",
        local_path=files["hq_splat"],
        artifact_type="hq_splat",
    )
    viewer_manifest_asset = storage.upload_file(
        storage_key=f"{ctx.output_prefix.rstrip('/')}/default/viewer_manifest.json",
        local_path=ctx.default_publish_dir / "viewer_manifest.json",
        artifact_type="viewer_manifest_json",
    )
    return build_hq_artifact_manifest(
        default_manifest=default_manifest,
        viewer_manifest_asset=viewer_manifest_asset,
        hq_asset=hq_asset,
    )
