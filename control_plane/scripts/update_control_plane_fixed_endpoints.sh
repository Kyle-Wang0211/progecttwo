#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
from pathlib import Path

path = Path("/root/control_plane/.env.runtime")
text = path.read_text()
repls = {
    "CONTROL_PLANE_PUBLIC_BASE_URL=": "CONTROL_PLANE_PUBLIC_BASE_URL=https://api.aether-3d.com",
    "CONTROL_PLANE_OBJECT_STORAGE_ENDPOINT_URL=": "CONTROL_PLANE_OBJECT_STORAGE_ENDPOINT_URL=https://sfo3.digitaloceanspaces.com",
    "CONTROL_PLANE_OBJECT_STORAGE_REGION=": "CONTROL_PLANE_OBJECT_STORAGE_REGION=sfo3",
    "CONTROL_PLANE_OBJECT_STORAGE_PUBLIC_BASE_URL=": "CONTROL_PLANE_OBJECT_STORAGE_PUBLIC_BASE_URL=https://sfo3.digitaloceanspaces.com/aether-control",
    "CONTROL_PLANE_OBJECT_STORAGE_ADDRESSING_STYLE=": "CONTROL_PLANE_OBJECT_STORAGE_ADDRESSING_STYLE=path",
}
out = []
seen = set()
for line in text.splitlines():
    replaced = False
    for prefix, newline in repls.items():
        if line.startswith(prefix):
            out.append(newline)
            seen.add(prefix)
            replaced = True
            break
    if not replaced:
        out.append(line)
for prefix, newline in repls.items():
    if prefix not in seen:
        out.append(newline)
path.write_text("\n".join(out) + "\n")
PY

set -a
. /root/control_plane/.env.runtime
set +a

pkill -f "minio server /root/minio-data" || true

bash /root/control_plane/scripts/start_control_plane.sh /root/control_plane /root/control-plane-logs 8787

cat /root/control_plane/.env.runtime
