#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <images_dir> [sleep_s]" >&2
  exit 2
fi

IMAGES_DIR="$1"
SLEEP_S="${2:-10}"
MIN_AGE_S="${3:-15}"
DONE_FILE="$(dirname "$IMAGES_DIR")/.complete"

if [[ ! -d "$IMAGES_DIR" ]]; then
  echo "images dir not found: $IMAGES_DIR" >&2
  exit 2
fi

export IMAGES_DIR DONE_FILE MIN_AGE_S

while true; do
  python3 - <<'PY'
import os
import time
from pathlib import Path

pil = None
cv2 = None
try:
    from PIL import Image
    pil = Image
except Exception:
    pil = None
if pil is None:
    try:
        import cv2  # type: ignore
    except Exception as exc:
        raise SystemExit(f"image backend import failed: {exc}")

images_dir = Path(os.environ["IMAGES_DIR"])
now = time.time()
converted = 0

for ppm in sorted(images_dir.glob("*.ppm")):
    try:
        st = ppm.stat()
    except FileNotFoundError:
        continue
    # Skip files that may still be actively written.
    if now - st.st_mtime < float(os.environ["MIN_AGE_S"]):
        continue
    jpg = ppm.with_suffix(".jpg")
    if jpg.exists():
        ppm.unlink(missing_ok=True)
        continue
    ok = False
    if pil is not None:
        try:
            with pil.open(ppm) as img:
                img.save(jpg, format="JPEG", quality=92)
            ok = True
        except Exception:
            ok = False
    else:
        img = cv2.imread(str(ppm), cv2.IMREAD_COLOR)
        if img is not None:
            ok = cv2.imwrite(str(jpg), img, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if ok:
        ppm.unlink(missing_ok=True)
        converted += 1

print(converted)
PY

  remaining_ppm="$(find "$IMAGES_DIR" -maxdepth 1 -type f -name '*.ppm' | wc -l | tr -d ' ')"
  if [[ -f "$DONE_FILE" && "$remaining_ppm" == "0" ]]; then
    echo "[stream-compress] done"
    break
  fi
  sleep "$SLEEP_S"
done
