#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEFAULT_DONOR="/tmp/gs_refs/HI-SLAM2"
if [[ -d "/root/gs_refs/HI-SLAM2" ]]; then
  DEFAULT_DONOR="/root/gs_refs/HI-SLAM2"
fi
DONOR="${HI_SLAM2_REPO:-$DEFAULT_DONOR}"
DEFAULT_PYTHON="python3"
if [[ -x "/venv/hislam2/bin/python" ]]; then
  DEFAULT_PYTHON="/venv/hislam2/bin/python"
fi
PYTHON_BIN="${HI_SLAM2_PYTHON:-$DEFAULT_PYTHON}"
TORCH_LIB_DIR="$("$PYTHON_BIN" - <<'PY'
import pathlib
import torch
print(pathlib.Path(torch.__file__).resolve().parent / "lib")
PY
)"
VENV_LIB_DIR="$(cd "$(dirname "$PYTHON_BIN")/.." && pwd)/lib"
export LD_LIBRARY_PATH="${TORCH_LIB_DIR}:${VENV_LIB_DIR}:${LD_LIBRARY_PATH:-}"
DEFAULT_SEQ_DIR="$ROOT/outputs/hislam2_room3x3"
if [[ -d "$ROOT/outputs/hislam2_room3x3_seq_b/images" ]]; then
  DEFAULT_SEQ_DIR="$ROOT/outputs/hislam2_room3x3_seq_b"
fi
SEQ_DIR="${HI_SLAM2_SEQ_DIR:-$DEFAULT_SEQ_DIR}"
OUT_DIR="${HI_SLAM2_OUT_DIR:-$ROOT/outputs/hislam2_room3x3_run}"
CONFIG="${HI_SLAM2_CONFIG:-$ROOT/configs/hislam2_owndata_dense.yaml}"
BUFFER_SIZE="${HI_SLAM2_BUFFER:-1000}"

if [[ ! -d "$DONOR" ]]; then
  echo "[run_hislam2_whitebox] missing donor repo: $DONOR" >&2
  exit 2
fi

if [[ ! -d "$SEQ_DIR/images" || ! -f "$SEQ_DIR/calib.txt" || ! -f "$SEQ_DIR/.complete" ]]; then
  echo "[run_hislam2_whitebox] sequence missing, generating at $SEQ_DIR"
  "$ROOT/scripts/build_room_sequence.sh" --output "$SEQ_DIR"
fi

export DONOR
export SEQ_DIR
export OUT_DIR
export CONFIG

if "$PYTHON_BIN" - <<'PY'
import importlib, json, os, shutil, sys
mods = ["torch", "cv2", "numpy", "lietorch", "yaml", "droid_backends"]
failures = {}
for mod in mods:
    try:
        importlib.import_module(mod)
    except Exception as exc:
        failures[mod] = f"{type(exc).__name__}: {exc}"
probe = {
    "python": sys.executable,
    "donor": os.environ["DONOR"],
    "seq_dir": os.environ["SEQ_DIR"],
    "config": os.environ["CONFIG"],
    "failed_imports": failures,
    "nvcc": shutil.which("nvcc"),
    "nvidia_smi": shutil.which("nvidia-smi"),
    "ld_library_path": os.environ.get("LD_LIBRARY_PATH", ""),
}
if not failures:
    import torch
    probe["torch_version"] = getattr(torch, "__version__", None)
    probe["cuda_available"] = bool(torch.cuda.is_available())
    probe["cuda_device_count"] = int(torch.cuda.device_count()) if torch.cuda.is_available() else 0
print(json.dumps(probe, indent=2))
if failures:
    raise SystemExit(3)
PY
then
  :
else
  exit_code=$?
  echo "[run_hislam2_whitebox] HI-SLAM2 environment is not runnable yet. Install donor deps first." >&2
  exit $exit_code
fi

mkdir -p "$OUT_DIR"
cd "$DONOR"
exec "$PYTHON_BIN" demo.py \
  --imagedir "$SEQ_DIR/images" \
  --calib "$SEQ_DIR/calib.txt" \
  --config "$CONFIG" \
  --buffer "$BUFFER_SIZE" \
  --output "$OUT_DIR"
