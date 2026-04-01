#!/usr/bin/env bash
set -euo pipefail

for pair in \
  "/root/gs_refs/Photo-SLAM/third_party/simple-knn/simple_knn.cu:/root/gs_refs/Photo-SLAM.clean/third_party/simple-knn/simple_knn.cu" \
  "/root/gs_refs/Photo-SLAM/src/gaussian_model.cpp:/root/gs_refs/Photo-SLAM.clean/src/gaussian_model.cpp" \
  "/root/gs_refs/Photo-SLAM/examples/replica_mono.cpp:/root/gs_refs/Photo-SLAM.clean/examples/replica_mono.cpp" \
  "/root/gs_refs/Photo-SLAM/examples/tum_mono.cpp:/root/gs_refs/Photo-SLAM.clean/examples/tum_mono.cpp" \
  "/root/gs_refs/Photo-SLAM/examples/tum_rgbd.cpp:/root/gs_refs/Photo-SLAM.clean/examples/tum_rgbd.cpp" \
  "/root/gs_refs/Photo-SLAM/examples/replica_rgbd.cpp:/root/gs_refs/Photo-SLAM.clean/examples/replica_rgbd.cpp" \
  "/root/gs_refs/Photo-SLAM/examples/euroc_stereo.cpp:/root/gs_refs/Photo-SLAM.clean/examples/euroc_stereo.cpp" \
  "/root/gs_refs/Photo-SLAM/examples/realsense_rgbd.cpp:/root/gs_refs/Photo-SLAM.clean/examples/realsense_rgbd.cpp" \
  "/root/gs_refs/Photo-SLAM/cuda_rasterizer/rasterizer_impl.h:/root/gs_refs/Photo-SLAM.clean/cuda_rasterizer/rasterizer_impl.h" \
  "/root/gs_refs/HI-SLAM2/demo.py:/root/gs_refs/HI-SLAM2.clean/demo.py" \
  "/root/gs_refs/HI-SLAM2/setup.py:/root/gs_refs/HI-SLAM2.clean/setup.py" \
  "/root/gs_refs/HI-SLAM2/hislam2/hi2.py:/root/gs_refs/HI-SLAM2.clean/hislam2/hi2.py" \
  "/root/gs_refs/HI-SLAM2/hislam2/midas/base_model.py:/root/gs_refs/HI-SLAM2.clean/hislam2/midas/base_model.py" \
  "/root/gs_refs/HI-SLAM2/hislam2/midas/omnidata.py:/root/gs_refs/HI-SLAM2.clean/hislam2/midas/omnidata.py" \
  "/root/gs_refs/HI-SLAM2/thirdparty/diff-gaussian-rasterization/cuda_rasterizer/rasterizer_impl.h:/root/gs_refs/HI-SLAM2.clean/thirdparty/diff-gaussian-rasterization/cuda_rasterizer/rasterizer_impl.h" \
  "/root/gs_refs/HI-SLAM2/thirdparty/lietorch/setup.py:/root/gs_refs/HI-SLAM2.clean/thirdparty/lietorch/setup.py" \
  "/root/gs_refs/HI-SLAM2/thirdparty/lietorch/lietorch/include/dispatch.h:/root/gs_refs/HI-SLAM2.clean/thirdparty/lietorch/lietorch/include/dispatch.h" \
  "/root/gs_refs/HI-SLAM2/thirdparty/lietorch/lietorch/extras/corr_index_kernel.cu:/root/gs_refs/HI-SLAM2.clean/thirdparty/lietorch/lietorch/extras/corr_index_kernel.cu" \
  "/root/gs_refs/HI-SLAM2/thirdparty/lietorch/lietorch/extras/extras.cpp:/root/gs_refs/HI-SLAM2.clean/thirdparty/lietorch/lietorch/extras/extras.cpp" \
  "/root/gs_refs/HI-SLAM2/thirdparty/lietorch/lietorch/src/lietorch_gpu.cu:/root/gs_refs/HI-SLAM2.clean/thirdparty/lietorch/lietorch/src/lietorch_gpu.cu" \
  "/root/gs_refs/HI-SLAM2/thirdparty/lietorch/lietorch/src/lietorch_cpu.cpp:/root/gs_refs/HI-SLAM2.clean/thirdparty/lietorch/lietorch/src/lietorch_cpu.cpp"
do
  src="${pair%%:*}"
  dst="${pair##*:}"
  if [[ -f "${src}" ]]; then
    cp "${src}" "${dst}"
    echo "copied ${src} -> ${dst}"
  fi
done

python3 - <<'PY'
from pathlib import Path

PATCHES = {
    "/root/gs_refs/HI-SLAM2.clean/setup.py": [
        (
            "-gencode=arch=compute_86,code=sm_86',",
            "-gencode=arch=compute_86,code=sm_86',\n                    '-gencode=arch=compute_120,code=sm_120',\n                    '-gencode=arch=compute_120,code=compute_120',",
        ),
        (
            "-gencode=arch=compute_75,code=compute_75',",
            "-gencode=arch=compute_75,code=compute_75',\n                    '-gencode=arch=compute_120,code=sm_120',\n                    '-gencode=arch=compute_120,code=compute_120',",
        ),
    ],
    "/root/gs_refs/HI-SLAM2.clean/thirdparty/lietorch/setup.py": [
        (
            """'nvcc': ['-O2',
                    '-gencode=arch=compute_60,code=sm_60', 
                    '-gencode=arch=compute_61,code=sm_61', 
                    '-gencode=arch=compute_70,code=sm_70', 
                    '-gencode=arch=compute_75,code=sm_75',
                    '-gencode=arch=compute_75,code=compute_75',
                    
                ]""",
            """'nvcc': ['-O2',
                    '-gencode=arch=compute_120,code=sm_120',
                    '-gencode=arch=compute_120,code=compute_120',
                ]""",
        ),
    ],
    "/root/gs_refs/HI-SLAM2.clean/thirdparty/diff-gaussian-rasterization/CMakeLists.txt": [
        (
            'set_target_properties(CudaRasterizer PROPERTIES CUDA_ARCHITECTURES "75;86")',
            'set_target_properties(CudaRasterizer PROPERTIES CUDA_ARCHITECTURES "120")',
        ),
    ],
    "/root/gs_refs/Photo-SLAM.clean/CMakeLists.txt": [
        (
            'set_target_properties(cuda_rasterizer PROPERTIES CUDA_ARCHITECTURES "75;86")',
            'set_target_properties(cuda_rasterizer PROPERTIES CUDA_ARCHITECTURES "120")',
        ),
    ],
    "/root/gs_refs/Photo-SLAM.clean/examples/replica_mono.cpp": [
        (
            "c10Alloc::Stat reserved_bytes",
            "c10::CachingAllocator::Stat reserved_bytes",
        ),
        (
            "c10Alloc::Stat alloc_bytes",
            "c10::CachingAllocator::Stat alloc_bytes",
        ),
    ],
    "/root/gs_refs/Photo-SLAM.clean/examples/tum_mono.cpp": [
        (
            "c10Alloc::Stat reserved_bytes",
            "c10::CachingAllocator::Stat reserved_bytes",
        ),
        (
            "c10Alloc::Stat alloc_bytes",
            "c10::CachingAllocator::Stat alloc_bytes",
        ),
    ],
    "/root/gs_refs/Photo-SLAM.clean/examples/tum_rgbd.cpp": [
        (
            "c10Alloc::Stat reserved_bytes",
            "c10::CachingAllocator::Stat reserved_bytes",
        ),
        (
            "c10Alloc::Stat alloc_bytes",
            "c10::CachingAllocator::Stat alloc_bytes",
        ),
    ],
    "/root/gs_refs/Photo-SLAM.clean/examples/replica_rgbd.cpp": [
        (
            "c10Alloc::Stat reserved_bytes",
            "c10::CachingAllocator::Stat reserved_bytes",
        ),
        (
            "c10Alloc::Stat alloc_bytes",
            "c10::CachingAllocator::Stat alloc_bytes",
        ),
    ],
    "/root/gs_refs/Photo-SLAM.clean/examples/euroc_stereo.cpp": [
        (
            "c10Alloc::Stat reserved_bytes",
            "c10::CachingAllocator::Stat reserved_bytes",
        ),
        (
            "c10Alloc::Stat alloc_bytes",
            "c10::CachingAllocator::Stat alloc_bytes",
        ),
    ],
    "/root/gs_refs/Photo-SLAM.clean/examples/realsense_rgbd.cpp": [
        (
            "c10Alloc::Stat reserved_bytes",
            "c10::CachingAllocator::Stat reserved_bytes",
        ),
        (
            "c10Alloc::Stat alloc_bytes",
            "c10::CachingAllocator::Stat alloc_bytes",
        ),
    ],
}

for file_path, replacements in PATCHES.items():
    path = Path(file_path)
    text = path.read_text()
    original = text
    for old, new in replacements:
        text = text.replace(old, new)
    if text != original:
        path.write_text(text)
        print(f"patched {file_path}")
    else:
        print(f"unchanged {file_path}")
PY
