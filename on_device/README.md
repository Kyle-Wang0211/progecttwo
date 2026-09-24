# on_device —— 手机端稠密点云管线(生产 168)与 LOD 查看器,源码快照

2026-09-24 按用户要求,在管线仓也存一份。**这是副本,不是开发主线**:改代码请到来源仓的来源分支。
逐文件的来源(仓库、提交、路径)和 sha256 见 `MANIFEST.tsv`。

## dense_168/ —— 生产 build 168 的稠密阶段(全部在手机上跑,不走云端)
- `engine/aether_cpp/src/dense/`:稠密计算本体(C++)。CasDiffMVS 推理(ONNX Runtime)、预处理、深度融合、逐帧交付。
  编成 iOS 的 `PWDense.xcframework`,由 Dart 经 C 接口 `pwdense_c.h` 调用;源码里没有苹果专属分支,其他平台编成 `.so` 即可。
  来源:Aether3D `snapshot/dense-168-native-src` @ 17b6889(这批改动此前只是共享工作树里的未提交改动)。
- `engine/aether_cpp/third_party/libyuv/`:上游 chromium libyuv @2dd42573,BSD-3-Clause + PATENTS,出处见其 `PW_VENDORED.md`。
- `app/lib/dense/`、`app/test/dense/`:Dart 层(调度、FFI 绑定、进度、边算边显示)。
- `app/vendor/pw_dense/`:C 接口头、导出符号表、`build_xcframeworks.sh` 构建脚本、podspec。
- `app/openspec/changes/`:`dense-lossless-speedup-v1`、`same-page-production-v1` 设计与任务记录。
  来源:pocketworld `snapshot/prod-168` @ 86a45cf(168 出货源码快照)。

## lod/ —— 点云 LOD 查看器(Potree 2.0 八叉树 + 手机端建树,正交照 Cesium,外观照产品查看器)
- `engine/aether_cpp/.../pointcloud_lod*`:选择/流式/控制器、手机端建树(PotreeConverter 2.0 移植,BSD-2 版权声明保留在各文件头)、
  渲染 pass 与冻结 C 接口 `pwlod_viewer.h`(ABI v3),以及全部测试。上游许可原文在 `src/pointcloud_lod/upstream_licenses/`,
  偏离清单在各模块 `DEVIATIONS.md`,总声明在 `NOTICE`。来源:Aether3D `feat/pointcloud-lod-viewer` @ 72ee817。
- `app/`:产品外壳(Dart 绑定、查看器接线、iOS 外壳 `PwLod*`、出包与核对脚本)相对 168 新增或修改的 38 个文件,
  加 `modified_vs_168.patch`(10 个被修改文件的补丁)。来源:pocketworld `feat/lod-on-dense-168` @ ed8282b(生产候选 171 的源码)。

## 没有放进来的
- 二进制和模型(PWDense 框架二进制、PWOnnxRuntime、三个 CasDiffMVS ONNX 模型、`libpw_lod_72ee817f.a`):
  不重复存放,位置和 sha256 在 `MANIFEST.tsv` 里标为 `(not copied)`,从来源分支取。
- 三个文件里的个人信息改成了占位符(作者邮箱、签名身份邮箱、本机家目录路径),`MANIFEST.tsv` 里标为 REDACTED。
