"""Poisson surface reconstruction + MVS Texturing subprocess.

Pipeline:
  1. Load VGGT/DA3 npz (world_points + conf + extrinsics + intrinsics)
  2. Filter by confidence + voxel downsample + estimate normals + consistent_tangent_plane
  3. Poisson surface reconstruction (depth=10) + q=0.05 density filter
  4. Build MVE scene folder (one .cam per 518x518 image)
  5. Invoke texrecon (nmoehrle/mvs-texturing) for photo-texture projection
  6. Load resulting OBJ, convert to GLB, write delivery/default_mesh.glb

Progress reported via --progress-path JSON file (parent polls).

Contract (matches baker output):
  delivery_dir/default_mesh.glb      (main deliverable)
  delivery_dir/optimized_mesh.ply    (geometry only, for downstream tools)
"""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys, time
from pathlib import Path

TEXRECON_BIN = '/workspace/mvs-texturing/build/apps/texrecon/texrecon'
TARGET_EDGE = 518


def _write_progress(path: Path, progress: float, title: str, detail: str, extra: dict | None = None):
    payload = {'progress': float(progress), 'title': title, 'detail': detail, 'metrics': extra or {}}
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--vggt-npz', required=True)
    ap.add_argument('--scene-images-dir', required=True, help='MVE-ready 518x518 images source')
    ap.add_argument('--delivery-dir', required=True)
    ap.add_argument('--progress-path', required=True)
    ap.add_argument('--conf-floor', type=float, default=1.5)
    ap.add_argument('--voxel-size', type=float, default=0.005)
    ap.add_argument('--poisson-depth', type=int, default=10)
    ap.add_argument('--density-quantile', type=float, default=0.05)
    args = ap.parse_args()

    delivery = Path(args.delivery_dir); delivery.mkdir(parents=True, exist_ok=True)
    prog = Path(args.progress_path)

    import numpy as np
    import open3d as o3d
    import trimesh

    _write_progress(prog, 0.05, '加载 VGGT 点云', 'Loading VGGT world_points + confidence')
    d = np.load(args.vggt_npz, allow_pickle=True)
    pts = d['world_points'].reshape(-1, 3)
    conf = d['world_points_conf'].reshape(-1)
    kept = pts[conf >= args.conf_floor]
    print(f'kept {len(kept):,} / {len(pts):,} points (conf >= {args.conf_floor})', flush=True)

    _write_progress(prog, 0.15, '点云降采 + 估法向', f'voxel {args.voxel_size*1000:.1f}mm')
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(kept)
    pcd = pcd.voxel_down_sample(voxel_size=args.voxel_size)
    n_pts = len(pcd.points)
    print(f'after voxel: {n_pts:,}', flush=True)
    pcd.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=args.voxel_size*4, max_nn=30))
    t0 = time.time()
    pcd.orient_normals_consistent_tangent_plane(k=15)
    print(f'consistent_tangent_plane done {time.time()-t0:.1f}s', flush=True)

    _write_progress(prog, 0.30, 'Poisson 表面重建', f'depth={args.poisson_depth}')
    t0 = time.time()
    mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=args.poisson_depth)
    print(f'Poisson done {time.time()-t0:.1f}s: {len(mesh.vertices):,}v {len(mesh.triangles):,}f', flush=True)

    _write_progress(prog, 0.45, '去除低密度面', f'q={args.density_quantile}')
    densities = np.asarray(densities)
    mesh.remove_vertices_by_mask(densities < np.quantile(densities, args.density_quantile))
    mesh.compute_vertex_normals()
    geom_ply = delivery / 'optimized_mesh.ply'
    o3d.io.write_triangle_mesh(str(geom_ply), mesh)
    print(f'geometry: {len(mesh.vertices):,}v {len(mesh.triangles):,}f -> {geom_ply}', flush=True)

    _write_progress(prog, 0.55, '构建 MVE 场景', '写 per-frame .cam 文件')
    scene_dir = delivery / '_mvs_scene'
    scene_dir.mkdir(exist_ok=True)
    extrinsic = np.asarray(d['extrinsic'])
    intrinsic = np.asarray(d['intrinsic'])
    N = extrinsic.shape[0]
    src_imgs = sorted(Path(args.scene_images_dir).glob('*.jpg')) + sorted(Path(args.scene_images_dir).glob('*.png'))
    assert len(src_imgs) == N, f'image count mismatch: {len(src_imgs)} vs {N}'
    larger = TARGET_EDGE
    for i, src in enumerate(src_imgs):
        dst_img = scene_dir / f'{i:05d}.jpg'
        if not dst_img.exists():
            shutil.copy(src, dst_img)
        R = extrinsic[i, :3, :3]
        t = extrinsic[i, :3, 3]
        K = intrinsic[i]
        fx, fy, cx, cy = float(K[0,0]), float(K[1,1]), float(K[0,2]), float(K[1,2])
        f_norm = fx / larger
        paspect = fy / fx
        ppx, ppy = cx / TARGET_EDGE, cy / TARGET_EDGE
        cam_text = (
            f'{t[0]:.6f} {t[1]:.6f} {t[2]:.6f} '
            f'{R[0,0]:.6f} {R[0,1]:.6f} {R[0,2]:.6f} '
            f'{R[1,0]:.6f} {R[1,1]:.6f} {R[1,2]:.6f} '
            f'{R[2,0]:.6f} {R[2,1]:.6f} {R[2,2]:.6f}\n'
            f'{f_norm:.6f} 0 0 {paspect:.6f} {ppx:.6f} {ppy:.6f}\n'
        )
        (scene_dir / f'{i:05d}.cam').write_text(cam_text)
    print(f'MVE scene: {N} cam files in {scene_dir}', flush=True)

    _write_progress(prog, 0.65, '投影多视图贴图 (MVS Texturing)', 'texrecon running...')
    tex_prefix = delivery / '_tex' / 'textured'
    tex_prefix.parent.mkdir(exist_ok=True)
    t0 = time.time()
    rc = subprocess.run(
        [TEXRECON_BIN, str(scene_dir), str(geom_ply), str(tex_prefix)],
        capture_output=True, text=True,
    )
    if rc.returncode != 0:
        print(f'texrecon FAILED rc={rc.returncode}', flush=True)
        print(f'stderr tail:\n{rc.stderr[-2000:]}', flush=True)
        return 2
    print(f'texrecon done {time.time()-t0:.1f}s', flush=True)

    _write_progress(prog, 0.90, '打包 GLB', '转换 OBJ → GLB')
    obj_path = tex_prefix.with_suffix('.obj')
    tex_mesh = trimesh.load(obj_path, process=False)
    glb_out = delivery / 'default_mesh.glb'
    tex_mesh.export(glb_out)
    print(f'GLB written: {glb_out} ({glb_out.stat().st_size/1024/1024:.1f} MB)', flush=True)

    _write_progress(prog, 1.0, 'Poisson+MVS 完成', 'mesh + texture 已生成')
    meta = {
        'backend': 'poisson_mvs',
        'conf_floor': args.conf_floor,
        'voxel_size_m': args.voxel_size,
        'poisson_depth': args.poisson_depth,
        'density_quantile': args.density_quantile,
        'vertex_count': int(len(mesh.vertices)),
        'face_count': int(len(mesh.triangles)),
        'glb_bytes': int(glb_out.stat().st_size),
    }
    (delivery / 'bake_default_texture.runtime.json').write_text(json.dumps({'status': 'ok', 'summary': meta}, indent=2))
    print(json.dumps(meta), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
