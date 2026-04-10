from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..config import config
from ..context import JobContext


def _resolve_binary(bin_name: str) -> str:
    if config.openmvs_bin_dir:
        candidate = Path(config.openmvs_bin_dir).expanduser() / bin_name
        if candidate.exists():
            return str(candidate)
    resolved = shutil.which(bin_name)
    if resolved:
        return resolved
    return bin_name


def _run_logged(command: list[str], *, log_path: Path, cwd: Path | None = None) -> None:
    result = subprocess.run(command, capture_output=True, text=True, cwd=str(cwd) if cwd else None)
    log_path.write_text(
        json.dumps(
            {
                "command": command,
                "cwd": str(cwd) if cwd else None,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"openmvs_command_failed:{Path(command[0]).name}:{result.stderr.strip() or result.stdout.strip()}"
        )


def run_openmvs_reconstruct(ctx: JobContext) -> None:
    assert ctx.surface_dir is not None
    assert ctx.curated_dir is not None
    assert ctx.sfm_dir is not None

    sfm_summary_path = ctx.sfm_dir / "pycolmap.json"
    if not sfm_summary_path.exists():
        raise RuntimeError("openmvs_missing_sfm_summary")
    sfm_summary = json.loads(sfm_summary_path.read_text(encoding="utf-8"))
    sparse_model_dir = Path(str(sfm_summary["sparse_model_dir"]))

    undistorted_dir = ctx.surface_dir / "undistorted"
    if undistorted_dir.exists():
        shutil.rmtree(undistorted_dir)
    undistorted_dir.mkdir(parents=True, exist_ok=True)

    colmap_bin = shutil.which(config.colmap_bin) or config.colmap_bin
    image_undistorter_cmd = [
        colmap_bin,
        "image_undistorter",
        "--image_path",
        str(ctx.curated_dir),
        "--input_path",
        str(sparse_model_dir),
        "--output_path",
        str(undistorted_dir),
        "--output_type",
        "COLMAP",
    ]
    _run_logged(
        image_undistorter_cmd,
        log_path=ctx.surface_dir / "01_image_undistorter.json",
        cwd=ctx.surface_dir,
    )

    interface_colmap_bin = _resolve_binary(config.openmvs_interface_colmap_bin)
    densify_bin = _resolve_binary(config.openmvs_densify_bin)
    reconstruct_bin = _resolve_binary(config.openmvs_reconstruct_bin)
    texture_bin = _resolve_binary(config.openmvs_texture_bin)

    scene_mvs = ctx.surface_dir / "scene.mvs"
    dense_mvs = ctx.surface_dir / "scene_dense.mvs"
    mesh_mvs = ctx.surface_dir / "scene_mesh.mvs"
    textured_scene = ctx.surface_dir / "scene_textured.obj"

    interface_cmd = [
        interface_colmap_bin,
        "-i",
        str(undistorted_dir),
        "-o",
        str(scene_mvs),
        "--image-folder",
        str(undistorted_dir / "images"),
    ]
    _run_logged(
        interface_cmd,
        log_path=ctx.surface_dir / "02_interface_colmap.json",
        cwd=ctx.surface_dir,
    )

    densify_cmd = [
        densify_bin,
        "-i",
        str(scene_mvs),
        "-o",
        str(dense_mvs),
    ]
    _run_logged(
        densify_cmd,
        log_path=ctx.surface_dir / "03_densify_point_cloud.json",
        cwd=ctx.surface_dir,
    )

    reconstruct_cmd = [
        reconstruct_bin,
        "-i",
        str(dense_mvs),
        "-o",
        str(mesh_mvs),
    ]
    _run_logged(
        reconstruct_cmd,
        log_path=ctx.surface_dir / "04_reconstruct_mesh.json",
        cwd=ctx.surface_dir,
    )

    texture_cmd = [
        texture_bin,
        "-i",
        str(mesh_mvs),
        "-o",
        str(textured_scene),
        "--export-type",
        "obj",
    ]
    _run_logged(
        texture_cmd,
        log_path=ctx.surface_dir / "05_texture_mesh.json",
        cwd=ctx.surface_dir,
    )

    (ctx.surface_dir / "openmvs.json").write_text(
        json.dumps(
            {
                "backend": "openmvs",
                "scene_mvs": str(scene_mvs),
                "dense_mvs": str(dense_mvs),
                "mesh_mvs": str(mesh_mvs),
                "textured_scene": str(textured_scene),
                "textured_obj": str(textured_scene),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
