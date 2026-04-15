from __future__ import annotations

from ..artifacts import (
    build_default_artifact_manifest,
    ensure_default_publish_files,
    write_viewer_manifest,
)
from ..context import JobContext
from ..quality_gate import finalize_quality_report, quality_report_path
from ..runtime import ControlPlaneClient
from ..storage_client import ObjectStorageClient


def publish_default_mesh(
    ctx: JobContext,
    client: ControlPlaneClient,
    storage: ObjectStorageClient,
) -> dict[str, object]:
    ctx.current_stage = "publish_default_mesh"
    report = finalize_quality_report(ctx)
    publish_allowed = bool(report.get("publish_allowed"))
    failed_cards = [str(card) for card in (report.get("failed_cards") or [])]
    files = ensure_default_publish_files(ctx)
    write_viewer_manifest(
        ctx,
        hq_ready=publish_allowed,
        publish_allowed=publish_allowed,
        failed_cards=failed_cards,
    )
    report_path = quality_report_path(ctx)

    default_asset = storage.upload_file(
        storage_key=f"{ctx.output_prefix.rstrip('/')}/default/{files['default_asset'].name}",
        local_path=files["default_asset"],
        artifact_type=f"default_mesh_{files['default_asset'].suffix.lower().lstrip('.')}",
    )
    preview_asset = storage.upload_file(
        storage_key=f"{ctx.output_prefix.rstrip('/')}/default/{files['poster'].name}",
        local_path=files["poster"],
        artifact_type="poster_png",
    )
    viewer_manifest_asset = storage.upload_file(
        storage_key=f"{ctx.output_prefix.rstrip('/')}/default/{files['viewer_manifest'].name}",
        local_path=files["viewer_manifest"],
        artifact_type="viewer_manifest_json",
    )
    quality_report_asset = None
    if report_path.exists():
        quality_report_asset = storage.upload_file(
            storage_key=f"{ctx.output_prefix.rstrip('/')}/default/{report_path.name}",
            local_path=report_path,
            artifact_type="quality_report_json",
        )
    artifact_manifest = build_default_artifact_manifest(
        default_asset=default_asset,
        preview_asset=preview_asset,
        viewer_manifest_asset=viewer_manifest_asset,
        quality_report_asset=quality_report_asset,
        publish_allowed=publish_allowed,
        failed_cards=failed_cards,
    )
    return {
        "artifact_manifest": artifact_manifest,
        "quality_report": report,
    }
