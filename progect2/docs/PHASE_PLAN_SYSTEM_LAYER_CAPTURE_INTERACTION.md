### 3.2 AetherDisplayLedger — CRDT增强的单调显示账本

**同行做法：** 简单的dictionary存储display值，重启丢失。
**我们的做法：** 基于CRDT理论的不可回退显示账本 + Merkle审计。

```swift
// 核心数据结构（概念设计，非实现代码）
struct AetherDisplayLedger {
    // G-Set: 只增不删的补丁集合
    var knownPatches: GrowOnlySet<StablePatchId>

    // LWW-Register per patch: 最后写入胜出，但配合monotonic guard
    var displayValues: [StablePatchId: MonotonicLWWRegister<Double>]

    // MonotonicLWWRegister: 标准LWW + 额外约束：新值必须 >= 旧值
    // 这是CRDT理论的自研扩展 — Monotonic-LWW-Register

    // Merkle审计链：每次display更新生成receipt
    // 利用核心层F5 Delta Patch Chain的Merkle tree验证
    var auditChain: MerkleAuditTrail
}
```

**Monotonic-LWW-Register 的理论创新：**
- 标准CRDT的LWW-Register允许任意方向写入
- 我们自研的Monotonic-LWW-Register额外约束：`newValue >= currentValue`
- 这保证了CRDT的最终一致性（任意写入顺序收敛到相同结果）
- 同时保证了显示的单调性（一旦变白不可回退）
- 数学证明：Monotonic-LWW在半格(semilattice)上形成bounded join-semilattice
  - join操作 = max(a, b)
  - 满足ACI性质（结合律/交换律/幂等律）
  - 因此满足CRDT的数学条件

### 3.3 AetherThermalCascade — 七级热力瀑布降级

**同行做法：** 3-4级简单降级（正常/中/低/最低）。
**我们的做法：** 七级精细瀑布，每级有独立的恢复阈值和迟滞窗口。

```
Level 0 — Apex（极致模式）
  │ 条件: thermal=nominal, GPU<10ms, 内存<70%
  │ 配置: 全PBR, 4级LOD, 粒子特效, 全触觉
  │ 最大三角形: 25000, MetalFX=off
  │
Level 1 — Prime（高质模式）
  │ 条件: thermal=nominal, GPU<12ms
  │ 降级: 禁用粒子特效, LOD上限降至3
  │ 最大三角形: 20000, MetalFX=off
  │
Level 2 — Balanced（均衡模式）
  │ 条件: thermal=fair, GPU<14ms
  │ 降级: PBR→简化Phong
  │ 最大三角形: 15000, MetalFX=1.5x upscale
  │
Level 3 — Guard（守护模式）
  │ 条件: thermal=fair, GPU<15ms
  │ 降级: Flip动画简化（overshoot→线性）, Ripple最大hop=4
  │ 最大三角形: 10000, MetalFX=2x upscale
  │
Level 4 — Shield（防护模式）
  │ 条件: thermal=serious
  │ 降级: 禁用Flip/Ripple, 禁用触觉, 目标30fps
  │ 最大三角形: 5000, MetalFX=2x upscale
  │
Level 5 — Fortress（堡垒模式）
  │ 条件: thermal=serious, 持续>30s
  │ 降级: 仅wireframe, 禁用所有shader特效
  │ 最大三角形: 3000, MetalFX=off（分辨率已低）
  │
Level 6 — Citadel（要塞模式）
  │ 条件: thermal=critical
  │ 降级: 暂停渲染overlay, 仅保留数据采集和UI文字提示
  │ 最大三角形: 0（纯AR相机流 + 文字引导）
  │ 保证: 数据采集不中断, display账本持续更新

升级规则（每级独立迟滞窗口）:
  Level N → Level N-1 需要:
    - thermal状态改善 AND
    - GPU P95 < targetMs[N-1] 持续 hysteresisSeconds[N] AND
    - 距上次降级 > cooldownSeconds[N]

  hysteresisSeconds = [-, 5, 8, 10, 15, 20, 30]
  cooldownSeconds   = [-, 3, 5, 8, 10, 15, 25]
```

**为什么比同行强：**
- 同行3-4级 → 我们7级，过渡更平滑，用户感知更小
- 每级独立迟滞窗口防止频繁震荡
- Level 6保证数据不丢失——即使GPU完全饱和，核心数据管线继续运行
- 升级比降级慢（非对称迟滞），避免"刚恢复就又过热"

### 3.4 AetherPerceptualMapper — 感知均匀色彩映射

**同行做法：** 线性RGB灰度映射。
**我们的做法：** CIE Lab感知均匀 + Stevens' Power Law + Weber-Fechner对数阈值。

```
当前S阈值（线性分布）: 0.10, 0.25, 0.50, 0.75, 0.88
改进S阈值（对数分布）: 0.05, 0.15, 0.35, 0.65, 0.88

理论依据:
- Weber-Fechner Law: 人眼对中间灰度辨别力最强
- 低证据区域（0-0.35）变化更快 → 给用户更强的"扫描开始"正反馈
- 高证据区域（0.65-0.88）变化更慢 → 避免"快完成了还在变"的焦虑
- S5阈值保持0.88不变 → 终态确定性不受影响

色彩空间:
- 当前: 线性RGB插值 → 中间段感知不均匀（Mach band效应）
- 改进: CIE Lab L*通道插值 → 感知均匀
- L* = 116 * (Y/Yn)^(1/3) - 16  (Y/Yn > 0.008856)
- 在Metal shader中实现Lab→sRGB转换（约3条额外指令）

边界柔化:
- 在S阈值边界 ±0.02 范围内使用 smoothstep 插值
- 消除Mach band假边缘
```

### 3.5 AetherCoreScheduler — 帧级核心层调度器（v2.0大幅扩展）

**同行做法：** 核心算法在后台线程独立运行，与渲染帧不同步。
**我们的做法：** 每一帧精确调度核心层C API，与渲染管线严格同步。
**v2.0自审修正：** v1.0仅调度10个API（6.5%），v2.0扩展至30+个API（>85%有效接入率）。

**完整帧级调度序列详见第四章 Phase 3F。**

核心理念：
```
v1.0: 系统层是"消费者" — 从核心层取结果渲染
v2.0: 系统层是"指挥官" — 每帧精确编排核心层的完整计算管线

关键增强：
1. 深度预处理闭环：depth_filter_run → integrate → fusion_feedback → 自适应调参
2. 帧级决策中枢：volume_controller_decide 替代手工跳帧逻辑
3. 回环检测触发：loop_detect → ICP精化 → 位姿图优化 → 身份重建
4. 动态物体隔离：F6 rejector → 排除虚假display更新
5. 碎片化渲染：核心层 generate_fracture_fragments → Metal直接渲染
6. 健康仪表盘：core_health → 全局异常自动降级
```

**为什么比同行强：**
- 核心层154个C API中，与拍摄交互相关的函数全部接入渲染循环
- 深度→融合→反馈三级闭环：同行无此反馈机制
- 帧级决策由核心层统一判断：跳帧/驱逐/关键帧标记/预分配，而非Swift层分散逻辑
- 回环检测驱动身份恢复：不是简单的空间匹配，而是block-overlap + 时序加权
- D-S证据理论驱动display更新：而非简单的EMA平滑
- 信息增益驱动的引导箭头（PR1模块）→ 不是静态提示而是真正的"下一步最佳视角"
- mesh稳定性查询驱动渲染决策 → 不稳定block显示特殊过渡效果而非硬切
- 每帧总调度预算：~3.6ms（在14.6ms可用预算内完全可行）

### 3.6 AetherRenderSurface — 双模式渲染表面

**同行做法：** 单一SCNView或单一MTKView。
**我们的做法：** SCNSceneRendererDelegate注入 + MTKView备选，自动选择最优路径。

```
模式A: SCNView Injection（首选）
  ARSCNView → SCNSceneRendererDelegate
  在 renderer(_:willRenderScene:atTime:) 中注入自定义Metal渲染
  优点: 与ARKit的世界追踪/遮挡完美集成
  缺点: 受SceneKit渲染节奏限制

模式B: MTKView Overlay（备选）
  MTKView 叠加在 ARSCNView 之上
  手动同步ARKit pose → Metal camera矩阵
  优点: 完全控制渲染管线
  缺点: 需要手动处理AR坐标系对齐

自动切换逻辑:
  if SCNView injection成功 && frameTiming < 14ms → 使用模式A
  else if MTKView可用 → 切换模式B
  else → fallback到纯SwiftUI overlay（最低保证）
```

### 3.7 AetherPoseStabilizer — 位姿稳定器（v3.0新增）

**同行做法：** 直接使用ARKit/ARCore输出的原始位姿，手抖时overlay飘动。
**我们的做法：** IMU-视觉融合 + 三级位姿精化管线，核心层C++实现，三端共用。

```
需要在核心层新增的C API:

aether_pose_stabilizer_create(config) → handle
aether_pose_stabilizer_update(
    handle,
    raw_pose[16],          ← ARKit/ARCore原始位姿矩阵
    gyro_xyz[3],           ← 陀螺仪角速度 @100Hz
    accel_xyz[3],          ← 加速度计 @100Hz
    timestamp_ns,          ← 纳秒精度时间戳
    &stabilized_pose[16],  → 稳定后的位姿
    &pose_quality          → 位姿质量评分(0-1)
)
aether_pose_stabilizer_predict(
    handle,
    target_timestamp_ns,   ← 渲染时刻（比采集晚~8ms）
    &predicted_pose[16]    → 预测位姿（补偿采集-渲染延迟）
)
```

**三级位姿精化管线（核心层实现）：**
```
Level 1: EKF融合（Extended Kalman Filter）
  状态向量: [position(3), orientation_quat(4), velocity(3), gyro_bias(3), accel_bias(3)]
  观测: ARKit位姿(6DoF) + IMU(gyro+accel @100Hz)
  更新频率: 100Hz（IMU驱动预测）+ 60Hz（视觉修正）
  效果: 消除ARKit位姿的帧间跳动，特别是手抖引起的高频震颤

Level 2: 运动模糊感知帧筛选
  参考: MBA-SLAM (TPAMI 2025) — 物理模型化曝光期间的相机轨迹
  输入: motion_analyzer的手抖检测 + angular_velocity
  规则:
    angular_velocity > 0.3 rad/s → 标记为模糊帧
    连续3帧模糊 → 降低深度融合权重至30%
    连续10帧模糊 → UI提示"请稳定手机"
  效果: 避免运动模糊帧的噪声深度进入TSDF

Level 3: 渲染时刻位姿预测
  问题: ARFrame采集时刻 vs Metal渲染提交时刻有~8-12ms延迟
  解决: aether_pose_stabilizer_predict 用EKF状态外推到渲染时刻
  效果: overlay牢牢贴合物体表面，消除"飘逸感"

三级联动:
  IMU @100Hz → EKF预测 → ARKit @60Hz修正 → 模糊帧标记 → 渲染时刻预测
  └──────────────────────────────────────────────────────────────────┘
                        全部在核心层C++执行，~0.15ms/帧
```

**为什么比同行强：**
- 同行直接使用平台位姿 → 手抖时overlay抖动
- 我们用EKF融合IMU+视觉 → 100Hz位姿更新（ARKit只有60Hz）→ 更平滑
- 渲染时刻预测补偿采集延迟 → overlay与物体零偏移
- 运动模糊帧筛选 → 保护TSDF数据质量 → display不碎片化
- **全部C++实现** → 三端共用，系统层仅传传感器数据

**前沿研究支撑（v3.0更新 — 2025-2026全球前沿调研）：**
- XR-VIO (arXiv 2025.02, P24): **4帧快速初始化** + 陀螺仪紧耦合 → 消除首秒漂移，XR专门设计
  - 自研整合: EKF初始化阶段采用4帧策略替代传统10帧warm-up → 用户秒开即稳
  - 混合特征匹配(光流+描述符) → 速度与鲁棒性兼得
- MBA-SLAM (TPAMI 2025, P25): **从模糊帧提取信息而非丢弃** → 革命性思路
  - 自研整合: Level 2升级 — 模糊帧不再简单降权/丢弃，而是估计曝光期间的相机轨迹
  - 曝光轨迹 → 子帧位姿插值 → 每帧皆对TSDF有贡献 → 扫描效率提升
- GS-LIVO IESKF (arXiv 2025.01, P28): 嵌入式设备实时IESKF → **验证A17 Pro可行性**
  - 自研整合: 可选升级路径 — EKF→IESKF（迭代误差状态Kalman），精度更高but计算量+20%
  - 如EKF精度不满足 → 切换IESKF，仍在0.2ms/帧预算内
- VIGS-SLAM (arXiv 2025.12, P26): 统一Pose+Depth+IMU联合优化 → 消除级联误差
  - 长期演进方向: 统一优化替代当前pipeline各自独立
- VINS-Mono: 紧耦合VIO，相机-IMU标定+非线性优化+回环

### 3.8 AetherPureVisionBridge — 纯视觉深度通道（v3.0新增 → 基于DA3 Fuser修订）

**同行做法：** 仅支持LiDAR设备，或降级为无3D功能。
**我们的做法：** 核心层已有DA3 Depth Fuser（贝叶斯融合） + 系统层ML推理桥接 → 无LiDAR也能完整3D扫描。

**核心优势：DA3 Fuser不是新设计，是已测试通过的Phase 11产物**

```
架构设计（基于已有DA3基础设施）:

系统层职责（极薄 — 各平台各自实现，仅ML推理桥接）:
  iOS:      CoreML推理 DepthAnythingV2Small → 相对深度图 (256×192)
  Android:  TFLite推理 → 相对深度图
  HarmonyOS: MindSpore推理 → 相对深度图
  → 系统层仅负责调用平台ML框架，不做任何深度处理逻辑

核心层职责（C++ — 三端共用 — 利用已有代码）:

  已有（直接复用）:
    fuse_da3_depth(DA3DepthSample& sample, float* confidence)
      → 贝叶斯加权: w_vision = 1/σ²_vision, w_tsdf = 1/σ²_tsdf * (0.5+0.5*reliability)
      → TriTetClass三级权重: kMeasured=1.0, kEstimated=0.75, kUnknown=0.4
      → 已通过Phase 11测试

  需新增（核心层C++实现 — 极小）:
    aether_monocular_depth_to_metric(
        relative_depth[],      ← 模型输出的相对深度
        width, height,
        camera_pose[16],       ← 当前帧位姿
        history_poses[],       ← 历史关键帧位姿
        &metric_depth[],       → 输出度量深度
        &scale_factor          → 尺度因子
    )

    内部算法:
      1. 多帧尺度对齐: 最近5关键帧，RANSAC估计全局尺度
      2. ARKit位姿尺度参考: translation向量模长
      3. 时序Kalman平滑: 相邻帧尺度因子变化<5%
      4. 边缘保持: 深度不连续处降低置信度

  需新增（C API包装 — 2个函数）:
    aether_da3_fuse_depth(sample, &confidence)    // 包装已有函数
    aether_monocular_depth_to_metric(...)          // 新增度量化

完整数据流:
  RGB帧 → 系统层ML推理 → relative_depth
    │
    ▼
  aether_monocular_depth_to_metric → metric_depth + scale_factor  (核心层)
    │
    ▼
  构建DA3DepthSample:
    depth_from_vision = metric_depth
    depth_from_tsdf   = TSDF已有深度（初始=0）
    sigma2_vision     = 基于模型置信度
    sigma2_tsdf       = 基于TSDF权重
    tri_tet_class     = kEstimated（单目=0.75）
    │
    ▼
  aether_da3_fuse_depth → fused_depth + confidence  (核心层 → 已有！)
    │
    ▼
  aether_depth_filter_run(fused_depth, confidence=MEDIUM)  (核心层)
    │
    ▼
  aether_tsdf_volume_integrate(depth_source=MONOCULAR_DA3, scale=0.5)  (核心层)
    → voxel权重增量缩小50% → display增速约为LiDAR的60%
```

**性能预算（纯视觉通道）：**
```
CoreML推理 (Neural Engine): ~33ms → 异步线程，不阻塞主线程
  → 有效输出帧率: ~30fps（交替帧推理）
  → 每帧多~0.2ms CPU用于度量化+DA3融合（核心层）
  → 对主线程零影响
```

**为什么比同行强：**
- 同行：LiDAR-only或完全放弃3D → 我们：任何iPhone都能3D扫描
- 同行：从零设计单目深度管线 → 我们：复用已测试的DA3 Fuser贝叶斯融合
- DA3的TriTetClass权重系统精准区分可靠/不可靠深度 → 噪声像素自动降权
- 核心层C++实现 → 三端共用，系统层各平台仅~100行ML推理桥接代码

**前沿研究支撑（v3.0更新 — 2025-2026全球前沿调研）：**
- PromptDepthAnything++ (arXiv 2024.12/2026.02修订, P20): **多尺度解码器融合稀疏LiDAR**
  - 自研整合: iPhone LiDAR ~250点作为稀疏提示 → 低分辨率LiDAR提示+高分辨率RGB特征 → 密集度量深度
  - 优于单纯度量对齐: 多尺度注入保留空间结构 → 边缘/深度不连续处精度提升
- MetricAnything (arXiv 2026.01, P19): **稀疏度量提示 → 20M参数度量基础模型**
  - 自研整合: 备选方案 — 若PDA++延迟过高可用MA的稀疏提示范式
  - 支撑点: MA证明少量度量锚点即可消除尺度歧义 → 与`monocular_depth_to_metric`方法论一致
- ConfidentSplat (arXiv 2025.09, P21): **动态置信权重 → 多视角几何×单目先验自适应融合**
  - 自研整合: **升级DA3 Fuser** — 当前TriTetClass是固定三级权重(1.0/0.75/0.4)
  - 升级方案: sigma²_vision基于模型不确定性动态估计（非固定值）
  - 纹理区域→多视角深度权重高, 反光/透明区域→单目权重高 → 自动适应场景
- HI-SLAM2 (T-RO 2025, P22): 网格尺度对齐 → 解决长时间扫描的尺度漂移
  - 自研整合: `scale_alignment_grid.cpp` 在128×128覆盖网格上维护局部尺度因子
- DepthAnything-AC (arXiv 2025.07, P23): 恶劣条件鲁棒性
  - 自研整合: 针对室内混合光照(明窗暗角)场景fine-tune → 替换标准CoreML模型
- Depth Anything V2 (2024): DA3 Fuser专为其设计
- Apple Depth Pro (ICLR 2025): 零shot度量深度，可作为度量化的替代方案

---

## 第四章 Phase分阶段设计

### Phase 0 — Foundation：渲染链闭合（最高优先级）

**目标：** 让用户能看到三角形。修复两处断裂。

**改动范围：**
```
修改文件（3个）:
  ScanViewModel.swift       — 实例化renderPipeline (do/try/catch)
  ARCameraPreview.swift     — 注入Metal overlay (SCNSceneRendererDelegate)
  ScanView.swift            — 添加渲染层到ZStack

修改核心逻辑:
  ScanViewModel.init():
    旧: self.renderPipeline = nil
    新: do { self.renderPipeline = try ScanGuidanceRenderPipeline(...) }
        catch { self.renderPipeline = nil; logger.warning("Metal pipeline disabled") }

  ARCameraPreview:
    旧: // Metal mesh overlay will be injected via ARSCNView delegate (future PR)
    新: 实现 SCNSceneRendererDelegate, 在willRenderScene中调用pipeline.render()
```

**验收标准：**
- [ ] 启动扫描后，屏幕上可见三角形覆盖在AR相机画面之上
- [ ] 三角形颜色随PatchDisplayMap的display值变化（黑→灰→白）
- [ ] Flip动画在阈值交叉时触发
- [ ] Ripple波纹在Flip后传播
- [ ] thermal=serious时三角形数量自动减少
- [ ] 全程60fps无掉帧（在nominal热力状态下）

**安全保证：**
- Pipeline创建失败时graceful fallback到nil（当前行为不变）
- 渲染异常时自动断开pipeline（不影响数据采集管线）
- Metal validation layer在DEBUG build始终启用

---

### Phase 0B — StartupSequence：用户体验启动序列（v3.0新增 — 编排已有核心层能力）

**目标：** 用户点击"开始拍摄" → 屏幕变黑 → 白色边框三角形逐渐出现 → 给用户"扫描正在开始"的强烈视觉反馈。

**用户关切(#3)对照：** "用户点击开始摄影，屏幕是不是变黑然后出现白色三角形"

**核心层已有算法（本Phase仅做系统层时序编排）：**
```
✅ fracture_display_mesh.cpp — Voronoi碎片化(1-6片/三角形, splitmix64确定性)
✅ flip_animation.cpp        — 翻转动画(Bezier overshoot cp1y=1.56, stagger 0.03s)
✅ ripple_propagation.cpp    — BFS涟漪传播(damping=0.85, max_hops=8, delay=0.06s)
✅ ScanGuidance.metal        — PBR渲染+白边框+flip旋转+ripple位移（已完整实现）
→ 本Phase不新增任何核心算法，只是把这些已有能力在启动时刻编排起来
```

**当前状态：❌ 启动时序编排缺失**
```
当前行为:
  用户点击"开始" → AR相机流立即开始 → 无任何过渡动画
  核心层算法全部就绪，但没有人在启动时刻触发它们
```

**设计方案：三阶段启动时序编排**
```
Stage 1: 遮罩淡入（0ms → 300ms）
  - 系统层: AR相机流启动 + 黑色遮罩覆盖（Metal全屏quad, alpha=1.0→0.8）
  - 核心层: 无调用（等待ARKit初始化）

Stage 2: 首批三角形出现（300ms → 1000ms）
  - 系统层: 遮罩alpha 0.8→0.3，启动渲染管线
  - 核心层调用编排:
    1. aether_generate_fracture_fragments() → 碎片化mesh（已有）
    2. aether_compute_fragment_visual_params() → 视觉参数（已有）
    3. compute_flip_states() → 翻转出现动画（已有，需暴露C API）
    4. compute_ripple_amplitudes() → 中心→边缘扩展（已有，需暴露C API）
  - 三角形初始状态: display=0（黑色填充+白色边框）
  - wedge厚度最大值0.008m（表面未扫描）

Stage 3: 遮罩消失 + 正常扫描（1000ms → 1500ms）
  - 系统层: 遮罩alpha 0.3→0.0
  - 核心层: 完整帧级调度开始（Phase 3F序列）
```

**改动范围：**
```
修改文件（2个）:
  ScanViewModel.swift    — 新增启动状态机（idle→starting→scanning）
  ScanGuidance.metal     — 新增全屏遮罩pass（黑色quad + alpha uniform）

新增文件（1个）:
  StartupSequenceController.swift — 三阶段时序编排器（仅编排，无算法）

系统层代码量: ~120行（纯时序状态机）
核心层改动: 0行（仅暴露已有C++ flip/ripple为C API — 在Phase 3中完成）
```

**验收标准：**
- [ ] 点击"开始" → 黑色遮罩覆盖AR画面
- [ ] 300ms后首批三角形从中心碎裂出现（Voronoi + flip动画）
- [ ] 三角形由中心向边缘波浪式出现（ripple传播）
- [ ] 1500ms后遮罩完全消失，进入正常扫描
- [ ] 三角形初始状态为黑色填充+白色边框（display=0）
- [ ] 整个启动序列零掉帧（预渲染遮罩quad开销<0.1ms）

**安全保证：**
- ARKit初始化失败 → 遮罩保持显示 + 文字提示
- 启动序列中用户按取消 → 立即中止 + 遮罩淡出
- 启动序列不阻塞ARKit初始化（并行执行）

---

### Phase 1 — Identity：补丁身份稳定化

**目标：** 镜头转走再回来，已涂色三角形保持最终状态。

**改动范围：**
```
新增文件（3个）:
  AetherPatchIdentityFusion.swift    — 四层身份融合器
  AetherDisplayLedger.swift          — CRDT增强显示账本
  CoreBridgeScheduler.swift          — 核心层C API帧级调度

修改文件（2个）:
  ScanViewModel.swift                — 接入新身份系统
  MeshExtractor.swift                — 扩展patchId生成为候选集
```

**四层身份融合器细节：**
```
Layer 1改进:
  旧: Int(round(centroid.x * 100)) → 1cm网格，硬边界
