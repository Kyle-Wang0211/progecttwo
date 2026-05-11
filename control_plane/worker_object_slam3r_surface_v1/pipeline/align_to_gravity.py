"""Rotate VGGT outputs into ARKit gravity-aligned world frame.

Why this stage exists
─────────────────────
VGGT outputs `world_points` and per-frame `extrinsic` in its own
arbitrary world frame (typically aligned with the first camera in
some canonical pose). Downstream Poisson + MVS Texturing have no
gravity awareness — they just consume the npz as-is. So the final
GLB lands in VGGT's frame, NOT a gravity-aligned frame. iOS / GLB
viewers spawn the initial camera with screen-up = +Y; if +Y in
GLB is NOT actually "up", the user sees the chair on its side / at
random tilt, with no obvious "ground".

iOS already collects everything we need:
  • ARSession runs with worldAlignment = .gravity, so ARKit world
    frame is gravity-aligned (+Y up by construction).
  • Each curated frame's manifest entry carries
    `arkit_extrinsic_4x4` = camera→ARKit-world 4×4 matrix
    (serialized column-major from simd_float4x4).

This stage computes a single 3×3 rotation R_align that takes
VGGT-world axes to ARKit-world axes, and applies it to:
  • all `world_points` (rotation only, no translation, so the
    centroid stays roughly where VGGT put it — downstream Poisson
    is shift-invariant anyway)
  • all per-frame `extrinsic` (world→cam) so downstream MVS
    Texturing's .cam files match the rotated geometry.

Math
────
Same physical camera at frame 0:
  ARKit-world: P_arkit = R_arkit_c2w · P_cam_opengl + t_arkit_c2w
  VGGT-world:  P_vggt  = R_vggt_c2w  · P_cam_opencv + t_vggt_c2w

Axis conventions:
  ARKit (OpenGL): +X right, +Y up,   +Z back  (toward viewer)
  VGGT  (OpenCV): +X right, +Y down, +Z forward
  → axis flip M = diag(1, -1, -1)
  P_cam_opengl = M · P_cam_opencv

Dropping translation (we only want orientation):
  R_align = R_arkit_c2w · M · R_vggt_c2w.T

Apply to a VGGT-world point P_vggt:
  P_arkit_oriented = R_align · P_vggt

Apply to a world→cam matrix [R_w2c | t_w2c]:
  P_cam = R_w2c · P_world_old + t_w2c
  After rotating world: P_world_old = R_align.T · P_world_new
  ⇒ R_w2c_new = R_w2c_old · R_align.T
  ⇒ t_w2c_new = t_w2c_old  (camera position relative to ITSELF is
                            unchanged, only the world axes rotated)

Frame correspondence
────────────────────
We use VGGT frame 0 ↔ manifest frame whose UUID matches the basename
of `frame_paths[0]` in the npz. iOS frame UUIDs are `cap-<N>`; the
worker's curate_from_client stage names jpgs after them. If lookup
fails (legacy capture without arkit_extrinsic, or all frames marked
pose_source=imu), the stage is a no-op — pipeline continues with
unrotated geometry.

Failure mode
────────────
This stage is **non-fatal**. Any error (manifest missing, no ARKit
pose, malformed extrinsic, singular VGGT pose) → log + skip, return
`{"skipped": True, "reason": ...}`. The GLB will still be produced,
just in VGGT-world orientation as before.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from ..context import JobContext


def _parse_arkit_extrinsic(flat_list: Any) -> Optional[np.ndarray]:
    """iOS serializes simd_float4x4 column-major as 16 floats
    (AetherARKitPlugin.swift:767). np.reshape(..., (4,4)) treats the
    flat array row-major, which yields M.T — we transpose back to
    recover the canonical math matrix (camera→world)."""
    if not flat_list or not isinstance(flat_list, (list, tuple)):
        return None
    flat = np.asarray(flat_list, dtype=np.float64)
    if flat.size != 16:
        return None
    return flat.reshape(4, 4).T


def _arkit_c2w_for_vggt_frame(
    manifest: dict[str, Any],
    vggt_frame_path: str,
) -> Optional[np.ndarray]:
    """Look up the manifest frame whose UUID matches VGGT frame's
    basename. Returns 4×4 ARKit c2w matrix or None on miss."""
    target = Path(vggt_frame_path).stem
    for f in manifest.get("frames", []) or []:
        if f.get("frame_uuid") == target:
            return _parse_arkit_extrinsic(f.get("arkit_extrinsic_4x4"))
    return None


def align_to_gravity(
    ctx: JobContext,
    *,
    progress_callback: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    ctx.current_stage = "align_to_gravity"

    # 2026-05-11 rollback: align_to_gravity 在 d2143bb7 第一次实测时怀疑导致
    # mesh 旋到 cameras 看不见的方向,texrecon 砍 99% face。先 disable 让
    # baseline 恢复,等单独 verify R_align 数学正确性 + 比较有/无 align 的
    # mesh 视觉效果再重启。
    # 设 AETHER_SKIP_ALIGN_GRAVITY=0 重新启用。
    if os.environ.get("AETHER_SKIP_ALIGN_GRAVITY", "1") == "1":
        print(
            "[align_to_gravity] AETHER_SKIP_ALIGN_GRAVITY=1; skipping (mesh stays in VGGT frame)",
            flush=True,
        )
        return {"skipped": True, "reason": "env_disabled"}

    vggt_npz_path = ctx.output_dir / "vggt" / "vggt_raw.npz"
    if not vggt_npz_path.exists():
        print(f"[align_to_gravity] vggt_raw.npz missing at {vggt_npz_path}; skipping", flush=True)
        return {"skipped": True, "reason": "vggt_npz_missing"}

    manifest_path = ctx.input_dir / "curated.json" if ctx.input_dir else None
    if not manifest_path or not manifest_path.exists():
        print("[align_to_gravity] curated.json manifest missing; skipping", flush=True)
        return {"skipped": True, "reason": "manifest_missing"}

    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception as exc:
        print(f"[align_to_gravity] manifest parse failed: {exc}; skipping", flush=True)
        return {"skipped": True, "reason": f"manifest_parse_failed:{exc}"}

    npz = np.load(vggt_npz_path, allow_pickle=True)
    try:
        extrinsic = np.asarray(npz["extrinsic"])           # (N, 3, 4) w2c OpenCV
        intrinsic = np.asarray(npz["intrinsic"])           # (N, 3, 3)
        world_points = np.asarray(npz["world_points"])     # (N, H, W, 3)
        world_points_conf = np.asarray(npz["world_points_conf"])
        depth_map = np.asarray(npz["depth_map"])
        depth_conf = np.asarray(npz["depth_conf"])
        frame_paths = np.asarray(npz["frame_paths"])
    finally:
        npz.close()

    if extrinsic.shape[0] == 0 or frame_paths.shape[0] == 0:
        return {"skipped": True, "reason": "empty_vggt_npz"}

    first_vggt_frame = str(frame_paths[0])
    arkit_c2w = _arkit_c2w_for_vggt_frame(manifest, first_vggt_frame)
    if arkit_c2w is None:
        # Try position 0 of manifest as last-ditch fallback (matches the
        # common case where curate preserves capture order).
        frames = manifest.get("frames") or []
        if frames:
            arkit_c2w = _parse_arkit_extrinsic(frames[0].get("arkit_extrinsic_4x4"))
            if arkit_c2w is not None:
                print(
                    f"[align_to_gravity] no UUID match for {Path(first_vggt_frame).stem};"
                    f" fell back to manifest frame 0 (uuid={frames[0].get('frame_uuid')})",
                    flush=True,
                )
    if arkit_c2w is None:
        print(
            f"[align_to_gravity] no ARKit pose available for first VGGT frame"
            f" ({Path(first_vggt_frame).stem}); skipping",
            flush=True,
        )
        return {"skipped": True, "reason": "no_arkit_pose"}

    R_arkit_c2w = arkit_c2w[:3, :3]

    # VGGT first frame: world→cam in OpenCV → invert to cam→world
    E_vggt_w2c = np.eye(4)
    E_vggt_w2c[:3, :4] = extrinsic[0].astype(np.float64)
    try:
        E_vggt_c2w = np.linalg.inv(E_vggt_w2c)
    except np.linalg.LinAlgError:
        return {"skipped": True, "reason": "vggt_extrinsic_singular"}
    R_vggt_c2w = E_vggt_c2w[:3, :3]

    # Axis-convention flip (ARKit OpenGL cam ↔ VGGT OpenCV cam)
    M_axis = np.diag([1.0, -1.0, -1.0])

    # The alignment rotation
    R_align = R_arkit_c2w @ M_axis @ R_vggt_c2w.T

    # Sanity: gravity direction (0,-1,0) in ARKit world, expressed in
    # VGGT world. Should agree with what we'd guess from VGGT's per-frame
    # camera-up vectors — useful for diagnostic logs.
    gravity_in_vggt = (R_align.T @ np.array([0.0, -1.0, 0.0])).tolist()

    # Apply to world_points: each 3D point gets rotated.
    # Shape (N, H, W, 3); rotation acts on the last axis.
    wp_shape = world_points.shape
    wp_dtype = world_points.dtype
    wp_flat = world_points.reshape(-1, 3).astype(np.float64)
    wp_rotated = wp_flat @ R_align.T  # row-vector convention: v' = v @ R.T
    world_points_new = wp_rotated.reshape(wp_shape).astype(wp_dtype)

    # Apply to per-frame extrinsics (world→cam):
    #   R_w2c_new = R_w2c_old @ R_align.T
    #   t_w2c_new = t_w2c_old (unchanged)
    extrinsic_new = extrinsic.copy()
    R_align_T = R_align.T
    for i in range(extrinsic.shape[0]):
        R_w2c_old = extrinsic[i, :3, :3].astype(np.float64)
        extrinsic_new[i, :3, :3] = (R_w2c_old @ R_align_T).astype(extrinsic.dtype)
        # extrinsic[i, :3, 3] (translation) untouched

    # Atomic-ish save: write to a tmp file then rename. np.savez writes
    # to disk; renaming over the source on POSIX is atomic for readers
    # not currently holding the file open.
    #
    # ⚠ np.savez(path_str) auto-appends ".npz" to the file name if not
    # already present (per numpy doc). With tmp_path "vggt_raw.npz.tmp"
    # numpy writes to "vggt_raw.npz.tmp.npz" instead, then the rename
    # below fails with Errno 2. Pass an open file object instead — that
    # codepath in numpy uses the file as-is, no suffix appended.
    # Diagnosed 2026-05-11 from job_d2143bb7's `[Errno 2] No such file
    # or directory: '...vggt_raw.npz.tmp' -> '...vggt_raw.npz'`.
    tmp_path = vggt_npz_path.with_suffix(".npz.tmp")
    with open(tmp_path, "wb") as tmp_fh:
        np.savez(
            tmp_fh,
            extrinsic=extrinsic_new,
            intrinsic=intrinsic,
            depth_map=depth_map,
            depth_conf=depth_conf,
            world_points=world_points_new,
            world_points_conf=world_points_conf,
            frame_paths=frame_paths,
        )
    tmp_path.replace(vggt_npz_path)

    summary = {
        "skipped": False,
        "first_vggt_frame": Path(first_vggt_frame).stem,
        "gravity_in_vggt_world": gravity_in_vggt,
        "R_align": [[float(x) for x in row] for row in R_align.tolist()],
        "points_rotated": int(wp_flat.shape[0]),
        "cameras_rotated": int(extrinsic.shape[0]),
    }
    print(
        f"[align_to_gravity] OK; rotated {summary['points_rotated']:,} points and"
        f" {summary['cameras_rotated']} cams;"
        f" gravity_in_vggt=({gravity_in_vggt[0]:.3f},{gravity_in_vggt[1]:.3f},{gravity_in_vggt[2]:.3f})",
        flush=True,
    )
    return summary
