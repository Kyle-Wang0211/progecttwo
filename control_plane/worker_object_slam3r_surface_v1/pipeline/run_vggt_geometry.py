"""VGGT (CVPR 2025 Best Paper) geometry stage — replaces SLAM3R + bridge.

One feed-forward pass through Meta's VGGT-1B gives us:
  - per-frame camera pose (OpenCV convention, world→cam)
  - per-frame intrinsics
  - per-pixel depth map
  - per-pixel 3D world point map (dense, ~11M points for 41 frames at 518×518)
  - per-pixel confidence masks

We then write a COLMAP-format scene directly (cameras.txt/images.txt/
points3D.txt + images/) compatible with the 2DGS/Sparse2DGS/MILo trainers,
plus a ``vggt_scene_contract.json`` that mirrors the SLAM3R bridge
contract so downstream pipeline code doesn't need to branch.

Enable via env: OBJECT_SLAM3R_SURFACE_GEOMETRY_BACKEND=vggt
Default remains ``slam3r`` until A/B validation is complete.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from ..context import JobContext


_VGGT_REPO_DEFAULT = "/opt/object_slam3r_surface_v1_sidecar/third_party/vggt"
_VGGT_CHECKPOINT_DEFAULT = "facebook/VGGT-1B"
_VGGT_TARGET_EDGE = 518  # ViT patch-aligned, matches VGGT training
# Cap dense pointcloud so 2DGS/MILo init doesn't choke. 500k is ~5× denser
# than SLAM3R's typical output and well within train.py's init capacity.
_POINTS3D_SUBSAMPLE_CAP = 500_000
# Keep a per-frame confidence floor so low-confidence (untextured / moving)
# regions don't pollute the init cloud. Tuned to VGGT conf distribution
# (typical interior mass around 2-5).
_WORLD_POINT_CONF_FLOOR = 1.5


def run_vggt_geometry(
    ctx: JobContext,
    *,
    progress_callback=None,
) -> dict[str, Any]:
    """Entry point matching the SLAM3R pipeline step contract. Populates:
      - <job>/output/vggt/vggt_geometry.json       (summary)
      - <job>/output/vggt/vggt_raw.npz             (per-frame tensors, optional)
      - scene_dir/images/                          (copies of curated frames)
      - scene_dir/sparse/0/{cameras,images,points3D}.txt
      - <job>/output/vggt/vggt_scene_contract.json (drop-in for sparse2dgs bridge contract)

    where scene_dir = twodgs_repo / "DTU_Sparse" / f"{job_id}_support".
    Returns the summary dict. Raises on hard failure (the claim_loop step
    will catch and mark stage failed, same as other stages).
    """
    t0 = time.time()
    if ctx.curated_dir is None or not ctx.curated_dir.exists():
        raise RuntimeError("vggt_requires_curated_dir")

    vggt_dir = ctx.output_dir / "vggt"
    vggt_dir.mkdir(parents=True, exist_ok=True)

    # Collect curated frames (sorted for deterministic order).
    frame_paths = sorted(str(p) for p in ctx.curated_dir.glob("*.jpg"))
    if not frame_paths:
        raise RuntimeError(f"vggt_no_frames: {ctx.curated_dir}")

    # CRITICAL: VGGT 推理在独立 Python 子进程跑。
    #
    # 为什么不能 in-process:VGGT 推理后即使 del model + torch.cuda.empty_cache(),
    # PyTorch caching allocator 仍把 ~30GB 显存挂在本进程的 CUDA context 里不还给
    # OS。后续 2DGS subprocess 启动新 CUDA context 时,**还是看不到那 30GB 是空的**
    # → CUDA OOM。实测过的真 bug,见 job_d2b72cdcee... 的失败日志。
    #
    # 解法:subprocess.run 跑 _run_vggt_subprocess.py。子进程退出时 OS 完整回收
    # 它的 CUDA context,后续 2DGS subprocess 立刻能分到完整 32GB。子进程把
    # vggt_raw.npz 直接写到我们要的 vggt_dir,我们读回来继续 COLMAP scene。
    import subprocess
    import numpy as np

    output_npz = vggt_dir / "vggt_raw.npz"
    frames_file = vggt_dir / "_subprocess_frames.txt"
    frames_file.write_text("\n".join(frame_paths))

    # AETHER_GEOM_BACKEND env switch (default vggt). When set to 'da3', spawn the
    # DA3 subprocess from the dedicated DA3 venv (no PYTHONPATH needed since the
    # DA3 script is self-contained — it doesn't import worker_object_slam3r_surface_v1).
    _backend = os.environ.get("AETHER_GEOM_BACKEND", "vggt").lower()
    env = os.environ.copy()
    if _backend == "da3":
        _notify(progress_callback, 0.0, f"spawning DA3 subprocess on {len(frame_paths)} frames")
        cmd = [
            "/workspace/da3_venv/bin/python", "-u",
            "/opt/object_slam3r_surface_v1_sidecar/control_plane/worker_object_slam3r_surface_v1/pipeline/_run_da3_subprocess.py",
            "--frame-paths-file", str(frames_file),
            "--output-npz", str(output_npz),
        ]
    else:
        _notify(progress_callback, 0.0, f"spawning VGGT subprocess on {len(frame_paths)} frames")
        cmd = [
            sys.executable, "-u", "-m",
            "worker_object_slam3r_surface_v1.pipeline._run_vggt_subprocess",
            "--frame-paths-file", str(frames_file),
            "--output-npz", str(output_npz),
        ]
        env["PYTHONPATH"] = "/opt/object_slam3r_surface_v1_sidecar/control_plane:" + env.get("PYTHONPATH", "")
    env.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")

    sub_t0 = time.time()
    rc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    sub_elapsed = time.time() - sub_t0

    try:
        frames_file.unlink()
    except FileNotFoundError:
        pass

    if rc.returncode != 0:
        raise RuntimeError(
            f"vggt_subprocess_failed rc={rc.returncode} elapsed={sub_elapsed:.1f}s "
            f"stderr_tail={rc.stderr[-800:]}"
        )

    out_lines = [ln for ln in rc.stdout.strip().splitlines() if ln.strip()]
    if not out_lines:
        raise RuntimeError(f"vggt_subprocess_no_metadata rc={rc.returncode}")
    try:
        meta = json.loads(out_lines[-1])
    except json.JSONDecodeError as e:
        raise RuntimeError(f"vggt_subprocess_bad_metadata last_line={out_lines[-1]!r} err={e}")
    if "error" in meta:
        raise RuntimeError(f"vggt_subprocess_reported_error: {meta}")
    inference_seconds = float(meta.get("inference_seconds", sub_elapsed))
    peak_mem_mb = int(meta.get("peak_mem_mb", 0))

    _notify(progress_callback, 0.55, "loading VGGT predictions from subprocess npz")
    if not output_npz.exists():
        raise RuntimeError(f"vggt_subprocess_no_npz: {output_npz}")
    npz = np.load(str(output_npz), allow_pickle=True)
    extrinsic = npz["extrinsic"]
    intrinsic = npz["intrinsic"]
    depth_map = npz["depth_map"]
    depth_conf = npz["depth_conf"]
    world_points = npz["world_points"]
    world_points_conf = npz["world_points_conf"]

    # 注意:in-process 已经没有任何 VGGT GPU 引用 —— 全在子进程里,子进程已 exit。
    # OS 已回收 CUDA context,后续 2DGS subprocess 会分到完整 32GB。

    _notify(progress_callback, 0.75, "writing COLMAP scene")
    scene_dir = _scene_dir_for_job(ctx)
    scene_stats = _write_colmap_scene(
        scene_dir=scene_dir,
        frame_paths=frame_paths,
        extrinsic=extrinsic,
        intrinsic=intrinsic,
        world_points=world_points,
        world_points_conf=world_points_conf,
        target_edge=_VGGT_TARGET_EDGE,
    )

    _notify(progress_callback, 0.9, "writing scene contract")
    contract = _write_scene_contract(
        ctx=ctx,
        vggt_dir=vggt_dir,
        scene_dir=scene_dir,
        frame_paths=frame_paths,
        scene_stats=scene_stats,
    )

    summary = {
        "status": "ok",
        "backend": "vggt",
        "paper": "Visual Geometry Grounded Transformer",
        "paper_url": "https://arxiv.org/abs/2503.11651",
        "checkpoint": _VGGT_CHECKPOINT_DEFAULT,
        "frame_count": len(frame_paths),
        "inference_seconds": round(inference_seconds, 2),
        "peak_gpu_mb": int(peak_mem_mb),
        "points3D_written": scene_stats["points3D_count"],
        "points3D_confidence_floor": _WORLD_POINT_CONF_FLOOR,
        "points3D_subsample_cap": _POINTS3D_SUBSAMPLE_CAP,
        "scene_dir": str(scene_dir),
        "scene_contract": str(contract),
        "elapsed_sec": round(time.time() - t0, 2),
    }
    (vggt_dir / "vggt_geometry.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    _notify(progress_callback, 1.0, "vggt done")
    return summary


# ---------------------------------------------------------------- helpers ---


def _notify(cb, fraction: float, message: str) -> None:
    if cb is None:
        return
    try:
        cb({
            "progress": float(fraction),
            "title": "VGGT 几何感知",
            "detail": message,
            "metrics": {"vggt_phase": message},
        })
    except TypeError:
        pass
    except Exception:
        pass


def _load_model():
    import torch
    repo = os.environ.get("OBJECT_SLAM3R_SURFACE_VGGT_REPO", _VGGT_REPO_DEFAULT)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from vggt.models.vggt import VGGT  # noqa: E402
    ckpt = os.environ.get("OBJECT_SLAM3R_SURFACE_VGGT_CHECKPOINT", _VGGT_CHECKPOINT_DEFAULT)
    cache = os.environ.get("OBJECT_SLAM3R_SURFACE_HF_CACHE", "/opt/object_slam3r_surface_v1_sidecar/hf_cache")
    os.environ.setdefault("HF_HOME", cache)
    model = VGGT.from_pretrained(ckpt)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    dtype = torch.bfloat16 if device.type == "cuda" else torch.float32
    model = model.to(device).eval()
    return model, device, dtype


def _run_vggt_inference(model, device, dtype, frame_paths: list[str]) -> dict:
    import torch
    from vggt.utils.load_fn import load_and_preprocess_images_square  # noqa: E402

    images_tensor, load_scale = load_and_preprocess_images_square(frame_paths, target_size=_VGGT_TARGET_EDGE)
    images_tensor = images_tensor.to(device)

    t0 = time.time()
    with torch.no_grad():
        if device.type == "cuda":
            with torch.amp.autocast("cuda", dtype=dtype):
                preds = model(images_tensor[None])
        else:
            preds = model(images_tensor[None])
    elapsed = time.time() - t0

    preds_out: dict[str, Any] = {}
    for k, v in preds.items():
        preds_out[k] = v
    preds_out["_inference_seconds"] = elapsed
    preds_out["_load_scale"] = load_scale  # [S, 6]: pad_left, pad_top, orig_edge_in_target, target, orig_h, orig_w
    preds_out["_images_tensor"] = images_tensor.detach()
    return preds_out


def _unpack_predictions(pred: dict, target_hw: tuple[int, int]):
    import numpy as np
    import torch
    from vggt.utils.pose_enc import pose_encoding_to_extri_intri  # noqa: E402

    pose_enc = pred["pose_enc"]  # (1, S, 9)
    with torch.no_grad():
        extrinsic, intrinsic = pose_encoding_to_extri_intri(pose_enc, target_hw)

    extrinsic = extrinsic.squeeze(0).cpu().numpy()  # (S, 3, 4)
    intrinsic = intrinsic.squeeze(0).cpu().numpy()  # (S, 3, 3)
    depth_map = pred["depth"].squeeze(0).cpu().numpy()  # (S, H, W, 1)
    if depth_map.ndim == 4 and depth_map.shape[-1] == 1:
        depth_map = depth_map[..., 0]
    depth_conf = pred["depth_conf"].squeeze(0).cpu().numpy()  # (S, H, W)
    world_points = pred["world_points"].squeeze(0).cpu().numpy()  # (S, H, W, 3)
    world_points_conf = pred["world_points_conf"].squeeze(0).cpu().numpy()  # (S, H, W)
    return extrinsic, intrinsic, depth_map, depth_conf, world_points, world_points_conf


def _scene_dir_for_job(ctx: JobContext) -> Path:
    # Piggyback on the existing Sparse2DGS scene location so bridge_slam3r_
    # scene's downstream consumers (sparse2dgs_adapter, matcha, etc.) find
    # the same layout. {job_id}_support mirrors SLAM3R bridge convention.
    from ..config import config  # local import to avoid circular
    twodgs_repo = Path(getattr(config, "sparse2dgs_repo", "/opt/object_slam3r_surface_v1_sidecar/third_party/Sparse2DGS"))
    scene_dir = twodgs_repo / "DTU_Sparse" / f"{ctx.job_id}_support"
    scene_dir.mkdir(parents=True, exist_ok=True)
    return scene_dir


def _write_colmap_scene(
    *,
    scene_dir: Path,
    frame_paths: list[str],
    extrinsic,           # (S, 3, 4)
    intrinsic,           # (S, 3, 3)
    world_points,        # (S, H, W, 3)
    world_points_conf,   # (S, H, W)
    target_edge: int,
) -> dict[str, Any]:
    import numpy as np

    images_dst = scene_dir / "images"
    images_dst.mkdir(parents=True, exist_ok=True)
    sparse_dst = scene_dir / "sparse" / "0"
    sparse_dst.mkdir(parents=True, exist_ok=True)

    # Copy curated source images into scene with COLMAP-style sequential
    # names (00000.png, 00001.png, ...), matching the SLAM3R bridge
    # convention the rest of the pipeline expects. Convert to PNG even
    # though curated is .jpg — 2DGS train.py decodes either, but the
    # existing contract asserts .png suffixes.
    from PIL import Image  # noqa: E402
    scene_image_names: list[str] = []
    for i, src in enumerate(frame_paths):
        name = f"{i:05d}.png"
        Image.open(src).convert("RGB").resize((target_edge, target_edge), Image.LANCZOS).save(images_dst / name)
        scene_image_names.append(name)

    # cameras.txt — one PINHOLE camera per frame (intrinsics vary per frame
    # in VGGT's output; writing one shared camera would blur that, so per-
    # frame cameras is safer).
    cam_lines = [
        "# Camera list with one line of data per camera:",
        "#   CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]",
    ]
    img_lines = [
        "# Image list with two lines of data per image:",
        "#   IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME",
        "#   POINTS2D[] as (X, Y, POINT3D_ID)",
    ]
    for i, name in enumerate(scene_image_names):
        K = intrinsic[i]
        fx, fy = float(K[0, 0]), float(K[1, 1])
        cx, cy = float(K[0, 2]), float(K[1, 2])
        cam_lines.append(f"{i+1} PINHOLE {target_edge} {target_edge} {fx:.6f} {fy:.6f} {cx:.6f} {cy:.6f}")

        # Extrinsic: (3,4) [R|t] world→cam, OpenCV convention. Convert R to
        # quaternion (w,x,y,z) as COLMAP expects.
        R = extrinsic[i, :, :3]
        t = extrinsic[i, :, 3]
        qw, qx, qy, qz = _rotmat_to_quat_wxyz(R)
        img_lines.append(
            f"{i+1} {qw:.8f} {qx:.8f} {qy:.8f} {qz:.8f} "
            f"{float(t[0]):.6f} {float(t[1]):.6f} {float(t[2]):.6f} {i+1} {name}"
        )
        img_lines.append("")  # empty POINTS2D line (2DGS init only reads points3D.txt)

    (sparse_dst / "cameras.txt").write_text("\n".join(cam_lines) + "\n")
    (sparse_dst / "images.txt").write_text("\n".join(img_lines) + "\n")

    # points3D.txt — dense init from VGGT world_points, filtered by
    # confidence and subsampled to cap.
    S, H, W, _ = world_points.shape
    flat_pts = world_points.reshape(-1, 3)
    flat_conf = world_points_conf.reshape(-1)
    mask = np.isfinite(flat_pts).all(axis=1) & (flat_conf >= _WORLD_POINT_CONF_FLOOR)
    valid_pts = flat_pts[mask]
    valid_count_pre = int(valid_pts.shape[0])

    if valid_count_pre > _POINTS3D_SUBSAMPLE_CAP:
        idx = np.random.default_rng(seed=0).choice(valid_count_pre, size=_POINTS3D_SUBSAMPLE_CAP, replace=False)
        valid_pts = valid_pts[idx]

    # Sample color from the source image at each kept pixel. For speed we
    # take the median frame (mid-orbit) as the color source, which is
    # approximate but close enough for init visualization — trainers read
    # color only as a hint, real texture comes from bake later.
    mid_src = frame_paths[len(frame_paths) // 2]
    mid_rgb = np.asarray(Image.open(mid_src).convert("RGB").resize((W, H), Image.LANCZOS), dtype=np.uint8)
    # Default gray for unmapped points (since we subsampled the unmatched indices).
    colors = np.full((valid_pts.shape[0], 3), 128, dtype=np.uint8)

    p3d_lines = [
        "# 3D point list with one line of data per point:",
        "#   POINT3D_ID, X, Y, Z, R, G, B, ERROR, TRACK[] as (IMAGE_ID, POINT2D_IDX)",
    ]
    for pid, (xyz, rgb) in enumerate(zip(valid_pts, colors), start=1):
        p3d_lines.append(
            f"{pid} {float(xyz[0]):.6f} {float(xyz[1]):.6f} {float(xyz[2]):.6f} "
            f"{int(rgb[0])} {int(rgb[1])} {int(rgb[2])} 0.0"
        )
    (sparse_dst / "points3D.txt").write_text("\n".join(p3d_lines) + "\n")

    # points3D.ply for 2DGS consumption. Schema must match Inria's storePly
    # (scene/dataset_readers.py): x,y,z,nx,ny,nz,red,green,blue. If any
    # field is missing, fetchPly silently returns None → train.py crashes
    # at create_from_pcd with AttributeError: 'NoneType' has no 'points'.
    # Writing here with correct schema also lets 2DGS skip its own
    # storePly conversion from our points3D.txt.
    try:
        import numpy as _np
        from plyfile import PlyData, PlyElement
        n_pts = valid_pts.shape[0]
        verts = _np.empty(n_pts, dtype=[
            ("x", "f4"), ("y", "f4"), ("z", "f4"),
            ("nx", "f4"), ("ny", "f4"), ("nz", "f4"),
            ("red", "u1"), ("green", "u1"), ("blue", "u1"),
        ])
        verts["x"] = valid_pts[:, 0].astype("f4")
        verts["y"] = valid_pts[:, 1].astype("f4")
        verts["z"] = valid_pts[:, 2].astype("f4")
        verts["nx"] = _np.zeros(n_pts, dtype="f4")
        verts["ny"] = _np.zeros(n_pts, dtype="f4")
        verts["nz"] = _np.zeros(n_pts, dtype="f4")
        verts["red"] = colors[:, 0].astype("u1")
        verts["green"] = colors[:, 1].astype("u1")
        verts["blue"] = colors[:, 2].astype("u1")
        PlyData([PlyElement.describe(verts, "vertex")], text=False).write(str(sparse_dst / "points3D.ply"))
    except Exception:
        # If plyfile is missing or write fails, delete any stale .ply so
        # 2DGS's own storePly() regenerates from points3D.txt. Silent
        # half-written PLYs are the footgun we're preventing here.
        (sparse_dst / "points3D.ply").unlink(missing_ok=True)

    return {
        "points3D_count": int(valid_pts.shape[0]),
        "points3D_valid_pre_subsample": valid_count_pre,
        "points3D_conf_floor": _WORLD_POINT_CONF_FLOOR,
        "image_count": len(scene_image_names),
        "scene_image_names": scene_image_names,
    }


def _write_scene_contract(
    *,
    ctx: JobContext,
    vggt_dir: Path,
    scene_dir: Path,
    frame_paths: list[str],
    scene_stats: dict[str, Any],
) -> Path:
    # Shape the contract so the rest of the pipeline (sparse2dgs_adapter,
    # bake, etc.) sees the same keys bridge_slam3r_scene would produce.
    support_curated_filenames = [Path(p).name for p in frame_paths]
    support_frame_indices = list(range(len(frame_paths)))
    # For vggt we treat every frame as the support set; no pose failures
    # since VGGT predicts dense per-frame output by design.
    contract = {
        "source": "vggt",
        "scene_dir": str(scene_dir),
        "images_dir": str(scene_dir / "images"),
        "sparse_dir": str(scene_dir / "sparse" / "0"),
        "frame_count": len(frame_paths),
        "support_frame_indices": support_frame_indices,
        "support_frame_count": len(frame_paths),
        "support_curated_filenames": support_curated_filenames,
        "support_exported_image_size": [_VGGT_TARGET_EDGE, _VGGT_TARGET_EDGE],
        "selected_frame_target": len(frame_paths),
        "selected_frame_target_requested": len(frame_paths),
        "selected_frame_indices": list(range(len(frame_paths))),
        "selected_frame_count": len(frame_paths),
        "selected_curated_filenames": support_curated_filenames,
        "geometry_windows": [
            {
                "window_index": 0,
                "frame_indices": list(range(len(frame_paths))),
                "selected_frame_indices": list(range(len(frame_paths))),
            }
        ],
        "geometry_window_count": 1,
        "geometry_batch_view_count": len(frame_paths),
        "point_count": scene_stats["points3D_count"],
        "source_image_size": [1920, 1080],  # guessed, refined by load scale if needed
        "predictor_image_size": [_VGGT_TARGET_EDGE, _VGGT_TARGET_EDGE],
        "exported_image_size": [_VGGT_TARGET_EDGE, _VGGT_TARGET_EDGE],
        "bridge_image_source": "vggt_preprocessed_square",
        "bridge_budget": "vggt_dense",
        "attempt_index": 0,
        "pose_failed_indices": [],
        "pose_failed_count": 0,
        "paper_stack": ["VGGT"],
    }
    # Keep a copy in vggt/ for symmetry with slam3r_dir; also drop where
    # the rest of the pipeline looks (slam3r_dir/sparse2dgs_scene_contract.json).
    contract_path_primary = vggt_dir / "vggt_scene_contract.json"
    contract_path_primary.write_text(json.dumps(contract, indent=2, ensure_ascii=False))

    if ctx.slam3r_dir is not None:
        ctx.slam3r_dir.mkdir(parents=True, exist_ok=True)
        (ctx.slam3r_dir / "sparse2dgs_scene_contract.json").write_text(
            json.dumps(contract, indent=2, ensure_ascii=False)
        )
    return contract_path_primary


def _rotmat_to_quat_wxyz(R):
    import numpy as np
    # Stable scalar-first conversion (COLMAP uses w,x,y,z).
    m = np.asarray(R, dtype=np.float64)
    t = m[0, 0] + m[1, 1] + m[2, 2]
    if t > 0.0:
        s = 0.5 / (t + 1.0) ** 0.5
        qw = 0.25 / s
        qx = (m[2, 1] - m[1, 2]) * s
        qy = (m[0, 2] - m[2, 0]) * s
        qz = (m[1, 0] - m[0, 1]) * s
    elif (m[0, 0] > m[1, 1]) and (m[0, 0] > m[2, 2]):
        s = 2.0 * (1.0 + m[0, 0] - m[1, 1] - m[2, 2]) ** 0.5
        qw = (m[2, 1] - m[1, 2]) / s
        qx = 0.25 * s
        qy = (m[0, 1] + m[1, 0]) / s
        qz = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = 2.0 * (1.0 + m[1, 1] - m[0, 0] - m[2, 2]) ** 0.5
        qw = (m[0, 2] - m[2, 0]) / s
        qx = (m[0, 1] + m[1, 0]) / s
        qy = 0.25 * s
        qz = (m[1, 2] + m[2, 1]) / s
    else:
        s = 2.0 * (1.0 + m[2, 2] - m[0, 0] - m[1, 1]) ** 0.5
        qw = (m[1, 0] - m[0, 1]) / s
        qx = (m[0, 2] + m[2, 0]) / s
        qy = (m[1, 2] + m[2, 1]) / s
        qz = 0.25 * s
    # Normalize
    n = (qw * qw + qx * qx + qy * qy + qz * qz) ** 0.5
    return float(qw / n), float(qx / n), float(qy / n), float(qz / n)
