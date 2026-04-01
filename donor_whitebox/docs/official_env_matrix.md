## Official Environment Matrix

This file records the environment requirements declared by each official donor
repo and the current status on the old 5090 host.

Date: 2026-03-08
Host: `31d08ff629dc`

### Host Baseline

- OS: Ubuntu 24.04.3 LTS
- GPU: NVIDIA GeForce RTX 5090
- Driver: 575.64.03
- CUDA toolkits available:
  - `/root/deps/cuda-11.8`
  - `/usr/local/cuda` / `/usr/local/cuda-12.9`
- Conda: `/opt/miniforge3`
- Disk on `/`: check before long installs

### HI-SLAM2 Official

Source:
- `README.md`
- `environment.yaml`

Official requirements:
- clone with submodules
- conda env from `environment.yaml`
- PyTorch `2.1.2`
- `pytorch-cuda=11.8`
- `cudatoolkit=11.8`
- compile kernels with `python setup.py install`
- repo warns to use CUDA 11, not 12

Current old-host status:
- conda env exists at `/opt/miniforge3/envs/hislam2_official`
- `python -V` => `3.10.20`
- `torch.__version__` => `2.1.2`, `torch.version.cuda` => `11.8`
- official `git submodule update --init --recursive` was required because
  `thirdparty/eigen` was empty before the submodule sync
- official `setup.py` hardcodes CUDA arch flags for:
  - `droid_backends`: `sm_60/61/70/75/80/86`
  - `lietorch_backends`: `sm_60/61/70/75/80/86`
- official `setup.py install` completed and these imports now pass:
  - `droid_backends`
  - `lietorch`
  - `simple_knn`
  - `diff_gaussian_rasterization`
- minimal CUDA probe in the env succeeds on 5090 for `cuda alloc + matmul`
- runtime result on the old 5090 is still blocked:
  - official `demo.py` starts and reaches `Processing keyframe 1`
  - it then dies in `CorrSampler.apply(...)` with
    `RuntimeError: CUDA error: no kernel image is available for execution on the device`
  - this matches the upstream arch list: the extensions import, but the first
    real custom CUDA kernel still has no image for the 5090 path

### MonoGS Official

Source:
- `README.md`
- `environment.yml`

Official requirements:
- tested on Ubuntu 20.04 / 18.04
- Python `3.7.13`
- PyTorch `1.12.1`
- `torchvision=0.13.1`
- `torchaudio=0.12.1`
- `cudatoolkit=11.6` or `11.3`
- pip packages from `environment.yml`
- submodules compiled from `submodules/simple-knn` and
  `submodules/diff-gaussian-rasterization`

Current old-host status:
- conda env exists at `/opt/miniforge3/envs/monogs_official`
- `python -V` => `3.7.13`
- `torch.__version__` => `1.12.1`
- official torch warns that RTX 5090 `sm_120` is not supported
- official pip payload is now installed:
  - `simple_knn`
  - `diff_gaussian_rasterization`
  - `opencv-python==4.8.1.78`
  - `open3d`, `wandb`, `lpips`, `torchmetrics`, `imgviz`, `PyOpenGL`,
    `glfw`, `PyGLM`, `rich`, `ruff`, `evo`, `munch`, `trimesh`
- import probe passes for `torch`, `simple_knn`, `diff_gaussian_rasterization`,
  `cv2`, `open3d`, `wandb`
- runtime boundary remains hard: official torch 1.12.1 still emits the
  unsupported-`sm_120` warning on 5090

### WildGS-SLAM Official

Source:
- `README.md`
- `requirements.txt`

Official requirements:
- Python `3.10`
- `numpy==1.26.3`
- CUDA toolkit `11.8`
- `torch==2.1.0`
- `torchvision==0.16.0`
- `torchaudio==2.1.0`
- `torch-scatter` for `torch-2.1.0+cu118`
- `xformers==0.0.22.post7+cu118`
- `setuptools==78.1.1`
- editable installs:
  - `thirdparty/lietorch`
  - `thirdparty/diff-gaussian-rasterization-w-pose`
  - `thirdparty/simple-knn`
- `pip install -e .`
- `pip install -r requirements.txt`
- `mmcv-full` for `cu118/torch2.1.0`

Current old-host status:
- conda env exists at `/opt/miniforge3/envs/wildgs_official`
- CUDA 11.8 toolkit installed inside env lane
- official torch / torchvision / torchaudio / torch-scatter / xformers installed
- editable install needed `--no-build-isolation` on modern pip because build
  isolation hid `torch`; versions unchanged
- upstream repo state is internally inconsistent with the README:
  - `thirdparty/lietorch/setup.py` hardcodes
    `-gencode=arch=compute_120,code=sm_120`
    `-gencode=arch=compute_120,code=compute_120`
  - README still says to use CUDA 11.8
- actual result on the old host:
  - with CUDA 11.8 nvcc: `nvcc fatal : Unsupported gpu architecture 'compute_120'`
  - with system CUDA 12.9 nvcc: the same `compute_120` failure still occurs
- conclusion so far: official WildGS install is blocked by the upstream
  hardcoded `compute_120` target on this host/toolchain set, without any local
  donor source edits

### Photo-SLAM Official

Source:
- `README.md`
- `build.sh`

Official requirements:
- tested on Ubuntu 20.04 / 22.04 / Jetpack 5.1.2
- gcc:
  - `10.5.0` on Ubuntu 20.04
  - `11.4.0` on Ubuntu 22.04
- cmake:
  - `3.27.5` on Ubuntu 20.04
  - `3.22.1` on Ubuntu 22.04
- CUDA `11.8`
- cuDNN:
  - `8.9.3`
  - `8.7.0`
- OpenCV with `opencv_contrib` and CUDA:
  - `4.7.0` or `4.8.0`
- LibTorch `cxx11-abi-shared-with-deps-2.0.1+cu118`

Current old-host status:
- `gcc-10/g++-10` installed
- `gcc-11/g++-11` already present
- local `cmake-3.22.1` downloaded to `/root/deps/cmake-3.22.1-linux-x86_64`
- official LibTorch already present at `/root/deps/libtorch`
- official cuDNN 8.9.3 for CUDA 11 extracted at
  `/root/deps/cudnn-linux-x86_64-8.9.3.28_cuda11-archive`
- a merged CUDA 11.8 + cuDNN 8 prefix is prepared at
  `/root/deps/cuda-11.8-cudnn8`
- exact OpenCV 4.8.0 + opencv_contrib + CUDA 11.8 + cuDNN 8.9.3 configure is
  complete in `/root/build/opencv-4.8.0-cuda118-ninja-r6`
- CMake confirmed:
  - `CMake 3.22.1`
  - `g++ 10.5.0`
  - `CUDA 11.8`
  - `cuDNN 8.9.3`
  - `OpenCV 4.8.0`
  - `opencv_contrib`
  - GPU arch `89`
- earlier exact build attempts failed because generated link lines emitted a
  bare `zlib` token
- current exact build lane was reconfigured with:
  - `BUILD_ZLIB=OFF`
  - `ZLIB_LIBRARY=/usr/lib/x86_64-linux-gnu/libz.so`
  - `ZLIB_INCLUDE_DIR=/usr/include`
- generated link deps now carry the concrete `libz.so` path instead of bare
  `zlib`
- exact OpenCV build/install is now running toward
  `/opt/opencv-4.8.0-cuda118-cudnn8`
- clean upstream `Photo-SLAM.clean/CMakeLists.txt` still hardcodes
  `set_target_properties(cuda_rasterizer PROPERTIES CUDA_ARCHITECTURES "75;86")`
- a wait pipeline is in place to:
  - wait for `OpenCVConfig.cmake`
  - rebuild `Photo-SLAM.clean` in official `build.sh` order
  - run `replica_mono` on the existing `room3x3_dense600` sequence with the
    known-good config pair:
    - `/root/donor_whitebox/configs/photoslam_room3x3_orb_dense600.yaml`
    - `/root/donor_whitebox/configs/photoslam_room3x3_mapper.yaml`
- current exact-runtime result on the old 5090:
  - clean top-level build in `build_official_exact` succeeds
  - `replica_mono` starts and prints `CUDA available! Training on GPU.`
  - it then fails inside OpenCV CUDA warping:
    `opencv_contrib-4.8.0/modules/cudawarping/src/cuda/resize.cu:175`
    `(-217:Gpu API call) no kernel image is available for execution on the device`
  - so even with the strict official `CUDA 11.8 + cuDNN 8.9.3 + OpenCV 4.8`
    lane closed, the exact stack still does not provide a runnable 5090 binary

### Main Mismatch Points

- Host OS is Ubuntu 24.04, newer than all official tested setups.
- Host GPU is RTX 5090 (`sm_120`), newer than the official torch wheels used by
  MonoGS and older cu118-based stacks.
- Official MonoGS torch (`1.12.1`) is known to warn about unsupported `sm_120`.
- Photo-SLAM officially expects cuDNN 8, while the host system libraries are
  cuDNN 9 by default.
- HI-SLAM2 official `setup.py` ignores `TORCH_CUDA_ARCH_LIST` because the repo
  itself hardcodes `sm_60` through `sm_86`.
- HI-SLAM2 clean upstream therefore fails at the first real custom CUDA kernel
  on the 5090, even though import-level validation passes.
- Photo-SLAM clean upstream still hardcodes `CUDA_ARCHITECTURES "75;86"` in its
  top-level CMake, so exact-environment closure does not by itself guarantee a
  runnable 5090 binary.
- The exact OpenCV 4.8.0 CUDA 11.8 lane is also not sufficient for 5090
  runtime: the first `opencv_contrib/cudawarping` CUDA resize hits
  `no kernel image is available for execution on the device`.
- WildGS official `thirdparty/lietorch/setup.py` hardcodes `compute_120/sm_120`,
  which currently fails with both CUDA 11.8 nvcc and the host CUDA 12.9 nvcc.
