#!/usr/bin/env bash
set -euo pipefail

HI_ENV_PREFIX="${HI_ENV_PREFIX:-/venv/hislam2}"
SCATTER_INIT="${HI_ENV_PREFIX}/lib/python3.11/site-packages/torch_scatter/__init__.py"

if [[ ! -f "${SCATTER_INIT}" ]]; then
  echo "[patch-torch-scatter] missing ${SCATTER_INIT}" >&2
  exit 1
fi

"${HI_ENV_PREFIX}/bin/python" - "${SCATTER_INIT}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

needle = """    if t_major != major:\n        raise RuntimeError(\n            f'Detected that PyTorch and torch_scatter were compiled with '\n            f'different CUDA versions. PyTorch has CUDA version '\n            f'{t_major}.{t_minor} and torch_scatter has CUDA version '\n            f'{major}.{minor}. Please reinstall the torch_scatter that '\n            f'matches your PyTorch install.')\n"""

replacement = """    if t_major != major:\n        import warnings\n        warnings.warn(\n            'torch_scatter CUDA version mismatch: '\n            f'torch={t_major}.{t_minor}, torch_scatter={major}.{minor}; '\n            'continuing because runtime extensions were rebuilt for this host.',\n            RuntimeWarning,\n        )\n"""

if needle not in text:
    if "torch_scatter CUDA version mismatch" in text:
        print(f"[patch-torch-scatter] already patched {path}")
        raise SystemExit(0)
    raise SystemExit(f"[patch-torch-scatter] expected block not found in {path}")

path.write_text(text.replace(needle, replacement, 1))
print(f"[patch-torch-scatter] patched {path}")
PY
