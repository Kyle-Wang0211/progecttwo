#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{
      --bg: #ece6dc;
      --panel: rgba(255,255,255,0.72);
      --ink: #2d241d;
      --muted: #6d6358;
      --accent: #8e4b24;
      --shadow: 0 18px 60px rgba(45, 36, 29, 0.18);
      --radius: 24px;
      --frame-shadow: 0 24px 60px rgba(26, 19, 12, 0.2);
    }}

    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background:
        radial-gradient(circle at top, rgba(255,255,255,0.9), rgba(255,255,255,0.35) 30%, rgba(236,230,220,0.95) 70%),
        linear-gradient(180deg, #f8f4ee, #e8dfd2);
      color: var(--ink);
      font-family: "Avenir Next", "Helvetica Neue", Helvetica, Arial, sans-serif;
    }}

    body {{
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 12px;
      padding: 20px;
    }}

    .bar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 20px;
      border-radius: var(--radius);
      background: var(--panel);
      backdrop-filter: blur(16px);
      box-shadow: var(--shadow);
    }}

    .title {{
      font-size: 18px;
      font-weight: 700;
      letter-spacing: 0.01em;
    }}

    .meta {{
      color: var(--muted);
      font-size: 13px;
    }}

    .stage {{
      position: relative;
      min-height: 0;
      border-radius: 32px;
      overflow: hidden;
      box-shadow: var(--frame-shadow);
      background:
        radial-gradient(circle at 50% 15%, rgba(255,255,255,0.95), rgba(255,255,255,0.55) 28%, rgba(212,196,175,0.42) 60%, rgba(155,125,94,0.2) 100%);
      user-select: none;
      touch-action: none;
      cursor: grab;
    }}

    .stage.dragging {{ cursor: grabbing; }}

    .backdrop {{
      position: absolute;
      inset: 0;
      background:
        radial-gradient(circle at 50% 16%, rgba(255,255,255,0.98), rgba(255,255,255,0.72) 25%, rgba(190,159,124,0.18) 60%, rgba(110,85,55,0.08) 100%),
        linear-gradient(180deg, rgba(255,255,255,0.42), rgba(181,143,105,0.08));
    }}

    .frame-wrap {{
      position: absolute;
      inset: 0;
      display: grid;
      place-items: center;
      transform-origin: center center;
      will-change: transform;
    }}

    .frame {{
      max-width: min(76vw, 880px);
      max-height: min(72vh, 980px);
      width: auto;
      height: auto;
      object-fit: contain;
      filter: drop-shadow(0 32px 46px rgba(79, 52, 25, 0.28));
      image-rendering: auto;
    }}

    .hint {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
      padding: 16px 20px;
      border-radius: var(--radius);
      background: rgba(255,255,255,0.68);
      box-shadow: var(--shadow);
      backdrop-filter: blur(16px);
      color: var(--muted);
      font-size: 13px;
    }}

    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(142,75,36,0.08);
      color: var(--accent);
      font-weight: 700;
    }}

    .progress {{
      min-width: 160px;
      text-align: right;
      font-variant-numeric: tabular-nums;
    }}

    .loading {{
      position: absolute;
      inset: 0;
      display: grid;
      place-items: center;
      color: var(--muted);
      font-size: 14px;
      letter-spacing: 0.04em;
      background: rgba(255,255,255,0.72);
      backdrop-filter: blur(12px);
      transition: opacity 160ms ease;
    }}

    .loading.hidden {{
      opacity: 0;
      pointer-events: none;
    }}
  </style>
</head>
<body>
  <div class="bar">
    <div>
      <div class="title">{title}</div>
      <div class="meta">{subtitle}</div>
    </div>
    <div class="pill"><span id="frameLabel">Frame 1</span></div>
  </div>

  <div id="stage" class="stage">
    <div class="backdrop"></div>
    <div id="frameWrap" class="frame-wrap">
      <img id="frame" class="frame" alt="Rendered turntable frame" />
    </div>
    <div id="loading" class="loading">Loading rendered views…</div>
  </div>

  <div class="hint">
    <div>Drag left/right to rotate. Use the mouse wheel or trackpad to zoom. Double-click to reset.</div>
    <div id="progress" class="progress">1 / 1</div>
  </div>

  <script>
    const frames = {frames_json};
    const stage = document.getElementById('stage');
    const frameEl = document.getElementById('frame');
    const frameWrap = document.getElementById('frameWrap');
    const frameLabel = document.getElementById('frameLabel');
    const progress = document.getElementById('progress');
    const loading = document.getElementById('loading');

    let current = 0;
    let zoom = 1;
    let dragging = false;
    let lastX = 0;
    let accumulator = 0;
    const pixelsPerFrame = 8;
    const cache = new Map();

    function clamp(value, min, max) {{
      return Math.min(max, Math.max(min, value));
    }}

    function normalizeIndex(index) {{
      const len = frames.length;
      return ((index % len) + len) % len;
    }}

    function updateZoom() {{
      frameWrap.style.transform = `scale(${{zoom.toFixed(3)}})`;
    }}

    function setFrame(index) {{
      current = normalizeIndex(index);
      const src = frames[current];
      if (frameEl.getAttribute('src') !== src) {{
        frameEl.setAttribute('src', src);
      }}
      frameLabel.textContent = `Frame ${{String(current + 1).padStart(4, '0')}}`;
      progress.textContent = `${{current + 1}} / ${{frames.length}}`;

      for (const offset of [1, 2, 3, -1, -2, -3]) {{
        const preloadIndex = normalizeIndex(current + offset);
        const preloadSrc = frames[preloadIndex];
        if (!cache.has(preloadSrc)) {{
          const img = new Image();
          img.src = preloadSrc;
          cache.set(preloadSrc, img);
        }}
      }}
    }}

    frameEl.addEventListener('load', () => {{
      loading.classList.add('hidden');
    }});

    stage.addEventListener('pointerdown', (event) => {{
      dragging = true;
      accumulator = 0;
      lastX = event.clientX;
      stage.classList.add('dragging');
      stage.setPointerCapture(event.pointerId);
    }});

    stage.addEventListener('pointermove', (event) => {{
      if (!dragging) return;
      const dx = event.clientX - lastX;
      lastX = event.clientX;
      accumulator += dx;
      if (Math.abs(accumulator) >= pixelsPerFrame) {{
        const step = Math.trunc(accumulator / pixelsPerFrame);
        accumulator -= step * pixelsPerFrame;
        setFrame(current - step);
      }}
    }});

    function stopDragging(event) {{
      if (!dragging) return;
      dragging = false;
      accumulator = 0;
      stage.classList.remove('dragging');
      if (event) {{
        try {{ stage.releasePointerCapture(event.pointerId); }} catch (_) {{}}
      }}
    }}

    stage.addEventListener('pointerup', stopDragging);
    stage.addEventListener('pointercancel', stopDragging);
    stage.addEventListener('pointerleave', stopDragging);

    stage.addEventListener('wheel', (event) => {{
      event.preventDefault();
      const factor = event.deltaY < 0 ? 1.08 : 0.92;
      zoom = clamp(zoom * factor, 0.6, 3.0);
      updateZoom();
    }}, {{ passive: false }});

    stage.addEventListener('dblclick', () => {{
      zoom = 1;
      updateZoom();
    }});

    window.addEventListener('keydown', (event) => {{
      if (event.key === 'ArrowRight') setFrame(current + 1);
      if (event.key === 'ArrowLeft') setFrame(current - 1);
      if (event.key === '+' || event.key === '=') {{
        zoom = clamp(zoom * 1.08, 0.6, 3.0);
        updateZoom();
      }}
      if (event.key === '-') {{
        zoom = clamp(zoom * 0.92, 0.6, 3.0);
        updateZoom();
      }}
      if (event.key.toLowerCase() === 'r') {{
        zoom = 1;
        updateZoom();
      }}
    }});

    updateZoom();
    setFrame(0);
  </script>
</body>
</html>
"""


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--images-dir", required=True)
    parser.add_argument("--output-html", required=True)
    parser.add_argument("--title", default="Interactive Render Viewer")
    parser.add_argument("--subtitle", default="Drag to rotate rendered views")
    return parser.parse_args()


def main():
    args = parse_args()
    images_dir = Path(args.images_dir)
    output_html = Path(args.output_html)
    frames = [p.name for p in sorted(images_dir.glob("*.jpg"))]
    if not frames:
        raise SystemExit(f"no jpg frames found in {images_dir}")

    output_html.write_text(
        HTML_TEMPLATE.format(
            title=args.title,
            subtitle=args.subtitle,
            frames_json=json.dumps(frames),
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "images_dir": str(images_dir),
                "output_html": str(output_html),
                "frames": len(frames),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
