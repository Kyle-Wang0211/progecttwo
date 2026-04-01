#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/root/gs_refs/HI-SLAM2/tsdf_integrate.py")
    text = path.read_text()
    bad = """    # TSDF_CPU_DEVICE_ENV\\n    device_name = os.environ.get('OPEN3D_TSDF_DEVICE', 'cuda:0')\\n    print(f"[tsdf] using device {device_name}")\\n    device = o3d.core.Device(device_name)\\n
"""
    good = """    # TSDF_CPU_DEVICE_ENV
    device_name = os.environ.get('OPEN3D_TSDF_DEVICE', 'cuda:0')
    print(f"[tsdf] using device {device_name}")
    device = o3d.core.Device(device_name)

"""
    if bad not in text:
        print(f"device block already clean or pattern missing: {path}")
        return 0
    path.write_text(text.replace(bad, good, 1))
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
