# Aether3D 闭环版本剪枝与模块化计划

日期：2026-03-19

## 1. 版本目标

本阶段只做一个稳定闭环：

- 手机本地拍摄
- 手机本地实时质量审核与提示
- 远端 5090 训练
- 训练结果回传手机
- 手机本地最终渲染与交互展示

明确不纳入本版：

- MobileGS
- MiniSplatting
- 地图/地理空间相关功能
- 泛化的证据链、Merkle、PIZ、合规模块
- 大而全的上传平台与研究性分支算法
- 面向发布版的本机完整 3DGS 训练链路

丹麦机器的角色建议定义为：

- 黄金环境
- 训练脚本、配置、评测指标的基准来源
- 所有 5090 训练任务都必须引用同一份版本化配置

## 2. 当前代码真实状态

当前本地工程已经有一条可用主链路，但它不是“远端训练闭环”，而是“本机渐进训练 + 本机导出 + 本机查看”：

- App 入口直接进 `HomePage`，这是当前真实首页。
- `HomePage` 从作品列表进入 `ScanView`，停止拍摄后马上进入 `SplatViewerView`。
- `ScanViewModel` 通过 `PipelineCoordinatorBridge` 驱动一个本机三线程管线：采集、质量/证据、训练。
- 停止拍摄后，当前实现会在本机等待训练若干步，然后导出 `PLY` 到手机本地文档目录。
- `ScanRecordStore` 只维护本地 `scans.json` 和本地 `artifactPath`，并没有远端任务模型。

这意味着：

- 现在已经有“从拍摄到展示”的用户路径。
- 但“远端 5090 训练、回传结果到手机”这段，目前还没有替换进主路径。
- 当前项目最需要的不是再堆算法，而是把主路径收束，并把不在闭环里的模块移出构建边界。

## 3. 当前主路径证据

- App 入口：`Aether3DApp -> HomePage`
- 首页拍摄入口：`HomePage -> ScanView`
- 停止拍摄后立即进入 viewer：`ScanView.handleStop()`
- 当前后台行为：`ScanViewModel.startBackgroundExport(recordId:)`
- 当前导出物：本地 `exports/<uuid>.ply` 和 `world_state.json`
- 当前训练桥接：`PipelineCoordinatorBridge`

这条链路说明，现阶段最值得保留的是“UI 路径”和“本地实时审核能力”，而不是现在编进包里的全部研究模块。

## 4. 目标架构

建议把闭环版本切成 6 个模块，边界要比现在清楚得多。

### 4.1 Capture Runtime

负责：

- AR 拍摄
- 帧采样
- 位姿、内参与基础传感数据采集
- 采集结束后的 bundle 打包

建议保留目录：

- `App/Scan`
- `App/Capture`
- `Core/Pipeline/Capture`
- `aether_cpp/src/capture`
- `aether_cpp/src/tsdf` 中 capture 必需子集

### 4.2 Quality Runtime

负责：

- 本地实时质量审核
- 覆盖率提示
- 运动、模糊、曝光、稳定性等实时反馈
- HUD 提示与触觉反馈

建议保留目录：

- `App/ScanGuidance`
- `Core/Quality` 中运行时审核必需子集
- `aether_cpp/src/quality`
- `aether_cpp/src/thermal`

注意：

- 质量审核模块只服务“采摄成功率”和“闭环体验”。
- 不要把它继续扩展成独立研究平台。

### 4.3 Remote Training Gateway

这是当前最缺的一层，建议新建，不要继续把“本机等待训练并导出 PLY”当正式方案。

负责：

- 上传 capture bundle
- 创建远端任务
- 轮询状态
- 失败重试
- 下载最终 artifact

建议新增接口：

```swift
protocol TrainingBackend {
    func submitCaptureBundle(_ bundleURL: URL) async throws -> RemoteTrainingJob
    func queryJob(_ id: String) async throws -> RemoteTrainingStatus
    func downloadArtifact(for id: String) async throws -> LocalArtifactPackage
}
```

### 4.4 Artifact Sync

负责：

- 手机本地缓存远端结果
- 版本管理
- 断点续传
- 作品卡片状态更新

建议回传结果统一成包，而不是只回一个裸 `PLY`：

- `artifact.spz` 或 `artifact.ply`
- `metrics.json`
- `preview.mp4` 或 `preview.jpg`
- `world_state.json`
- `viewer_manifest.json`

### 4.5 Local Viewer

负责：

- 本地交互展示
- 作品库回看
- 分享

建议保留目录：

- `App/Viewer`
- `App/GaussianSplatting`
- `Core/Render` 或当前 viewer 必需渲染子集
- `aether_cpp/src/splat`
- `aether_cpp/src/render` 中 Metal 设备与 viewer 必需子集

### 4.6 Golden Config Layer

负责：

- 丹麦机器上的黄金配置版本
- 评测指标版本
- 训练脚本版本
- 5090 训练任务使用的统一 recipe

建议所有远端任务都绑定：

- `pipelineVersion`
- `goldenProfileVersion`
- `trainerVersion`
- `artifactFormatVersion`

## 5. 当前应该保留的代码

这些是闭环版的骨架，不应该误删。

- 首页与作品流：
  - `App/Aether3DApp.swift`
  - `App/Home/*`
- 拍摄主流程：
  - `App/Scan/ScanView.swift`
  - `App/Scan/ScanViewModel.swift`
  - `App/Scan/ARCameraPreview.swift`
- 实时审核与提示：
  - `App/ScanGuidance/*`
  - `Core/Quality/*` 中真正被扫描界面使用的运行时部分
- 本地 viewer：
  - `App/Viewer/*`
  - `App/GaussianSplatting/*`
- 当前 C++ 主链路中仍有价值的部分：
  - `aether_cpp/src/pipeline/*`
  - `aether_cpp/src/training/gaussian_training_engine.cpp`
  - `aether_cpp/src/splat/*`
  - `aether_cpp/src/quality/*`
  - `aether_cpp/src/thermal/*`
  - `aether_cpp/src/tsdf/*` 中 capture 与 surface/export 必需子集

## 6. 当前应该移出闭环版本的代码

这些内容不一定要立刻物理删除，但至少应该先从闭环版 target 和 build scheme 里移出去。

### 6.1 App 层

- `App/Demo/*`
- 未参与主入口的 demo/旧页面
- 泛型 whitebox 展示器里与闭环无关的分支能力

### 6.2 Swift Core 层

- `Core/Upload/*` 的大规模上传基础设施
- `Core/Jobs/*` 的大状态机与 saga 编排
- `Core/Compliance/*`
- `Core/DeviceAttestation/*`
- `Core/Evidence/*`
- `Core/MerkleTree/*`
- `Core/PIZ/*`
- `Core/Router/*`
- `Core/Replay/*`
- `Core/TimeAnchoring/*`
- `Core/Network/*` 中非闭环必要部分
- `Core/Security/*` 中与当前闭环无关的重型能力
- `Core/Mobile/*`
- `Core/Geo` 或地理相关目录

### 6.3 SwiftPM / PR4 / 研究模块

`Package.swift` 当前把大量 PR4、SharedSecurity、研究性目标和原生桥一起放进一个大包里。对闭环版来说，这个边界太大了。

建议闭环版先不承载：

- `PR4Math`
- `PR4PathTrace`
- `PR4Ownership`
- `PR4Overflow`
- `PR4LUT`
- `PR4Determinism`
- `PR4Softmax`
- `PR4Health`
- `PR4Uncertainty`
- `PR4Calibration`
- `PR4Package`
- `PR4Golden`
- `PR4Quality`
- `PR4Gate`
- `PR4Fusion`

### 6.4 C++ Bridge 层

`CAetherNativeBridge` 现在编译了太多不属于闭环版本的源文件。建议拆成更小的桥接 target。

优先移出闭环 build 的目录：

- `aether_cpp/src/innovation/*`
- `aether_cpp/src/geo/*`
- `aether_cpp/src/merkle/*`
- `aether_cpp/src/upload/*`
- `aether_cpp/src/evidence/*` 中非实时审核必要部分
- `aether_cpp/src/render/*` 中老式 wedge / fracture / ripple / meshlet 等非 viewer 必需部分

## 7. 最大的架构问题

当前最大问题不是“算法不够”，而是“本机实时闭环”和“远端训练闭环”没有被拆开。

现在 `ScanViewModel` 既负责：

- 采集
- 本地审核
- 本地训练等待
- 本地导出
- 作品记录更新

这个职责过重，导致后面要接 5090 远端训练时，很容易继续在旧代码上缝补。

建议把 `ScanViewModel` 拆成：

- `CaptureSessionController`
- `CaptureQualityController`
- `CaptureBundleExporter`
- `RemoteTrainingCoordinator`
- `ArtifactRepository`

`ScanViewModel` 只做 UI orchestration，不再直接持有完整训练导出逻辑。

## 8. 建议的最小数据契约

### 8.1 手机上传给远端

- `session.json`
- `video.mp4`
- `poses.json`
- `intrinsics.json`
- `quality_summary.json`
- `selected_frames.json`

### 8.2 远端回传给手机

- `artifact.spz` 或 `artifact.ply`
- `metrics.json`
- `preview.mp4`
- `world_state.json`
- `manifest.json`

## 9. 三阶段实施顺序

### 第一阶段：先收口，不先求新

- 固定闭环版唯一入口：`HomePage -> ScanView -> Viewer`
- 停止继续把 demo 和研究页作为潜在入口
- 给闭环版单独建 scheme / target
- 给现有本机训练路径加 feature flag：`LOCAL_TRAINING_FALLBACK`

### 第二阶段：把远端训练接进主链路

- 新建 `RemoteTrainingClient`
- 新建 `CaptureBundleExporter`
- `handleStop()` 后不再以本机 `PLY` 导出为主方案
- 改成：
  - 本地结束拍摄
  - 打包数据
  - 上传并创建远端任务
  - viewer 进入“等待结果/渐进可视化”状态
  - 结果下载完成后切换到最终 artifact

### 第三阶段：做真正的剪枝

- 从 `Package.swift` 中拆闭环最小 target
- 把 `CAetherNativeBridge` 拆成闭环必要桥和研究桥
- 把无关目录移出默认编译
- 对主链路做一次全量回归测试

## 10. 本周最值得先做的 8 件事

1. 画出闭环版唯一用户路径，不再允许多入口并行发展。
2. 给 `ScanRecord` 增加远端任务字段：`jobId`、`status`、`artifactVersion`、`metricsPath`。
3. 新建 `RemoteTrainingClient` 协议和 mock 实现。
4. 新建 `CaptureBundleExporter`，把上传输入从当前本机导出里抽离。
5. 给 `ScanViewModel` 增加 feature flag，区分本机 fallback 和远端正式链路。
6. 建立“丹麦黄金环境配置版本号”并接入任务请求。
7. 新建闭环版 Xcode scheme，只编译必要模块。
8. 把 `App/Demo`、PR4、Geo、Upload 大模块从闭环版构建中排除。

## 11. 结论

教授的方向是对的：现在最重要的是“剪枝”和“模块化”，不是继续加新算法。

对这个工程来说，最核心的动作不是直接删几十个文件，而是先做两件事：

- 用闭环版边界重切 target
- 用远端训练链路替换掉当前本机训练导出主路径

只要这两件事先做对，后面的删减就会非常顺；否则会一直处于“旧研究代码都在，主路径又不断缝补”的状态。
