#!/usr/bin/env python3
import importlib
import inspect
import os
import re
from pathlib import Path


def fail(message: str) -> None:
    raise SystemExit(f"PRECHECK_RUNTIME_FAIL {message}")


def main() -> None:
    repo_root = Path(os.environ.get("WILDGS_REPO_ROOT", "/root/gs_refs/WildGS-SLAM.clean")).resolve()
    expected_raster_root = (repo_root / "thirdparty" / "diff-gaussian-rasterization-w-pose").resolve()
    trajectory_filler = (repo_root / "src" / "trajectory_filler.py").resolve()
    mapper_py = (repo_root / "src" / "mapper.py").resolve()
    slam_utils_py = (repo_root / "src" / "utils" / "slam_utils.py").resolve()

    print(f"WILDGS_REPO_ROOT {repo_root}")
    print(f"EXPECTED_RASTER_ROOT {expected_raster_root}")
    print(f"TRAJECTORY_FILLER {trajectory_filler}")
    print(f"MAPPER_PY {mapper_py}")
    print(f"SLAM_UTILS_PY {slam_utils_py}")
    print(f"PYTHONPATH {os.environ.get('PYTHONPATH', '')}")

    if not trajectory_filler.is_file():
        fail(f"missing_trajectory_filler {trajectory_filler}")
    filler_text = trajectory_filler.read_text(encoding="utf-8")
    patched_markers = (
        "buffer_capacity = int(self.video.timestamp.shape[0])",
        "temp_slots = max(1, buffer_capacity - current_count)",
        "chunk_size = min(16, temp_slots)",
    )
    official_markers = (
        "if len(timestamps) == 16:",
        "if len(timestamps) > 0:",
    )
    if all(marker in filler_text for marker in patched_markers):
        print("TRAJECTORY_FILLER_MODE patched")
    elif all(marker in filler_text for marker in official_markers):
        print("TRAJECTORY_FILLER_MODE official")
    else:
        fail(
            "trajectory_filler_unknown_variant "
            f"patched_missing={[marker for marker in patched_markers if marker not in filler_text]} "
            f"official_missing={[marker for marker in official_markers if marker not in filler_text]}"
        )

    if not mapper_py.is_file():
        fail(f"missing_mapper_py {mapper_py}")
    if not slam_utils_py.is_file():
        fail(f"missing_slam_utils_py {slam_utils_py}")
    mapper_text = mapper_py.read_text(encoding="utf-8")
    slam_utils_text = slam_utils_py.read_text(encoding="utf-8")
    mapper_passes_opacity = bool(re.search(r"get_loss_mapping\([\s\S]{0,240}?viewpoint,\s*\n?\s*opacity,\s*\n?\s*initialization=", mapper_text))
    slam_utils_accepts_opacity = bool(re.search(r"def\s+get_loss_mapping\([^\)]*opacity\s*=", slam_utils_text))
    print(f"MAPPER_PASSES_OPACITY {int(mapper_passes_opacity)}")
    print(f"SLAM_UTILS_ACCEPTS_OPACITY {int(slam_utils_accepts_opacity)}")
    if mapper_passes_opacity and not slam_utils_accepts_opacity:
        fail("mapper_slam_utils_signature_mismatch")
    savefig_markers = (
        "uncertainty_map = uncertainty_map.detach().cpu()",
        "ssim_loss = ssim_loss.detach().cpu()",
        "uncertainty_map = uncertainty_map.mean(dim=0)",
        "ssim_loss = ssim_loss.mean(dim=0)",
        "save_fig_everything_failed",
    )
    missing_savefig_markers = [marker for marker in savefig_markers if marker not in mapper_text]
    if missing_savefig_markers:
        fail(f"mapper_savefig_cpu_patch_missing {missing_savefig_markers}")

    try:
        raster = importlib.import_module("diff_gaussian_rasterization")
    except Exception as exc:
        fail(f"cannot_import_diff_gaussian_rasterization {exc!r}")

    raster_path = Path(getattr(raster, "__file__", "")).resolve()
    print(f"RASTER_MODULE {raster_path}")
    if expected_raster_root not in raster_path.parents and raster_path != expected_raster_root:
        fail(f"wrong_raster_module {raster_path}")

    try:
        params = inspect.signature(raster.GaussianRasterizationSettings).parameters
    except Exception as exc:
        fail(f"cannot_inspect_raster_signature {exc!r}")

    print("RASTER_SIGNATURE", " ".join(params.keys()))
    if "prefiltered" not in params:
        fail("raster_signature_missing_prefiltered")

    for mod_name in ("droid_backends", "lietorch_backends"):
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:
            fail(f"cannot_import_{mod_name} {exc!r}")
        mod_path = Path(getattr(mod, "__file__", "")).resolve()
        print(f"{mod_name.upper()}_MODULE {mod_path}")

    print("PRECHECK_RUNTIME_OK")


if __name__ == "__main__":
    main()
