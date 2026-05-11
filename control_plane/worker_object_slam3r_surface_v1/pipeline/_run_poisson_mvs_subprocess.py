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

# Binary paths: env override first, then probe the two known install layouts.
# Original /workspace/mvs-texturing/... was the legacy sidecar layout; new
# Vast.ai workers build mvs-texturing under /root/third_party/. Without the
# env-or-probe fallback, run_poisson_mvs_subprocess() fails immediately on
# any non-legacy worker.
def _resolve_bin(env_key: str, candidates: list[str]) -> str:
    override = os.environ.get(env_key)
    if override:
        return override
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]


TEXRECON_BIN = _resolve_bin('TEXRECON_BIN', [
    '/root/third_party/mvs-texturing/build/apps/texrecon/texrecon',
    '/workspace/mvs-texturing/build/apps/texrecon/texrecon',
])
POISSON_RECON_BIN = os.environ.get('POISSON_RECON_BIN', '/root/third_party/PoissonRecon/Bin/Linux/PoissonRecon')
SURFACE_TRIMMER_BIN = os.environ.get('SURFACE_TRIMMER_BIN', '/root/third_party/PoissonRecon/Bin/Linux/SurfaceTrimmer')
TARGET_EDGE = 518


def _run_poisson(pcd, *, depth: int, work_dir: Path):
    """Poisson surface reconstruction with multi-threaded fast path.

    Fast: PoissonRecon C++ binary (mkazhdan/PoissonRecon, OpenMP, 16-32x speedup on 32+ cores).
    Fallback: Open3D Python (single-threaded; used if binary missing or fails).

    Returns (open3d.geometry.TriangleMesh, np.ndarray of per-vertex density).
    """
    import numpy as np
    import open3d as o3d

    if os.path.exists(POISSON_RECON_BIN) and os.access(POISSON_RECON_BIN, os.X_OK):
        try:
            n_threads = int(os.environ.get('POISSON_RECON_THREADS',
                                           os.environ.get('OMP_NUM_THREADS', 32)))
            in_ply = work_dir / '_poisson_in.ply'
            out_ply = work_dir / '_poisson_out.ply'
            o3d.io.write_point_cloud(str(in_ply), pcd, write_ascii=False)

            cmd = [POISSON_RECON_BIN,
                   '--in', str(in_ply),
                   '--out', str(out_ply),
                   '--depth', str(depth),
                   '--density']
            # PoissonRecon CLI 不接受 --threads,通过 OMP_NUM_THREADS env 控制
            env = {**os.environ, 'OMP_NUM_THREADS': str(n_threads)}
            print(f'PoissonRecon binary: depth={depth} OMP_NUM_THREADS={n_threads}', flush=True)
            rc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=env)
            if rc.returncode != 0:
                raise RuntimeError(f'rc={rc.returncode} stderr={rc.stderr[-400:]}')

            # Layer 1: SurfaceTrimmer (Misha Kazhdan 官方 PoissonRecon 工具) ——
            # 砍低 sampling density 区域(噪声/外推) + 小 island(碎片)
            # 参考: https://www.cs.jhu.edu/~misha/Code/PoissonRecon/
            if os.path.exists(SURFACE_TRIMMER_BIN) and os.access(SURFACE_TRIMMER_BIN, os.X_OK):
                trim_density = float(os.environ.get('POISSON_TRIM_DENSITY', '7'))
                trim_aratio = float(os.environ.get('POISSON_TRIM_ARATIO', '0.005'))
                trim_smooth = int(os.environ.get('POISSON_TRIM_SMOOTH', '5'))
                trimmed_ply = work_dir / '_poisson_trimmed.ply'
                trim_cmd = [SURFACE_TRIMMER_BIN,
                            '--in', str(out_ply),
                            '--out', str(trimmed_ply),
                            '--trim', str(trim_density),
                            '--aRatio', str(trim_aratio),
                            '--smooth', str(trim_smooth)]
                print(f'SurfaceTrimmer: trim={trim_density} aRatio={trim_aratio} smooth={trim_smooth}', flush=True)
                trim_rc = subprocess.run(trim_cmd, capture_output=True, text=True, timeout=300, env=env)
                if trim_rc.returncode == 0 and trimmed_ply.exists():
                    # Sanity check:trim 不能砍到接近空(均匀低 density 输入会被全砍掉)
                    raw_size = out_ply.stat().st_size
                    trim_size = trimmed_ply.stat().st_size
                    if trim_size > 1024 and trim_size > raw_size * 0.01:
                        print(f'SurfaceTrimmer OK: {raw_size:,}b → {trim_size:,}b', flush=True)
                        out_ply.unlink(missing_ok=True)
                        out_ply = trimmed_ply
                    else:
                        print(f'SurfaceTrimmer too aggressive ({trim_size}b vs raw {raw_size:,}b);'
                              f' falling back to raw PoissonRecon output', flush=True)
                        trimmed_ply.unlink(missing_ok=True)
                else:
                    print(f'SurfaceTrimmer failed (rc={trim_rc.returncode}); using raw PoissonRecon output', flush=True)

            from plyfile import PlyData
            ply = PlyData.read(str(out_ply))
            v = ply['vertex']
            verts = np.column_stack([v['x'], v['y'], v['z']]).astype(np.float64)
            density_props = [p.name for p in v.properties if p.name in ('quality', 'density', 'value')]
            if density_props:
                densities = np.asarray(v[density_props[0]], dtype=np.float64)
            else:
                print('WARN: no density property in PoissonRecon output', flush=True)
                densities = np.ones(len(verts))
            faces = np.vstack(ply['face']['vertex_indices']).astype(np.int32)

            mesh = o3d.geometry.TriangleMesh()
            mesh.vertices = o3d.utility.Vector3dVector(verts)
            mesh.triangles = o3d.utility.Vector3iVector(faces)
            in_ply.unlink(missing_ok=True)
            out_ply.unlink(missing_ok=True)
            return mesh, densities
        except Exception as e:
            print(f'PoissonRecon binary failed ({type(e).__name__}: {e}); fallback to Open3D', flush=True)
    else:
        print(f'PoissonRecon binary not at {POISSON_RECON_BIN}; using Open3D (slow)', flush=True)

    return o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=depth)


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
    # Octree depth for Poisson surface reconstruction.
    #
    # Default 9 (not 10): for VGGT-derived point clouds, 255k voxel-downsampled
    # points lives at the depth=8-9 sweet spot. depth=10 was the original
    # c380d33 default but in practice over-resolves the octree (max 1024³
    # cells), spending 20+ minutes on sparse cells with no payoff in quality
    # — and amplifying VGGT depth noise. Measured 2026-05-11: depth=10 took
    # 1303 s on a 30 s dome capture; depth=9 expected 60-180 s on same input.
    #
    # Override via AETHER_POISSON_DEPTH (also: --poisson-depth on CLI).
    ap.add_argument(
        '--poisson-depth',
        type=int,
        default=int(os.environ.get('AETHER_POISSON_DEPTH', '9')),
    )
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
    mesh, densities = _run_poisson(pcd, depth=args.poisson_depth, work_dir=delivery)
    print(f'Poisson done {time.time()-t0:.1f}s: {len(mesh.vertices):,}v {len(mesh.triangles):,}f', flush=True)

    _write_progress(prog, 0.45, '去除低密度面', f'q={args.density_quantile}')
    densities = np.asarray(densities)
    mesh.remove_vertices_by_mask(densities < np.quantile(densities, args.density_quantile))

    # Layer 2: keep only top-N largest connected components (Open3D 文档化 standard)
    # "useful for reconstruction methods that don't always produce single mesh; smaller parts are often noise"
    # 参考: https://www.open3d.org/docs/release/python_api/open3d.geometry.TriangleMesh.html
    keep_top_n = int(os.environ.get('POISSON_KEEP_TOP_COMPONENTS', '5'))
    if keep_top_n > 0 and len(mesh.triangles) > 0:
        clusters, n_tri, _ = mesh.cluster_connected_triangles()
        n_tri = np.asarray(n_tri)
        clusters = np.asarray(clusters)
        if len(n_tri) > keep_top_n:
            largest_idx = np.argsort(n_tri)[-keep_top_n:]
            drop_mask = ~np.isin(clusters, largest_idx)
            n_dropped = int(drop_mask.sum())
            mesh.remove_triangles_by_mask(drop_mask)
            mesh.remove_unreferenced_vertices()
            print(f'cluster cleanup: {len(n_tri)} components → {keep_top_n}, dropped {n_dropped:,} faces', flush=True)

    # Layer 3: hole fill (Open3D tensor mesh fill_holes).
    #
    # After SurfaceTrimmer + density filter + cluster cleanup, the mesh
    # still has boundary loops where VGGT confidence dropped below
    # conf_floor (smooth surfaces, occluded faces). Poisson can't bridge
    # those gaps without point support. Open3D's tensor fill_holes (port
    # of Liepa 2003 + Barequet & Sharir 1995) re-triangulates boundary
    # loops up to a given perimeter. Doesn't invent geometry — just
    # patches small/medium holes with planar triangulation, leaves big
    # missing regions alone (which is the right behavior: if VGGT didn't
    # see it, we shouldn't fabricate it).
    #
    # `POISSON_FILL_HOLES_SIZE` is a length threshold (boundary loop
    # perimeter in mesh units, typically meters): loops shorter than
    # this get filled, longer ones (genuine missing chunks) stay open.
    # Default 0.3 m ≈ ~30cm boundary perimeter,适合椅子座面这种
    # smooth-surface hole 但不会乱填整个 mesh.
    # Set to 0 to disable.
    fill_hole_size = float(os.environ.get('POISSON_FILL_HOLES_SIZE', '0.3'))
    if fill_hole_size > 0 and len(mesh.triangles) > 0:
        try:
            v_before, f_before = len(mesh.vertices), len(mesh.triangles)
            tm = o3d.t.geometry.TriangleMesh.from_legacy(mesh)
            tm = tm.fill_holes(hole_size=fill_hole_size)
            mesh = tm.to_legacy()
            v_after, f_after = len(mesh.vertices), len(mesh.triangles)
            print(
                f'hole fill: hole_size={fill_hole_size}m, '
                f'{v_before:,}v {f_before:,}f → {v_after:,}v {f_after:,}f '
                f'(+{f_after - f_before:,}f)', flush=True)
        except Exception as e:
            print(f'hole fill failed ({type(e).__name__}: {e}); using mesh as-is', flush=True)

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
