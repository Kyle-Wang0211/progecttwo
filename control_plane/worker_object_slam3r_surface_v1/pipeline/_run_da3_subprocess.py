"""DA3 推理子进程 wrapper —— 替代 _run_vggt_subprocess.py。

输出 npz 完全兼容 VGGT contract:
  extrinsic, intrinsic, depth_map, depth_conf, world_points, world_points_conf, frame_paths
所有 spatial dim resize 到 518x518(VGGT 默认),intrinsics 同步 scale。下游 parent
(run_vggt_geometry.py) 0 改即可读回。

GPU 隔离逻辑同 VGGT:子进程退出 → OS 回收 CUDA context → 后续 2DGS subprocess
拿到完整 32GB。

env:
  AETHER_DA3_MODEL_ID  default=depth-anything/DA3-BASE (Apache 2.0,可商用)
"""
from __future__ import annotations
import argparse, json, os, sys, time
from pathlib import Path

# Match VGGT downstream contract — image/depth size in scene_dir.
TARGET_EDGE = 518


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--frame-paths-file', required=True)
    ap.add_argument('--output-npz', required=True)
    ap.add_argument('--model-id', default=os.environ.get('AETHER_DA3_MODEL_ID', 'depth-anything/DA3-BASE'))
    args = ap.parse_args()

    frames_file = Path(args.frame_paths_file)
    if not frames_file.exists():
        print(json.dumps({'error': 'frames_file_missing', 'path': str(frames_file)}), flush=True)
        return 2
    frame_paths = [line.strip() for line in frames_file.read_text().splitlines() if line.strip()]
    if not frame_paths:
        print(json.dumps({'error': 'no_frames'}), flush=True)
        return 2

    import numpy as np
    import torch
    import torch.nn.functional as F
    from depth_anything_3.api import DepthAnything3

    t_total = time.time()
    device = torch.device('cuda')
    model = DepthAnything3.from_pretrained(args.model_id).to(device=device)

    t_inf = time.time()
    with torch.no_grad():
        pred = model.inference(frame_paths)
    inference_seconds = time.time() - t_inf
    peak_mb = int(torch.cuda.max_memory_allocated() // 1024 ** 2) if torch.cuda.is_available() else 0

    depth_native = np.asarray(pred.depth, dtype=np.float32)        # (N, Hn, Wn)
    conf_native  = np.asarray(pred.conf,  dtype=np.float32)        # (N, Hn, Wn)
    extrinsic = np.asarray(pred.extrinsics, dtype=np.float32)      # (N, 3, 4)
    intrinsic_native = np.asarray(pred.intrinsics, dtype=np.float32)  # (N, 3, 3)

    N, Hn, Wn = depth_native.shape
    Ht = Wt = TARGET_EDGE

    # Stretch resize depth + conf to (Ht, Wt) using bilinear (smooth fields).
    depth_t = torch.from_numpy(depth_native).unsqueeze(1)  # (N,1,Hn,Wn)
    conf_t  = torch.from_numpy(conf_native).unsqueeze(1)
    depth_resized = F.interpolate(depth_t, size=(Ht, Wt), mode='bilinear', align_corners=False).squeeze(1).numpy()
    conf_resized  = F.interpolate(conf_t,  size=(Ht, Wt), mode='bilinear', align_corners=False).squeeze(1).numpy()

    # Scale intrinsics: same camera, different image grid → fx,cx scale by Wt/Wn; fy,cy by Ht/Hn
    sx = Wt / Wn
    sy = Ht / Hn
    intrinsic = intrinsic_native.copy()
    intrinsic[:, 0, 0] *= sx  # fx
    intrinsic[:, 0, 2] *= sx  # cx
    intrinsic[:, 1, 1] *= sy  # fy
    intrinsic[:, 1, 2] *= sy  # cy

    # Unproject depth + extrinsics → world_points (N, Ht, Wt, 3) at target resolution
    v, u = np.meshgrid(np.arange(Ht, dtype=np.float32), np.arange(Wt, dtype=np.float32), indexing='ij')
    world_points = np.zeros((N, Ht, Wt, 3), dtype=np.float32)
    for i in range(N):
        K = intrinsic[i]
        fx, fy, cx, cy = float(K[0,0]), float(K[1,1]), float(K[0,2]), float(K[1,2])
        z = depth_resized[i]
        x = (u - cx) * z / fx
        y = (v - cy) * z / fy
        cam_pts = np.stack([x, y, z], axis=-1).reshape(-1, 3)
        R = extrinsic[i, :, :3]
        t = extrinsic[i, :, 3]
        # extrinsic is w2c (opencv): cam = R @ world + t  →  world = R.T @ (cam - t)
        world = (R.T @ (cam_pts - t).T).T
        world_points[i] = world.reshape(Ht, Wt, 3)

    np.savez_compressed(
        args.output_npz,
        extrinsic=extrinsic,
        intrinsic=intrinsic,
        depth_map=depth_resized.astype(np.float32),
        depth_conf=conf_resized.astype(np.float32),
        world_points=world_points,
        world_points_conf=conf_resized.astype(np.float32),  # DA3 reuses depth conf
        frame_paths=np.array(frame_paths, dtype=object),
    )

    print(json.dumps({
        'inference_seconds': round(float(inference_seconds), 2),
        'peak_mem_mb': peak_mb,
        'frame_count': len(frame_paths),
        'total_subprocess_seconds': round(time.time() - t_total, 2),
        'model_id': args.model_id,
        'native_shape': [int(Hn), int(Wn)],
        'output_shape': [int(Ht), int(Wt)],
        'backend': 'da3',
    }), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
