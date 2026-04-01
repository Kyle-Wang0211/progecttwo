#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
from pathlib import Path
import re

setup_py = Path("/root/gs_refs/HI-SLAM2/setup.py")
text = setup_py.read_text()
original = text

pattern = re.compile(
    r"include_dirs=\[\n"
    r"\s*osp\.join\(ROOT, 'thirdparty/lietorch/lietorch/include'\), \n"
    r"\s*osp\.join\(ROOT, 'thirdparty/eigen'\)(?:,\n.*?)*?\],",
    re.S,
)

new_include = """include_dirs=[
                osp.join(ROOT, 'thirdparty/lietorch/lietorch/include'), 
                osp.join(ROOT, 'thirdparty/eigen'),
                '/usr/include/eigen3',
                '/usr/local/cuda/include'],"""

if not pattern.search(text):
    raise SystemExit("missing lietorch_backends include_dirs block in /root/gs_refs/HI-SLAM2/setup.py")

text = pattern.sub(new_include, text, count=1)

text = re.sub(
    r"\s*'-gencode=arch=compute_\d+,code=(?:sm|compute)_\d+',\n",
    "",
    text,
)
anchor = "                ]\n            }),"
replacement = (
    "                    '-gencode=arch=compute_120,code=sm_120',\n"
    "                    '-gencode=arch=compute_120,code=compute_120',\n"
    "                ]\n            }),"
)
if anchor not in text:
    raise SystemExit("missing nvcc flags block in /root/gs_refs/HI-SLAM2/setup.py")
text = text.replace(anchor, replacement, 2)

if text == original:
    raise SystemExit("no changes applied to /root/gs_refs/HI-SLAM2/setup.py")

setup_py.write_text(text)
print("patched", setup_py)
PY

export CUDA_HOME=/usr/local/cuda
export PATH="$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"
export TORCH_CUDA_ARCH_LIST=12.0
export CC=/usr/bin/gcc
export CXX=/usr/bin/g++
export CUDAHOSTCXX=/usr/bin/g++

cd /root/gs_refs/HI-SLAM2
rm -rf build
/venv/hislam2/bin/python setup.py install

/venv/hislam2/bin/python - <<'PY'
import importlib

mods = [
    "droid_backends",
    "lietorch",
]

for name in mods:
    module = importlib.import_module(name)
    print(f"import_ok {name} -> {module.__file__}")
PY
