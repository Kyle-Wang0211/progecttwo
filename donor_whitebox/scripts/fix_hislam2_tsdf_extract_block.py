#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/root/gs_refs/HI-SLAM2/tsdf_integrate.py")
    text = path.read_text()
    old = """    # TSDF_CPU_EXTRACT_ALWAYS
    if hasattr(vbg, 'cpu'):
        print("[tsdf] creating CPU VBG copy before mesh extraction")
        vbg_extract = vbg.cpu()
    else:
        print("[tsdf] moving VBG to CPU before mesh extraction")
        vbg_extract = vbg.to(o3d.core.Device('CPU:0'))
    for w in args.weight:
        mesh = vbg_extract.extract_triangle_mesh(weight_threshold=w)
        mesh = mesh.to_legacy()
        out = f'{args.result}/tsdf_mesh_w{w:.1f}.ply'
        o3d.io.write_triangle_mesh(out, mesh)
        print(f"TSDF saved to {out}")
"""
    new = """    # TSDF_CPU_EXTRACT_ALWAYS
    extract_device = os.environ.get('OPEN3D_TSDF_DEVICE', 'cuda:0').lower()
    if extract_device.startswith('cpu'):
        print("[tsdf] using existing CPU VBG for mesh extraction")
        vbg_extract = vbg
    elif hasattr(vbg, 'cpu'):
        print("[tsdf] creating CPU VBG copy before mesh extraction")
        vbg_extract = vbg.cpu()
    else:
        print("[tsdf] moving VBG to CPU before mesh extraction")
        vbg_extract = vbg.to(o3d.core.Device('CPU:0'))
    for w in args.weight:
        mesh = vbg_extract.extract_triangle_mesh(weight_threshold=w)
        mesh = mesh.to_legacy()
        out = f'{args.result}/tsdf_mesh_w{w:.1f}.ply'
        o3d.io.write_triangle_mesh(out, mesh)
        print(f"TSDF saved to {out}")
"""
    if old not in text:
        print(f"extract block already clean or pattern missing: {path}")
        return 0
    path.write_text(text.replace(old, new, 1))
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
