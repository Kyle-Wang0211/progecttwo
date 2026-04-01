#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/root/gs_refs/HI-SLAM2}"
FILE="${ROOT}/tsdf_integrate.py"

python3 - <<'PY' "${FILE}"
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

if "TSDF_CPU_DEVICE_ENV" in text and "\\n    device_name" not in text:
    print(path)
    raise SystemExit(0)

if "import json\n" not in text:
    text = text.replace(
        "import os\nimport argparse\nimport numpy as np\n",
        "import os\nimport json\nimport argparse\nimport numpy as np\n",
        1,
    )

old = """    # TSDF_CPU_EXTRACT_FALLBACK\n    vbg_cpu = None\n    for w in args.weight:\n        try:\n            mesh = vbg.extract_triangle_mesh(weight_threshold=w)\n        except RuntimeError as exc:\n            message = str(exc)\n            print(f\"[tsdf] cuda extract failed for weight={w}: {message}\")\n            if vbg_cpu is None:\n                print(\"[tsdf] creating CPU VBG fallback for mesh extraction\")\n                if hasattr(vbg, 'cpu'):\n                    vbg_cpu = vbg.cpu()\n                else:\n                    vbg_cpu = vbg.to(o3d.core.Device('CPU:0'))\n            mesh = vbg_cpu.extract_triangle_mesh(weight_threshold=w)\n        mesh = mesh.to_legacy()\n        out = f'{args.result}/tsdf_mesh_w{w:.1f}.ply'\n        o3d.io.write_triangle_mesh(out, mesh)\n        print(f\"TSDF saved to {out}\")\n"""
old_always = """    # TSDF_CPU_EXTRACT_ALWAYS\n    if hasattr(vbg, 'cpu'):\n        print(\"[tsdf] creating CPU VBG copy before mesh extraction\")\n        vbg_extract = vbg.cpu()\n    else:\n        print(\"[tsdf] moving VBG to CPU before mesh extraction\")\n        vbg_extract = vbg.to(o3d.core.Device('CPU:0'))\n    for w in args.weight:\n        mesh = vbg_extract.extract_triangle_mesh(weight_threshold=w)\n        mesh = mesh.to_legacy()\n        out = f'{args.result}/tsdf_mesh_w{w:.1f}.ply'\n        o3d.io.write_triangle_mesh(out, mesh)\n        print(f\"TSDF saved to {out}\")\n"""

new = """    # TSDF_CPU_EXTRACT_ALWAYS\n    extract_device = os.environ.get('OPEN3D_TSDF_DEVICE', 'cuda:0').lower()\n    if extract_device.startswith('cpu'):\n        print(\"[tsdf] using existing CPU VBG for mesh extraction\")\n        vbg_extract = vbg\n    elif hasattr(vbg, 'cpu'):\n        print(\"[tsdf] creating CPU VBG copy before mesh extraction\")\n        vbg_extract = vbg.cpu()\n    else:\n        print(\"[tsdf] moving VBG to CPU before mesh extraction\")\n        vbg_extract = vbg.to(o3d.core.Device('CPU:0'))\n    for w in args.weight:\n        mesh = vbg_extract.extract_triangle_mesh(weight_threshold=w)\n        mesh = mesh.to_legacy()\n        out = f'{args.result}/tsdf_mesh_w{w:.1f}.ply'\n        o3d.io.write_triangle_mesh(out, mesh)\n        print(f\"TSDF saved to {out}\")\n"""

if old_always in text:
    text = text.replace(old_always, new, 1)
elif "TSDF_CPU_EXTRACT_ALWAYS" not in text:
    if old in text:
        text = text.replace(old, new, 1)
    else:
        legacy_old = """    for w in args.weight:\n        mesh = vbg.extract_triangle_mesh(weight_threshold=w)\n        mesh = mesh.to_legacy()\n        out = f'{args.result}/tsdf_mesh_w{w:.1f}.ply'\n        o3d.io.write_triangle_mesh(out, mesh)\n        print(f\"TSDF saved to {out}\")\n"""
        if legacy_old not in text:
            raise SystemExit(f"expected mesh extraction block not found in {path}")
        text = text.replace(legacy_old, new, 1)

old_device = "    device = o3d.core.Device('cuda:0')\n"
new_device = """    # TSDF_CPU_DEVICE_ENV
    device_name = os.environ.get('OPEN3D_TSDF_DEVICE', 'cuda:0')
    print(f"[tsdf] using device {device_name}")
    device = o3d.core.Device(device_name)
"""

if old_device in text:
    text = text.replace(old_device, new_device, 1)
else:
    broken_device = "    # TSDF_CPU_DEVICE_ENV\\\\n    device_name = os.environ.get('OPEN3D_TSDF_DEVICE', 'cuda:0')\\\\n    print(f\\\"[tsdf] using device {device_name}\\\")\\\\n    device = o3d.core.Device(device_name)\\\\n\n"
    if broken_device in text:
        text = text.replace(broken_device, new_device, 1)
    elif "TSDF_CPU_DEVICE_ENV" not in text:
        raise SystemExit(f"expected device line not found in {path}")

path.write_text(text)
print(path)
PY

sed -n '1,140p' "${FILE}"
