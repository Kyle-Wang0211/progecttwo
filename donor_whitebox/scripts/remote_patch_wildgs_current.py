from pathlib import Path
import re

root = Path('/root/gs_refs/WildGS-SLAM')


def replace_all_arch_flags(text: str) -> str:
    text = re.sub(r"\s*'-gencode=arch=compute_\d+,code=(?:sm|compute)_\d+',\n", '', text)
    return text

# root setup.py
root_setup = root / 'setup.py'
text = root_setup.read_text()
text = replace_all_arch_flags(text)
text = text.replace(
    "'nvcc': ['-O3',\n",
    "'nvcc': ['-O3',\n"
    "                    '-gencode=arch=compute_120,code=sm_120',\n"
    "                    '-gencode=arch=compute_120,code=compute_120',\n",
    1,
)
root_setup.write_text(text)

# lietorch setup.py
lt_setup = root / 'thirdparty/lietorch/setup.py'
text = lt_setup.read_text()
text = text.replace(
    "'/venv/hislam2/targets/x86_64-linux/include',\n                '/venv/hislam2/lib/python3.10/site-packages/nvidia/cuda_runtime/include'",
    "'/usr/local/cuda/include'",
)
text = replace_all_arch_flags(text)
text = text.replace(
    "'nvcc': ['-O2',\n",
    "'nvcc': ['-O2',\n"
    "                    '-gencode=arch=compute_120,code=sm_120',\n"
    "                    '-gencode=arch=compute_120,code=compute_120',\n",
    1,
)
text = text.replace(
    "'nvcc': ['-O2',\n",
    "'nvcc': ['-O2',\n"
    "                    '-gencode=arch=compute_120,code=sm_120',\n"
    "                    '-gencode=arch=compute_120,code=compute_120',\n",
    1,
)
lt_setup.write_text(text)

# source patches
for rel in [
    'thirdparty/lietorch/lietorch/extras/corr_index_kernel.cu',
    'thirdparty/lietorch/lietorch/src/lietorch_cpu.cpp',
    'thirdparty/lietorch/lietorch/src/lietorch_gpu.cu',
    'src/lib/correlation_kernels.cu',
    'src/lib/altcorr_kernel.cu',
]:
    fp = root / rel
    s = fp.read_text()
    s = s.replace('.type()', '.scalar_type()')
    fp.write_text(s)

fp = root / 'thirdparty/lietorch/lietorch/extras/extras.cpp'
s = fp.read_text()
s = s.replace(
    '#define CHECK_CUDA(x) TORCH_CHECK(x.type().is_cuda(), #x " must be a CUDA tensor")',
    '#define CHECK_CUDA(x) TORCH_CHECK(x.is_cuda(), #x " must be a CUDA tensor")',
)
fp.write_text(s)

fp = root / 'thirdparty/lietorch/lietorch/include/dispatch.h'
s = fp.read_text()
s = s.replace(
    '    at::ScalarType _st = ::detail::scalar_type(the_type);                            \\\n',
    '    at::ScalarType _st = the_type;                            \\\n',
)
fp.write_text(s)

print('patched WildGS current tree')
