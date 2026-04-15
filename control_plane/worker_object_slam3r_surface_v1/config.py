from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, str(default)))


def _env_flag(name: str, default: bool) -> bool:
    fallback = "1" if default else "0"
    return os.environ.get(name, fallback).strip().lower() in {"1", "true", "yes", "on"}


def _default_storage_region() -> str:
    explicit = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_REGION")
    if explicit:
        return explicit

    for env_name in ("CONTROL_PLANE_OBJECT_STORAGE_ENDPOINT_URL", "CONTROL_PLANE_OBJECT_STORAGE_PUBLIC_BASE_URL"):
        raw_url = os.environ.get(env_name, "").strip()
        if not raw_url:
            continue
        hostname = urlsplit(raw_url).hostname or raw_url
        hostname = hostname.strip().lower()
        if hostname.endswith(".digitaloceanspaces.com"):
            parts = hostname.split(".")
            if len(parts) >= 3:
                if len(parts) == 3:
                    return parts[0]
                return parts[1]
    return "auto"


def _configured_vram_mb() -> int:
    return _env_int("OBJECT_SLAM3R_SURFACE_VRAM_MB", _env_int("OBJECT_FAST_PUBLISH_VRAM_MB", 24576))


def _default_sparse2dgs_contract_max_image_size() -> int:
    vram_mb = _configured_vram_mb()
    if vram_mb <= 32768:
        return 1600
    if vram_mb <= 49152:
        return 1408
    if vram_mb <= 81920:
        return 1536
    return 1792


def _default_sparse2dgs_contract_min_image_size() -> int:
    vram_mb = _configured_vram_mb()
    if vram_mb <= 32768:
        return 640
    return 768


def _default_sparse2dgs_contract_total_pixel_budget() -> int:
    vram_mb = _configured_vram_mb()
    if vram_mb <= 32768:
        return 18_000_000
    if vram_mb <= 49152:
        return 24_000_000
    if vram_mb <= 81920:
        return 36_000_000
    return 56_000_000


def _default_matcha_command_template() -> str:
    script_path = (
        _DEFAULT_REPO_ROOT
        / "control_plane"
        / "worker_object_slam3r_surface_v1"
        / "scripts"
        / "run_matcha_official.sh"
    )
    return f"bash {script_path} {{repo_dir}} {{scene_dir}} {{sparse2dgs_dir}} {{output_dir}}"


def _default_slam3r_command_template() -> str:
    script_path = (
        _DEFAULT_REPO_ROOT
        / "control_plane"
        / "worker_object_slam3r_surface_v1"
        / "scripts"
        / "run_slam3r_official.sh"
    )
    return f"bash {script_path} {{repo_dir}} {{curated_dir}} {{output_dir}}"


def _default_sparse2dgs_command_template() -> str:
    script_path = (
        _DEFAULT_REPO_ROOT
        / "control_plane"
        / "worker_object_slam3r_surface_v1"
        / "scripts"
        / "run_sparse2dgs_official.sh"
    )
    return f"bash {script_path} {{repo_dir}} {{scene_dir}} {{output_dir}}"


def _default_sugar_command_template() -> str:
    script_path = (
        _DEFAULT_REPO_ROOT
        / "control_plane"
        / "worker_object_slam3r_surface_v1"
        / "scripts"
        / "run_sugar_official.sh"
    )
    return f"bash {script_path} {{repo_dir}} {{scene_dir}} {{gs_output_dir}} {{output_dir}}"


def _default_hgs_command_template() -> str:
    script_path = (
        _DEFAULT_REPO_ROOT
        / "control_plane"
        / "worker_object_slam3r_surface_v1"
        / "scripts"
        / "run_3dhgs_official.sh"
    )
    return f"bash {script_path} {{repo_dir}} {{scene_dir}} {{output_dir}}"


_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_LOCAL_ROOT = Path(
    os.environ.get("OBJECT_SLAM3R_SURFACE_LOCAL_ROOT")
    or os.environ.get("OBJECT_FAST_PUBLISH_LOCAL_ROOT")
    or "/tmp/aether-object-slam3r-surface"
)


@dataclass(frozen=True)
class WorkerConfig:
    control_plane_base_url: str = os.environ.get("CONTROL_PLANE_BASE_URL", "http://127.0.0.1:8080")
    control_plane_timeout_sec: float = _env_float("CONTROL_PLANE_TIMEOUT_SEC", 15.0)

    provider: str = os.environ.get("OBJECT_SLAM3R_SURFACE_PROVIDER") or os.environ.get("OBJECT_FAST_PUBLISH_PROVIDER", "unconfigured")
    region: str = os.environ.get("OBJECT_SLAM3R_SURFACE_REGION") or os.environ.get("OBJECT_FAST_PUBLISH_REGION", "us-central")
    instance_label: str = os.environ.get("OBJECT_SLAM3R_SURFACE_INSTANCE_LABEL", "object-slam3r-surface-v1")
    host_fingerprint: str = os.environ.get("OBJECT_SLAM3R_SURFACE_HOST_FINGERPRINT", "")
    gpu_model: str = os.environ.get("OBJECT_SLAM3R_SURFACE_GPU_MODEL") or os.environ.get("OBJECT_FAST_PUBLISH_GPU_MODEL", "RTX 5090")
    gpu_count: int = _env_int("OBJECT_SLAM3R_SURFACE_GPU_COUNT", _env_int("OBJECT_FAST_PUBLISH_GPU_COUNT", 1))
    vram_mb: int = _env_int("OBJECT_SLAM3R_SURFACE_VRAM_MB", _env_int("OBJECT_FAST_PUBLISH_VRAM_MB", 24576))
    cpu_cores: int = _env_int("OBJECT_SLAM3R_SURFACE_CPU_CORES", _env_int("OBJECT_FAST_PUBLISH_CPU_CORES", 16))
    ram_mb: int = _env_int("OBJECT_SLAM3R_SURFACE_RAM_MB", _env_int("OBJECT_FAST_PUBLISH_RAM_MB", 65536))
    scheduler_tick_interval_sec: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_SCHEDULER_TICK_INTERVAL_SEC",
        _env_float("OBJECT_FAST_PUBLISH_SCHEDULER_TICK_INTERVAL_SEC", 1.0),
    )
    heartbeat_interval_sec: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_HEARTBEAT_INTERVAL_SEC",
        _env_int("OBJECT_FAST_PUBLISH_HEARTBEAT_INTERVAL_SEC", 15),
    )
    claim_enabled: bool = _env_flag("OBJECT_SLAM3R_SURFACE_CLAIM_ENABLED", False)
    standby_note: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_STANDBY_NOTE",
        "official repos are pinned, but native data contracts are not validated yet",
    )

    local_root: str = os.environ.get("OBJECT_SLAM3R_SURFACE_LOCAL_ROOT", str(_DEFAULT_LOCAL_ROOT))
    local_jobs_directory: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_LOCAL_JOBS_DIR",
        str(_DEFAULT_LOCAL_ROOT / "jobs"),
    )

    ffmpeg_bin: str = os.environ.get("OBJECT_SLAM3R_SURFACE_FFMPEG_BIN") or os.environ.get("OBJECT_FAST_PUBLISH_FFMPEG_BIN", "ffmpeg")
    extract_fps: float = _env_float("OBJECT_SLAM3R_SURFACE_EXTRACT_FPS", _env_float("OBJECT_FAST_PUBLISH_EXTRACT_FPS", 2.0))
    curated_max_frames: int = _env_int("OBJECT_SLAM3R_SURFACE_CURATED_MAX_FRAMES", 64)
    curated_min_blur_score: float = _env_float("OBJECT_SLAM3R_SURFACE_CURATED_MIN_BLUR_SCORE", 24.0)
    curated_dark_threshold_brightness: float = _env_float("OBJECT_SLAM3R_SURFACE_CURATED_DARK_THRESHOLD_BRIGHTNESS", 60.0)
    curated_bright_threshold_brightness: float = _env_float("OBJECT_SLAM3R_SURFACE_CURATED_BRIGHT_THRESHOLD_BRIGHTNESS", 200.0)
    curated_max_frame_similarity: float = _env_float("OBJECT_SLAM3R_SURFACE_CURATED_MAX_FRAME_SIMILARITY", 0.92)
    curated_min_accept_interval_sec: float = _env_float("OBJECT_SLAM3R_SURFACE_CURATED_MIN_ACCEPT_INTERVAL_SEC", 0.28)
    curated_min_orb_features: int = _env_int("OBJECT_SLAM3R_SURFACE_CURATED_MIN_ORB_FEATURES", 500)
    curated_warn_orb_features: int = _env_int("OBJECT_SLAM3R_SURFACE_CURATED_WARN_ORB_FEATURES", 800)
    curated_min_target_signal: float = _env_float("OBJECT_SLAM3R_SURFACE_CURATED_MIN_TARGET_SIGNAL", 0.10)
    curated_warn_target_signal: float = _env_float("OBJECT_SLAM3R_SURFACE_CURATED_WARN_TARGET_SIGNAL", 0.16)
    curated_min_global_variance: float = _env_float("OBJECT_SLAM3R_SURFACE_CURATED_MIN_GLOBAL_VARIANCE", 10.0)
    curated_hq_preserve_valid_frames_enabled: bool = _env_flag(
        "OBJECT_SLAM3R_SURFACE_CURATED_HQ_PRESERVE_VALID_FRAMES_ENABLED",
        True,
    )
    curated_hq_preserve_valid_frames_threshold: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_CURATED_HQ_PRESERVE_VALID_FRAMES_THRESHOLD",
        48,
    )
    curated_temporal_coverage_sectors: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_CURATED_TEMPORAL_COVERAGE_SECTORS",
        8,
    )
    curated_min_slam_frames: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_CURATED_MIN_SLAM_FRAMES",
        _env_int("OBJECT_SLAM3R_SURFACE_CURATED_MIN_SURFACE_FRAMES", 12),
    )
    curated_min_surface_frames: int = _env_int("OBJECT_SLAM3R_SURFACE_CURATED_MIN_SURFACE_FRAMES", 12)

    slam3r_repo: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_SLAM3R_REPO",
        str(_DEFAULT_REPO_ROOT / "third_party" / "SLAM3R"),
    )
    slam3r_command_template: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_SLAM3R_COMMAND",
        _default_slam3r_command_template(),
    )
    slam3r_summary_filename: str = os.environ.get("OBJECT_SLAM3R_SURFACE_SLAM3R_SUMMARY_FILENAME", "slam3r.json")
    slam3r_stage_timeout_sec: int = _env_int("OBJECT_SLAM3R_SURFACE_SLAM3R_TIMEOUT_SEC", 7200)

    sparse2dgs_repo: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_REPO",
        str(_DEFAULT_REPO_ROOT / "third_party" / "Sparse2DGS"),
    )
    sparse2dgs_command_template: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_COMMAND",
        _default_sparse2dgs_command_template(),
    )
    sparse2dgs_summary_filename: str = os.environ.get("OBJECT_SLAM3R_SURFACE_SPARSE2DGS_SUMMARY_FILENAME", "sparse2dgs_surface.json")
    sparse2dgs_resume_state_filename: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_RESUME_STATE_FILENAME",
        "sparse2dgs_resume_state.json",
    )
    sparse2dgs_stage_timeout_sec: int = _env_int("OBJECT_SLAM3R_SURFACE_SPARSE2DGS_TIMEOUT_SEC", 7200)
    sparse2dgs_target_views: int = _env_int("OBJECT_SLAM3R_SURFACE_SPARSE2DGS_TARGET_VIEWS", 24)
    sparse2dgs_min_views: int = _env_int("OBJECT_SLAM3R_SURFACE_SPARSE2DGS_MIN_VIEWS", 12)
    sparse2dgs_contract_max_image_size: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_CONTRACT_MAX_IMAGE_SIZE",
        _default_sparse2dgs_contract_max_image_size(),
    )
    sparse2dgs_contract_min_image_size: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_CONTRACT_MIN_IMAGE_SIZE",
        _default_sparse2dgs_contract_min_image_size(),
    )
    sparse2dgs_contract_total_pixel_budget: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_CONTRACT_TOTAL_PIXEL_BUDGET",
        _default_sparse2dgs_contract_total_pixel_budget(),
    )
    sparse2dgs_max_oom_retries: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_MAX_OOM_RETRIES",
        5,
    )
    sparse2dgs_checkpoint_interval: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_CHECKPOINT_INTERVAL",
        1000,
    )
    sparse2dgs_max_checkpoint_resume_retries: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_MAX_CHECKPOINT_RESUME_RETRIES",
        2,
    )
    sparse2dgs_oom_resolution_scale: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_OOM_RESOLUTION_SCALE",
        0.84,
    )
    sparse2dgs_oom_view_drop_step: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_OOM_VIEW_DROP_STEP",
        2,
    )
    sparse2dgs_window_enabled: bool = _env_flag(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_WINDOW_ENABLED",
        True,
    )
    sparse2dgs_window_trigger_views: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_WINDOW_TRIGGER_VIEWS",
        16,
    )
    sparse2dgs_window_target_views: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_WINDOW_TARGET_VIEWS",
        12,
    )
    sparse2dgs_window_overlap_views: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_WINDOW_OVERLAP_VIEWS",
        4,
    )
    sparse2dgs_window_max_count: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_WINDOW_MAX_COUNT",
        4,
    )
    sparse2dgs_support_scene_max_image_size: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_SUPPORT_SCENE_MAX_IMAGE_SIZE",
        1536,
    )
    sparse2dgs_fused_point_cap: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_FUSED_POINT_CAP",
        2500000,
    )
    sparse2dgs_fused_point_voxel_divisor: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_FUSED_POINT_VOXEL_DIVISOR",
        2048.0,
    )

    matcha_repo: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_MATCHA_REPO",
        str(_DEFAULT_REPO_ROOT / "third_party" / "MAtCha"),
    )
    matcha_command_template: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_MATCHA_COMMAND",
        _default_matcha_command_template(),
    )
    matcha_summary_filename: str = os.environ.get("OBJECT_SLAM3R_SURFACE_MATCHA_SUMMARY_FILENAME", "matcha_mesh.json")
    matcha_stage_timeout_sec: int = _env_int("OBJECT_SLAM3R_SURFACE_MATCHA_TIMEOUT_SEC", 7200)
    matcha_working_point_trigger: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_MATCHA_WORKING_POINT_TRIGGER",
        1600000,
    )
    matcha_working_point_cap: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_MATCHA_WORKING_POINT_CAP",
        1200000,
    )
    matcha_working_point_voxel_divisor: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_MATCHA_WORKING_POINT_VOXEL_DIVISOR",
        2048.0,
    )
    matcha_max_retries: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_MATCHA_MAX_RETRIES",
        2,
    )
    matcha_retry_point_cap_scale: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_MATCHA_RETRY_POINT_CAP_SCALE",
        0.72,
    )
    matcha_retry_point_cap_floor: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_MATCHA_RETRY_POINT_CAP_FLOOR",
        450000,
    )
    delivery_mesh_summary_filename: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_MESH_SUMMARY_FILENAME",
        "delivery_mesh.json",
    )
    delivery_texture_summary_filename: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_TEXTURE_SUMMARY_FILENAME",
        "delivery_texture.json",
    )
    delivery_preserve_geometry_default: bool = _env_flag(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_PRESERVE_GEOMETRY",
        True,
    )
    delivery_target_face_count: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_TARGET_FACE_COUNT", 180000)
    delivery_hard_max_face_count: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_HARD_MAX_FACE_COUNT", 240000)
    delivery_component_min_faces: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_COMPONENT_MIN_FACES", 128)
    delivery_component_ratio_floor: float = _env_float("OBJECT_SLAM3R_SURFACE_DELIVERY_COMPONENT_RATIO_FLOOR", 0.01)
    delivery_texture_max_views: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_TEXTURE_MAX_VIEWS", 24)
    delivery_texture_atlas_size: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_TEXTURE_ATLAS_SIZE", 4096)
    delivery_projection_image_size: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_PROJECTION_IMAGE_SIZE", 2048)
    delivery_texture_fill_kernel: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_TEXTURE_FILL_KERNEL", 5)
    delivery_texture_min_view_cosine: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_TEXTURE_MIN_VIEW_COSINE",
        -0.15,
    )
    delivery_texture_view_consistency_margin: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_TEXTURE_VIEW_CONSISTENCY_MARGIN",
        0.12,
    )
    delivery_texture_view_smoothing_rounds: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_TEXTURE_VIEW_SMOOTHING_ROUNDS",
        2,
    )
    delivery_hole_fill_iterations: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_HOLE_FILL_ITERATIONS", 2)
    delivery_small_hole_max_edges: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_SMALL_HOLE_MAX_EDGES", 40)
    delivery_small_hole_max_perimeter_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_SMALL_HOLE_MAX_PERIMETER_RATIO",
        0.03,
    )
    delivery_small_hole_hq_safety_margin: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_SMALL_HOLE_HQ_SAFETY_MARGIN",
        0.75,
    )
    delivery_small_hole_max_planarity_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_SMALL_HOLE_MAX_PLANARITY_RATIO",
        0.008,
    )
    delivery_native_fill_holes_enabled: bool = _env_flag(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_NATIVE_FILL_HOLES_ENABLED",
        False,
    )
    delivery_aggressive_repair_face_cap: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_AGGRESSIVE_REPAIR_FACE_CAP",
        250000,
    )
    delivery_optimize_subprocess_timeout_sec: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_OPTIMIZE_SUBPROCESS_TIMEOUT_SEC",
        10800,
    )
    delivery_bake_subprocess_timeout_sec: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_BAKE_SUBPROCESS_TIMEOUT_SEC",
        10800,
    )
    delivery_bake_stall_timeout_sec: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_BAKE_STALL_TIMEOUT_SEC",
        600,
    )
    delivery_bake_progress_emit_interval_sec: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_BAKE_PROGRESS_EMIT_INTERVAL_SEC",
        2.0,
    )
    delivery_bake_max_retries: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_BAKE_MAX_RETRIES",
        2,
    )
    delivery_bake_retry_max_views_scale: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_BAKE_RETRY_MAX_VIEWS_SCALE",
        0.80,
    )
    delivery_bake_retry_atlas_scale: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_BAKE_RETRY_ATLAS_SCALE",
        0.75,
    )
    delivery_bake_retry_projection_scale: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_BAKE_RETRY_PROJECTION_SCALE",
        0.75,
    )
    delivery_bake_retry_max_views_floor: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_BAKE_RETRY_MAX_VIEWS_FLOOR",
        8,
    )
    delivery_bake_retry_atlas_floor: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_BAKE_RETRY_ATLAS_FLOOR",
        1024,
    )
    delivery_bake_retry_projection_floor: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_BAKE_RETRY_PROJECTION_FLOOR",
        768,
    )
    delivery_bake_mesh_proxy_trigger_faces: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_BAKE_MESH_PROXY_TRIGGER_FACES",
        450000,
    )
    delivery_bake_mesh_proxy_face_cap: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_BAKE_MESH_PROXY_FACE_CAP",
        320000,
    )
    delivery_bake_mesh_proxy_min_faces: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_BAKE_MESH_PROXY_MIN_FACES",
        140000,
    )
    delivery_bake_mesh_proxy_target_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_BAKE_MESH_PROXY_TARGET_RATIO",
        0.55,
    )
    delivery_bake_retry_mesh_face_cap_scale: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_BAKE_RETRY_MESH_FACE_CAP_SCALE",
        0.80,
    )
    delivery_bake_retry_mesh_face_cap_floor: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_BAKE_RETRY_MESH_FACE_CAP_FLOOR",
        160000,
    )
    delivery_working_mesh_trigger_faces: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_WORKING_MESH_TRIGGER_FACES",
        900000,
    )
    delivery_working_mesh_face_cap: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_WORKING_MESH_FACE_CAP",
        900000,
    )
    delivery_working_mesh_min_faces: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_WORKING_MESH_MIN_FACES",
        300000,
    )
    delivery_working_mesh_target_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_WORKING_MESH_TARGET_RATIO",
        0.22,
    )
    delivery_optimize_max_retries: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_OPTIMIZE_MAX_RETRIES",
        2,
    )
    delivery_optimize_retry_face_cap_scale: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_OPTIMIZE_RETRY_FACE_CAP_SCALE",
        0.75,
    )
    delivery_optimize_retry_trigger_face_scale: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_OPTIMIZE_RETRY_TRIGGER_FACE_SCALE",
        0.70,
    )
    delivery_optimize_retry_min_faces_scale: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_OPTIMIZE_RETRY_MIN_FACES_SCALE",
        0.80,
    )
    delivery_optimize_retry_target_ratio_scale: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_OPTIMIZE_RETRY_TARGET_RATIO_SCALE",
        0.72,
    )
    delivery_optimize_retry_face_cap_floor: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_OPTIMIZE_RETRY_FACE_CAP_FLOOR",
        220000,
    )
    delivery_optimize_retry_trigger_face_floor: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_OPTIMIZE_RETRY_TRIGGER_FACE_FLOOR",
        300000,
    )
    delivery_optimize_retry_min_faces_floor: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_OPTIMIZE_RETRY_MIN_FACES_FLOOR",
        140000,
    )
    delivery_optimize_retry_target_ratio_floor: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_OPTIMIZE_RETRY_TARGET_RATIO_FLOOR",
        0.08,
    )
    delivery_taubin_iterations: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_TAUBIN_ITERATIONS", 8)
    delivery_taubin_lambda: float = _env_float("OBJECT_SLAM3R_SURFACE_DELIVERY_TAUBIN_LAMBDA", 0.45)
    delivery_taubin_nu: float = _env_float("OBJECT_SLAM3R_SURFACE_DELIVERY_TAUBIN_NU", -0.5)
    delivery_feature_preserve_angle_deg: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_FEATURE_PRESERVE_ANGLE_DEG",
        28.0,
    )
    delivery_self_intersection_cleanup_enabled: bool = _env_flag(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_SELF_INTERSECTION_CLEANUP_ENABLED",
        True,
    )
    delivery_self_intersection_cleanup_trigger_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_SELF_INTERSECTION_CLEANUP_TRIGGER_RATIO",
        0.05,
    )
    delivery_self_intersection_cleanup_max_face_drop_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_SELF_INTERSECTION_CLEANUP_MAX_FACE_DROP_RATIO",
        0.08,
    )
    delivery_self_intersection_cleanup_max_passes: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_SELF_INTERSECTION_CLEANUP_MAX_PASSES",
        2,
    )
    delivery_local_patch_surgery_enabled: bool = _env_flag(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_LOCAL_PATCH_SURGERY_ENABLED",
        True,
    )
    delivery_local_patch_surgery_sample_cap: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_LOCAL_PATCH_SURGERY_SAMPLE_CAP",
        3072,
    )
    delivery_local_patch_surgery_max_hotspots: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_LOCAL_PATCH_SURGERY_MAX_HOTSPOTS",
        3,
    )
    delivery_local_patch_surgery_patch_expansion_rings: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_LOCAL_PATCH_SURGERY_PATCH_EXPANSION_RINGS",
        2,
    )
    delivery_local_patch_surgery_max_patch_faces: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_LOCAL_PATCH_SURGERY_MAX_PATCH_FACES",
        2400,
    )
    delivery_local_patch_surgery_min_pair_count: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_LOCAL_PATCH_SURGERY_MIN_PAIR_COUNT",
        12,
    )
    delivery_local_patch_surgery_max_boundary_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_LOCAL_PATCH_SURGERY_MAX_BOUNDARY_RATIO",
        0.25,
    )
    delivery_local_patch_surgery_max_plane_gap_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_LOCAL_PATCH_SURGERY_MAX_PLANE_GAP_RATIO",
        0.006,
    )
    delivery_local_patch_surgery_cluster_radius_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_LOCAL_PATCH_SURGERY_CLUSTER_RADIUS_RATIO",
        0.035,
    )
    delivery_local_patch_surgery_min_ratio_improvement: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_LOCAL_PATCH_SURGERY_MIN_RATIO_IMPROVEMENT",
        0.003,
    )
    delivery_local_patch_surgery_min_surface_area_retention: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_LOCAL_PATCH_SURGERY_MIN_SURFACE_AREA_RETENTION",
        0.94,
    )
    delivery_local_patch_surgery_max_surface_area_retention: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_LOCAL_PATCH_SURGERY_MAX_SURFACE_AREA_RETENTION",
        1.10,
    )
    delivery_local_patch_surgery_min_extent_retention: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_LOCAL_PATCH_SURGERY_MIN_EXTENT_RETENTION",
        0.96,
    )
    delivery_multilayer_prune_enabled: bool = _env_flag(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_MULTILAYER_PRUNE_ENABLED",
        True,
    )
    delivery_multilayer_prune_max_face_drop_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_MULTILAYER_PRUNE_MAX_FACE_DROP_RATIO",
        0.12,
    )
    delivery_multilayer_prune_normal_cosine: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_MULTILAYER_PRUNE_NORMAL_COSINE",
        0.85,
    )
    delivery_multilayer_prune_sample_cap: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_MULTILAYER_PRUNE_SAMPLE_CAP",
        8192,
    )
    quality_report_filename: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_QUALITY_REPORT_FILENAME",
        "quality_report.json",
    )
    publish_requires_hq_gate: bool = _env_flag(
        "OBJECT_SLAM3R_SURFACE_PUBLISH_REQUIRES_HQ_GATE",
        True,
    )
    geometry_hq_min_selected_views: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_GEOMETRY_HQ_MIN_SELECTED_VIEWS",
        24,
    )
    geometry_hq_min_exported_short_side: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_GEOMETRY_HQ_MIN_EXPORTED_SHORT_SIDE",
        896,
    )
    geometry_hq_min_coverage_sectors: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_GEOMETRY_HQ_MIN_COVERAGE_SECTORS",
        7,
    )
    geometry_hq_min_baseline_median_deg: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_GEOMETRY_HQ_MIN_BASELINE_MEDIAN_DEG",
        10.0,
    )
    geometry_coverage_debug_filename: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_GEOMETRY_COVERAGE_DEBUG_FILENAME",
        "coverage_debug.json",
    )
    texture_hq_min_projected_views: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_TEXTURE_HQ_MIN_PROJECTED_VIEWS",
        12,
    )
    texture_hq_min_atlas_coverage_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_TEXTURE_HQ_MIN_ATLAS_COVERAGE_RATIO",
        0.65,
    )
    texture_hq_min_photo_projected_face_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_TEXTURE_HQ_MIN_PHOTO_PROJECTED_FACE_RATIO",
        0.92,
    )
    texture_hq_max_fallback_face_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_TEXTURE_HQ_MAX_FALLBACK_FACE_RATIO",
        0.05,
    )
    texture_hq_max_neighbor_view_disagreement: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_TEXTURE_HQ_MAX_NEIGHBOR_VIEW_DISAGREEMENT",
        0.10,
    )
    texture_hq_max_low_saturation_texel_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_TEXTURE_HQ_MAX_LOW_SATURATION_TEXEL_RATIO",
        0.18,
    )
    open_surface_hq_min_largest_component_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_OPEN_SURFACE_HQ_MIN_LARGEST_COMPONENT_RATIO",
        0.93,
    )
    open_surface_hq_min_surface_area_retention: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_OPEN_SURFACE_HQ_MIN_SURFACE_AREA_RETENTION",
        0.75,
    )
    open_surface_hq_max_surface_area_retention: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_OPEN_SURFACE_HQ_MAX_SURFACE_AREA_RETENTION",
        1.10,
    )
    open_surface_hq_min_extent_retention: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_OPEN_SURFACE_HQ_MIN_EXTENT_RETENTION",
        0.75,
    )
    open_surface_hq_max_boundary_edge_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_OPEN_SURFACE_HQ_MAX_BOUNDARY_EDGE_RATIO",
        0.12,
    )
    open_surface_hq_max_balling_score: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_OPEN_SURFACE_HQ_MAX_BALLING_SCORE",
        0.10,
    )
    sheetness_hq_max_self_intersection_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_SHEETNESS_HQ_MAX_SELF_INTERSECTION_RATIO",
        0.01,
    )
    sheetness_hq_warn_local_sheet_branch_count_p95: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_SHEETNESS_HQ_WARN_LOCAL_SHEET_BRANCH_COUNT_P95",
        1.20,
    )
    hole_fill_hq_max_filled_hole_perimeter_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_HOLE_FILL_HQ_MAX_FILLED_HOLE_PERIMETER_RATIO",
        0.03,
    )
    hole_fill_hq_max_fill_added_face_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_HOLE_FILL_HQ_MAX_FILL_ADDED_FACE_RATIO",
        0.04,
    )
    hole_fill_hq_max_boundary_length_drop_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_HOLE_FILL_HQ_MAX_BOUNDARY_LENGTH_DROP_RATIO",
        0.10,
    )
    mesh_fidelity_hq_min_surface_area_retention: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_MESH_FIDELITY_HQ_MIN_SURFACE_AREA_RETENTION",
        0.72,
    )
    mesh_fidelity_hq_max_surface_area_retention: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_MESH_FIDELITY_HQ_MAX_SURFACE_AREA_RETENTION",
        1.10,
    )
    mesh_fidelity_hq_min_extent_retention: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_MESH_FIDELITY_HQ_MIN_EXTENT_RETENTION",
        0.78,
    )
    mesh_fidelity_hq_max_vertex_distance_mean_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_MESH_FIDELITY_HQ_MAX_VERTEX_DISTANCE_MEAN_RATIO",
        0.012,
    )
    mesh_fidelity_hq_max_vertex_distance_p95_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_MESH_FIDELITY_HQ_MAX_VERTEX_DISTANCE_P95_RATIO",
        0.030,
    )

    sugar_repo: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_SUGAR_REPO",
        str(_DEFAULT_REPO_ROOT / "third_party" / "SuGaR"),
    )
    sugar_command_template: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_SUGAR_COMMAND",
        _default_sugar_command_template(),
    )
    sugar_summary_filename: str = os.environ.get("OBJECT_SLAM3R_SURFACE_SUGAR_SUMMARY_FILENAME", "sugar_mesh.json")
    sugar_stage_timeout_sec: int = _env_int("OBJECT_SLAM3R_SURFACE_SUGAR_TIMEOUT_SEC", 7200)

    hgs_repo: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_HGS_REPO",
        str(_DEFAULT_REPO_ROOT / "third_party" / "3D-HGS"),
    )
    hgs_command_template: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_HGS_COMMAND",
        _default_hgs_command_template(),
    )
    hgs_summary_filename: str = os.environ.get("OBJECT_SLAM3R_SURFACE_HGS_SUMMARY_FILENAME", "3dhgs.json")
    hgs_stage_timeout_sec: int = _env_int("OBJECT_SLAM3R_SURFACE_HGS_TIMEOUT_SEC", 7200)
    enable_hgs_refine_default: bool = _env_flag("OBJECT_SLAM3R_SURFACE_ENABLE_HGS_REFINE_DEFAULT", False)

    default_camera_pitch_deg: float = _env_float("OBJECT_SLAM3R_SURFACE_DEFAULT_CAMERA_PITCH_DEG", 16.0)
    default_camera_yaw_deg: float = _env_float("OBJECT_SLAM3R_SURFACE_DEFAULT_CAMERA_YAW_DEG", -26.0)
    default_camera_distance_scale: float = _env_float("OBJECT_SLAM3R_SURFACE_DEFAULT_CAMERA_DISTANCE_SCALE", 2.4)

    object_storage_endpoint_url: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_ENDPOINT_URL", "")
    object_storage_region: str = _default_storage_region()
    object_storage_bucket: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_BUCKET", "")
    artifact_bucket_prefix: str = os.environ.get("CONTROL_PLANE_ARTIFACT_PREFIX", "artifacts")
    object_storage_access_key_id: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_ACCESS_KEY_ID", "")
    object_storage_secret_access_key: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_SECRET_ACCESS_KEY", "")
    object_storage_session_token: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_SESSION_TOKEN", "")
    object_storage_public_base_url: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_PUBLIC_BASE_URL", "")
    object_storage_addressing_style: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_ADDRESSING_STYLE", "auto")
    object_storage_presign_expiry_sec: int = _env_int("CONTROL_PLANE_OBJECT_STORAGE_PRESIGN_EXPIRY_SEC", 3600)

    @property
    def repo_root(self) -> Path:
        return Path(_DEFAULT_REPO_ROOT)


config = WorkerConfig()
