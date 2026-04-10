# Object Mode V2 实施清单（2026-04-07）

## 目标

- 新开一条 `Object Mode V2` 并行管线
- 旧版 `云端高质量` 完全不动，继续用户测试
- 新版从 UI 入口、远端三阶段、资产 manifest、viewer 全部独立

## 五栏表

| 新增文件/模块名 | 负责人建议 | 先复制谁 | 第一周先跑通什么 | 哪些地方绝对不要碰旧线 |
| --- | --- | --- | --- | --- |
| `App/ObjectModeV2/ObjectModeV2CaptureView.swift` | iOS/SwiftUI | `App/Demo/PipelineDemoView.swift` + `App/Home/HomePage.swift` | 新入口可进、新版页面可选视频、可启动新版 pipeline | 不改 `ScanView.swift` 的旧拍摄逻辑 |
| `App/ObjectModeV2/ObjectModeV2CaptureViewModel.swift` | iOS/状态机 | `App/Demo/PipelineDemoViewModel.swift` | 选视频、跑三阶段状态、落一条 gallery 记录 | 不改旧 `PipelineDemoViewModel` |
| `Core/Pipeline/ObjectModeV2RemoteClient.swift` | 后端协议 / iOS 网络层 | `Core/Pipeline/RemoteB1Client.swift` | 新版多阶段协议先跑本地 stub | 不改旧 `RemoteB1Client.swift` 接口 |
| `Core/Pipeline/LocalObjectModeV2RemoteClient.swift` | iOS / 本地联调 | `Core/Pipeline/LocalAetherRemoteB1Client.swift` | Preview / Default / HQ 三阶段本地假远端跑通 | 不改旧 `LocalAetherRemoteB1Client.swift` |
| `Core/Pipeline/ObjectModeV2PipelineRunner.swift` | iOS / Pipeline | `Core/Pipeline/PipelineRunner.swift` | manifest 包、阶段下载、阶段替换跑通 | 不改旧 `PipelineRunner.swift` 主链 |
| `Core/Pipeline/ObjectModeV2AssetManifest.swift` | 资产协议 | 新增 | 定义 preview/default/hq + camera/interaction preset | 不改旧 manifest 结构 |
| `App/ObjectModeV2/ObjectModeV2ViewerViewController.swift` | iOS / Viewer | `App/Viewer/WhiteboxViewerViewController.swift` | 新版 manifest viewer 可打开 preview/default/hq | 不改旧 `WhiteboxViewerViewController.swift` |
| `App/ObjectModeV2/ObjectModeV2ViewerScreen.swift` | iOS / SwiftUI bridge | 复制旧 UIViewController bridge 风格 | 新版 viewer 可由 SwiftUI 打开 | 不改旧 viewer 打开方式 |
| `App/Home/RecordViewerDestinationView.swift` | iOS / 导航 | 新增 | gallery 能根据记录类型打开旧 viewer 或新 viewer | 不改旧 gallery 数据存储逻辑 |
| `App/Home/ScanRecord.swift` | 数据模型 | 轻扩展 | 允许 record 区分旧线 / 新线 | 不移除任何旧字段 |
| `App/Home/ScanRecordStore.swift` | 持久化 | 轻扩展 | 支持 `upsertRecord`，让新版阶段更新能回写 | 不改现有 `saveRecord` 语义 |
| `App/Home/HomePage.swift` | 入口整合 | 原文件增量修改 | 首页出现“双入口”：旧版 + 对象模式 Beta | 不删旧 “开始拍摄（旧版）” |

## 第一周闭环

1. 首页出现两个入口：
   - `对象模式 Beta`
   - `开始拍摄（旧版）`
2. 进入 `对象模式 Beta` 后可以选择视频。
3. 新版 pipeline 以本地 stub 跑出：
   - `preview`
   - `default`
   - `hq`
4. 生成一个 `manifest.json + stage assets` 的对象包。
5. gallery 出现一条 `对象模式 Beta` 记录。
6. 点开记录后进入新版 viewer，能切换三个阶段。

## 第二周开始接真实远端

1. 用新的 `ObjectModeV2RemoteClient` 接 control plane 新接口。
2. 新 worker 开三阶段脚本：
   - `object_mode_v2_preview_phase`
   - `object_mode_v2_default_phase`
   - `object_mode_v2_hq_phase`
3. app 端开始支持：
   - preview 先回
   - default 覆盖
   - hq 覆盖
4. 旧 worker 和旧 `hislam2` job 名称不改。

## 旧线禁区

- 不改旧 `云端高质量` 的入口文案和行为
- 不改旧 `PipelineRunner`
- 不改旧 `RemoteB1Client`
- 不改旧 `WhiteboxViewerViewController`
- 不改旧 `ScanView` / `ScanViewModel` 的主逻辑
- 不把新版字段设成旧 JSON 解码必填字段

## 当前已落代码

- 首页双入口
- 新版 gallery 打开链
- 新版 `ObjectModeV2` capture 页面
- 新版三阶段 pipeline stub
- 新版 asset manifest
- 新版 constrained viewer

## 下一步建议

- 第一个真实远端版本，不要直接接完整 HQ
- 先接 `preview` 真阶段
- 然后接 `default`
- 最后再接 `HQ`

