from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..config import config
from ..context import JobContext


def _run_colmap(command: list[str], *, log_path: Path) -> None:
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    result = subprocess.run(command, capture_output=True, text=True, env=env)
    payload = {
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    log_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            f"colmap_command_failed:{Path(command[0]).name}:{result.stderr.strip() or result.stdout.strip()}"
        )


def run_pycolmap(ctx: JobContext) -> None:
    assert ctx.sfm_dir is not None
    assert ctx.curated_dir is not None

    colmap_bin = shutil.which(config.colmap_bin) or config.colmap_bin
    database_path = ctx.sfm_dir / "database.db"
    sparse_root = ctx.sfm_dir / "sparse"
    text_model_dir = ctx.sfm_dir / "text_model"

    if database_path.exists():
        database_path.unlink()
    if sparse_root.exists():
        shutil.rmtree(sparse_root)
    if text_model_dir.exists():
        shutil.rmtree(text_model_dir)
    sparse_root.mkdir(parents=True, exist_ok=True)
    text_model_dir.mkdir(parents=True, exist_ok=True)

    use_gpu = "1" if config.colmap_use_gpu else "0"
    single_camera = "1" if config.colmap_single_camera else "0"

    feature_command = [
        colmap_bin,
        "feature_extractor",
        "--database_path",
        str(database_path),
        "--image_path",
        str(ctx.curated_dir),
        "--ImageReader.single_camera",
        single_camera,
        "--ImageReader.camera_model",
        config.colmap_camera_model,
        "--SiftExtraction.use_gpu",
        use_gpu,
    ]
    _run_colmap(feature_command, log_path=ctx.sfm_dir / "01_feature_extractor.json")

    matcher_command = [
        colmap_bin,
        "exhaustive_matcher",
        "--database_path",
        str(database_path),
        "--SiftMatching.use_gpu",
        use_gpu,
    ]
    _run_colmap(matcher_command, log_path=ctx.sfm_dir / "02_exhaustive_matcher.json")

    mapper_command = [
        colmap_bin,
        "mapper",
        "--database_path",
        str(database_path),
        "--image_path",
        str(ctx.curated_dir),
        "--output_path",
        str(sparse_root),
        "--Mapper.ba_refine_focal_length",
        "0",
        "--Mapper.ba_refine_principal_point",
        "0",
        "--Mapper.ba_refine_extra_params",
        "0",
    ]
    _run_colmap(mapper_command, log_path=ctx.sfm_dir / "03_mapper.json")

    model_dirs = sorted(path for path in sparse_root.iterdir() if path.is_dir())
    if not model_dirs:
        raise RuntimeError("colmap_mapper_produced_no_model")
    sparse_model_dir = model_dirs[0]

    model_converter_command = [
        colmap_bin,
        "model_converter",
        "--input_path",
        str(sparse_model_dir),
        "--output_path",
        str(text_model_dir),
        "--output_type",
        "TXT",
    ]
    _run_colmap(model_converter_command, log_path=ctx.sfm_dir / "04_model_converter.json")

    image_names = sorted(path.name for path in ctx.curated_dir.glob("*.jpg"))
    (ctx.sfm_dir / "pycolmap.json").write_text(
        json.dumps(
            {
                "backend": "colmap_cli",
                "colmap_bin": colmap_bin,
                "database_path": str(database_path),
                "sparse_model_dir": str(sparse_model_dir),
                "text_model_dir": str(text_model_dir),
                "image_count": len(image_names),
                "images": image_names,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
