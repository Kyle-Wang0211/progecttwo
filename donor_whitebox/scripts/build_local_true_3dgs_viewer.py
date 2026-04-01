#!/usr/bin/env python3
import argparse
import math
import os
import shutil
import stat
import struct
from pathlib import Path


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{
      --bg0: #0d1014;
      --bg1: #171b21;
      --bg2: #222934;
      --ink: #f3f6fb;
      --muted: rgba(243, 246, 251, 0.72);
      --panel: rgba(17, 21, 28, 0.74);
      --line: rgba(255, 255, 255, 0.12);
      --accent: #9fd0ff;
      --shadow: 0 18px 80px rgba(0, 0, 0, 0.32);
      --radius: 24px;
    }}

    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background:
        radial-gradient(circle at top, rgba(77, 120, 180, 0.18), transparent 30%),
        linear-gradient(180deg, var(--bg2), var(--bg1) 26%, var(--bg0));
      color: var(--ink);
      font-family: "Avenir Next", "Helvetica Neue", Helvetica, Arial, sans-serif;
    }}

    body {{
      position: relative;
    }}

    #viewer {{
      position: absolute;
      inset: 0;
    }}

    .overlay {{
      position: absolute;
      left: 20px;
      right: 20px;
      top: 20px;
      display: flex;
      justify-content: space-between;
      gap: 20px;
      padding: 18px 22px;
      border-radius: var(--radius);
      background: var(--panel);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
      pointer-events: none;
      z-index: 10;
    }}

    .title {{
      font-size: 22px;
      font-weight: 700;
      letter-spacing: 0.01em;
      margin-bottom: 6px;
    }}

    .subtitle {{
      color: var(--muted);
      font-size: 14px;
      line-height: 1.45;
    }}

    .badge {{
      display: flex;
      align-items: center;
      justify-content: center;
      min-width: 180px;
      padding: 14px 16px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.04);
      color: var(--accent);
      font-size: 13px;
      font-weight: 700;
      text-align: center;
    }}

    .status {{
      position: absolute;
      left: 20px;
      bottom: 20px;
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 14px 18px;
      border-radius: 999px;
      background: rgba(17, 21, 28, 0.72);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
      color: var(--muted);
      font-size: 13px;
      z-index: 10;
      pointer-events: none;
    }}

    .dot {{
      width: 9px;
      height: 9px;
      border-radius: 999px;
      background: #69d29f;
      box-shadow: 0 0 18px rgba(105, 210, 159, 0.8);
    }}

    .error {{
      color: #ffb4b4;
      font-weight: 700;
    }}
  </style>
  <script type="importmap">
  {{
    "imports": {{
      "three": "./lib/three.module.js"
    }}
  }}
  </script>
</head>
<body>
  <div id="viewer"></div>
  <div class="overlay">
    <div>
      <div class="title">{title}</div>
      <div class="subtitle">{subtitle}</div>
    </div>
    <div class="badge">True Gaussian Runtime<br/>Offline Local Viewer</div>
  </div>
  <div id="status" class="status"><span class="dot"></span><span>Loading Gaussian scene…</span></div>

  <script type="module">
    import * as THREE from "three";
    import * as GaussianSplats3D from "./lib/gaussian-splats-3d.module.js";

    const statusEl = document.getElementById("status");
    const viewerRoot = document.getElementById("viewer");
    const initialCameraPosition = new THREE.Vector3(...{initial_camera_position});
    const initialCameraLookAt = new THREE.Vector3(...{initial_camera_look_at});
    const sceneRadius = {scene_radius};

    const viewer = new GaussianSplats3D.Viewer({{
      rootElement: viewerRoot,
      cameraUp: {camera_up},
      initialCameraPosition: initialCameraPosition.toArray(),
      initialCameraLookAt: initialCameraLookAt.toArray(),
      selfDrivenMode: true,
      useBuiltInControls: false,
      sharedMemoryForWorkers: false,
      gpuAcceleratedSort: false,
      integerBasedSort: false,
      ignoreDevicePixelRatio: false,
      sceneRevealMode: GaussianSplats3D.SceneRevealMode.Instant,
      logLevel: GaussianSplats3D.LogLevel.None
    }});

    const pointerState = {{
      mode: null,
      x: 0,
      y: 0
    }};
    const worldUp = new THREE.Vector3(0, 1, 0);

    function getForward() {{
      return new THREE.Vector3(0, 0, -1).applyQuaternion(viewer.camera.quaternion).normalize();
    }}

    function getRight() {{
      return new THREE.Vector3(1, 0, 0).applyQuaternion(viewer.camera.quaternion).normalize();
    }}

    function getMoveStep() {{
      return Math.max(0.03, sceneRadius * 0.05);
    }}

    function translateCamera(delta) {{
      viewer.camera.position.add(delta);
      viewer.camera.updateMatrixWorld();
    }}

    function rotateCamera(dx, dy) {{
      const sensitivity = 0.003;
      if (Math.abs(dx) > 0.0) {{
        const yawQuat = new THREE.Quaternion().setFromAxisAngle(worldUp, -dx * sensitivity);
        viewer.camera.quaternion.premultiply(yawQuat);
      }}
      if (Math.abs(dy) > 0.0) {{
        const right = getRight();
        const pitchQuat = new THREE.Quaternion().setFromAxisAngle(right, -dy * sensitivity);
        viewer.camera.quaternion.premultiply(pitchQuat);
      }}
      viewer.camera.quaternion.normalize();
      viewer.camera.updateMatrixWorld();
    }}

    function resetCamera() {{
      viewer.camera.position.copy(initialCameraPosition);
      viewer.camera.up.copy(worldUp);
      viewer.camera.lookAt(initialCameraLookAt);
      viewer.camera.quaternion.normalize();
      viewer.camera.updateMatrixWorld();
    }}

    function handlePointerDown(event) {{
      pointerState.mode = event.button === 2 ? "pan" : "look";
      pointerState.x = event.clientX;
      pointerState.y = event.clientY;
    }}

    function handlePointerMove(event) {{
      if (!pointerState.mode) return;
      const dx = event.clientX - pointerState.x;
      const dy = event.clientY - pointerState.y;
      pointerState.x = event.clientX;
      pointerState.y = event.clientY;

      if (pointerState.mode === "look") {{
        rotateCamera(dx, dy);
      }} else if (pointerState.mode === "pan") {{
        const panScale = getMoveStep() * 0.015;
        const delta = new THREE.Vector3();
        delta.add(getRight().multiplyScalar(-dx * panScale));
        delta.add(worldUp.clone().multiplyScalar(dy * panScale));
        translateCamera(delta);
      }}
    }}

    function handlePointerUp() {{
      pointerState.mode = null;
    }}

    function handleWheel(event) {{
      event.preventDefault();
      const delta = new THREE.Vector3();
      const scale = getMoveStep() * 0.12;
      if (Math.abs(event.deltaY) > 0.01) {{
        delta.add(getForward().multiplyScalar(Math.sign(-event.deltaY) * scale));
      }}
      if (Math.abs(event.deltaX) > 0.01) {{
        delta.add(getRight().multiplyScalar(Math.sign(event.deltaX) * scale * 0.8));
      }}
      if (delta.lengthSq() > 0) translateCamera(delta);
    }}

    viewer.addSplatScene("./scene/scene.ply", {{
      progressiveLoad: false,
      showLoadingUI: true,
      splatAlphaRemovalThreshold: 5
    }}).then(() => {{
      viewer.start();
      resetCamera();
      const canvas = viewer.renderer.domElement;
      canvas.tabIndex = 0;
      canvas.style.outline = "none";
      canvas.addEventListener("contextmenu", (event) => event.preventDefault());
      canvas.addEventListener("mousedown", handlePointerDown);
      window.addEventListener("mousemove", handlePointerMove);
      window.addEventListener("mouseup", handlePointerUp);
      canvas.addEventListener("wheel", handleWheel, {{ passive: false }});
      canvas.addEventListener("dblclick", resetCamera);
      statusEl.innerHTML = '<span class="dot"></span><span>True free-fly mode. Drag to look, secondary-drag to pan, two-finger/scroll to move. Double-click to reset.</span>';
    }}).catch((error) => {{
      console.error(error);
      statusEl.innerHTML = '<span class="error">Viewer failed to load. Check viewer_server.log.</span>';
    }});
  </script>
</body>
</html>
"""


SERVER_SCRIPT = """#!/usr/bin/env python3
import http.server
import os
import socketserver
from pathlib import Path


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def choose_port(start=8765, end=8799):
    for port in range(start, end + 1):
        try:
            httpd = socketserver.TCPServer(("127.0.0.1", port), Handler)
            return httpd, port
        except OSError:
            continue
    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    return httpd, httpd.server_address[1]


def main():
    root = Path(__file__).resolve().parent
    os.chdir(root)
    httpd, port = choose_port()
    (root / "viewer_port.txt").write_text(str(port), encoding="utf-8")
    print(f"viewer running on http://127.0.0.1:{port}/index.html", flush=True)
    try:
        httpd.serve_forever()
    finally:
        try:
            (root / "viewer_port.txt").unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
"""


LAUNCHER_SCRIPT = """#!/bin/zsh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$DIR/viewer_server.pid"
PORTFILE="$DIR/viewer_port.txt"
LOGFILE="$DIR/viewer_server.log"

if [[ -f "$PIDFILE" ]]; then
  PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ -n "${PID}" ]] && kill -0 "${PID}" 2>/dev/null; then
    if [[ -f "$PORTFILE" ]]; then
      PORT="$(cat "$PORTFILE")"
      open "http://127.0.0.1:${PORT}/index.html"
      exit 0
    fi
  fi
fi

rm -f "$PORTFILE"
/usr/bin/python3 "$DIR/serve_local_viewer.py" >"$LOGFILE" 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"

for _ in {1..120}; do
  if [[ -f "$PORTFILE" ]]; then
    PORT="$(cat "$PORTFILE")"
    open "http://127.0.0.1:${PORT}/index.html"
    exit 0
  fi
  sleep 0.1
done

echo "Viewer server failed to start. Check: $LOGFILE"
exit 1
"""


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-ply", required=True)
    parser.add_argument("--viewer-module", required=True)
    parser.add_argument("--three-module", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--title", default="Interactive 3DGS Viewer")
    parser.add_argument(
        "--subtitle",
        default="Offline local Gaussian viewer. Drag to look, secondary-drag to pan, two-finger/scroll to move.",
    )
    return parser.parse_args()


def percentile(values, p):
    if not values:
        raise SystemExit("cannot compute percentile of empty sequence")
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    pos = (len(values) - 1) * p
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return values[lo]
    weight = pos - lo
    return values[lo] * (1 - weight) + values[hi] * weight


def read_binary_ply_bbox(path: Path):
    with path.open("rb") as f:
        header_lines = []
        while True:
            line = f.readline()
            if not line:
                raise SystemExit("invalid ply header")
            header_lines.append(line.decode("latin1").strip())
            if line.strip() == b"end_header":
                break
        data_offset = f.tell()

    if "format binary_little_endian 1.0" not in header_lines:
        raise SystemExit("only binary_little_endian ply is supported")

    vertex_count = None
    properties = []
    in_vertex = False
    for line in header_lines:
        if line.startswith("element vertex "):
            vertex_count = int(line.split()[-1])
            in_vertex = True
            continue
        if line.startswith("element ") and not line.startswith("element vertex "):
            if in_vertex:
                break
        if in_vertex and line.startswith("property "):
            _, type_name, prop_name = line.split()
            properties.append((type_name, prop_name))

    if vertex_count is None or not properties:
        raise SystemExit("could not parse vertex layout")

    type_map = {
        "char": ("b", 1),
        "uchar": ("B", 1),
        "int8": ("b", 1),
        "uint8": ("B", 1),
        "short": ("h", 2),
        "ushort": ("H", 2),
        "int16": ("h", 2),
        "uint16": ("H", 2),
        "int": ("i", 4),
        "uint": ("I", 4),
        "int32": ("i", 4),
        "uint32": ("I", 4),
        "float": ("f", 4),
        "float32": ("f", 4),
        "double": ("d", 8),
        "float64": ("d", 8),
    }

    fmt = "<"
    x_index = y_index = z_index = None
    for idx, (type_name, prop_name) in enumerate(properties):
        if type_name not in type_map:
            raise SystemExit(f"unsupported ply property type: {type_name}")
        fmt += type_map[type_name][0]
        if prop_name == "x":
            x_index = idx
        elif prop_name == "y":
            y_index = idx
        elif prop_name == "z":
            z_index = idx

    if x_index is None or y_index is None or z_index is None:
        raise SystemExit("ply does not contain x/y/z")

    vertex_struct = struct.Struct(fmt)
    xs = []
    ys = []
    zs = []

    with path.open("rb") as f:
        f.seek(data_offset)
        for _ in range(vertex_count):
            record = f.read(vertex_struct.size)
            if len(record) != vertex_struct.size:
                raise SystemExit("unexpected eof while reading ply vertices")
            values = vertex_struct.unpack(record)
            xyz = (values[x_index], values[y_index], values[z_index])
            xs.append(xyz[0])
            ys.append(xyz[1])
            zs.append(xyz[2])

    p05 = [percentile(xs, 0.05), percentile(ys, 0.05), percentile(zs, 0.05)]
    p50 = [percentile(xs, 0.50), percentile(ys, 0.50), percentile(zs, 0.50)]
    p95 = [percentile(xs, 0.95), percentile(ys, 0.95), percentile(zs, 0.95)]
    center = [(lo + hi) * 0.5 for lo, hi in zip(p05, p95)]
    extents = [hi - lo for lo, hi in zip(p05, p95)]
    radius = max(max(extents) * 0.9, 0.5)
    eye_height = p50[1]
    look_at = [center[0], eye_height, p50[2]]
    camera_position = [center[0], eye_height, p95[2] + radius * 1.1]
    return look_at, camera_position, radius


def write_text(path: Path, content: str):
    path.write_text(content, encoding="utf-8")


def main():
    args = parse_args()
    source_ply = Path(args.source_ply).expanduser().resolve()
    viewer_module = Path(args.viewer_module).expanduser().resolve()
    three_module = Path(args.three_module).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()

    if not source_ply.exists():
        raise SystemExit(f"missing source ply: {source_ply}")
    if not viewer_module.exists():
        raise SystemExit(f"missing viewer module: {viewer_module}")
    if not three_module.exists():
        raise SystemExit(f"missing three module: {three_module}")

    look_at, camera_position, scene_radius = read_binary_ply_bbox(source_ply)

    lib_dir = output_dir / "lib"
    scene_dir = output_dir / "scene"
    output_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)
    scene_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(viewer_module, lib_dir / "gaussian-splats-3d.module.js")
    shutil.copy2(three_module, lib_dir / "three.module.js")
    shutil.copy2(source_ply, scene_dir / "scene.ply")

    write_text(
        output_dir / "index.html",
        HTML_TEMPLATE.format(
            title=args.title,
            subtitle=args.subtitle,
            camera_up=str([0, 1, 0]),
            initial_camera_position=str([round(v, 6) for v in camera_position]),
            initial_camera_look_at=str([round(v, 6) for v in look_at]),
            scene_radius=round(scene_radius, 6),
        ),
    )
    write_text(output_dir / "serve_local_viewer.py", SERVER_SCRIPT)
    launcher = output_dir / "Open HI-SLAM2 Realdesk 3DGS Viewer.command"
    write_text(launcher, LAUNCHER_SCRIPT)
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    write_text(
        output_dir / "README.txt",
        "\n".join(
            [
                args.title,
                "",
                "Double-click: Open HI-SLAM2 Realdesk 3DGS Viewer.command",
                "This starts a local offline viewer on localhost and opens your browser.",
                "",
                f"Scene source: {source_ply.name}",
            ]
        )
        + "\n",
    )

    print(f"output_dir={output_dir}")
    print(f"camera_look_at={look_at}")
    print(f"camera_position={camera_position}")
    print(f"scene_radius={scene_radius}")


if __name__ == "__main__":
    main()
