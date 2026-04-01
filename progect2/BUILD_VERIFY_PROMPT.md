# 构建验证提示词

## 任务

用 repo 根目录的 `project.yml` 重新生成 Xcode 项目，然后执行 `xcodebuild build` 验证编译。

## 步骤

### 1. 重新生成 Xcode 项目

```bash
cd /Users/kaidongwang/Documents/progecttwo/progect2
xcodegen generate --spec project.yml
```

这会在 `/Users/kaidongwang/Documents/progecttwo/progect2/Aether3DApp.xcodeproj` 生成项目。

### 2. 执行构建

```bash
xcodebuild build \
  -project "/Users/kaidongwang/Documents/progecttwo/progect2/Aether3DApp.xcodeproj" \
  -scheme Aether3DApp \
  -destination 'generic/platform=iOS' \
  -configuration Debug \
  CODE_SIGNING_ALLOWED=NO 2>&1 | tee /tmp/build_output.log
```

### 3. 检查结果

```bash
# 筛选错误和结果
grep -E "error:|BUILD SUCCEEDED|BUILD FAILED" /tmp/build_output.log
```

### 4. 如果有错误，区分我们的修改 vs 预存问题

**我们修改的文件（这些文件的错误需要修复）：**

| 文件 | 改动内容 |
|------|---------|
| `progect2/App/Scan/ScanViewModel.swift` | 签名修复、管线实例化、动画接入、时钟注入 |
| `progect2/App/ScanGuidance/ScanGuidanceRenderPipeline.swift` | 可选管线、三重缓冲同步、borderCalculator |
| `progect2/App/ScanGuidance/Shaders/ScanGuidance.metal` | fp16 GGX、Kelemen、NaN 防护、热 LOD、STBN、移除未使用的 NdotV |
| `progect2/App/Scan/ARCameraPreview.swift` | 抑制默认网格渲染 |
| `progect2/Core/Evidence/PatchDisplayMap.swift` | 1-Euro 滤波器 |
| `progect2/App/ScanGuidance/GrayscaleMapper.swift` | Oklab 色彩、错误日志 |
| `progect2/Core/Quality/Animation/FlipAnimationController.swift` | 内存泄漏修复、外部时钟注入 |
| `progect2/Core/Evidence/PatchEvidenceMap.swift` | errorStreak 锁定修复 |
| `progect2/Core/Constants/ScanGuidanceConstants.swift` | 扩展验证 |
| `progect2/Core/Quality/Performance/ThermalQualityAdapter.swift` | LOD 转换日志 |
| `progect2/App/Demo/PipelineDemoViewModel.swift` | 移除错误的 `import Aether3DCore` |
| `progect2/App/Viewer/WhiteboxViewerViewController.swift` | 移除错误的 `import Aether3DCore` |

**已知的预存问题（与我们的修改无关，不要碰）：**

- `App/Capture/CameraSession.swift` — `ResolutionTier` not found, `CaptureRecordingConstants` not found, `VideoCodec` ambiguous
- `App/Capture/CaptureMetadata.swift` — `RecordingError` duplicate, `VideoCodec` duplicate, `ResolutionTier` not found
- `App/Capture/CameraSessionDelegate.swift` — public/internal access mismatch
- `App/Capture/CameraConfig.swift` — `VideoCodec` redeclaration
- `App/Capture/RecordingController.swift` — `RecordingState` conformance, access control
- `App/Capture/RecordingResult.swift` — access control
- `App/TSDF/MetalTSDFIntegrator.swift` — missing types (TSDFConstants, VoxelBlockAccessor 等)
- `App/TSDF/MetalBufferPool.swift` — missing MetalConstants

这些 Capture 和 TSDF 模块的错误是因为 Xcode 项目只包含 `App/` 源码，Core/ 类型通过 `Aether3DCore` Swift Package 提供，但 App/ 代码中有些文件忘记 `import Aether3DCore`，有些类型有重复定义。这些问题在我们的修改之前就已存在。

### 5. 修复策略

如果我们修改的文件有编译错误：
- 仔细阅读错误信息
- 对照上面的文件列表确认是我们引入的问题
- 修复错误

如果只有预存问题的文件有错误：
- 构建验证通过（我们的修改没有引入新错误）
- 报告 BUILD 状态即可

## 关键上下文

这是 Aether3D 3D 扫描应用的 Scan Guidance UI 子系统。我们完成了 5 个阶段的修复：
1. 编译错误修复（签名、重复方法、force-unwrap）
2. 架构连接（Metal 管线实例化、动画数据接入、默认网格抑制）
3. GPU 安全（NaN 防护、三重缓冲同步、色调映射精度）
4. 前沿算法升级（fp16-safe GGX、Kelemen 可见性、Oklab、1-Euro 滤波器、热感知 LOD、STBN）
5. 中低优先级修复（内存泄漏、时钟统一、LOD 日志、常量验证）
