#!/usr/bin/env bash
set -euo pipefail

TARGET_FILE="${TARGET_FILE:-/root/gs_refs/GPS-SLAM.clean/CMakeLists.txt}"

python3 - <<'PY'
from pathlib import Path

path = Path("/root/gs_refs/GPS-SLAM.clean/CMakeLists.txt")
text = path.read_text(encoding="utf-8")
old = """    set(TORCH_CUDA_ARCH_LIST ${CMAKE_CUDA_ARCHITECTURES})\n    list(TRANSFORM TORCH_CUDA_ARCH_LIST REPLACE \"([0-9])([0-9])\" \"\\\\1.\\\\2\")\n    string(REPLACE \";\" \" \" TORCH_CUDA_ARCH_LIST \"${TORCH_CUDA_ARCH_LIST}\")\n    message(STATUS \"** Updated TORCH_CUDA_ARCH_LIST to ${TORCH_CUDA_ARCH_LIST} **\")\n"""
new = """    set(TORCH_CUDA_ARCH_LIST \"\")\n    foreach(TORCH_ARCH ${CMAKE_CUDA_ARCHITECTURES})\n        string(REGEX REPLACE \"-.*$\" \"\" TORCH_ARCH_CLEAN \"${TORCH_ARCH}\")\n        if(TORCH_ARCH_CLEAN MATCHES \"^([0-9])([0-9])$\")\n            list(APPEND TORCH_CUDA_ARCH_LIST \"${CMAKE_MATCH_1}.${CMAKE_MATCH_2}\")\n        elseif(TORCH_ARCH_CLEAN MATCHES \"^([0-9][0-9])([0-9])$\")\n            list(APPEND TORCH_CUDA_ARCH_LIST \"${CMAKE_MATCH_1}.${CMAKE_MATCH_2}\")\n        else()\n            list(APPEND TORCH_CUDA_ARCH_LIST \"${TORCH_ARCH_CLEAN}\")\n        endif()\n    endforeach()\n    string(REPLACE \";\" \" \" TORCH_CUDA_ARCH_LIST \"${TORCH_CUDA_ARCH_LIST}\")\n    message(STATUS \"** Updated TORCH_CUDA_ARCH_LIST to ${TORCH_CUDA_ARCH_LIST} **\")\n"""
if old in text:
    text = text.replace(old, new)
elif new not in text:
    raise SystemExit("expected torch arch block not found")

cuda_marker = """endif()\n\n\nset(CMAKE_CXX_STANDARD 17)\n"""
cuda_insert = """endif()\n\nif (GPU_RUNTIME STREQUAL \"CUDA\" AND CUDAToolkit_FOUND AND NOT TARGET CUDA::nvToolsExt)\n    add_library(CUDA::nvToolsExt INTERFACE IMPORTED)\n    target_include_directories(CUDA::nvToolsExt INTERFACE ${CUDAToolkit_INCLUDE_DIRS})\nendif()\n\nset(CMAKE_CXX_STANDARD 17)\n"""
if cuda_insert not in text:
    if cuda_marker not in text:
        raise SystemExit("expected cuda marker not found")
    text = text.replace(cuda_marker, cuda_insert)

protobuf_old = """find_package(Protobuf REQUIRED)\nset(TENSORBOARD_LOGGER_DIR \"${CMAKE_SOURCE_DIR}/ThirdLibs/install\")\nfind_package(tensorboard_logger REQUIRED REQUIRED HINTS ${TENSORBOARD_LOGGER_DIR})\nset(Protobuf_LIBRARIES \"/usr/lib/x86_64-linux-gnu/libprotobuf.so\")\n"""
protobuf_new = """find_package(Protobuf REQUIRED)\nset(Protobuf_INCLUDE_DIRS \"/usr/include\")\nset(Protobuf_LIBRARIES \"/usr/lib/x86_64-linux-gnu/libprotobuf.so\")\nset(Protobuf_PROTOC_EXECUTABLE \"/usr/bin/protoc\")\nset(TENSORBOARD_LOGGER_DIR \"${CMAKE_SOURCE_DIR}/ThirdLibs/install\")\nfind_package(tensorboard_logger REQUIRED REQUIRED HINTS ${TENSORBOARD_LOGGER_DIR})\n"""
if protobuf_old in text:
    text = text.replace(protobuf_old, protobuf_new)
elif protobuf_new not in text:
    raise SystemExit("expected protobuf block not found")

slam_inc_old = """target_include_directories(slam_trainer PRIVATE\n    ${PROJECT_SOURCE_DIR}/include\n"""
slam_inc_new = """target_include_directories(slam_trainer BEFORE PRIVATE\n    ${Protobuf_INCLUDE_DIRS}\n    ${PROJECT_SOURCE_DIR}/include\n"""
if slam_inc_old in text:
    text = text.replace(slam_inc_old, slam_inc_new)
elif slam_inc_new not in text:
    raise SystemExit("expected slam_trainer include block not found")

viewer_inc_old = """target_include_directories(remote_viewer PRIVATE\n    ${PROJECT_SOURCE_DIR}/include\n"""
viewer_inc_new = """target_include_directories(remote_viewer BEFORE PRIVATE\n    ${Protobuf_INCLUDE_DIRS}\n    ${PROJECT_SOURCE_DIR}/include\n"""
if viewer_inc_old in text:
    text = text.replace(viewer_inc_old, viewer_inc_new)
elif viewer_inc_new not in text:
    raise SystemExit("expected remote_viewer include block not found")

path.write_text(text, encoding="utf-8")
print(f"[gpsslam-arch-patch] patched {path}")
PY
