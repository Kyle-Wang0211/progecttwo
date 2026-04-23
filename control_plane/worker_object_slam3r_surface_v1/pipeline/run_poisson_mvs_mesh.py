"""Production Poisson + MVS Texturing pipeline stage.

Replaces the 2DGS → MAtCha → optimize → bake chain with a single
geometry+texture stage built on:
  - Open3D Poisson surface reconstruction (MIT license)
  - nmoehrle/mvs-texturing (Waechter 2014, BSD 3-Clause)

Inputs (from upstream vggt_geometry stage):
  ctx.output_dir/vggt/vggt_raw.npz          -- world_points + pose/intrinsic
  ctx.sparse2dgs_scene_dir/images/*.jpg     -- 518x518 stretched frames

Outputs (matches baker contract so downstream delivery adapter is untouched):
  ctx.delivery_dir/default_mesh.glb         -- textured GLB deliverable
  ctx.delivery_dir/optimized_mesh.ply       -- geometry only
  ctx.delivery_dir/bake_default_texture.runtime.json
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path
from typing import Any, Callable

from ..context import JobContext

_DA3_VENV_PY = '/workspace/da3_venv/bin/python'
_SUBPROCESS_SCRIPT = '/opt/object_slam3r_surface_v1_sidecar/control_plane/worker_object_slam3r_surface_v1/pipeline/_run_poisson_mvs_subprocess.py'


def run_poisson_mvs_mesh(ctx: JobContext, *, progress_callback: Callable[..., Any] | None = None) -> dict[str, Any]:
    ctx.current_stage = 'poisson_mvs_mesh'
    assert ctx.delivery_dir is not None, 'delivery_dir must be set'
    assert ctx.output_dir is not None

    vggt_npz = ctx.output_dir / 'vggt' / 'vggt_raw.npz'
    if not vggt_npz.exists():
        raise RuntimeError(f'poisson_mvs_vggt_npz_missing: {vggt_npz}')

    # Derive scene images dir from contract (same dir the baker read from)
    contract = ctx.slam3r_dir / 'sparse2dgs_scene_contract.json' if ctx.slam3r_dir else None
    if contract is None or not contract.exists():
        raise RuntimeError(f'poisson_mvs_contract_missing: {contract}')
    scene_dir = Path(json.loads(contract.read_text())['scene_dir'])
    images_dir = scene_dir / 'images'
    if not images_dir.is_dir():
        raise RuntimeError(f'poisson_mvs_images_missing: {images_dir}')

    progress_path = ctx.delivery_dir / 'poisson_mvs.progress.json'
    log_path = ctx.delivery_dir / 'poisson_mvs.log'
    for stale in (progress_path, log_path):
        try: stale.unlink()
        except FileNotFoundError: pass

    cmd = [
        _DA3_VENV_PY, '-u', _SUBPROCESS_SCRIPT,
        '--vggt-npz', str(vggt_npz),
        '--scene-images-dir', str(images_dir),
        '--delivery-dir', str(ctx.delivery_dir),
        '--progress-path', str(progress_path),
    ]
    env = os.environ.copy()
    env.setdefault('PYTORCH_ALLOC_CONF', 'expandable_segments:True')

    t0 = time.time()
    # Spawn subprocess, poll progress file
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    log_fh = log_path.open('w')
    last_emit = None
    try:
        while True:
            rc = proc.poll()
            line = proc.stdout.readline() if proc.stdout else ''
            if line:
                log_fh.write(line); log_fh.flush()
            if progress_path.exists():
                try:
                    pl = json.loads(progress_path.read_text())
                    sig = (pl.get('progress'), pl.get('title'))
                    if sig != last_emit and progress_callback is not None:
                        progress_callback(pl)
                        last_emit = sig
                except Exception:
                    pass
            if rc is not None and not line:
                break
            if not line:
                time.sleep(0.2)
    finally:
        log_fh.close()

    elapsed = time.time() - t0
    if proc.returncode != 0:
        tail = log_path.read_text()[-2000:] if log_path.exists() else ''
        raise RuntimeError(f'poisson_mvs_subprocess_failed rc={proc.returncode} elapsed={elapsed:.1f}s tail={tail}')

    runtime_json = ctx.delivery_dir / 'bake_default_texture.runtime.json'
    summary: dict[str, Any] = {}
    if runtime_json.exists():
        try:
            summary = json.loads(runtime_json.read_text()).get('summary', {})
        except Exception:
            pass
    summary['elapsed_sec'] = round(elapsed, 2)
    summary['backend'] = 'poisson_mvs'
    return summary
