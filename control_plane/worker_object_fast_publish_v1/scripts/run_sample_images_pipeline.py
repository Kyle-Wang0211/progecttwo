from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from worker_object_fast_publish_v1.artifacts import ensure_default_publish_files, write_viewer_manifest
from worker_object_fast_publish_v1.context import JobContext
from worker_object_fast_publish_v1.paths import ensure_job_layout
from worker_object_fast_publish_v1.pipeline.curate_frames import curate_frames
from worker_object_fast_publish_v1.pipeline.run_cleanup import run_cleanup
from worker_object_fast_publish_v1.pipeline.run_masks import run_masks
from worker_object_fast_publish_v1.pipeline.run_openmvs import run_openmvs
from worker_object_fast_publish_v1.pipeline.run_sfm import run_sfm
from worker_object_fast_publish_v1.pipeline.run_support_plane import run_support_plane


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--images-dir", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--job-id", default="manual-sample")
    return parser.parse_args()


def build_context(*, images_dir: Path, work_dir: Path, job_id: str) -> JobContext:
    assignment = {
        "job_id": job_id,
        "input": {"storage_key": f"manual/{job_id}/images"},
        "pipeline_profile": {
            "strategy": "object_fast_publish_v1",
            "hq_refine": "off",
            "stack": {
                "sfm": "pycolmap",
                "mask": "sam2",
                "surface": "openmvs",
                "cleanup": "open3d",
            },
        },
        "output_prefix": f"manual-artifacts/{job_id}/",
    }
    root_dir = work_dir / job_id
    input_dir = root_dir / "input"
    output_dir = root_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    ctx = JobContext(
        job_id=job_id,
        worker_id="manual",
        assignment=assignment,
        root_dir=root_dir,
        input_dir=input_dir,
        output_dir=output_dir,
        input_video=input_dir / "manual.images",
        output_prefix=f"manual-artifacts/{job_id}/",
        should_run_hq_refine=False,
    )
    ensure_job_layout(ctx)
    assert ctx.frames_dir is not None
    for source in sorted(images_dir.iterdir()):
        if source.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        target_name = source.name
        if source.suffix.lower() == ".png":
            target_name = f"{source.stem}.jpg"
            try:
                from PIL import Image

                with Image.open(source) as image:
                    image.convert("RGB").save(ctx.frames_dir / target_name, format="JPEG", quality=95)
                continue
            except Exception:
                pass
        shutil.copy2(source, ctx.frames_dir / target_name)
    return ctx


def main() -> None:
    args = parse_args()
    images_dir = Path(args.images_dir).expanduser().resolve()
    work_dir = Path(args.work_dir).expanduser().resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    ctx = build_context(images_dir=images_dir, work_dir=work_dir, job_id=args.job_id)
    curate_frames(ctx)
    run_sfm(ctx)
    run_masks(ctx)
    run_support_plane(ctx)
    run_openmvs(ctx)
    run_cleanup(ctx)
    files = ensure_default_publish_files(ctx)
    viewer_manifest = write_viewer_manifest(ctx, hq_ready=False)
    summary = {
        "job_id": ctx.job_id,
        "curated_dir": str(ctx.curated_dir),
        "support_summary": str(ctx.support_dir / "support_plane.json"),
        "surface_dir": str(ctx.surface_dir),
        "cleanup_summary": str(ctx.surface_dir / "cleanup.json"),
        "default_glb": str(files["default_glb"]),
        "poster": str(files["poster"]),
        "viewer_manifest": str(viewer_manifest),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
