# Aether3D — Online 3DGS Optimizer 全流程本地化 Phase 计划

> 版本: 2.6 | 日期: 2026-02-19
> 基于 2024-2026 年 60+ 篇前沿论文/开源代码的多语言（中英法西阿）交叉调研
> v2.0: 移除两段式冷启动，改为直接极致工程方案 + 多重前沿方案交叉验证
> v2.1: iOS 首发策略 + 自适应测试取代固定场景
> v2.2: Scaffold 统一内核 + 证据链操作系统（V1/V1.5/V2 分层）+ 止损线 + 范围控制
> v2.3: 量化决策规则 + 运行时自动回退 + 签名/密钥轮换 + 统计口径升级 + crash-free 指标 + 下游指标扩展
> v2.4: 全球前沿调研附录（20条狂野创新 + 冷静分析 + 竞争格局更新 + 护城河验证）
> v2.5: W1-W20 + U1-U8 合并去重 → 最终 22 条 F1-F19 决策表 + 9 项护城河验证
> v2.6: 8 轮用户反馈后最终定稿 — 4 个颠覆性模块 (F1/F3/F5/F6) 详细方案 + 产品流程修正 + X+Y 策略全覆盖 + 11 项护城河
> v2.6.1: F1 时间之镜重写 — 从"渲染顺序改变"升级为"渲染方式颠覆"（scaffold 碎片刚体飞行 + Gaussian 就地变换 + 免费无缝拼合）

---

## 目录

- [架构确认](#架构确认)
- [Phase 0 — 预研门禁（Go/No-Go）](#phase-0--预研门禁gono-go)
- [Phase 1 — C++ 核心算法骨架](#phase-1--c-核心算法骨架)
- [Phase 2 — 纯视觉采集链 + Scaffold 即时构建](#phase-2--纯视觉采集链--scaffold-即时构建)
- [Phase 3 — 双层表示：Scaffold 绑定与迁移](#phase-3--双层表示scaffold-绑定与迁移)
- [Phase 4 — GPU 后端（Metal / Vulkan）](#phase-4--gpu-后端metal--vulkan)
- [Phase 5 — LOD / 压缩 / 二状态调度器](#phase-5--lod--压缩--二状态调度器)
- [Phase 5.5 — 统一测量协议](#phase-55--统一测量协议)
- [Phase 6 — iOS 首发集成 + S5 验收（Android/Harmony 留接口）](#phase-6--ios-首发集成--s5-验收androidharmony-留接口)
- [前沿参考文献索引](#前沿参考文献索引)
- [创新模块：Scaffold 统一内核 + 证据链操作系统](#创新模块scaffold-统一内核--证据链操作系统)
- [附录 B — 最终版保留创新模块详细方案（v2.6 定稿）](#附录-b--最终版保留创新模块详细方案v26-定稿)

---

## 架构确认

```
双层表示（不变）
├── 结构层: Tri/Tet scaffold（几何一致性、可编辑、物理/UI 特效支撑）
└── 外观层: 3DGS（真实观测驱动，不补假纹理）

执行分层
├── C++ 核心算法层（跨平台，纯计算，aether_cpp）
├── GPU dispatch 层（Metal compute / Vulkan compute）
└── 平台壳（iOS / Android / Harmony，只做 UI + 相机接入）

直接极致方案（不分冷启动阶段）
├── 第一帧起同时运行 Scaffold 生成 + Gaussian 优化
├── 多重前沿方案并行竞争，交叉验证选优
└── 自研创新填补方案间的空白

二状态 GPU 调度器（拍摄→完成，无暂停查看）
├── 拍摄中:   跟踪 > 低频增量优化 > 低保真预览
└── 拍摄结束: 全力优化 > 破镜重圆进度渲染 > 跟踪关闭

产品流程（用户视角）
├── 用户拍摄 → 手机自动边采边构建（用户只管扫）
├── 拍摄完成 → 系统进入全力优化（几秒到几十秒）
├── 渲染呈现 → "破镜重圆"效果：世界按拍摄顺序一片一片拼起来
└── 黑洞 = 没拍到的区域 → 天然告诉用户还差哪里

S5 分档
├── S5-RT:       白名单高端机，本地实时高质交互
├── S5-Deferred: 次级机，采集+预览+本地延时高质生成
└── S4:          普适覆盖，采集+低保真预览+导出
```

### 多重方案交叉验证策略

贯穿全流程的核心原则：对每个关键技术点，同时实现 2-3 种前沿方案，用统一 benchmark 交叉验证，最终集成最优方案或自研混合方案。

```
方案竞争模式:
├── 每个技术点 → 2-3 个候选实现（均为 C++ 模块，统一接口）
├── 统一评测: 同一测试场景、同一指标集、同一硬件
├── 交叉验证: 方案 A 的输出做方案 B 的输入，检查兼容性
└── 集成决策: 量化打分 → 冠军或自研混合 → 写入技术选型文档
```

**冠军选择量化打分公式:**
```
Score = 0.35 * quality_norm       // PSNR/SSIM 归一化到 [0,1]
      + 0.30 * latency_norm       // 1 - (p95_ms / budget_ms), 越快越高
      + 0.15 * thermal_norm       // 1 - (kHot占比 / 总时间), 越冷越高
      + 0.10 * memory_norm        // 1 - (peak_MB / budget_MB), 越省越高
      + 0.10 * stability_norm     // 1 - (p99/p50 - 1), 抖动越小越高

决策规则:
├── 领先 ≥5%: 直接选冠军，淘汰其余
├── 领先 <5%: 双跑延长 2 周，补充数据后重评
└── 双跑 2 周后仍 <5%: 选延迟更低者（打平时偏好低延迟）
```

---

## Phase 0 — 预研门禁（Go/No-Go）

**时长:** 4 周
**目标:** 验证三大核心技术假设（0-A/0-B/0-D），全部通过才进入全面开发。0-C（Harmony）延后到有设备时验证

### 实验 0-A: Scaffold 即时生成（双口径）

| 项目 | 规格 |
|------|------|
| 输入 | 10 帧 RGB + 位姿（SfM 稀疏点云 200-500 点） |
| 目标 | ≤50ms 生成局部 Tri/Tet scaffold，覆盖率 >80% |
| 测试场景 | 固定：3m×3m 桌面场景，纹理丰富度中等，手持匀速环绕 |
| 实现位置 | `aether_cpp/src/scaffold/` 纯 CPU C++，CMake host 构建 |

**双口径测试（P0 修复）:**
- **口径 A（理想）:** GT 位姿 + 完整稀疏点 → 验证算法上限
- **口径 B（在线噪声注入）:** 位姿加高斯噪声（平移 σ=2cm, 旋转 σ=1°）+ 稀疏点随机丢失 20% + 深度抖动 σ=3cm → 验证实际工况下限

**多方案并行:**
- **方案 α:** 增量 Delaunay 三角剖分（2D 投影 + 深度提升到 3D）
- **方案 β:** PLANING 风格松耦合三角生成（arXiv:2601.22046）
- **方案 γ:** EA-3DGS 自适应网格初始化（arXiv:2505.10787）+ TeT-Splatting 四面体（arXiv:2406.01579）混合

**验收标准（两个口径均须通过）:**
- [ ] 50ms 内生成 ≥200 个三角形（口径 A）
- [ ] 80ms 内生成 ≥150 个三角形（口径 B，容许降级）
- [ ] 覆盖率 ≥80%（口径 A）/ ≥65%（口径 B）
- [ ] 无明显拓扑破洞（连通分量 ≤3）
- [ ] 三方案交叉对比报告

### 实验 0-B: 在线 Rebinding（双档压力）

| 项目 | 规格 |
|------|------|
| 策略 | 只重绑 dirty region，不全局重绑 |
| 实现位置 | `aether_cpp/src/binding/` 纯 CPU C++ |

**双档压力模型（P1 修复）:**

| 档位 | Gaussian 数 | 三角变化数 | 目标延迟 (host) | 统计口径 |
|------|-------------|------------|------------------|----------|
| 标准档 | 10,000 | 500 增删改 | p50 <1ms, p95 <3ms, p99 <5ms | 1000 次采样 |
| 压力档 | 100,000 | 2,000 增删改 | p50 <5ms, p95 <10ms, p99 <20ms | 1000 次采样 |

**多方案并行:**
- **方案 α:** Mani-GS barycentric + normal offset（CVPR 2025）
- **方案 β:** GaMeS 三角形顶点参数化（arXiv:2402.01459）
- **方案 γ:** 自研 KD-tree 空间索引 + 增量 dirty 传播

**验收标准:**
- [ ] 两档压力测试均达标（p50/p95/p99 全部达标）
- [ ] 无 ID 复用导致的错误绑定
- [ ] dirty region 检测正确率 100%
- [ ] 100k 档位下内存增长线性可控
- [ ] 三方案延迟/正确率对比报告

### 实验 0-C: Harmony Vulkan 可用性（延后，当前无设备）

> 当前只有 iOS 设备。Harmony Vulkan 验证延后到有设备时再做。
> Vulkan compute shader 代码在 Phase 1-4 中同步编写并做 SPIR-V 编译验证。

| 项目 | 规格 |
|------|------|
| 目标 | 确认无 ratified 扩展时能否避免每帧 CPU↔GPU 大规模回拷 |
| 测试负载 | 渲染 10 万个 Gaussian，测量单帧 CPU↔GPU 传输量和耗时 |
| 判定 | 若无法稳定避开大规模回拷 → 该平台仅做 S5-Deferred |
| 状态 | **延后执行**，不阻塞 Phase 0 Go/No-Go 决策 |

**技术路径:**
- VK_OHOS_surface (2025-05-16, not ratified)
- VK_OHOS_external_memory (2025-11-04, not ratified)
- 回退路径: Vulkan 1.1 通用 buffer upload

**验收标准（有设备后执行）:**
- [ ] 量化: CPU↔GPU 单帧传输量 < 2MB
- [ ] 或: 单帧传输耗时 < 1ms
- [ ] 若不满足: 正式确认 Harmony 只做 S5-Deferred

### 实验 0-D: Device Spike（真机最小内核验证）

> **[新增]** 解决 "host benchmark 通过但手机失败" 的风险

| 项目 | 规格 |
|------|------|
| 目标 | iOS 真机跑通最小 forward + backward 内核（Android/Harmony 留 Vulkan 接口，暂不实机验证） |
| 内核规模 | 1000 个 Gaussian，单帧 forward render + backward gradient |
| 不含 | 不含 scaffold、不含 SLAM、不含完整 pipeline |

**测试矩阵:**

| 平台 | 机型 | GPU 后端 | 指标 | 状态 | 时间 |
|------|------|----------|------|------|------|
| iOS S5-RT | iPhone 15 Pro (A17 Pro) | Metal compute | forward <2ms, backward <5ms | **Phase 0 实机验证** | Week 1-4 |
| iOS S5-Deferred | iPhone 12 或 13 (A15/A16) | Metal compute | forward <5ms, backward <15ms | **最晚 Week 10 补测** | Week 6-10 |
| Android | — | Vulkan 1.1 compute | — | 留接口，后续验证 | — |
| Harmony | — | Vulkan 1.1 compute | — | 留接口，后续验证 | — |

> S5-Deferred 机型不阻塞 Phase 0 Go/No-Go，但必须在 Phase 1 结束前完成。
> 若 S5-Deferred spike 失败 → 重新评估 S5-Deferred 的目标指标或降级为 S4。

**实现:**
```
aether_cpp/gpu/spike/
├── minimal_forward.metal              // iOS Metal 实机验证
├── minimal_backward.metal             // iOS Metal 实机验证
├── minimal_forward.comp.glsl          // Vulkan 接口预留（编译验证，暂不上机）
├── minimal_backward.comp.glsl         // Vulkan 接口预留
├── spike_runner.cpp                   // host 调度
└── CMakeLists.txt                     // iOS 交叉编译 + Vulkan SPIR-V 编译验证
```

**验收标准:**
- [ ] iOS 真机跑通 forward + backward，无 crash
- [ ] iOS 延迟在阈值内（forward <2ms, backward <5ms）
- [ ] GPU 内存分配/释放正确（Instruments 验证）
- [ ] Vulkan compute shader SPIR-V 编译通过（不要求上机）
- [ ] 记录 iOS driver 版本、Metal feature set、性能特征

### Phase 0 硬性止损线

| 条件 | 触发动作 |
|------|----------|
| iPhone 15 Pro 10k Gaussian forward+backward+相机预览不能同时稳定运行 | 降级: 放弃第一帧即 Scaffold，改回"延迟 Scaffold"策略 |
| 0-A 双口径中口径 B（噪声注入）全部方案失败 | 不进入 Phase 1，重新评估 Scaffold 技术路线 |
| 0-B 100k 档 p99 > 50ms | 降低目标 Gaussian 上限至 50k，重新规划内存预算 |

### Phase 0 交付物
- 三份算法实验报告（0-A/0-B/0-D）+ 百分位统计
- 多方案交叉验证对比表（0-A 三方案、0-B 三方案）
- iOS 真机 spike 测试报告（Vulkan SPIR-V 编译验证报告附录）
- Go/No-Go 决策文档（含上述硬性止损线）
- 失败回退方案确认

---

## Phase 1 — C++ 核心算法骨架

**时长:** 6 周
**前置:** Phase 0 通过
**目标:** 在 `aether_cpp` 中建立 Online 3DGS Optimizer 的完整 C++ 接口和 CPU 参考实现
**并行:** GPU 后端 spike 代码继续迭代（Phase 4 前置）

### 1.1 Gaussian 数据结构

```
aether/optimizer/gaussian.h
├── GaussianPrimitive
│   ├── position: float[3]
│   ├── rotation: float[4] (quaternion)
│   ├── scale: float[3]
│   ├── opacity: float
│   ├── sh_coeffs: float[N] (SH degree 0-2, 最多 27 floats)
│   ├── binding: BindingRecord
│   └── generation: uint32
├── GaussianCloud
│   ├── primitives: GaussianPrimitive[]
│   ├── count / capacity
│   ├── allocate / deallocate / compact
│   └── statistics (mean opacity, coverage, etc.)
```

**关键设计决策（基于调研）:**
- SH 系数最多 2 阶（27 floats），不用 3 阶。参考 LightGaussian (NeurIPS 2024) 的 SH 蒸馏结论：2 阶保留 95%+ 视觉质量
- opacity 用 sigmoid 参数化（存储 logit），与 gsplat 和原始 3DGS 保持一致
- 参考 ContraGS (arXiv:2509.03775) 的 codebook 压缩训练：数据结构预留 codebook index 字段

### 1.2 Scaffold 数据结构

```
aether/optimizer/scaffold.h
├── ScaffoldUnit (Triangle or Tetrahedron)
│   ├── unit_id: uint64 (全局单调递增，永不复用)
│   ├── generation: uint32
│   ├── vertices: uint32[3] 或 uint32[4] (index into vertex buffer)
│   ├── normal: float[3]
│   ├── area: float
│   └── flags: uint8 (dirty, boundary, etc.)
├── ScaffoldMesh
│   ├── vertices: float[3][]
│   ├── units: ScaffoldUnit[]
│   ├── next_unit_id: uint64
│   ├── dirty_queue: FixedVec<uint32, N>
│   └── topology operations: add_unit / remove_unit / split_unit
```

### 1.3 Binding Manager

```
aether/optimizer/binding_manager.h
├── BindingRecord
│   ├── host_unit_id: uint64
│   ├── bind_generation: uint32
│   ├── barycentric: float[3] (or float[4] for tet)
│   ├── normal_offset: float
│   └── binding_state: enum { kFree, kSoft, kHard }
├── BindingManager
│   ├── find_host(position, scaffold) → BindingRecord
│   ├── migrate_dirty_region(dirty_units, gaussians)
│   ├── harden_bindings(weight_schedule)  // soft→hard 渐进
│   └── orphan_queue: migration queue for homeless Gaussians
```

**多方案竞争接口:**
```
aether/optimizer/binding_strategy.h
├── BindingStrategy (纯虚基类)
│   ├── compute_binding(position, scaffold) → BindingRecord
│   ├── update_on_topology_change(diff, gaussians) → rebind_count
│   └── name() → const char*
├── BarycentricOffsetStrategy : BindingStrategy  // Mani-GS 方案
├── VertexParameterStrategy : BindingStrategy     // GaMeS 方案
├── SpatialIndexStrategy : BindingStrategy        // 自研 KD-tree 方案
```

### 1.4 Online Optimizer Core

```
aether/optimizer/online_optimizer.h
├── OptimizerConfig
│   ├── micro_batch_size: int (默认 4-8 关键帧)
│   ├── max_gaussians: int
│   ├── learning_rates: LRSchedule
│   ├── densification_interval: int
│   ├── pruning_threshold: float
│   └── regularization_weights: RegWeights
├── OnlineOptimizer
│   ├── step(keyframes, scaffold, cloud) → OptimizationResult
│   ├── densify(cloud, gradients) → new primitives
│   ├── prune(cloud, thresholds) → removed count
│   └── compute_loss(rendered, observed) → LossBreakdown
```

**多方案竞争 — 优化策略:**
```
aether/optimizer/optimization_strategy.h
├── OptimizationStrategy (纯虚基类)
│   ├── step(batch, cloud, scaffold) → OptResult
│   └── name() → const char*
├── ClassicAdamStrategy        // 原始 3DGS Adam 优化
├── FrequencyModulatedStrategy // Opti3DGS coarse-to-fine
├── MCMCStrategy               // gsplat MCMC 采样策略
├── TwoStageStrategy           // Micro-splatting Growth+Refinement
```

**Micro-batch 策略（基于调研）:**
- 参考 Trick-GS (ICASSP 2025): 渐进分辨率训练，初期用低分辨率降低 GPU 开销
- 参考 Opti3DGS (arXiv:2503.14475): 频率调制 coarse-to-fine，初始强制粗表示，逐步放开高频
- 参考 Micro-splatting (arXiv:2504.05740): 两阶段（Growth + Refinement），用 trace-based 协方差正则化
- 参考 PocketGS (arXiv:2601.17354): 移动端反向传播的 cached alpha compositing + index-mapped gradient scattering

**损失函数:**
```
L_total = λ_photo * L1 + λ_ssim * (1 - SSIM) + λ_geo * L_scaffold_regularization
        + λ_sparse * L_opacity_sparse + λ_normal * L_normal_consistency

其中:
- L_scaffold_regularization = 形变正则 + 边长一致性 + 法向一致性
- 不含任何生成式补洞项
```

### 1.5 Densification & Pruning

**多方案竞争 — Densification:**
```
aether/optimizer/densification_strategy.h
├── DensificationStrategy (纯虚基类)
├── GradientSplitStrategy     // 原始 3DGS gradient-based splitting
├── AbsGradientStrategy       // AbsGS 绝对值梯度策略
├── MCMCSamplingStrategy      // MCMC 采样 (gsplat MCMCStrategy)
├── ScaffoldGuidedStrategy    // 自研: 从 scaffold 表面采样 + barycentric 扰动
```

**Pruning（基于调研）:**
- 参考 Micro-splatting: opacity-scale importance metric
- 参考 SVR-GS (arXiv:2509.11116): 空间自适应正则化 mask
- 低贡献 Gaussian（opacity < threshold 或 scale 过小）直接剔除
- 置信度裁剪: 使用证据链模块的观测计数作为置信度来源

### Phase 1 多策略范围控制

> Phase 0 结束后，每个策略点砍掉最差的 1 个方案，Phase 1 每个策略点 **≤2 方案**。
> Phase 3 末冻结为单冠军 + 1 热备。Phase 4 起不允许新增方案。
> 所有淘汰和选型决策使用架构确认章节的量化打分公式，不做主观拍板。

### Phase 1 交付物
- `aether/optimizer/` 头文件完整定义（含多方案竞争接口）
- `aether_cpp/src/optimizer/` CPU 参考实现（每个策略点 2 种方案，Phase 0 最差方案已淘汰）
- `aether_cpp/tests/optimizer/` 单元测试
- 在 macOS host 上端到端跑通: 给定 10 帧图片 + 位姿 → 输出 Gaussian cloud
- CPU 单帧优化耗时 benchmark
- 多方案对比报告（策略 × 指标矩阵）

---

## Phase 2 — 纯视觉采集链 + Scaffold 即时构建

**时长:** 8 周
**前置:** Phase 1 完成
**目标:** 从 RGB+IMU 输入到增量建图的完整采集链，Scaffold 从第一帧起即时构建
**并行:** GPU 后端 Metal 内核开发已启动（Phase 4 前置）

### 2.1 视觉跟踪模块

```
aether/tracking/visual_tracker.h
├── VisualTracker
│   ├── process_frame(rgb, imu_data) → TrackingResult
│   ├── get_pose() → float[16]
│   ├── get_sparse_points() → SparsePointCloud
│   └── tracking_state: enum { kNormal, kLimited, kLost }
```

**技术路径（基于调研）:**
- 纯视觉基线: 参考 DROID-SLAM 的 recurrent dense optical flow + BA
- 参考 Splat-SLAM (CVPR 2025 Workshop): RGB-only + 单目深度估计器修正
- 参考 WildGS-SLAM (CVPR 2025): DINOv2 特征 + 不确定性 MLP 处理动态物体
- IMU 融合: 预积分 + 松耦合（IMU 提供重力方向和尺度先验）

### 2.2 关键帧管理

```
aether/tracking/keyframe_manager.h
├── KeyframeManager
│   ├── should_add_keyframe(pose, motion_stats) → bool
│   ├── add_keyframe(frame, pose, sparse_points)
│   ├── get_active_window(size) → Keyframe[]
│   ├── get_loop_candidates(pose) → Keyframe[]
│   └── evict_old_keyframes(budget)
```

**选帧策略:**
- 基于运动量: 平移 > 0.3m 或旋转 > 15° 触发新关键帧
- 复用你现有的 `KEYFRAME_INTERVAL=6`, `KEYFRAME_ANGULAR_TRIGGER_DEG=15.0f`, `KEYFRAME_TRANSLATION_TRIGGER=0.3f`
- 最大关键帧预算: 来自你现有的 `MAX_KEYFRAMES_PER_SESSION=30`

### 2.3 回环检测 + 位姿图优化

```
aether/tracking/loop_closure.h
├── LoopDetector
│   ├── detect(current_keyframe, candidates) → LoopCandidate[]
│   ├── verify(candidate, relative_pose) → bool
│   └── get_loop_edges() → PoseGraphEdge[]
├── PoseGraphOptimizer
│   ├── add_odometry_edge(from, to, relative_pose, info_matrix)
│   ├── add_loop_edge(from, to, relative_pose, info_matrix)
│   ├── optimize() → corrected_poses[]
│   └── apply_corrections(gaussian_cloud, scaffold)
```

**技术路径（基于调研）:**
- 参考 LoopSplat (3DV 2025 Oral): 3DGS submap 注册做回环约束
- 参考 Splat-SLAM: 全局 BA + 可变形 Gaussian map（回环时 deform）
- 参考 DROID-Splat (ICCV 2025 Workshop): 端到端跟踪 + 回环检测 + 3DGS 渲染
- 回环检测用视觉词袋（DBoW2）或学习特征（DINOv2/NetVLAD）
- 位姿修正后触发 scaffold 局部重网格 + Gaussian rebinding

### 2.4 Scaffold 即时构建（极致工程方案）

> **不分 A/B 阶段。从第一帧起，Scaffold 生成与 Gaussian 优化同时运行。**

**核心设计:**
```
从第一帧起:
├── 跟踪器输出位姿 + 稀疏点
├── Scaffold 生成器增量更新（每次新关键帧触发）
├── Gaussian 优化器同步运行
│   ├── 新 Gaussian spawn 同时分配 scaffold 绑定（kSoft）
│   ├── scaffold 拓扑变化 → 增量 rebinding
│   └── 绑定渐进加强: 观测次数 ≥ threshold → kHard
└── 所有模块通过统一接口互操作
```

**运行时自动回退机制（不只是项目级止损）:**
```
// 每帧评估 scaffold 生成健康度:
scaffold_health = {
    tracking_confidence: tracker.confidence()      // [0,1]
    sparse_point_count: tracker.sparse_points().count
    scaffold_coverage: scaffold.observed_area_ratio()
}

if tracking_confidence < 0.3 连续 ≥ 5 帧:
    → 自动切入"延迟 scaffold"模式:
      - 暂停 scaffold 增量更新
      - 新 Gaussian 以 kFree 状态 spawn（不绑定）
      - 等待 tracking_confidence 恢复 > 0.5 持续 ≥ 3 帧后恢复
    → 恢复时: 对 kFree Gaussian 做一次批量 find_host() 绑定

if sparse_point_count < 50 连续 ≥ 10 帧:
    → 降级: scaffold 生成频率从每关键帧降到每 3 关键帧
    → 恢复条件: sparse_point_count > 100 持续 ≥ 5 帧
```

**多方案竞争 — Scaffold 增量策略:**
- **方案 α:** 帧到帧增量 Delaunay（每次新关键帧追加点+重三角化局部区域）
- **方案 β:** PLANING 风格流式三角生成（arXiv:2601.22046）
- **方案 γ:** 自研 voxel-guided 增量网格（利用现有 TSDF spatial hash 加速邻域查询）

**绑定强化规则（替代冷启动 A/B，自适应阈值）:**
```
// 阈值不再硬编码，基于全局观测统计自适应:
soft_threshold = max(3, percentile_25(all_gaussian_observation_counts))
hard_threshold = max(10, percentile_75(all_gaussian_observation_counts))

binding_strength(gaussian) =
    if observation_count < soft_threshold:  kFree
    elif observation_count < hard_threshold: kSoft  // 正则项权重 = (count - soft) / (hard - soft)
    else:                                    kHard
```
- 不存在全局的 "phase A" 或 "phase B"
- 每个 Gaussian 独立地根据自身观测次数决定绑定强度
- 早期帧的 Gaussian 自然先达到 kHard，后期帧的逐步跟上
- 无全局切换点 → 无可见跳变

### Phase 2 交付物
- 采集链端到端在 macOS host CPU 上跑通
- 标准测试场景（10m×10m 室内）完整采集
- 位姿漂移 < 2cm RMSE（含回环）— 真值来源见 Phase 5.5 测量协议
- Scaffold 从第一帧起增量构建，无分阶段切换
- 多方案 scaffold 增量策略交叉对比
- benchmark: 跟踪帧率 / 关键帧选取率 / 回环检测延迟

---

## Phase 3 — 双层表示：Scaffold 绑定与迁移

**时长:** 6 周
**前置:** Phase 2 完成
**目标:** 双层表示的完整实现，包括增量拓扑更新和绑定管理

### 3.1 增量 Scaffold 更新

```
aether/optimizer/scaffold_updater.h
├── ScaffoldUpdater
│   ├── add_observation(new_points, new_normals) → topology_diff
│   ├── split_large_triangles(area_threshold) → topology_diff
│   ├── merge_small_triangles(area_threshold) → topology_diff
│   ├── apply_loop_correction(pose_deltas) → topology_diff
│   └── TopologyDiff
│       ├── added_units: ScaffoldUnit[]
│       ├── removed_unit_ids: uint64[]
│       ├── modified_unit_ids: uint64[] (顶点位置变化)
│       └── affected_gaussian_count: int
```

**关键设计:**
- 每次拓扑变化生成 `TopologyDiff`
- `TopologyDiff` 驱动 `BindingManager` 的增量迁移
- removed_unit_ids 中的 Gaussian 进入 orphan_queue
- modified_unit_ids 中的 Gaussian 只需更新 barycentric（不重绑）

### 3.2 Scaffold 正则化

```
L_scaffold = λ_deform * Σ |edge_length - edge_length_rest|²
           + λ_normal * Σ (1 - dot(n_unit, n_gaussian))²
           + λ_area   * Σ max(0, area_unit - area_max)²
```

**参考:**
- Mani-GS (CVPR 2025): shape-aware adaptation，三角形面积/边长/法向变化驱动 Gaussian 属性适应
- GS-Verse (arXiv:2510.11878): 物理仿真驱动 mesh 形变 → Gaussian 自动适应
- PhysGaussian (CVPR 2024): MPM grid 驱动 Gaussian 运动，协方差随局部形变梯度更新

### 3.3 与证据链的集成

每次 scaffold 更新 + Gaussian 优化后:
```
evidence_observation = {
    patch_id: scaffold_unit_id,
    timestamp: current_time,
    frame_id: keyframe_id,
    view_angle: compute_view_angle(camera_pose, unit_normal)
}
replay_engine.process_observation(observation)
```

覆盖率指标:
```
aether/optimizer/coverage_tracker.h
├── CoverageTracker
│   ├── update(scaffold, gaussians, keyframes)
│   ├── observed_area_ratio() → float
│   ├── unknown_ratio() → float
│   ├── min_view_count_per_unit() → int
│   ├── export_coverage_proof() → MerkleProof
│   └── per_unit_confidence: map<uint64, float>
```

### Phase 3 交付物
- 双层表示在 CPU 上完整运行
- Scaffold 增量更新 + Gaussian rebinding 无画面跳变
- 回环修正后 scaffold+Gaussian 全局一致
- 覆盖率追踪 + Merkle 证明生成
- benchmark: rebinding 延迟 (p50/p95/p99) / 拓扑更新开销 / 正则化收敛速度

---

## Phase 4 — GPU 后端（Metal / Vulkan）

**时长:** 8 周
**前置:** Phase 3 完成（CPU 参考实现全部跑通）
**注意:** Phase 0-D spike 代码和 Phase 1-2 期间积累的 GPU 原型在此正式集成
**并行:** 平台壳基础工作（相机接入、权限申请）同步启动

### 4.1 GPU 抽象层

```
aether/gpu/compute_backend.h
├── ComputeBackend (纯虚基类)
│   ├── allocate_buffer(size, usage) → BufferHandle
│   ├── upload(buffer, data, size)
│   ├── dispatch_compute(kernel, grid, block, buffers)
│   ├── readback(buffer, data, size)
│   └── fence / sync primitives
├── MetalBackend : ComputeBackend  // iOS / macOS
├── VulkanBackend : ComputeBackend // Android / Harmony
```

### 4.2 需要 GPU 化的内核

| 内核 | 输入 | 输出 | 优先级 |
|------|------|------|--------|
| Forward rasterization | Gaussian cloud + camera | rendered image | P0 |
| Backward pass (gradient) | rendered vs observed + cloud | gradients | P0 |
| Tile sorting | Gaussian depths | sorted order | P0 |
| Densification | gradients + cloud | new Gaussians | P1 |
| Pruning | cloud + thresholds | compacted cloud | P1 |
| Scaffold-Gaussian constraint | scaffold + bindings | constraint forces | P1 |
| SSIM computation | rendered vs observed | SSIM map | P2 |
| Coverage map update | scaffold + keyframes | coverage per unit | P2 |

**关键参考（基于调研）:**
- gsplat (nerfstudio): 开源 CUDA 内核，forward+backward 完整实现，Apache 2.0
- PocketGS (arXiv:2601.17354): 移动端反向传播关键创新——cached alpha compositing + index-mapped gradient scattering，唯一一篇明确在手机上实现 backward pass 的论文
- GS-Scale (arXiv:2509.15645): host offloading 策略——Gaussian 存 CPU，每帧只传子集到 GPU
- Sort-free GS (ICLR 2025, arXiv:2410.18931): 用 weighted sum 替代 alpha blending，消除排序，移动端 1.23x 加速
- SqueezeMe (SIGGRAPH 2025, arXiv:2412.15171): **唯一一篇明确描述 Vulkan splatting pipeline 的论文**，Meta Quest 3 上 72 FPS
- FlashGS (arXiv:2408.07967): 移动消费级 GPU 4x 加速

**多方案竞争 — 排序策略:**
- **方案 α:** Bitonic sort（经典 GPU 排序）
- **方案 β:** Sort-free weighted sum（arXiv:2410.18931）— 完全消除排序
- **方案 γ:** Radix sort（PocketGS 方案）

### 4.3 Metal 实现（iOS 首发）

```
aether_cpp/gpu/metal/
├── forward_rasterize.metal    // tile-based splatting
├── backward_gradient.metal    // PocketGS 风格 cached alpha + index-mapped scatter
├── sort_by_depth.metal        // 多方案: bitonic / sort-free / radix
├── densify_prune.metal        // gradient-based split + opacity prune
└── MetalBackend.mm            // Objective-C++ 桥接
```

### 4.4 Vulkan 实现（Android / Harmony）

```
aether_cpp/gpu/vulkan/
├── forward_rasterize.comp.glsl
├── backward_gradient.comp.glsl
├── sort_by_depth.comp.glsl
├── densify_prune.comp.glsl
├── VulkanBackend.cpp
└── harmony_surface_compat.cpp  // VK_OHOS_surface 双路径容错
```

### Phase 4 交付物
- **Metal backend 在 iPhone 15 Pro 上跑通 forward + backward（首要目标）**
- Vulkan backend SPIR-V 编译通过 + host 模拟测试（不要求 Android/Harmony 上机）
- 10 万 Gaussian 渲染 > 30 FPS（iPhone 15 Pro）
- 单次 backward pass < 10ms（iPhone 15 Pro）
- 多方案排序策略对比报告
- GPU 内存峰值 < 1.5GB（iPhone 15 Pro Instruments 实测）

---

## Phase 5 — LOD / 压缩 / 三状态调度器

**时长:** 6 周
**前置:** Phase 4 完成
**目标:** 生产级性能优化和资源管理

### 5.1 LOD / 压缩系统

```
aether/optimizer/lod_manager.h
├── LODManager
│   ├── build_hierarchy(cloud) → LODHierarchy
│   ├── select_level(camera_pose, budget) → active_gaussians
│   ├── compress(cloud, target_ratio) → CompressedCloud
│   └── LODHierarchy
│       ├── levels: LODLevel[] (coarse → fine)
│       └── each level: Gaussian 子集 + 可选 codebook
```

**技术路径（基于调研）:**
- LightGaussian (NeurIPS 2024): 15x 压缩，significance-based pruning + SH 蒸馏 + VQ
- LFGS (ScienceDirect 2025): 90x 压缩，entropy-constrained VQ，专为移动端设计
- Frequency-Aware GS Decomposition (3DV 2026, arXiv:2503.21226): Laplacian 金字塔频率分带，支持渐进流式
- A LoD of Gaussians (arXiv:2507.01110): Sequential Point Trees + 外部存储流式加载
- Virtual Memory for 3DGS (arXiv:2506.19415): just-in-time GPU 流式，已在移动端测试
- Voyager (arXiv:2506.02774): 时间感知 LOD + 抢占式 alpha 过滤，移动端 6.6x 加速

### 5.2 三状态调度器（修正帧预算）

```
aether/scheduler/gpu_scheduler.h
├── MotionState: enum { kCapturing, kPaused, kFinished }
├── GPUBudget
│   ├── tracking_ms: float
│   ├── rendering_ms: float
│   ├── optimization_ms: float
│   ├── system_reserve_ms: float   // [新增] 硬预留
│   └── total_frame_budget_ms: float (16.6ms for 60fps)
├── GPUScheduler
│   ├── detect_motion_state(imu_data, pose_delta) → MotionState
│   ├── allocate_budget(state) → GPUBudget
│   ├── execute_frame(budget) → FrameResult
│   └── thermal_throttle(temperature) → adjusted GPUBudget
```

**状态切换触发（复用现有代码）:**
- 复用你的 `IDLE_TRANSLATION_SPEED=0.01f`, `IDLE_ANGULAR_SPEED=0.05f` 检测暂停
- 复用你的 `MOTION_DEFER_TRANSLATION_SPEED=0.5f` 检测高速运动
- 复用你的 AIMD 热管理: `THERMAL_MAX_INTEGRATION_SKIP=12`

**预算分配（硬预算 + 弹性预算，含系统预留）:**

> **[修正]** 每状态硬预留 3.5ms 给 camera pipeline / compositor / driver jitter / OS 调度。
> 可用预算 = 16.6ms - 3.5ms = 13.1ms

| 状态 | 跟踪 (硬) | 渲染 (硬) | 优化 (弹性) | 系统预留 (硬) | 弹性池 | 备注 |
|------|-----------|-----------|-------------|---------------|--------|------|
| 采集中 | 4ms | 4ms | 1ms | 3.5ms | 4.1ms | 弹性池按优先级分配: 跟踪>渲染>优化 |
| 暂停查看 | 0.5ms (待机) | 5ms | 3ms | 3.5ms | 4.6ms | 弹性池按优先级分配: 渲染>优化>跟踪 |
| 采集结束 | 0ms | 2ms (进度条) | 7ms | 3.5ms | 4.1ms | 弹性池全部给优化 |

**弹性池分配规则:**
```
// 硬预算: 每个任务的最低保证，不可被借用
// 弹性池: 剩余时间按当前状态的优先级瀑布分配
// 系统预留: 不可触碰，用于 camera/compositor/driver/OS
//
// 如果某任务提前完成，剩余时间回流到弹性池
// 如果弹性池用完，降级到硬预算运行
```

### 5.3 热管理（修正策略）

> **[修正]** 从 "20分钟不降频" 改为 "允许降频，画质与帧率不跌破下限"

```
aether/scheduler/thermal_manager.h
├── ThermalPolicy
│   ├── current_thermal_state: enum { kNormal, kWarm, kHot, kCritical }
│   ├── quality_floor: QualityFloor
│   │   ├── min_fps: 15 (采集中) / 20 (暂停查看)
│   │   ├── min_psnr_estimate: 24dB
│   │   └── max_gaussian_reduction_ratio: 0.5  // 最多砍一半
│   └── thermal_actions:
│       ├── kNormal:   全预算运行
│       ├── kWarm:     弹性池缩减 50%
│       ├── kHot:      弹性池归零 + 优化频率降半
│       └── kCritical: 仅跟踪+低 LOD 渲染，暂停优化
```

**热稳定性验收指标（修正）:**

| 指标 | 目标 |
|------|------|
| 持续采集时长 | ≥20 分钟（允许降频） |
| 降频后帧率下限 | ≥15 FPS（采集中）/ ≥20 FPS（暂停查看） |
| 降频后恢复到 kNormal | <30 秒（暂停采集后） |
| 降频期间质量下限 | PSNR estimate ≥24dB |
| 过热保护触发 | 仅 kCritical 时暂停优化，不暂停跟踪 |

### 5.4 自适应质量控制

```
aether/scheduler/quality_controller.h
├── QualityController
│   ├── assess_current_quality(rendered, keyframes) → QualityMetrics
│   ├── should_densify(metrics) → bool
│   ├── should_prune(metrics, memory_budget) → bool
│   ├── adjust_learning_rate(metrics) → LRSchedule
│   └── QualityMetrics
│       ├── psnr_estimate: float
│       ├── coverage_ratio: float
│       ├── gaussian_count: int
│       ├── memory_usage_bytes: int64
│       └── thermal_headroom: float
```

### Phase 5 交付物
- LOD 层级构建 + 自适应选择
- 三状态调度器完整运行（含系统预留）
- 暂停时画面质量明显提升（可量化 PSNR 提升 ≥2dB）
- 热管理自适应：降频后画质/帧率不跌破下限
- 内存占用: LOD 模式下 < 800MB（iPhone 15 Pro）

---

## Phase 5.5 — 统一测量协议

> **[新增]** 在 Phase 6 验收前固化所有指标的测量方式，避免验收争议

**时长:** 2 周（与 Phase 5 后半段并行）
**目标:** 建立可复现的真值测量、标定流程和统计口径

### 5.5.1 真值采集（Ground Truth）

| 指标类型 | GT 采集方式 | 设备 | 精度 |
|----------|-------------|------|------|
| 位姿 (RMSE) | OptiTrack / Vicon 动捕系统 | 多相机刚体追踪 | <0.5mm |
| 几何 (Chamfer) | 高精度结构光扫描仪 (如 Artec Eva) | 离线扫描 | <0.1mm |
| 渲染质量 (PSNR/SSIM/LPIPS) | 三脚架固定相机拍摄 NVS 测试视角 | 同采集设备 | N/A (像素级) |
| 回环位姿 | 同位姿 GT，多圈标定 | 同上 | <0.5mm |

### 5.5.2 测试场景策略

> **设计原则:** 算法必须自适应任意真实环境（不同城市、室内外、光线、时间点）。
> 固定场景仅用于 **回归测试基线** 和 **算法间横向对比**，不用于限定算法能力边界。

**回归基线场景（开发期快速迭代用）:**

| 场景 ID | 类型 | 用途 | 来源 |
|---------|------|------|------|
| REG-01 | 开发者随手拍的任意桌面 | Phase 0 门禁快速验证 | 自采 |
| REG-02 | 公开数据集 (如 ScanNet/Matterport3D) | 算法横向对比 + CI 自动回归 | 公开下载 |

**自适应压力测试（持续积累，模拟真实用户多样性）:**

| 维度 | 测试项 | 目的 |
|------|--------|------|
| 光照 | 逆光 / 室内暗光 / 日落侧光 / 荧光灯闪烁 | 曝光自适应 |
| 纹理 | 白墙 / 玻璃反射 / 重复瓷砖 / 树叶密布 | 特征匹配鲁棒性 |
| 运动 | 快速甩动 / 极慢移动 / 突然转向 | 跟踪稳定性 |
| 尺度 | 桌面小物 / 单房间 / 多楼层 / 户外建筑 | LOD + 内存管理 |
| 动态 | 有人走动 / 宠物 / 窗帘飘动 | 动态物体鲁棒性 |

- 每次发现新的失败 case → 加入压力测试集（只增不删）
- 算法必须在整个压力测试集上不低于质量下限
- 不设"标准场景达标即可"的逃逸口

### 5.5.3 统计口径

| 项目 | 口径 |
|------|------|
| 位姿 RMSE | ATE (Absolute Trajectory Error)，Umeyama 对齐后 |
| PSNR / SSIM | 留出 10% 视角做 NVS，计算全图均值 |
| LPIPS | AlexNet backbone，同 NVS 视角 |
| Chamfer Distance | 双向 Chamfer，采样 100k 点，单位 mm |
| 帧率 | 连续 60 秒中位数（不含启动前 5 秒） |
| 延迟百分位 | 1000 次采样，报告 p50 / p95 / p99 |
| 内存 | Instruments (iOS) / perfetto (Android) 峰值 RSS |
| 覆盖率 | observed_area / total_scaffold_area，按 scaffold unit 计 |
| 置信区间 | 关键指标（PSNR/SSIM/ATE/帧率）每场景跑 **10 次**，报告 mean ± 95% CI；其余指标 ≥ 3 次 |

### Phase 5.5 交付物
- 测量协议文档（包含上述全部内容）
- 回归基线数据（REG-01/02 + 公开数据集）
- 自动化评测脚本（输入: 重建结果 + GT → 输出: 指标报告）
- 初始压力测试集（≥10 个多样性场景）
- 各场景基线数据（用 Phase 5 完成的系统跑一次基线）
- 持续积累机制: 新失败 case → 自动加入测试集 → CI 回归

---

## Phase 6 — iOS 首发集成 + S5 验收（Android/Harmony 留接口）

**时长:** 8 周
**前置:** Phase 5 + Phase 5.5 完成
**目标:** iOS 全流程部署 + S5 分档验收；Android/Harmony 确保接口编译通过

### 6.1 平台壳集成

| 平台 | GPU 后端 | 相机接入 | 部署形态 | 本期状态 |
|------|----------|----------|----------|----------|
| iOS | Metal | AVCaptureSession + ARKit (可选) | Framework (.xcframework) | **完整实现 + 验收** |
| Android | Vulkan 1.1 | Camera2 API + ARCore (可选) | Shared library (.so) | 接口编译通过，不上机 |
| Harmony | Vulkan 1.1 + 双路径容错 | Camera Kit + AR Engine (可选) | Shared library (.so) | 接口编译通过，不上机 |

### 6.2 S5 验收指标

#### S5-RT（白名单高端机）

| 指标 | 目标 | 测试方法 (按 Phase 5.5 协议) |
|------|------|------------------------------|
| 采集帧率 | ≥30 FPS | 回归场景 + 压力测试集, 60 秒中位数 |
| 交互渲染帧率 | ≥30 FPS | 自由视角浏览, 60 秒中位数 |
| 位姿精度 | ATE RMSE < 2cm | 公开数据集 (有 GT) + OptiTrack 自采 |
| 渲染质量 (PSNR) | ≥28 dB | 留出 10% NVS 视角 |
| 渲染质量 (SSIM) | ≥0.90 | 同上 |
| 采集到预览 | <2 秒 | 第一帧 Gaussian 出现时间 |
| 采集结束到高质 | <3 分钟 | 全力优化到 PSNR ≥28dB |
| 热稳定性 | 20 分钟持续采集，降频后 ≥15 FPS | iPhone 真机实测 |
| 内存峰值 | <2 GB | Instruments 实测 |
| Crash-free 会话率 (10min) | ≥ 99.0% | 连续 100 次 10 分钟采集会话，统计无 crash 比例 |
| Crash-free 会话率 (20min) | ≥ 97.0% | 连续 50 次 20 分钟采集会话（含热降频场景） |

#### S5-Deferred（次级机）

| 指标 | 目标 |
|------|------|
| 采集帧率 | ≥30 FPS |
| 预览渲染帧率 | ≥15 FPS（低 LOD） |
| 延时高质生成 | <10 分钟 |
| 最终 PSNR | ≥26 dB |
| 内存峰值 | <1.5 GB |

#### 可验证覆盖率（所有档位）— 含硬门槛

> **[修正]** 增加硬门槛，按场景类型分档。不再是"报告值不设下限"。

| 指标 | 标准室内 (≤5m×5m) | 大空间 (>5m×5m) | 弱纹理/极端条件 |
|------|----------------------|--------------------|--------------------|
| Observed Area Ratio | **≥ 85%** | **≥ 75%** | **≥ 60%** |
| Unknown Ratio 上限 | **≤ 15%** | **≤ 25%** | **≤ 40%** |
| Min View Count per unit | **≥ 3** | **≥ 2** | **≥ 2** |
| Merkle 证明可验证 | 任意第三方可校验 | 同 | 同 |

> 场景类型由系统自动判定（基于 scaffold 总面积 + 纹理复杂度指标），无需用户手动选择。

**未达标处理:**
- 覆盖率不达标时，系统主动提示用户"以下区域需要补拍"并高亮未覆盖区域
- 不补假纹理，不做任何生成式填充
- 最终输出标注 observed / unknown 区域边界

### 6.3 白名单首批机型

**iOS S5-RT:**
- iPhone 15 Pro / 15 Pro Max (A17 Pro)
- iPhone 16 / 16 Pro 系列 (A18 / A18 Pro)

**iOS S5-Deferred:**
- iPhone 12 ~ 14 系列 (A14 ~ A16)

**Android S5-RT:**
- Snapdragon 8 Gen 2 / Gen 3 / Gen 4
- Dimensity 9300 / 9400

**Android S5-Deferred:**
- Snapdragon 7 系 / 8s Gen 3
- Dimensity 8000 系列

**Harmony S5-Deferred (2026H1 默认):**
- Mate 60/70 系列, Pura 70/80 系列 (12GB+)

**Harmony S5-RT (待 Vulkan 扩展 ratified):**
- 同上旗舰机型，等 VK_OHOS_surface ratified 后验证

### 6.4 下游任务验收

| 下游场景 | 验收指标 | GT 来源 (按 Phase 5.5 协议) |
|----------|----------|------------------------------|
| 新视角合成 | PSNR ≥28dB, SSIM ≥0.90, LPIPS ≤0.15 | 三脚架固定拍摄 |
| 几何精度 | Chamfer Distance < 5mm (桌面), < 2cm (房间) | 结构光扫描仪 |
| 机器人导航训练 | 导航成功率提升 ≥5%（vs 无 3DGS 数据增强） | 仿真环境对比 |
| 自动驾驶仿真（感知质量） | FID ≤30（vs 真实图像） | 真实行车记录 |
| 自动驾驶仿真（检测精度） | 3D detection mAP ≥ baseline × 0.95 | 合成数据训练 → 真实数据 eval |
| 自动驾驶仿真（分割精度） | segmentation mIoU ≥ baseline × 0.95 | 同上 |
| 自动驾驶仿真（轨迹预测） | trajectory prediction ADE ≤ baseline × 1.10 | 合成轨迹 vs GT 轨迹 |

### Phase 6 交付物
- **iOS 版完整可运行 demo**（全流程: 拍摄 → 本地处理 → 交互渲染 → 质量摘要 + 覆盖率证明）
- iOS S5 验收报告（含全部指标数据 + 置信区间）
- iOS 白名单机型测试报告
- Android/Harmony: Vulkan backend + 平台壳编译通过，接口文档就绪，待后续上机验证

---

## 全流程时间线总览（含并行 + 风险缓冲）

> **[修正]** GPU 后端和平台壳前置并行，不等 Phase 4 才首次上机。总排期加 20% 风险缓冲。

```
                        0    4    10   18   24   32   38  40  48   56 weeks
Phase 0 (4w)            ████
Phase 1 (6w)                 ██████
Phase 2 (8w)                       ████████
Phase 3 (6w)                               ██████
Phase 4 (8w)                                     ████████
Phase 5 (6w)                                             ██████
Phase 5.5 (2w, parallel)                                 ██
Phase 6 (8w)                                                   ████████
                        ─────────────────────────────────────────────────
GPU spike/proto (前置)  ████████████████████████                          ← 从 Phase 0 起持续
Platform shell (前置)                  ████████████████████               ← 从 Phase 2 起持续
Risk buffer (+20%)                                                  ████████  ← 11w 风险缓冲
                        ─────────────────────────────────────────────────
                        0    4    10   18   24   32   38  40  48   56 weeks
```

**核心排期: ~48 周**
**含 20% 风险缓冲: ~56 周（约 13 个月）**

### 前置并行工作流

| 并行线 | 起始 | 内容 | 目的 |
|--------|------|------|------|
| GPU spike/proto (Metal) | Phase 0 同步 | iOS Metal 最小内核 → 逐步扩展到完整 forward/backward → 优化 | Phase 4 开始时已有可用 Metal 原型 |
| iOS platform shell | Phase 2 同步 | AVCaptureSession 接入、权限框架、xcframework build、真机 CI | Phase 6 开始时 iOS 壳已就绪 |
| Vulkan 接口 | Phase 1 同步 | Vulkan compute shader 编写 + SPIR-V 编译验证（不上机） | 为后续 Android/Harmony 扩展留好接口 |

### 关键里程碑

| Week | 里程碑 | 关键产出 |
|------|--------|----------|
| 4 | Go/No-Go 决策 | 四份实验报告 + 真机 spike 验证 |
| 10 | C++ 骨架完成 | CPU 端到端可跑 + 多方案对比 |
| 18 | 采集链完成 | 纯视觉 + Scaffold 即时构建，含回环 |
| 24 | 双层表示完成 | Scaffold↔Gaussian 全流程 |
| 32 | GPU 后端跑通 | 首次手机上端到端运行（有 GPU proto 铺垫） |
| 38 | 性能优化完成 | LOD + 调度器 + 热管理 |
| 40 | 测量协议固化 | GT 数据 + 评测脚本 + 基线 |
| 48 | iOS 首发 S5 验收 | Demo + 验收报告（Android/Harmony 接口编译通过） |
| 56 | 风险缓冲结束 | 回归修复 + 边界打磨 |

### 风险缓冲使用规则
- 11 周缓冲不预分配到任何 Phase
- 哪个 Phase 延期，从缓冲池扣除
- 每个 Phase 结束时更新缓冲余额
- 缓冲消耗 >50% 时触发全局排期重评审

---

## 前沿参考文献索引

按模块分类，标注关键程度: ★★★ 必读 / ★★ 重要 / ★ 参考

### 一、Online/增量 3DGS 优化

| Ref | 论文 | 会议/日期 | 关键贡献 | 关键度 |
|-----|------|-----------|----------|--------|
| R01 | [PocketGS](https://arxiv.org/abs/2601.17354) | arXiv 2026-01 | **唯一移动端 3DGS 训练论文**。cached alpha compositing + index-mapped gradient scattering | ★★★ |
| R02 | [On-the-fly Reconstruction](https://arxiv.org/abs/2506.05558) | SIGGRAPH 2025 | 增量 Gaussian spawning + progressive clustering + GPU offloading | ★★★ |
| R03 | [GS-Scale](https://arxiv.org/abs/2509.15645) | arXiv 2025-09 | Host offloading: Gaussian 存 CPU，子集传 GPU。RTX 4070 Mobile 测试 | ★★★ |
| R04 | [Opti3DGS](https://arxiv.org/abs/2503.14475) | ACM 2025 | 频率调制 coarse-to-fine: 62% fewer Gaussians, 40% less GPU mem | ★★ |
| R05 | [Micro-splatting](https://arxiv.org/abs/2504.05740) | arXiv 2025-04 | 两阶段 Growth+Refinement, 60% 减少 splat 数 | ★★ |
| R06 | [ContraGS](https://arxiv.org/abs/2509.03775) | arXiv 2025-09 | Codebook 压缩训练: 3.49x 峰值内存减少 | ★★ |
| R07 | [Trick-GS](https://arxiv.org/abs/2501.14534) | ICASSP 2025 | 渐进分辨率训练，2x 训练加速，明确面向手机 | ★★ |
| R08 | [CoRe-GS](https://arxiv.org/abs/2509.04859) | arXiv 2025-09 | 语义选择性优化: 75% 训练时间减少 | ★ |
| R09 | [PLANING](https://arxiv.org/abs/2601.22046) | arXiv 2026-01 | **松耦合三角-高斯流式重建**，5x 快于 2DGS | ★★★ |
| R10 | [Scale-GS](https://arxiv.org/abs/2508.21444) | arXiv 2025-08 | 层级 scale 组织 + 双向自适应 masking | ★ |
| R11 | [LongSplat](https://arxiv.org/abs/2507.16144) | arXiv 2025-07 | 流式冗余消减: 44% fewer Gaussians | ★ |

### 二、Tri/Tet Scaffold + Gaussian 混合表示

| Ref | 论文 | 会议/日期 | 关键贡献 | 关键度 |
|-----|------|-----------|----------|--------|
| R12 | [Mani-GS](https://arxiv.org/abs/2405.17811) | CVPR 2025 | **Shape-aware barycentric+offset 绑定方法**，大形变下保质量 | ★★★ |
| R13 | [GaMeS](https://arxiv.org/abs/2402.01459) | arXiv 2024-02 | Gaussian 参数完全由三角形顶点推导，编辑即动 | ★★★ |
| R14 | [3DGUT](https://openaccess.thecvf.com/content/CVPR2025/html/Wu_3DGUT_Enabling_Distorted_Cameras_and_Secondary_Rays_in_Gaussian_Splatting_CVPR_2025_paper.html) | CVPR 2025 | 用非结构化三角形替代 Gaussian 椭球 | ★★ |
| R15 | [TeT-Splatting](https://arxiv.org/abs/2406.01579) | arXiv 2024-06 | 四面体网格 + SDF + tile-based 可微渲染 | ★★ |
| R16 | [SuGaR](https://arxiv.org/abs/2311.12775) | CVPR 2024 | Gaussian → mesh → 精细化 Gaussian 流水线 | ★★ |
| R17 | [2DGS](https://arxiv.org/abs/2403.17888) | SIGGRAPH 2024 | 2D 平面 Gaussian 盘，几何精度大幅优于 3DGS | ★★ |
| R18 | [GOF](https://arxiv.org/abs/2404.10610) | NeurIPS 2024 | 从 Gaussian opacity field 提取高质量 mesh | ★ |
| R19 | [GS-Verse](https://arxiv.org/abs/2510.11878) | arXiv 2025-10 | GaMeS + 物理仿真 → VR 交互 | ★ |
| R20 | [PhysGaussian](https://arxiv.org/abs/2311.12198) | CVPR 2024 | MPM grid 驱动 Gaussian 物理仿真 | ★ |

### 三、Visual SLAM + 3DGS

| Ref | 论文 | 会议/日期 | 关键贡献 | 关键度 |
|-----|------|-----------|----------|--------|
| R21 | [Splat-SLAM](https://arxiv.org/abs/2405.16544) | CVPR 2025 Workshop | **RGB-only + 全局 BA + 可变形 Gaussian map** | ★★★ |
| R22 | [LoopSplat](https://github.com/GradientSpaces/LoopSplat) | 3DV 2025 Oral | **3DGS submap 注册做回环**，PGO | ★★★ |
| R23 | [WildGS-SLAM](https://github.com/GradientSpaces/WildGS-SLAM) | CVPR 2025 | 单目 + 动态环境 + DINOv2 不确定性 | ★★ |
| R24 | [DROID-Splat](https://openaccess.thecvf.com/content/ICCV2025W/NeuSLAM/papers/Homeyer_DROID-Splat_Combining_end-to-end_SLAM_with_3D_Gaussian_Splatting_ICCVW_2025_paper.pdf) | ICCV 2025 Workshop | 端到端 SLAM + 3DGS，含回环 | ★★ |
| R25 | [MonoGS](https://github.com/muskie82/MonoGS) | CVPR 2024 Highlight | 首个纯单目 3DGS SLAM | ★★ |
| R26 | [RTGS](https://arxiv.org/abs/2510.06644) | MICRO 2025 | 硬件加速 3DGS SLAM: ≥30 FPS on edge | ★★ |

### 四、移动端渲染 + 压缩

| Ref | 论文 | 会议/日期 | 关键贡献 | 关键度 |
|-----|------|-----------|----------|--------|
| R27 | [SqueezeMe](https://arxiv.org/abs/2412.15171) | SIGGRAPH 2025 | **唯一 Vulkan splatting pipeline 论文**, Quest 3 72FPS | ★★★ |
| R28 | [Sort-free GS](https://arxiv.org/abs/2410.18931) | ICLR 2025 | 消除排序, weighted sum 替代 alpha blending, 移动端加速 | ★★★ |
| R29 | [LightGaussian](https://arxiv.org/abs/2311.17245) | NeurIPS 2024 | 15x 压缩, pruning + SH 蒸馏 + VQ | ★★ |
| R30 | [LFGS](https://www.sciencedirect.com/science/article/abs/pii/S0097849325001505) | ScienceDirect 2025 | **90x 压缩**, entropy-constrained VQ, 移动端专用 | ★★ |
| R31 | [Virtual Memory for 3DGS](https://arxiv.org/abs/2506.19415) | TVCG (submitted) | JIT GPU streaming, 移动端测试 | ★★ |
| R32 | [Freq-Aware GS Decomp.](https://arxiv.org/abs/2503.21226) | 3DV 2026 | Laplacian 金字塔频率 LOD + 渐进流式 | ★★ |
| R33 | [A LoD of Gaussians](https://arxiv.org/abs/2507.01110) | arXiv 2025-07 | Sequential Point Trees + 外部存储流式 | ★ |
| R34 | [Voyager](https://arxiv.org/abs/2506.02774) | arXiv 2025-06 | 城市级移动端渲染, 6.6x 加速 | ★ |
| R35 | [FlashGS](https://arxiv.org/abs/2408.07967) | CVPR 2025 | 移动 GPU 4x 加速 | ★ |
| R36 | [STREAMINGGS](https://arxiv.org/abs/2506.09070) | arXiv 2025-06 | 体素流式渲染, 移动 Ampere 45.7x 加速 | ★ |
| R37 | [Compact3D](https://arxiv.org/abs/2311.13681) | arXiv 2023-11 | 25x 压缩, learnable mask + grid neural field + VQ | ★ |
| R38 | [Mini-Splatting](https://arxiv.org/abs/2403.14166) | ECCV 2024 | 位置重组织 + 约束数量 Gaussian | ★ |

### 五、质量指标 + 下游验证

| Ref | 论文 | 会议/日期 | 关键贡献 | 关键度 |
|-----|------|-----------|----------|--------|
| R39 | [AutoSplat](https://autosplat.github.io/) | ICRA 2025 | 自动驾驶场景 3DGS | ★ |
| R40 | [NeurIPS 2025 Driving GS](https://neurips.cc/virtual/2025/loc/san-diego/poster/115990) | NeurIPS 2025 | 外观建模 + Bilateral Grid | ★ |

### 六、开源代码库

| 代码库 | 语言 | 许可证 | 用途 |
|--------|------|--------|------|
| [gsplat](https://github.com/nerfstudio-project/gsplat) | CUDA/Python | Apache 2.0 | **Forward+backward 内核参考实现** |
| [gaussian-splatting-cuda](https://github.com/MrNeRF/gaussian-splatting-cuda) | C++/CUDA | Custom | 纯 C++ 3DGS 训练参考 |
| [LoopSplat](https://github.com/GradientSpaces/LoopSplat) | Python | MIT | 回环检测 + submap 注册 |
| [MonoGS](https://github.com/muskie82/MonoGS) | Python | License varies | 单目 GS-SLAM 参考 |
| [SuGaR](https://github.com/Anttwo/SuGaR) | Python | Apache 2.0 | Mesh↔Gaussian 绑定参考 |
| [2DGS](https://github.com/hbb1/2d-gaussian-splatting) | Python/CUDA | Custom | 2D 平面 Gaussian 几何参考 |
| [GOF](https://github.com/autonomousvision/gaussian-opacity-fields) | Python | MIT | Opacity field mesh 提取 |
| [PhysGaussian](https://github.com/XPandora/PhysGaussian) | Python | Apache 2.0 | 物理仿真 + Gaussian 参考 |

---

## 调研中发现的关键空白（= 你的创新机会）

1. **无论文在手机上做完整 3DGS training + rendering 的全流程。** PocketGS 最接近但只覆盖训练。
2. **无论文做在线增量 mesh 生成 + 同时 Gaussian binding。** Mani-GS/GaMeS 的 mesh 都是离线生成的。
3. **无论文明确描述 Metal compute shader 的 3DGS 实现。** Vulkan 只有 SqueezeMe 一篇。
4. **无论文将 Merkle/证据链用于 3D 重建质量验证。** 这是你独有的。
5. **micro-batch 在线优化策略无专门论文。** 这是一个研究空白。
6. **[新增] 无论文在采集开始就同时运行 scaffold 生成 + Gaussian 优化。** 现有方案都是先离线建模后绑定。

**这六个空白的交集就是你的技术护城河。**

---

## 创新模块：Scaffold 统一内核 + 证据链操作系统

> v2.2 新增。基于 7+12 条创新想法的冷静交叉分析。
> 原则：每条都必须 (a) 接得上你现有 C++ 模块 (b) 不增加新的 GPU 实时开销或可证明开销可控 (c) 有明确的 go/no-go 阈值。

### 核心论点

**Scaffold 不只是结构层。它是整个系统的统一操作系统内核。**

```
Scaffold 的多重身份（V1 主线 3 个 + V1.5 灰度 2 个 + V2 研究 2 个）:
├── [V1] 几何骨架 + Gaussian 绑定宿主          (已在计划中)
├── [V1] LOD 层级结构 = Scaffold 三角面积层级    (Scaffold-IS-LOD)
├── [V1] 深度先验场 = Scaffold ray intersection   (Trajectory-as-Depth)
├── [V1.5] 跟踪失锁救援 = 渲染残差反求位姿       (Scaffold-as-Tracker lite)
├── [V1.5] 光照变化检测 = 法向辐照度场           (Scaffold-as-Light-Probe)
├── [V2] 物理仿真骨架                           (已有 PhysGaussian 参考)
└── [V2] 可编辑几何                             (已有 Mani-GS 参考)

证据链的多重身份（V1 主线 3 个 + V1.5 灰度 2 个 + V2 研究 2 个）:
├── [V1] 覆盖率验证 + Merkle 证明               (已在计划中)
├── [V1] 3D Scene Passport / 证书               (Merkle certificate)
├── [V1] 补拍引导 = 低 belief 区域高亮            (已在计划中)
├── [V1.5] 优化调度 = belief 驱动注意力分配       (Evidence Auction lite)
├── [V1.5] 动态物体检测 = 矛盾观测标记            (DS conflict > threshold)
├── [V2] 反事实质量场 = "如果停拍会怎样"          (Counterfactual Quality)
└── [V2] 下游任务导向采集                        (Robot-First Policy)
```

### V1 主线（3 个模块，Phase 1-3 内实现）

#### M1: Scaffold-IS-LOD（Scaffold 即 LOD）

**核心思路:** 不需要独立 LOD 系统。Scaffold 三角形面积层级天然就是 LOD 层级。

```
aether/optimizer/scaffold_lod.h
├── ScaffoldLOD
│   ├── classify_units(scaffold) → per-unit LOD level
│   │   // area > threshold_far → LOD 0 (远景, 绑定少量粗 Gaussian)
│   │   // area > threshold_mid → LOD 1 (中景)
│   │   // else              → LOD 2 (近景, 密集 Gaussian)
│   ├── select_active_gaussians(camera_pose, budget) → GaussianSubset
│   │   // 按视锥 + LOD level 选择
│   └── split_for_refinement(unit, target_area) → TopologyDiff
│       // split 同时完成: 几何细化 + LOD 提升 + 新 Gaussian spawn 区域
```

**对接现有代码:**
- `ScaffoldUnit::area` 字段已存在 → 直接用作 LOD 判据
- `ScaffoldUpdater::split_large_triangles(threshold)` 已在计划中 → split 操作天然产生更高 LOD
- 替代 Phase 5 独立 `LODManager` 的大部分功能 → **减少代码量**

**验证方法:** Phase 1 末在 CPU 上实现，Phase 4 末在 iPhone 上对比 Scaffold-LOD vs 独立 LOD 的帧率/内存/画质。

**go/no-go (Week 10):** 若 Scaffold-LOD 在 10 万 Gaussian 场景下，选择延迟 >2ms → 回退到独立 LOD。

**风险:** 三角面积分布不均匀时，LOD 切换可能产生 popping。需要 LOD 过渡带（相邻 LOD 级别的三角形共享边界 Gaussian 做 opacity 渐变）。

---

#### M2: Trajectory-as-Depth（轨迹即深度先验）

**核心思路:** 不需要深度相机，也不需要重量级神经网络。用 Scaffold ray intersection 提供稠密深度先验。

```
aether/tracking/scaffold_depth.h
├── ScaffoldDepthProvider
│   ├── query_depth(camera_pose, pixel_coords[], count) → depth[], confidence[]
│   │   // 对 Scaffold 已覆盖区域: ray-triangle intersection → 高置信度深度
│   │   // 未覆盖区域: 返回 confidence=0，让多帧三角测量补充
│   ├── build_bvh(scaffold) → BVH
│   │   // 复用你的 SpatialHashTable 做粗阶段加速
│   │   // BVH 只在 scaffold 拓扑变化时重建（增量）
│   └── fuse_depth(scaffold_depth, triangulation_depth) → fused_depth, uncertainty
```

**不确定性通道与冲突处理:**
```
// 每个像素的深度有两个来源，各自带置信度:
scaffold_depth:       confidence = scaffold_unit.belief * (1 - ray_grazing_angle/90°)
triangulation_depth:  confidence = parallax_ratio * feature_match_score

// 融合规则（禁止低置信几何覆盖高置信光度）:
if |scaffold_depth - triangulation_depth| < tolerance:
    fused = weighted_average(两者, 按 confidence 加权)
elif scaffold_confidence > triangulation_confidence * 1.5:
    fused = scaffold_depth   // scaffold 占优
elif triangulation_confidence > scaffold_confidence * 1.5:
    fused = triangulation_depth  // 三角测量占优
else:
    fused = 取更保守者(离相机更远的)  // 不确定时保守
    conflict_count++  // 记录冲突率，用于监控

// 冲突率监控: 若全局冲突率 > 15% → 报警，可能 scaffold 质量不足
```

**对接现有代码:**
- `SpatialHashTable` (Niessner hash) 可直接做空间粗筛 → 缩小 ray intersection 候选集
- 深度融合可复用 `MarzulloFusion` 的加权区间融合思路（多源深度 = 多个区间估计）
- `DSMassFunction::occupied` → 直接作为 scaffold_unit 的 belief 置信度来源
- 不依赖任何 ML 模型 → 纯 C++，确定性，可验证

**验证方法:** Phase 2 中实现。对比: (a) 纯多帧三角测量 (b) Scaffold ray depth (c) 两者融合。用公开数据集深度 GT 评估 RMSE。

**go/no-go (Week 18):** 若融合深度 RMSE 比纯三角测量差或相当 → 降级为仅做稀疏区域的深度估计辅助，不做稠密替代。

**风险:** Scaffold 初期很稀疏（尤其第一帧），ray depth 覆盖率低。因此此模块是**渐进增强**的：Scaffold 越密 → 深度先验越强 → 新 Gaussian 位置越准 → Scaffold 越密。这个正反馈环需要监控收敛性，防止某些区域永远得不到 Scaffold 覆盖。

---

#### M3: 3D Scene Passport（场景证书）

**核心思路:** 你的 Merkle 树 + DS 证据链 + 确定性 JSON + SHA256 组合起来 = 不可篡改的 3D 扫描证书。

```
aether/certificate/scene_passport.h
├── ScenePassport
│   ├── scene_id: Hash32 (Merkle root)
│   ├── timestamp_range: [first_frame_ms, last_frame_ms]
│   ├── coverage: CoverageReport
│   │   ├── total_units: uint32
│   │   ├── observed_units: uint32
│   │   ├── min_views_per_unit: uint32
│   │   └── merkle_root: Hash32
│   ├── quality: QualityReport
│   │   ├── estimated_psnr: float (quantized to 0.1dB)
│   │   ├── estimated_coverage_ratio: float (quantized to 0.01)
│   │   └── unknown_ratio: float
│   ├── device: DeviceAttestation
│   │   ├── device_model: string
│   │   └── capture_session_hash: Hash32
│   └── proof: vector<InclusionProof>
│
├── generate_passport(replay_engine, merkle_tree, scaffold, cloud) → ScenePassport
├── serialize_passport(passport) → canonical JSON string
├── verify_passport(passport, merkle_root) → bool
│   // 任意第三方可验证，不需要原始数据
```

**对接现有代码 — 全部现成:**
- `MerkleTree::inclusion_proof()` → 直接生成 proof
- `EvidenceReplayEngine::export_state_json()` → 覆盖率数据
- `CanonicalJsonValue::make_object()` + `encode_canonical_json()` → 确定性序列化
- `canonical_json_sha256_hex()` → 证书签名
- `DSMassFunction` 的 `occupied/free_/unknown` → 直接作为区域质量置信度

**验证方法:** Phase 3 末实现。生成证书 → 篡改某个 Gaussian → 重新验证 → 必须 fail。

**go/no-go:** 无。这个模块工程风险几乎为零（只是现有模块的 read-only 组合），差异化价值最大。**建议 Phase 1 就开始做骨架。**

**应用场景:** 房产交易 / 保险理赔 / 建筑验收 / 文物保护 / 法庭证据。

**对标:** C2PA 2.2 内容证书标准 + W3C Verifiable Credentials 2.0。未来可做格式对齐。

**签名与密钥管理:**
```
签名算法: Ed25519（compact 64-byte signature，移动端友好）
密钥轮换:
├── 设备本地 Keychain/Keystore 存储签名私钥
├── 轮换周期: 每 90 天自动生成新 keypair
├── 旧 key 保留 180 天（用于验证历史证书）
└── 轮换时自动对最近 30 天证书做 re-sign（双签过渡）

离线验证流程:
├── 1. 导出: passport.json + merkle_proof + signature + public_key
├── 2. 验证方取得 public_key（嵌入证书 / 公钥服务器 / 二维码）
├── 3. Ed25519.verify(signature, canonical_json_sha256, public_key)
├── 4. MerkleTree::verify_inclusion(proof, leaf_hash, root)
└── 5. 全部通过 → 证书有效；任一失败 → 拒绝
```

---

### V1.5 灰度（2 个模块，Phase 4-5 验证后启用）

#### M4: Scaffold-as-Tracker Rescue（跟踪失锁救援）

**核心思路:** 跟踪器置信度下降时（弱纹理/运动模糊），用已有 Scaffold+Gaussian 模型渲染当前估计位姿，与实际帧做光度对比，反推位姿修正。

**冷静限制:** 不做每帧全开（会和渲染/优化抢 GPU 预算），只做**失锁时短窗口触发**。

```
触发条件: tracker.tracking_state == kLimited 且持续 ≥3 帧
执行:
  1. 用当前粗位姿渲染 1/4 分辨率图像 (低成本)
  2. 计算渲染 vs 实际帧的光度残差
  3. 对 camera extrinsics 求梯度（复用 backward pass 对 viewpoint 的偏导）
  4. 梯度下降修正位姿（3-5 次迭代，每次 <0.5ms）
退出: tracker 恢复 kNormal 或迭代收敛
```

**对接现有代码:**
- `IntegrationSkipReason::kTrackingLost` / `kPoseJitter` → 触发条件已有
- Forward rasterizer → 已在 Phase 4 实现
- Backward pass 对 camera extrinsics 的梯度 → gsplat 已有参考实现，需扩展

**验证方法:** Phase 4 末，在弱纹理场景（白墙）上对比: (a) 纯跟踪器 (b) 跟踪器 + rescue。度量: 恢复到 kNormal 的帧数。

**go/no-go (Week 32):** 若 rescue 在 iPhone 15 Pro 上单次迭代 >1ms → 不上线。若恢复帧数无显著改善 → 不上线。

**风险:** 渲染残差反求位姿在"模型本身就不准"时会把位姿往错误方向拉。需要加**置信度门限**：只在 Scaffold 覆盖率 >60% 且 belief >0.7 的区域做 rescue。

---

#### M5: Evidence Auction Scheduler（证据驱动优化调度）

**核心思路:** 用 DS mass function 的 belief/plausibility 驱动 GPU 优化预算分配。

**冷静限制:** 先做 read-only（只读证据数据做调度决策），不反向修改证据参数。

```
aether/scheduler/evidence_scheduler.h
├── EvidenceScheduler
│   ├── rank_units_by_expected_gain(scaffold, evidence_state) → priority_queue
│   │   // expected_gain(unit) = (1 - belief) * plausibility * area
│   │   // 高 expected_gain = 不确定但有潜力 → 优先优化
│   │   // 低 expected_gain = 已收敛或无望 → 跳过
│   ├── allocate_optimization_budget(priority_queue, budget_ms) → unit_budget_map
│   └── detect_anomaly(unit) → bool
│       // conflict = dempster_combine().conflict
│       // if conflict > 0.3 → 可能是动态物体，标记不优化
```

**对接现有代码:**
- `DSMassFunction::occupied` = belief，`1 - occupied - free_` = plausibility → 直接读
- `DSMassFusion::dempster_combine()` 返回 `conflict` → 动态物体检测
- `AdmissionController::check_admission()` → 已有的质量门控可复用
- `compute_shadow_price_micros()` → GCRA 竞价思路可直接改造为 "每 ms 预计质量增益" 竞价

**验证方法:** Phase 5 中 A/B 测试: (a) 均匀分配优化预算 (b) 证据驱动分配。度量: 相同总预算下的 PSNR 收敛速度。

**go/no-go (Week 38):** 若证据驱动 PSNR 收敛速度提升 <10% → 不上线（复杂度不值得）。

**防振荡机制:**
```
// 问题: 如果每帧重新竞价，预算会在区域间来回跳 → 优化器频繁切换 → 都做不好
// 解决: hysteresis（滞回）+ 最短保持窗口

hysteresis:
    // 只有当新排名比当前排名高出 margin 时才切换
    if new_rank - current_rank < rank_margin(3):
        keep current allocation  // 不切换

min_hold_window:
    // 每个区域获得预算后，至少持有 N 个优化步才能被抢占
    min_hold_steps = 5  // 约 5 个 micro-batch
    if steps_since_allocation < min_hold_steps:
        skip reallocation for this unit

cooldown:
    // 被降级的区域在 M 步内不能重新竞价
    cooldown_steps = 10
```

**风险:** evidence_state 更新延迟。如果优化器在证据更新之前就分配了预算，可能做无用功。需要保证证据更新和调度之间的因果顺序。

---

### V2 研究（2 个模块，仅离线实验，不进实时链路）

#### M6: Gaussian Inheritance（守恒继承）

**核心思路:** prune 时不直接删除，做"守恒继承"——把被删 Gaussian 的 SH/opacity/观测统计按距离权重传给邻居。

**为什么只能是 V2:**
- 没有直接文献先例，最可能引入色偏累积
- 继承操作在 100k Gaussian 下的计算开销未知
- 需要大量 A/B 测试验证"继承后质量 ≥ 不继承"

**验证方法:** 离线: 取 Phase 3 输出的 Gaussian cloud → 做 50% pruning with inheritance vs without → 对比 PSNR。

**go/no-go:** 若 inheritance 版 PSNR 提升 <0.5dB → 放弃。

#### M7: Counterfactual Quality Field（反事实质量场）

**核心思路:** 实时显示"如果现在停止采集，各区域的预计质量下降"。

**为什么只能是 V2:**
- 需要对每个区域做 "假设移除后续观测" 的质量预测 → 计算量与区域数成正比
- 预测模型需要离线训练/标定
- 用户交互设计未确定

**验证方法:** 离线: 对已完成的采集，逐帧截断 → 度量截断后质量 → 拟合预测模型。

---

### 硬性止损线（写入 Go/No-Go）

| Week | 条件 | 触发动作 |
|------|------|----------|
| 4 | iPhone 15 Pro 上 10k Gaussian forward+backward+相机预览 **不能稳定** | 降级: 放弃第一帧即 Scaffold，改回"延迟 Scaffold"策略 |
| 10 | kHot 状态占比持续 **>30%**（20 分钟采集） | 暂停追求 S5-RT，优先保证 S5-Deferred |
| 18 | 第一帧 Scaffold 在弱纹理场景 **连续 5 场景失败** | 启用"局部延迟 Scaffold"：弱纹理区域暂不生成 Scaffold，等观测积累 |
| 32 | p95 帧时延 **无法压到 16.6ms** | 冻结策略并行，锁定单冠军策略，剩余预算全给优化 |
| 38 | 任何 V1.5 模块 go/no-go **未通过** | 直接砍掉，不带入 Phase 6 |

### 绑定阈值自适应（替代硬编码 <3/<10）

> **[修正]** `observation_count < 3` / `< 10` 过于硬编码。改为场景自适应：

```
// 不再硬编码阈值
// 改为基于当前 scaffold 全局统计的百分位数:
soft_threshold = max(3, percentile_25(all_gaussian_observation_counts))
hard_threshold = max(10, percentile_75(all_gaussian_observation_counts))

// 效果:
// 快速扫描 (少观测): 阈值低，快速锁定
// 精细扫描 (多观测): 阈值高，更谨慎
// 避免一刀切
```

### 多策略并行范围控制

> **[修正]** 当前 6 处并行策略（0-A 三方案、0-B 三方案、binding 三方案、optimizer 四方案、densification 四方案、sort 三方案）= 最多 20 个并行实现，容易失控。

**范围控制规则:**
```
Phase 0: 允许每个门禁跑满 3 方案（总共 6 个，4 周内完成 → 可控）
Phase 1: 每个策略点 2 方案上限（砍掉 Phase 0 中最差的 1 个）
Phase 2-3: 每个策略点保留 2 方案，Phase 3 末锁定冠军
Phase 4 起: 每个策略点只保留 1 个冠军 + 1 个备选（热备）
```

**决策机制:** 每个 Phase 结束时开"选型评审会"，用统一 benchmark 数据做硬决策，不允许"再多试一个 Phase"。

---

**这六个空白 + 七个 Scaffold 身份 + 七个证据链身份的交集 = 你的技术护城河。**
**对手要抄某一个功能很容易，但要抄这种统一内核架构，他们得从头重写整个系统。**

---

## 版本变更记录

### v2.0 (2026-02-18)

**架构变更:**
- 移除两段式冷启动 A/B 阶段，改为从第一帧起即时 Scaffold + Gaussian 并行运行
- 绑定强度改为 per-Gaussian 观测次数驱动，无全局 phase 切换
- 全流程贯穿多重前沿方案交叉验证 + 自研混合

**8 项修正:**
1. [+] Phase 0-D: Device Spike 真机最小内核验证（解决 host≠device 风险）
2. [~] Phase 0-A: 双口径测试（理想位姿 + 在线噪声注入）
3. [~] Phase 0-B: 双档压力模型（10k + 100k，含 p50/p95/p99 统计）
4. [~] Phase 5.2: 帧预算改为硬预算+弹性预算，预留 3.5ms 系统开销
5. [~] Phase 5.3: 热稳定性改为"允许降频，画质/帧率不跌破下限"
6. [~] Phase 6.2: S5 覆盖率增加硬门槛（标准 ≥85%，大空间 ≥75%，弱纹理 ≥60%）
7. [+] Phase 5.5: 统一测量协议（GT 采集、标定流程、统计口径、场景清单）
8. [~] 总排期加 20% 风险缓冲（48w → 56w），GPU 后端 + 平台壳前置并行

### v2.1 (2026-02-19)

**平台策略变更:**
- iOS 首发实跑，Android/Harmony 留接口（编译验证，不上机）
- Phase 0-C (Harmony Vulkan) 延后到有设备时执行，不阻塞 Go/No-Go
- Phase 0-D 缩减为 iOS 真机验证 + Vulkan SPIR-V 编译验证
- Phase 4/6 交付物以 iOS 为主，Vulkan 保持编译通过

**测试策略变更:**
- 移除固定 5 场景清单（SCN-01~05）
- 改为: 回归基线（随手拍 + 公开数据集）+ 持续积累压力测试集
- 算法必须自适应任意真实环境，不为固定场景特化
- 覆盖率门槛的场景类型由系统自动判定

### v2.2 (2026-02-19)

**创新模块分层:**
- [+] Scaffold 统一内核理论：7 重身份定义
- [+] 证据链操作系统理论：7 重身份定义
- [+] V1 主线 3 模块: Scaffold-IS-LOD / Trajectory-as-Depth / 3D Scene Passport
- [+] V1.5 灰度 2 模块: Scaffold-as-Tracker Rescue / Evidence Auction Scheduler
- [+] V2 研究 2 模块: Gaussian Inheritance / Counterfactual Quality Field
- 每个模块有明确 go/no-go 阈值和对接现有代码的具体路径

**风控加固:**
- [+] Phase 0 硬性止损线（3 条）
- [+] 全生命周期止损线（Week 4/10/18/32/38）
- [~] 绑定阈值: 硬编码 <3/<10 → 基于全局百分位数的自适应阈值
- [+] 多策略并行范围控制: Phase 0 = 3 方案 → Phase 1 = 2 → Phase 3 末 = 1 冠军 + 1 热备
- [~] Phase 0 交付物修正（消除"三平台真机"表述不一致）

**你的 7+12 条想法逐条分析结果:**

| 想法 | 分层 | 现有代码对接 | 冷静判断 |
|------|------|-------------|----------|
| Scaffold-IS-LOD | V1 | ScaffoldUnit::area 直接用 | **做，替代独立 LOD** |
| Trajectory-as-Depth | V1 | SpatialHashTable + MarzulloFusion | **做，渐进增强** |
| 3D Scene Passport | V1 | MerkleTree + ReplayEngine + CanonicalJson + SHA256 全现成 | **做，Phase 1 起** |
| Scaffold-as-Tracker | V1.5 | IntegrationSkipReason + forward/backward 已有 | 仅失锁触发，go/no-go Week 32 |
| Evidence Auction | V1.5 | DSMassFunction + GCRA 可改造 | read-only 先行，go/no-go Week 38 |
| Scaffold-as-Light-Probe | V1.5→V2 | ScaffoldUnit::normal + SH DC 分量 | 暂不进实时，可做 pause 模式 |
| Gaussian Inheritance | V2 | BindingManager 邻居查询已有 | 离线 A/B 验证，go/no-go PSNR ≥0.5dB |
| Counterfactual Quality | V2 | DSMassFunction 可扩展 | 离线预测模型，用户交互未定 |
| Proof-Carrying Pixel | V2 | 需要 per-pixel Merkle → 开销未知 | 研究，不进 V1 |
| Geometry Parliament | = 多策略并行 | 已在范围控制规则中 | 就是现有方案竞争机制 |
| Render-as-Tracker | = M4 的别名 | 同 M4 | 合并到 V1.5 M4 |
| Evidence Auction | = M5 的别名 | 同 M5 | 合并到 V1.5 M5 |
| Temporal Branching | 类似 git branch for optimizer | 内存开销 ×N | V2 研究 |
| Robot-First Capture | 需要下游任务定义 | 独立研究课题 | V2+ |
| Lighting Branches | 需要多光照分支管理 | 内存开销显著 | V2 研究 |
| 3D Scene Passport | = M3 的扩展 | 同 M3 | 合并到 V1 M3 |

### v2.3 (2026-02-19)

**量化决策与运行时安全网:**
1. [+] 冠军选择量化公式: Score = 0.35*quality + 0.30*latency + 0.15*thermal + 0.10*memory + 0.10*stability，领先 <5% 则双跑延长 2 周
2. [+] Phase 0-D 补充 S5-Deferred 机型（iPhone 12/13）spike 测试，最晚 Week 10
3. [+] Scaffold 首帧运行时自动回退: tracking_confidence < 0.3 连续 ≥5 帧 → 自动延迟 scaffold；sparse_point < 50 连续 ≥10 帧 → 降频
4. [+] Trajectory-as-Depth 增加 uncertainty channel + 冲突率监控（>15% 报警）
5. [+] Evidence Auction 增加 hysteresis + min_hold_window + cooldown 防振荡

**安全与可信:**
6. [+] Scene Passport 签名规范: Ed25519 签名 + 90 天密钥轮换 + 离线验证五步流程

**测量与验收加固:**
7. [~] 统计口径: 关键指标（PSNR/SSIM/ATE/帧率）每场景 10 次 → 95% CI；其余 ≥3 次
8. [+] Phase 6 S5-RT 增加 crash-free 会话率: 10min ≥99.0%, 20min ≥97.0%
9. [+] 下游驾驶仿真指标扩展: +detection mAP + segmentation mIoU + trajectory prediction ADE

**文档一致性:**
- [~] TOC Phase 6 标题: "三平台集成" → "iOS 首发集成 + S5 验收（Android/Harmony 留接口）"
- [~] 时间线表: "三平台 S5 验收" → "iOS 首发 S5 验收"

### v2.6.1 (2026-02-19)

**F1 时间之镜重写:**
- [~] B.1 完全重写: 从"per-Gaussian fade-in 时间遮罩"升级为"scaffold 碎片刚体飞行渲染"
- [+] 渲染单元明确定义: 碎片 = ScaffoldUnit + 绑定的 GaussianPrimitive 组
- [+] 飞行起点: 从 CameraTrajectory 反查拍摄时相机位姿 → 碎片从拍摄方向飞出
- [+] 渲染方式: 不改渲染器内核 → 送入前对 Gaussian 做刚体变换 → 零侵入
- [+] 拼缝处理: Gaussian alpha blending 天然无缝 → 不需要额外代码
- [+] SH 旋转简化: 飞行中用 0 阶 DC → 归位时 crossfade 到完整 SH
- [+] 新增 f1_time_mirror.h 接口设计: CameraTrajectoryEntry, FragmentFlightParams
- [+] 性能分析: 每 Gaussian ~20 FLOP 变换 → 100k GS < 0.05ms → 可忽略
- [+] 4 项风险 + 缓解措施

---

### v2.6 (2026-02-19)

**产品流程修正:**
- [~] 三状态 GPU 调度器 → 二状态（拍摄中/拍摄结束）。产品无"暂停查看"环节
- [+] 明确产品流程: 拍摄 → 完成 → 全力优化 → 破镜重圆展示

**颠覆性创新最终定稿 (8 轮反馈后):**
- [+] F1 时间之镜: 破镜重圆渲染 = 按拍摄时间线重演世界生成 + 黑洞补拍引导
- [+] F3 可信云存储: 证据驱动存储分层 + Scene Passport 随模型上传 + 云端审计链
- [+] F5 活化石链: 吸收 F9 水印 + M3 Scene Passport → 时间线 + 身份证 + 防篡改三合一
- [+] F6 幽灵层: DS conflict 时间戳驱动动态检测 → 运动轨迹提取 → 可视化
- [×] F2 碰撞体: 手机 App 无 AR 碰撞场景 → 淘汰
- [×] F4 主动补拍: 被 F1 黑洞机制自然替代 → 淘汰
- [×] F7 双脑渲染: 产品无暂停查看环节 → 淘汰
- [×] F8 不确定性场: 被 F1 黑洞 + M5 证据调度覆盖 → 淘汰
- [×] F9 水印: 合并进 F5 → 淘汰（功能保留）

**X+Y 算法策略全覆盖:**
- [+] F1/F3/F5/F6 每个模块: X₁/X₂/X₃ 竞品方案 + Y 独有方案
- [+] M1/M2/M4/M5/M6/M7 补充 B 维度 X+Y 策略

**护城河升级:**
- [~] 9 → 11 项技术空白（新增: evidence 驱动动态轨迹提取 + 拍摄时间序列渲染）

---

### v2.4 (2026-02-19)

**全球前沿调研:**
- [+] 附录 A: 20+ 次多语言搜索，覆盖 CVPR/ICCV/ICLR/SIGGRAPH/NeurIPS/AAAI/RSS/MICRO 2025-2026
- [+] 竞争格局更新: PocketGS (移动训练)、Mobile-GS (无排序渲染)、Metal 4 ShaderML、glTF 标准化
- [+] 20 条新创新想法 (W1-W20)，含工程路径、go/no-go、冷静分析
- [+] V1 直接纳入: W2 渐进式压缩 + W11 Scaffold 碰撞体
- [+] V1.5 灰度: W1 ShaderML + W4 不确定性场 + W10 水印
- [+] V2 研究: W3 持续学习 + W5 物理感知 + W6 语义 + W8 逆渲染
- [+] 明确不做: Diffusion 填充（与"不造假"冲突）、4D 时序（内存过重）、脉冲相机（无硬件）
- [+] 护城河验证: 7 个技术空白经搜索确认仍全球唯一

### v2.5 (2026-02-19)

**创新管线合并决策:**
- [+] W1-W20 (调研想法) + U1-U8 (用户精筛) 合并去重 → 最终 22 条 (F1-F19 + 3 条不做)
- [+] 第一档 V1 直接纳入 3 条: F1 渐进压缩 + F2 碰撞体 + F3 证据约束压缩
- [+] 第二档 V1.5 灰度 6 条: F4 主动补拍 + F5 补丁链 + F6 冲突剔除 + F7 ShaderML + F8 不确定性 + F9 水印
- [+] 第三档 V2 研究 6 条: F10 双轨引擎 + F11 rebinding 最小化 + F12 物理FEM + F13 PBR + F14 语义 + F15 自蒸馏
- [+] 第四档 V2+ 远期 4 条: F16 Task-LOD + F17 联邦 + F18 IMU + F19 机器人
- [+] M1-M7 与 F1-F19 映射关系固化
- [+] 护城河升级: 7 → 9 项空白 (新增: 证据约束压缩 + Merkle delta chain)
- 核心结论: "不是再扩创意库，而是把'创意→门槛→回退'闭环做硬"

---

## 附录 A — 2025-2026 全球前沿调研 + 狂野创新冷静分析

> v2.3 新增。基于 20+ 次多语言（中英）搜索，覆盖 CVPR/ICCV/ICLR/SIGGRAPH/NeurIPS/AAAI/RSS/MICRO 2025-2026 论文。
> **分析原则:** 每条想法必须回答三个问题：(1) 与 Aether3D 现有架构的匹配度 (2) 移动端可行性 (3) 值不值得占你的工程带宽。
> 评级说明: ★★★ = 强烈推荐纳入, ★★ = 值得追踪/灰度, ★ = 仅关注/V3+

---

### A.1 已确认的竞争格局变化（你必须知道的）

| 事件 | 时间 | 影响 |
|------|------|------|
| PocketGS: 手机上完整 3DGS 训练 <5min, <3GB | 2026.01 | **你不再是唯一做移动端训练的人。但 PocketGS 无 scaffold、无 SLAM、无证据链。** |
| Mobile-GS: 无排序渲染 + SH 蒸馏 + 量化剪枝 (ICLR 2026) | 2026 | 推理端优化值得借鉴，但没有训练能力 |
| Neo: 硬件级排序加速 (ASPLOS 2026) | 2026 | 系统层优化，与你的 Metal compute 方案互补 |
| Metal 4 + MTLTensor + ShaderML (WWDC 2025) | 2025.06 | **Apple 官方支持 shader 内嵌 ML 推理。你的 Metal compute 路径得到平台级加持。** |
| glTF + KHR_gaussian_splatting 标准化 | 2025.08 | 导出格式已有标准，Scene Passport 可对齐 |
| PCGS 渐进式压缩 (AAAI 2026 Oral) | 2026 | 自适应码率控制，与你的 LOD 系统天然契合 |
| LapisGS 分层流式传输 (3DV 2025 Best Paper) | 2025 | 流式架构参考，Voyager 城市级 100× 压缩 |
| Waymo World Model (Genie 3) | 2026.02 | 生成式世界模型是 3DGS 的互补而非替代 |

**冷静判断:** PocketGS 证明了移动端训练的可行性，但你的护城河在于 **scaffold+Gaussian 同时在线运行 + 证据链 + Merkle 证书**，这个组合仍然全球唯一。Metal 4 的 MTLTensor/ShaderML 是意外利好——你可以在 fragment shader 里直接跑微型 NN 做纹理解压或 Gaussian 属性预测。

---

### A.2 新增狂野创新想法（20 条，按与你架构的匹配度排序）

---

#### W1: Metal 4 ShaderML 纹理神经解压 ★★★

**来源:** WWDC 2025 Metal 4 — 在 shader 内嵌入微型 NN 做纹理解压，比块压缩再省 50% 显存。

**核心思路:** 不存储每个 Gaussian 的完整 SH 系数，而是训练一个 tiny MLP (< 1KB weights)，输入 Gaussian 位置 + 视角 → 输出颜色。推理直接在 fragment shader 里通过 ShaderML 完成。

**与 Aether3D 匹配度:** 极高。你的 scaffold 已经提供了空间结构，可以做 scaffold-local MLP（每个 scaffold region 一个微型 NN）。这比全局 SH 更紧凑，且 Apple 官方提供了硬件加速路径。

**工程路径:**
```
Phase 4 (Metal 后端) 增加实验分支:
├── 训练: 每个 scaffold_unit 学一个 tiny MLP (4层×16宽, <512 params)
├── 推理: MTL4MachineLearningCommandEncoder 或 ShaderML inline
├── go/no-go: 相比 SH DC+1阶, 显存减少 ≥30% 且 PSNR 差 <0.5dB
└── 风险: ShaderML 延迟未知，需 Phase 0-D spike 验证
```

**冷静限制:** ShaderML 是 WWDC 2025 新 API，文档和最佳实践还很少。必须在 Phase 0-D 实测。

---

#### W2: 渐进式码率自适应 (PCGS + LapisGS 融合) ★★★

**来源:** PCGS (AAAI 2026 Oral) + LapisGS (3DV 2025 Best Paper) + Voyager 流式系统。

**核心思路:** 你的 Scaffold-IS-LOD 已经提供了层级结构。加上渐进式编解码，可以做到：网速好 → 传全精度，网速差 → 只传 LOD 0 骨架。本地存储也同理：内存紧张时只加载近景 LOD 2。

**与 Aether3D 匹配度:** 极高。你的 scaffold LOD level 天然就是渐进层级的锚点。

**工程路径:**
```
Phase 5 (LOD/压缩) 自然包含:
├── scaffold LOD level → 渐进层级映射
├── 每层 Gaussian 属性用 feature plane + video codec (CodecGS 思路, ICCV 2025)
├── 导出格式对齐 glTF KHR_gaussian_splatting
└── go/no-go: 渐进 3 层总存储 < 全精度 30%, PSNR 最高层 <0.3dB 差
```

**冷静限制:** 编解码延迟可能影响实时交互。先做离线导出，再考虑实时流式。

---

#### W3: 持续学习 — 场景更新不遗忘 ★★★

**来源:** GaussianUpdate (ICCV 2025) + CL-Splats (ICCV 2025) + REACT3D (MICRO 2025)。

**核心思路:** 用户第一次扫描了房间，一个月后家具搬了。再次扫描时，只更新变化区域，保留不变部分的 Gaussian。你的 evidence chain 天然能检测哪些区域 belief 变了（新观测与旧 Gaussian 冲突）。

**与 Aether3D 匹配度:** 极高。你已有的 `EvidenceReplayEngine` + `DSMassFunction` 可以做 change detection。Scaffold 的空间划分提供了 local update boundary。CL-Splats 的"无灾难性遗忘"策略可以直接借鉴。

**工程路径:**
```
V1.5 → V2 新模块 M8: IncrementalUpdate
├── change_detector: DS belief conflict > threshold → 标记变更区域
├── local_optimizer: 只对变更区域的 scaffold units 重新优化
├── history_manager: 旧 Gaussian 快照 + 新 Gaussian 增量
├── 对接: EvidenceReplayEngine::export_state_json() 做 diff
└── go/no-go: 更新 ≤20% 面积时, 全场景 PSNR 下降 <0.5dB
```

**冷静限制:** 需要可靠的重定位（第二次扫描如何与第一次对齐）。这依赖 SLAM 回环质量。

---

#### W4: View-Dependent 不确定性场 ★★★

**来源:** View-Dependent Uncertainty (arXiv 2025) + PUP 3D-GS (CVPR 2025) + SA-ResGS (2025)。

**核心思路:** 给每个 Gaussian 加一个不确定性 SH（和颜色 SH 同构）。某视角看过很多次 → 低不确定性；某视角从未观测 → 高不确定性。这比你现在的 DS belief 更精细（DS 是 per-scaffold-unit，这是 per-Gaussian-per-view）。

**与 Aether3D 匹配度:** 极高。(1) 补拍引导: 渲染不确定性热力图比 DS coverage map 更精准 (2) 优化调度: 不确定性高的 Gaussian 优先分配 GPU 预算 (3) LOD: 不确定性高的区域渲染时自动降级，避免用户看到低质量假象。

**工程路径:**
```
Phase 3 增加实验:
├── per-Gaussian uncertainty SH (0阶即可, 1 float per Gaussian)
├── 更新规则: 每次观测到 → uncertainty *= decay; 未观测 → 不变
├── 渲染时: uncertainty > threshold → 半透明/虚线标记
├── 与 DS belief 融合: final_confidence = α * ds_belief + (1-α) * (1-uncertainty)
└── go/no-go: 补拍引导精度 ≥ DS-only baseline
```

**冷静限制:** 多 1 float/Gaussian 的内存开销。100k Gaussian = 100KB，可接受。

---

#### W5: 物理感知 Gaussian — Scaffold 即物理骨架 ★★

**来源:** PIDG (AAAI 2026) + PhysTwin (ICCV 2025) + Physically Embodied Gaussian Splatting (CoRL 2024)。

**核心思路:** 你的 scaffold 不只是几何骨架——它可以是物理仿真骨架。每个 scaffold vertex 带质量和弹性参数，Gaussian 跟着 scaffold 做物理变形。用途：(1) 扫描结果可做碰撞检测 (2) AR 物体放置时有物理约束 (3) 结构工程验收。

**与 Aether3D 匹配度:** 中高。Scaffold 天然有三角/四面体结构，可直接作为 FEM mesh。但实时物理仿真在手机上有额外 GPU 开销。

**工程路径:**
```
V2 研究模块:
├── scaffold vertex → FEM node (mass, Young's modulus)
├── scaffold edge → spring/beam element
├── Gaussian deformation = scaffold 变形的插值
├── 用途: AR 碰撞、建筑验收模拟、结构应力可视化
└── go/no-go: 简单碰撞检测 < 2ms/frame on iPhone 15 Pro
```

**冷静限制:** V2+ 方向。手机端实时物理仿真只能做简化版（刚体碰撞可以，软体不行）。

---

#### W6: 语义 Gaussian — 每个 Gaussian 带语言标签 ★★

**来源:** 3D VL-GS (ICLR 2025) + Identity-aware LangSplat (ICCV 2025) + 4D LangSplat (CVPR 2025) + SceneSplat++ (NeurIPS 2025)。

**核心思路:** 每个 Gaussian 存储一个 CLIP 语义向量（或其压缩版）。用户可以用自然语言查询 3D 场景："找到红色沙发" → 对应 Gaussian 高亮。

**与 Aether3D 匹配度:** 中。(1) 移动端跑 CLIP 编码器有性能压力 (2) 每 Gaussian 加一个语义向量增加显著内存 (3) 但差异化价值极大——"可搜索的 3D 扫描"是杀手级功能。

**工程路径:**
```
V2 研究模块:
├── 离线: 用 MobileCLIP 或蒸馏版 CLIP 生成 per-pixel 语义
├── 投影: 2D 语义 → 3D Gaussian（按可见性加权平均）
├── 存储: PCA 压缩到 16 维 (vs CLIP 512维)，每 Gaussian +64 bytes
├── 查询: 用户文本 → CLIP embed → cosine similarity → 高亮匹配 Gaussian
└── go/no-go: top-5 retrieval accuracy ≥80% on ScanNet scenes
```

**冷静限制:** 内存开销大。100k Gaussian × 64 bytes = 6.4MB 额外。先做离线生成，暂不做实时。

---

#### W7: 事件相机融合 — 极端运动鲁棒性 ★★

**来源:** EventSplat (CVPR 2025) + IncEventGS (CVPR 2025) + EF-3DGS (NeurIPS 2025 Spotlight)。

**核心思路:** 当用户快速甩动手机（传统帧模糊严重）时，事件相机提供微秒级运动信息来救场。虽然 iPhone 目前没有事件相机，但其思路——**连续时间运动模型 + 帧间插值**——可以用 IMU 数据近似。

**与 Aether3D 匹配度:** 中。iPhone 没有事件相机硬件，但 EF-3DGS 的"事件辅助帧间连续监督"思路可以改造为"IMU 辅助帧间连续监督"。你的 Scaffold-as-Tracker Rescue (M4) 已经在做类似的事情。

**工程路径:**
```
V1.5 M4 增强:
├── 将 EF-3DGS 的 Event Generation Model 概念替换为 IMU Motion Model
├── 帧间用 IMU 积分生成 virtual pose → 从 scaffold+GS 渲染 → 光度损失
├── 等效于"没有事件相机的 EF-3DGS"
└── go/no-go: 快速甩动场景 ATE 改善 ≥20% vs 纯视觉
```

**冷静限制:** IMU 累积漂移。短窗口（<100ms）内可信，长窗口不行。

---

#### W8: 逆渲染 — Gaussian 即材质载体 ★★

**来源:** SVG-IR (CVPR 2025) + GeoSplatting (ICCV 2025) + RTR-GS (2025) + GI-GS (ICLR 2025)。

**核心思路:** 每个 Gaussian 不只存颜色 SH，还存 albedo + roughness + metallic (PBR 材质参数)。配合 scaffold normal，可以做重光照：换一个光源，场景渲染自动变化。

**与 Aether3D 匹配度:** 中。Scaffold normal 已有，是逆渲染的基础。但 PBR 分解在手机上的计算量大（需要 Monte Carlo 采样或 split-sum 近似）。且光照分解的歧义性在单目扫描中很高。

**工程路径:**
```
V2 研究模块:
├── 每 Gaussian 增加 3 个 PBR 参数 (albedo RGB + roughness + metallic)
├── 利用 scaffold normal 做 split-sum 近似渲染
├── 训练时联合优化: L_render + L_pbr_regularization
├── 应用: 重光照、AR 虚实融合光照一致性
└── go/no-go: relighting 场景 PSNR ≥25dB, 额外渲染开销 <3ms
```

**冷静限制:** 单目 + 未知光照 → 材质分解歧义大。需要 diffusion prior (如 2D Material Diffusion to 3D Gaussians, Feb 2026) 辅助。移动端 V2+ 方向。

---

#### W9: 联邦式多设备协同建图 ★★

**来源:** Fed3DGS (2024) + MAGiC-SLAM (CVPR 2025) + Nebula 城市级协同渲染。

**核心思路:** 多人拿着手机从不同角度扫描同一个大空间。每台设备做本地 3DGS 训练，中心节点做联邦聚合。你的 Merkle 证书可以用来验证每台设备贡献的数据真实性。

**与 Aether3D 匹配度:** 中高。你的 Scene Passport + Merkle proof 天然可以做设备贡献溯源和防篡改。但联邦学习的通信开销和场景对齐是难题。

**工程路径:**
```
V2+ 研究方向:
├── 每设备: 本地 scaffold + GS + evidence chain
├── 聚合: 上传 scaffold vertices + compressed GS delta + Merkle proof
├── 服务端: 对齐多设备 scaffold → 融合 GS → 验证 Merkle
├── Scene Passport: 多方签名 = 每台设备的 Ed25519 签名
└── go/no-go: 2 台 iPhone 协同扫描, PSNR ≥ 单机 95%
```

**冷静限制:** 需要可靠的跨设备位姿对齐。LAN 环境可控，WAN 太慢。先做 2 台设备 PoC。

---

#### W10: 3DGS 水印 — 知识产权保护 ★★

**来源:** Mark3DGS (2025) + WATER-GS (2025) + NGS-Marker (2025) + CompMarkGS (2025) + GaussianSeal。完整综述: IP Protection for 3DGS Survey (arXiv Feb 2026)。

**核心思路:** 你的 3D 扫描结果是有价值的数字资产。在 Gaussian 属性里嵌入不可见水印，即使被截取、压缩、部分裁剪，都能验证所有权。

**与 Aether3D 匹配度:** 高。你已有 Merkle proof 做完整性验证，水印补充了**所有权验证**。组合起来 = 完整的 3D 数字资产保护链（完整性 + 所有权 + 防篡改）。

**工程路径:**
```
V1.5 M3 扩展 (Scene Passport 增强):
├── 训练时: 在 Gaussian opacity/SH 低位嵌入 watermark bits
├── 方法: 参考 NGS-Marker 的梯度渐进注入策略
├── 验证: 从任意局部区域提取 → 解码 → 匹配 device_id
├── 与 Scene Passport 集成: passport.watermark_hash = SHA256(watermark)
└── go/no-go: 提取准确率 ≥99%, 渲染 PSNR 下降 <0.3dB
```

**冷静限制:** 水印和压缩有冲突（压缩会破坏水印）。CompMarkGS 专门解决了这个问题，可参考。

---

#### W11: Scaffold-as-Collision-Mesh — AR 即时碰撞 ★★

**来源:** SAGE-3D (Feb 2026) + Physically Embodied GS (CoRL 2024)。

**核心思路:** 你的 scaffold 三角/四面体 mesh 直接作为 AR 碰撞体。虚拟物体放到扫描场景中时，碰到 scaffold 面就停下。零额外计算——scaffold 已经存在了。

**与 Aether3D 匹配度:** 极高。几乎零工程成本。Scaffold mesh → 导出为 SCNPhysicsBody (iOS SceneKit) 或 RealityKit CollisionComponent。

**工程路径:**
```
Phase 6 iOS 集成增加:
├── scaffold_to_collision_mesh() → 三角面列表
├── iOS: RealityKit ShapeResource.generateConvex(from: mesh)
├── 每帧更新: scaffold 增长 → collision mesh 增量更新
└── go/no-go: AR 物体碰撞检测延迟 < 1ms
```

**冷静限制:** 近乎免费。唯一风险是 scaffold 精度不够导致碰撞穿模，但对 AR 体验够用。

---

#### W12: 机器人 — 3DGS 作为操作先验 ★

**来源:** GaussianGrasper (RA-L 2024) + RoboSplat (RSS 2025) + ManiGaussian + SplatSim (ICRA 2025)。

**核心思路:** 你扫描的 3D 场景可以直接给机器人做抓取规划。每个 Gaussian 的法向 + 语义 = 抓取点候选。RoboSplat 证明了 1-shot demo + 3DGS 数据增强 = 87.8% 抓取成功率。

**与 Aether3D 匹配度:** 低→中。这是下游应用，不是你核心系统的一部分。但 "可被机器人使用的 3D 扫描" 是重要的差异化故事。

**冷静限制:** V3+ 方向。需要机器人硬件，不在手机端。但可以先做数据格式兼容。

---

#### W13: Diffusion Prior 修复缺失区域 ★

**来源:** Lyra (ICLR 2026, NVIDIA) + DiffSplat (ICLR 2025) + DiET-GS (CVPR 2025)。

**核心思路:** 用户没扫到的区域，用预训练 diffusion model 生成 "合理猜测" 来填充。Lyra 证明了 video diffusion → 3DGS 蒸馏的可行性。

**与 Aether3D 匹配度:** 低。(1) 你的核心原则是"不补假纹理"——evidence chain 明确标记 observed vs unknown (2) Diffusion model 在手机端跑不了（需要云端） (3) 生成内容与"可验证3D扫描"矛盾。

**冷静判断:** **不做。** 这与你的"不造假"核心价值直接冲突。保持 unknown 区域诚实标记。如果用户需要填充，告诉他们去补拍。

---

#### W14: 4D 时序 — 记录场景随时间的变化 ★

**来源:** 4D-GS (CVPR 2024) + TVGS (IET 2026) + Anchored 4DGS (SIGGRAPH Asia 2025) + MEGA (ICCV 2025)。

**核心思路:** 不只记录空间，还记录时间。每个 Gaussian 有 temporal opacity / deformation，可以回放场景在不同时间的状态。

**与 Aether3D 匹配度:** 中→低。你的系统是 static scene reconstruction。4D 需要额外 temporal 参数（内存 ×2~5×）。但"场景时间线"功能有商业价值（施工进度对比、装修前后对比）。

**冷静限制:** V3+ 方向。内存开销太大（MEGA 专门解决这个问题但仍然显著）。且与你的 IncrementalUpdate (W3) 有重叠——W3 更轻量。

---

#### W15: GS-SLAM 二阶优化加速 ★

**来源:** FSGS (ICLR 2026 submission) — Stochastic Local Newton 优化。

**核心思路:** 用 per-parameter 二阶信息（类似 Adam 但更精确）加速 Gaussian 参数收敛。声称显著改善了百万级参数的优化速度。

**与 Aether3D 匹配度:** 中。你的在线优化需要快速收敛（帧预算有限）。二阶方法可能帮助在更少迭代内收敛到更好的解。但每个参数的 Hessian 近似增加内存。

**冷静限制:** 需要验证在 micro-batch 设置下是否仍然有效。可在 Phase 1 optimizer 对比中加一个候选。

---

#### W16: 自动驾驶 3DGS 资产迁移 (AstroSplat) ★

**来源:** AstroSplat (2025) — per-Gaussian decoder 实现跨场景资产迁移，比 SplatAD 好 10^4×。

**与 Aether3D 匹配度:** 低。自动驾驶专用。但"跨场景资产迁移"的思路可以借鉴：用户扫描的家具可以移到另一个场景中。

---

#### W17: Spike Camera Array 高速动态 ★

**来源:** Spike4DGS (NeurIPS 2025) — 脉冲相机阵列 + 4DGS。

**冷静判断:** 硬件不存在于手机端。纯学术参考。

---

#### W18: 3DGS 作为事件流合成器 (GS2E) ★

**来源:** GS2E (NeurIPS 2025 D&B) — 用 3DGS 生成合成事件流做数据增强。

**冷静判断:** 有趣但离你的核心用例太远。

---

#### W19: 主动扫描规划 (Next-Best-View) ★

**来源:** SA-ResGS (2025) + HGS-Planner (ICRA 2025)。

**核心思路:** 基于不确定性估计，自动建议用户下一步该扫哪里。

**与 Aether3D 匹配度:** 中。你已经有 DS belief 驱动的补拍引导。SA-ResGS 的残差监督可以增强 uncertainty 估计精度。但 W4 (View-Dependent Uncertainty) 已经覆盖了这个方向。

---

#### W20: REACT3D 边缘加速器 — 硬件启示 ★

**来源:** REACT3D (MICRO 2025) — 专用边缘加速器做增量 3DGS 训练。

**冷静判断:** 你在 iPhone 上用 Metal compute, 不做定制硬件。但其"增量训练优化稀疏计算"的软件策略可参考。

---

### A.3 合并决策表（W1-W20 + U1-U8 合并去重）— v2.5 历史版本

> ⚠️ **注意:** 此表为 v2.5 时的中间版本。经 8 轮用户反馈后，最终保留 4 个颠覆性模块 (F1/F3/F5/F6)，详见 **附录 B**。
> 将附录 A.2 的 20 条调研想法 (W) 与用户多语言交叉验证后的 8 条精筛想法 (U) 合并。
> 重叠项合并，独立项保留。最终 22 条，分 4 档。

#### 第一档：V1 直接纳入（3 条）

| ID | 名称 | 来源 | Phase | 一句话 |
|----|------|------|-------|--------|
| **F1** | 渐进式压缩 (scaffold LOD 锚定) | W2 | Phase 5 | scaffold LOD level = 渐进编解码锚点，CodecGS + glTF KHR_gaussian_splatting 对齐 |
| **F2** | Scaffold 即碰撞体 | W11 | Phase 6 | scaffold mesh → RealityKit CollisionComponent，近乎零成本 AR 碰撞 |
| **F3** | 证据约束压缩 | U3 + W2 融合 | Phase 5 | 高 belief 区域激进压缩, 低 belief 区域保留冗余。**全球唯一: 压缩预算绑定 coverage proof** |

#### 第二档：V1.5 灰度（6 条）

| ID | 名称 | 来源 | Phase | go/no-go |
|----|------|------|-------|----------|
| **F4** | P-Optimal 主动补拍导航 | U2 + W4 融合 | Phase 3-5 | 先被动提示(DS belief 热力图), Week 32 评估是否升级为主动路径规划。补拍引导精度 ≥ DS-only +15% |
| **F5** | 场景增量补丁链 (Delta Chain) | U7 + W3 融合 | Phase 5+ | 每次增量更新 = delta patch, MerkleTree::append() 新 leaf, 证书链自动延伸。更新 ≤20% 面积 PSNR 下降 <0.5dB |
| **F6** | 冲突证据动态体剔除 | U8 | Phase 3 | DS conflict > threshold 且持续 ≥5 帧 → 标记 Gaussian 为动态体 → 不参与 scaffold binding。误杀率 <3% |
| **F7** | Metal 4 ShaderML 纹理神经解压 | W1 | Phase 4 spike | scaffold-local tiny MLP 替代 SH，显存减少 ≥30% 且 PSNR 差 <0.5dB。Phase 0-D 先验证 ShaderML 延迟 |
| **F8** | View-Dependent 不确定性场 | W4 | Phase 3 | 1 float/Gaussian 的 uncertainty SH。与 DS belief 融合，增强 F4 补拍引导精度 |
| **F9** | 3DGS 水印 (Scene Passport 增强) | W10 + U3 关联 | Phase 5 | Merkle 验完整性 + Ed25519 验所有权 + watermark 验溯源 = 三合一 3D 资产保护链 |

#### 第三档：V2 研究（6 条）

| ID | 名称 | 来源 | 条件 |
|----|------|------|------|
| **F10** | 真假双轨引擎 (Truth + Sim) | U1 | Sim Branch 严格物理隔离: `is_simulated=true`, 不进 coverage proof, 不进 Merkle, 内存独立分配 |
| **F11** | Rebinding 最小化参数化 | U4 | 先离线统计: 当前 rebind 频率是多少？如果 >10%/关键帧 才值得做 |
| **F12** | 物理感知 Scaffold (FEM) | W5 | scaffold vertex → FEM node, 简单碰撞 <2ms 可做, 软体不做 |
| **F13** | 逆渲染 PBR 材质分解 | W8 | 单目歧义大, 需 diffusion prior 辅助。先做 scaffold normal + albedo 粗分解 |
| **F14** | 语义 Gaussian (CLIP 嵌入) | W6 | 离线 MobileCLIP → PCA 16 维 → 100k GS ≈ 6.4MB 额外。先做离线, 不做实时 |
| **F15** | 热预算自蒸馏 (Pause→Move) | U6 | 暂停高质渲染做 teacher → 蒸馏出更紧凑的移动时 student 表示。画质跳变需无感切换实验 |

#### 第四档：V2+ 远期 / 仅追踪（4 条）

| ID | 名称 | 来源 | 备注 |
|----|------|------|------|
| **F16** | Task-aware LOD | U5 | 只用于机器人/自动驾驶专用模式，不动通用管线 |
| **F17** | 联邦多设备协同 | W9 | 需可靠跨设备位姿对齐，先 2 台 PoC |
| **F18** | IMU 连续帧间监督 | W7 | M4 Tracker Rescue 已部分覆盖 |
| **F19** | 下游机器人抓取 | W12 | 数据格式兼容即可，不做核心功能 |

#### 明确不做（3 条，原因不变）

| ID | 原因 |
|----|------|
| W13 Diffusion 填充 | 与"不补假纹理"原则冲突。U1 真假双轨引擎用物理隔离解决了这个矛盾，不再需要 W13 |
| W14 4D 时序 | 内存 ×2~5，F5 补丁链更轻量地解决同类需求 |
| W17/W18 脉冲相机/事件流合成 | 手机无此硬件 |

---

### A.4 模块总览（M1-M7 + F1-F19 映射）— v2.5 历史版本

> ⚠️ **注意:** 此映射为 v2.5 时的中间版本。最终定稿见 **附录 B.0**。

```
已确定模块 (v2.2):              新增模块 (v2.4 合并后):
├── M1 Scaffold-IS-LOD          → 含 F1 渐进压缩 + F3 证据约束压缩
├── M2 Trajectory-as-Depth      → 不变
├── M3 3D Scene Passport        → 含 F9 水印增强 + F5 补丁链
├── M4 Scaffold-as-Tracker      → 含 F8 不确定性 + F4 主动补拍
├── M5 Evidence Auction         → 含 F6 冲突剔除
├── M6 Gaussian Inheritance     → 含 F11 rebinding 最小化
├── M7 Counterfactual Quality   → 不变
│
├── [新] F2  Scaffold 碰撞体    → Phase 6 iOS 集成
├── [新] F7  ShaderML 纹理解压  → Phase 4 spike
├── [新] F10 真假双轨引擎       → V2 研究
├── [新] F12 物理感知 FEM       → V2 研究
├── [新] F13 逆渲染 PBR         → V2 研究
├── [新] F14 语义 Gaussian      → V2 研究
└── [新] F15 热预算自蒸馏       → V2 研究
```

---

### A.5 关键空白（技术护城河 — v2.5 版）

> ⚠️ **注意:** v2.6 护城河已升级至 11 项，详见 **附录 B.7**。

经 20+ 次多语言搜索 + 用户交叉验证后确认:

1. ✅ **无论文在手机上做 scaffold+Gaussian 同时在线训练。** PocketGS 只做 GS, 无 scaffold。
2. ✅ **无论文做在线增量 mesh 生成 + 同时 Gaussian binding。** MILo (SIGGRAPH Asia 2025) 最接近但是离线。
3. ✅ **无论文明确描述 Metal 4 ShaderML 的 3DGS 实现。** Metal 4 太新 (WWDC 2025.06)。
4. ✅ **无论文将 Merkle + DS 证据链 + 水印三合一用于 3D 资产保护。** IP Protection Survey (Feb 2026) 确认。
5. ✅ **无论文在采集第一帧就同时运行 scaffold + GS + evidence chain。** 全球唯一。
6. ✅ **无论文将 continual learning 与 evidence chain 结合做场景增量更新。** CL-Splats (ICCV 2025) 无证据链。
7. ✅ **无论文在 scaffold LOD 上做渐进式 3DGS 编解码。** PCGS/LapisGS 无 scaffold 结构。
8. ✅ **[新增] 无论文把压缩预算绑定到 coverage proof (证据约束压缩 F3)。** 全球唯一。
9. ✅ **[新增] 无论文将 Merkle delta chain 用于 3D 场景增量更新版本管理 (F5)。** 全球唯一。

**护城河判断:** 9 个空白全部经搜索验证成立。你的技术组合在全球前沿论文中仍然唯一。最接近的竞争者 PocketGS 只覆盖 1/9 维度（移动端训练）。

**一句话:** 你现在最该做的不是再扩创意库，而是把"创意 → go/no-go 门槛 → 运行时回退"闭环做硬。上面 22 条里只有 9 条 (F1-F9) 适合近期进线。

---

> 调研附录结束。最终 22 条想法已合并去重，V1/V1.5/V2/V2+ 分层决策已固化。

---

## 附录 B — 最终版保留创新模块详细方案（v2.6 定稿）

> **经过 8 轮用户反馈后的最终筛选结果。**
> 每个模块包含 A 维度（用户体验颠覆）和 B 维度（X+Y 算法策略）。
> 产品核心流程：用户拍摄 → 拍摄完成 → 系统渲染 → 破镜重圆效果展示。**没有"暂停查看"环节。**

---

### B.0 最终保留清单总览

```
核心架构模块（v2.2 继承，不变）:
├── M1: Scaffold-IS-LOD
├── M2: Trajectory-as-Depth
├── M3: 3D Scene Passport       → 并入 F5（活化石链的一部分）
├── M4: Scaffold-as-Tracker Rescue
├── M5: Evidence Auction Scheduler
├── M6: Gaussian Inheritance     (V2)
└── M7: Counterfactual Quality   (V2)

颠覆性创新模块（经 8 轮筛选后保留 4 个）:
├── F1: 时间之镜 (Time Mirror)            — 破镜重圆渲染
├── F3: 可信云存储 (Trusted Cloud)          — 证据驱动云端 3D 资产管理
├── F5: 活化石链 (Living Fossil Chain)      — 吸收原 F9，= 时间线 + 身份证 + 防篡改
└── F6: 幽灵层 (Ghost Layer)               — 动态物体→运动轨迹层

已淘汰:
├── F2: Scaffold 碰撞体                   ← 手机 App 无 AR 碰撞场景
├── F4: 主动补拍导航                      ← 被 F1 黑洞机制自然吸收
├── F7: 双脑渲染 (ShaderML)               ← 产品流程无"暂停查看"环节
├── F8: 不确定性场                        ← 功能被 F1 黑洞 + M5 证据调度覆盖
└── F9: 3DGS 水印                        ← 合并进 F5 活化石链
```

---

### B.1 F1: 时间之镜 — 破镜重圆渲染（v2.6.1 重写：渲染方式颠覆，不只是渲染顺序）

> **v2.6 版 B.1 的问题：** 只改了"哪些 Gaussian 先出现"（时间遮罩），没改渲染方式本身。
> 那不是颠覆，只是一个 fade-in 动画。
> **v2.6.1 修正：** 渲染单元 = scaffold 三角形碎片，碎片在空间中飞行归位，拼合成完整世界。

#### A 维度：用户体验颠覆

**一句话：** 你的 3D 世界不是"从模糊变清晰"，而是一面碎镜子在你眼前重新愈合——每一块碎片从你拍它的那个方向飞出来，落到它在世界中的正确位置。

**用户看到什么：**
```
拍摄完成后，点击"查看结果":
├── 第 0 秒: 黑色空间
├── 第 0.3 秒: 第一块碎片从画面左侧飞入 → 带有纹理的三角形碎片
│              → 碎片是你拍的第一个角度看到的那块墙面/地板/桌子
├── 第 0.5-3 秒: 越来越多的碎片从不同方向飞入
│              → 每块碎片从"你当时拍它的那个方向"飞出
│              → 碎片按拍摄时间顺序出现
│              → 碎片之间有黑色间隙 = 碎镜子的裂缝
├── 第 3-5 秒: 碎片接近最终位置
│              → 碎片间隙越来越小
│              → Gaussian 重叠区域自动混合 → 裂缝自然消失
├── 第 5 秒: 所有碎片归位 → 完整 3D 世界
│              → 和普通 3DGS 渲染效果完全一致
│              → 但有几个黑洞（你没拍到的地方）
└── 黑洞: 深黑 + 边缘泛光呼吸 → "这里需要补拍"
```

**这为什么是颠覆：**
```
传统 3DGS 渲染:
├── 整个场景瞬间出现（或从模糊到清晰）
├── 所有 Gaussian 同时渲染
├── 渲染过程没有空间运动
└── 用户看到的是"照片突然出现"

我们的渲染:
├── 世界从碎片中诞生（不是从模糊中浮现）
├── 每块碎片 = scaffold 三角形 + 绑定的 Gaussian 组 = 有纹理的碎镜片
├── 碎片在 3D 空间中做刚体飞行（平移+旋转）
├── 碎片到位后自然拼合（Gaussian alpha blending = 免费的无缝拼接）
└── 用户看到的是"世界在我面前被组装出来"

关键区别: 不是"先显示后显示"的时间遮罩，是碎片在物理空间中的运动
```

**黑洞交互：**
```
黑洞 = 没有任何 scaffold_unit 覆盖的区域
├── 视觉: 深黑色 + 边缘泛星光呼吸效果
├── 动画中: 黑洞是最后才暴露出来的（周围碎片都归位了，黑洞自然显现）
├── 交互: 用户点击黑洞 → "转到左边，把那面墙扫一下"
├── 补拍后: 新碎片从用户新拍的方向飞入 → 黑洞缩小/消失
└── 无黑洞 → 恭喜，全覆盖！镜子完整了
```

#### B 维度：X+Y 算法策略

**X（渲染压缩领域，别人都做的）:**

| 编号 | 方案 | 来源 | 做什么 |
|------|------|------|--------|
| X₁ | PCGS 渐进式编码 | AAAI 2026 Oral | LOD 分层 → 先传骨架再传细节 |
| X₂ | LapisGS 分层流式 | 3DV 2025 Best Paper | 空间分块 + 按视距优先级加载 |
| X₃ | CodecGS 视频编码 | ICCV 2025 | 相邻 Gaussian 属性用视频编码器压缩 |

**Y（只有我们能做的：scaffold 碎片飞行渲染）:**

```
Y = Scaffold Fragment Flight Rendering（碎片飞行渲染）

═══════════════════════════════════════════════
核心认知突破:
═══════════════════════════════════════════════

别人没有 scaffold → 没有"碎片"概念 → 只能做 per-Gaussian 的 fade-in
我们有 scaffold → 每个三角形 = 一块碎片 → 碎片可以做刚体飞行
我们有 evidence chain → 每块碎片知道"我什么时候被拍到的"和"从哪个方向被拍到的"
→ 碎片的出场顺序 + 飞入方向都是真实数据，不是随机效果

═══════════════════════════════════════════════
实现: 4 个独立技术问题
═══════════════════════════════════════════════

────────────────────────────────────────
问题 1: 渲染单元（碎片 = 什么？）
────────────────────────────────────────

碎片 = 一个 ScaffoldUnit + 它绑定的所有 GaussianPrimitive

    一块碎片的定义:
    ├── ScaffoldUnit u (三角形几何: v0, v1, v2, normal, area)
    ├── GaussianPrimitive[] gs = patch_map.gaussian_ids_for_unit(u.unit_id)
    │   → 这组 Gaussian 提供碎片的"纹理"
    ├── 碎片的中心点 = triangle_centroid(v0, v1, v2)
    ├── 碎片的朝向 = u.normal
    └── 碎片的大小 = u.area

    代码中已有的支撑:
    ├── GaussianPrimitive::host_unit_id → 绑定关系（已有）
    ├── ScaffoldPatchMap::gaussian_ids_for_unit() → 查询碎片内容（已有）
    ├── ScaffoldUnit::area → 碎片大小（已有）
    ├── ScaffoldUnit::v0/v1/v2 + ScaffoldVertex::position → 碎片几何（已有）
    └── GaussianPrimitive::first_observed_ms → 出场时间（已有）

    不是单个 Gaussian:
    → 单个 Gaussian 是一个模糊的椭球，没有"碎镜片"的感觉
    → 一组 Gaussian 渲染出来是一块有纹理的面片 = 镜子碎片

────────────────────────────────────────
问题 2: 碎片从哪飞来？（飞行起点）
────────────────────────────────────────

    起点 = 拍摄该碎片时的相机位置附近

    推导:
    ├── 每个 GaussianPrimitive 有 first_observed_frame_id（已有）
    ├── 通过 frame_id 反查拍摄该帧时的 CameraPose（需要存储相机轨迹）
    ├── 碎片起始位置 = camera_pose.position + camera_pose.forward × 0.3m
    │   → 碎片"从你当时手机的位置"飞出
    └── 碎片起始朝向 = 面向相机（normal 朝向 camera_pose.position）

    需要新增的数据:
    ├── CameraTrajectory: frame_id → CameraPose 的映射
    │   → 拍摄过程中每帧记录 {frame_id, CameraPose}
    │   → 拍摄结束后不丢弃，保留用于破镜重圆动画
    └── 内存开销: ~32 bytes/frame × 1800 frames (60fps×30s) = ~56KB → 可忽略

    为什么这个起点是最好的:
    ├── 碎片从你拍它的方向飞出 → 重演你的拍摄过程
    ├── 先拍的碎片先飞出 → 时间顺序自然
    └── 不同碎片从不同方向飞入 → 视觉丰富，不是一个方向涌入

────────────────────────────────────────
问题 3: 飞行中怎么渲染？（刚体变换）
────────────────────────────────────────

    核心洞察: 不需要改 Gaussian splatting 渲染器。
    只需要在送入渲染器之前，对碎片内的 Gaussian 做刚体变换。

    每帧的渲染流程:
    ├── 1. 确定当前动画时刻 t ∈ [0, T]
    ├── 2. 对每个碎片 fragment_i:
    │   ├── 如果 fragment_i.first_observed_ms > animation_time → 不渲染（还没出场）
    │   ├── 如果 fragment_i 已归位（t > arrival_time_i）→ 正常渲染，不变换
    │   └── 否则: 碎片正在飞行中 → 计算插值变换
    │
    ├── 3. 飞行中碎片的变换:
    │   ├── 起始: start_pos = camera_trajectory[first_observed_frame_id].position + forward*0.3
    │   ├── 终点: end_pos = fragment_centroid（最终世界坐标）
    │   ├── 进度: progress = ease_out_cubic((t - appear_time) / flight_duration)
    │   ├── 当前位置: current_pos = lerp(start_pos, end_pos, progress)
    │   ├── 当前旋转: current_rot = slerp(start_rot, end_rot, progress)
    │   └── 位移偏移: offset = current_pos - end_pos
    │
    ├── 4. 变换碎片内每个 Gaussian:
    │   ├── position_animated = rotate(current_rot, position_local) + current_pos
    │   │   其中 position_local = gaussian.position - fragment_centroid
    │   ├── rotation_animated = current_rot * gaussian.rotation （SH 旋转）
    │   ├── scale: 不变
    │   └── opacity: opacity_final × smoothstep(0, 0.3, progress) // 飞出时淡入
    │
    ├── 5. 把变换后的 Gaussian cloud 送入标准 splatting 渲染器
    │   → 渲染器完全不知道发生了什么
    │   → 它看到的就是一堆普通 Gaussian，只是位置不同
    │
    └── 6. 黑洞单独渲染: 全屏 pass → 无 Gaussian 覆盖的区域 = 黑色 + bloom

    性能分析:
    ├── 额外开销 = 每个 Gaussian 一次 vec3 加法 + 一次 3×3 旋转 = ~20 FLOP
    ├── 100k Gaussian × 20 FLOP = 2M FLOP → 在 GPU 上 < 0.05ms
    ├── 内存: 无额外分配（就地修改 Gaussian position/rotation）
    ├── 动画结束后: offset = (0,0,0), rotation = identity → 零开销
    └── 结论: 性能影响可忽略

    SH 旋转简化:
    ├── 完整 SH 旋转需要 Wigner-D 矩阵 → 对 2 阶 SH 是 5×5 矩阵
    ├── 但飞行时碎片远且快，用户不会细看颜色准确度
    ├── 简化: 飞行中只用 SH 0 阶 (DC 分量, 即平均颜色)
    ├── 归位最后 20%: 0 阶→完整 SH 渐变过渡
    └── 效果: 飞行中碎片颜色正确但无视角依赖高光 → 归位后高光恢复

────────────────────────────────────────
问题 4: 碎片拼合（缝→无缝）
────────────────────────────────────────

    这个问题是免费解决的。

    原因: Gaussian splatting 天然是无缝的
    ├── 每个 Gaussian 是一个软边椭球 (opacity 从中心到边缘衰减)
    ├── 相邻碎片的 Gaussian 投影天然重叠
    ├── alpha blending 自动混合重叠区域
    └── 当碎片全部归位 → 重叠区域混合 → 无缝

    视觉效果:
    ├── 碎片分离时: 碎片之间有黑色间隙 → 碎镜子效果 ✓
    ├── 碎片接近时: 间隙越来越小 → 镜子在愈合 ✓
    ├── 碎片归位: 间隙完全消失 → 完整世界 ✓
    └── 不需要任何额外代码处理拼缝 → 免费的

    边界情况:
    ├── Gaussian 稀疏区域: 碎片归位后仍有小缝隙
    │   → 用 scaffold 三角形做半透明底色填充 (fallback mesh)
    │   → ScaffoldUnit 的 v0/v1/v2 渲染为低 opacity 三角形
    └── 实际上这种情况意味着该区域观测不足 → 本来就该是黑洞的边缘

═══════════════════════════════════════════════
完整数据流
═══════════════════════════════════════════════

拍摄阶段（已有流程，无改动）:
├── 每帧: 记录 CameraPose → CameraTrajectory[]
├── 每帧: EvidenceReplayEngine 记录 observation + timestamp
├── 每帧: Gaussian 创建时写入 first_observed_ms + first_observed_frame_id
├── 每帧: BindingManager 维护 Gaussian → ScaffoldUnit 绑定
└── 拍摄结束: 全力优化 Gaussian（标准流程，不变）

破镜重圆阶段（拍摄完成 + 优化完成后）:
├── 1. 构建碎片列表:
│   ├── 遍历所有 ScaffoldUnit
│   ├── 对每个 unit: 查 gaussian_ids_for_unit() → 获取碎片内容
│   ├── 对每个碎片: 取 min(first_observed_ms) of all its Gaussians → 碎片出场时间
│   ├── 按出场时间排序碎片 → fragment_queue[]
│   └── 碎片数量 = scaffold_unit 数量（典型: 500-5000）
│
├── 2. 计算飞行参数:
│   ├── 对每个碎片: first_observed_frame_id → 查 CameraTrajectory → 起始位姿
│   ├── 终点 = triangle_centroid(v0, v1, v2)
│   ├── 飞行时长 = 0.3-0.8 秒（大碎片慢，小碎片快）
│   ├── 出场延迟 = 碎片在 fragment_queue 中的归一化位置 × 动画总时长
│   └── 预计算完成: < 10ms on CPU → 一次性，不影响帧率
│
├── 3. 逐帧动画循环 (Metal compute):
│   ├── 输入: gaussian_cloud (优化完成的最终版) + fragment_params[]
│   ├── GPU kernel: 对每个 Gaussian, 查其所属碎片的当前变换 → 变换 position
│   ├── 变换后的 Gaussian cloud → 标准 splatting 渲染
│   ├── 额外 pass: 黑洞区域渲染 (全屏 bloom)
│   └── 输出: 一帧画面
│
└── 4. 动画结束:
    ├── 所有碎片归位 → offset = 0 → 标准 3DGS 渲染
    ├── 用户可自由旋转查看
    ├── 黑洞区域仍保持泛光呼吸效果
    └── 用户可重播动画（按钮）

═══════════════════════════════════════════════
需要新增的代码 (对接现有结构)
═══════════════════════════════════════════════

aether/innovation/f1_time_mirror.h (新文件):
├── struct CameraTrajectoryEntry {
│       uint64_t frame_id;
│       CameraPose pose;
│       int64_t timestamp_ms;
│   };
│
├── struct FragmentFlightParams {
│       ScaffoldUnitId unit_id;
│       Float3 start_position;      // 从 CameraTrajectory 查到
│       Float3 end_position;        // triangle_centroid
│       Float3 start_normal;        // 面向相机
│       Float3 end_normal;          // ScaffoldUnit::normal
│       int64_t appear_time_ms;     // min(first_observed_ms) of bound Gaussians
│       float flight_duration_s;    // 0.3-0.8s, 按 area 缩放
│       uint32_t gaussian_count;    // 碎片内 Gaussian 数量
│   };
│
├── f1_build_fragment_queue(
│       ScaffoldUnit* units, size_t unit_count,
│       GaussianPrimitive* gaussians, size_t gaussian_count,
│       ScaffoldPatchMap* patch_map,
│       CameraTrajectoryEntry* trajectory, size_t trajectory_count,
│       FragmentFlightParams** out_params, size_t* out_count);
│
├── f1_animate_frame(
│       GaussianPrimitive* gaussians, size_t gaussian_count,  // 就地修改
│       FragmentFlightParams* params, size_t param_count,
│       ScaffoldPatchMap* patch_map,
│       float animation_time_s,
│       float animation_total_s);
│
└── f1_detect_black_holes(
        ScaffoldUnit* units, size_t unit_count,
        DSMassFunction* evidence,      // belief 查询
        Float3** out_hole_centers, float** out_hole_radii, size_t* out_count);

对现有代码的改动 (最小侵入):
├── GaussianPrimitive: 无改动（first_observed_ms, first_observed_frame_id 已有）
├── ScaffoldUnit: 无改动（v0/v1/v2, normal, area 已有）
├── ScaffoldPatchMap: 无改动（gaussian_ids_for_unit 已有）
├── 需要新增: CameraTrajectory 记录（拍摄阶段每帧存一个 CameraPose）
└── 需要新增: Metal compute kernel 做 per-Gaussian 刚体变换

═══════════════════════════════════════════════
go/no-go 与风险
═══════════════════════════════════════════════

go/no-go:
├── 动画帧率: iPhone 15 Pro 上 60fps 无掉帧（变换开销 < 0.1ms/frame）
├── 视觉效果: 主观评分 ≥ 4.2/5（10 人盲测 vs 传统 fade-in）
├── 黑洞准确率: ≥ 95%（vs GT 覆盖率标注）
├── 碎片归位后: 与标准 3DGS 渲染 PSNR 差 < 0.01dB（变换残差为零）
└── 拼缝: 归位后无可见拼缝（alpha blending 自动处理）

风险:
├── R1: 碎片太多 (>5000) 时排序开销
│   → 缓解: 预排序一次 (< 1ms), 动画过程中不重排
├── R2: 小碎片 (area < 阈值) 飞行效果不明显
│   → 缓解: 小碎片合并成组 (同一 patch_id 的相邻碎片 = 一组)
├── R3: SH 旋转近似在归位瞬间有颜色跳变
│   → 缓解: 最后 20% 进度做 SH 0阶→完整 SH 的 crossfade
└── R4: 相机轨迹噪声导致碎片起始位置不自然
    → 缓解: 对 CameraTrajectory 做 moving average 平滑 (窗口 5 帧)
```

**冠军选择：** X₁/X₂/X₃ 竞争压缩效率（存储/传输）。Y 是独立的**渲染展示层**，不参与压缩竞争——它叠加在压缩冠军之上，改变的是"用户怎么第一次看到这个场景"。

---

### B.2 F3: 可信云存储 — 证据驱动 3D 资产管理

#### A 维度：用户体验颠覆

**一句话：** 竞品给你一个云端文件夹存 3D 模型。我们给你一个带"身份证"的保险箱——每个模型都有不可伪造的质量证明和完整性校验。

**用户看到什么：**
```
拍摄完成 → 本地生成 3D 模型 + Scene Passport（身份证）
├── 自动上传: 模型 + 身份证 → 云端加密存储
├── 云端列表: 每个模型显示"信任度评分" (基于 evidence chain 的覆盖率+观测数)
│   ├── 🟢 高信任 (belief >0.8, 覆盖率 >90%): "验证完整"
│   ├── 🟡 中信任 (belief 0.5-0.8, 覆盖率 60-90%): "部分区域未验证"
│   └── 🔴 低信任 (belief <0.5, 覆盖率 <60%): "建议补拍"
├── 分享: 对方收到模型时，可一键验证"这个模型没被篡改过"
└── 存储省钱: 高信任模型自动压缩（质量有保证，可以激进压缩）
             低信任模型保留冗余（质量不确定，不敢压太狠）
```

**为什么是颠覆：**
- 竞品的云存储：纯文件管理，模型质量靠肉眼看
- 我们的云存储：每个文件自带质量证明 + 防篡改证书 + 智能压缩
- 用户不只是存文件，而是在管理"可信 3D 资产"

#### B 维度：X+Y 算法策略

**X（竞品都有的云存储功能）:**

| 编号 | 方案 | 做什么 |
|------|------|--------|
| X₁ | 标准对象存储 (S3/OSS/COS) | 上传/下载/CDN 分发 |
| X₂ | 渐进式上传 | 网络差时先传低精度版本，后台补传高精度 |
| X₃ | 端对端加密 | 用户数据加密后上传，云端无法查看内容 |

**Y（只有我们能做的）:**

```
Y = Evidence-Driven Storage Tiering（证据驱动存储分层）

核心洞察: 我们有每个 scaffold_unit 的 DS belief 和 observation_count
→ 高信任区域: 数据冗余低，可以激进压缩 (节省 30-50% 存储)
→ 低信任区域: 数据冗余高，保留原始精度 (宁可多花存储)
→ 全球没有第二家有这个信息，因为没人有 evidence chain

实现路径:

1. 上传时自动分层:
├── 高信任块 (belief > 0.8):
│   ├── Gaussian 属性: SH 蒸馏到 0 阶 + codebook 量化
│   ├── Scaffold 几何: 简化到 LOD 0
│   └── 预计压缩率: 原始大小的 25-35%
├── 中信任块 (belief 0.5-0.8):
│   ├── Gaussian 属性: SH 保留 1 阶 + 轻量化
│   ├── Scaffold 几何: LOD 1
│   └── 预计压缩率: 原始大小的 50-60%
└── 低信任块 (belief < 0.5):
    ├── Gaussian 属性: 完整保留
    ├── Scaffold 几何: 完整保留
    └── 预计压缩率: 原始大小的 90-100%（几乎不压缩）

2. Scene Passport 随模型上传:
├── passport.json: 包含 merkle_root + coverage_report + quality_report
├── merkle_proof: 可独立验证完整性
├── ed25519_signature: 设备私钥签名 → 证明来源
└── 任何人下载后可一键验证: 模型未篡改 + 来源可信 + 质量有据可查

3. 云端审计链:
├── 每次上传: MerkleTree::append(upload_event) → 新 leaf
├── 每次修改: MerkleTree::append(modification_event) → 新 leaf
├── 每次分享: MerkleTree::append(share_event) → 新 leaf
└── 完整的操作历史，不可伪造

4. 对接现有代码:
├── DS belief ← DSMassFunction::occupied (已有)
├── 覆盖率 ← EvidenceReplayEngine::export_state_json() (已有)
├── 压缩 ← Phase 5 LOD 压缩管线 (已在计划中)
├── 签名 ← Ed25519 (已在 M3 Scene Passport 中设计)
├── Merkle ← merkle_tree.h / inclusion_proof.h (已有)
└── 云端 SDK: 标准 S3/OSS 接口，新增 metadata 层

5. go/no-go:
├── 证据驱动压缩: 高信任区域压缩后 PSNR 下降 < 0.3dB
├── 低信任区域保留: 补拍后重新压缩，质量不低于首次
├── 端到端验证: 上传 → 下载 → verify_passport → 100% 通过
└── 存储节省: 相比全精度上传，总存储减少 ≥ 30%
```

---

### B.3 F5: 活化石链 — 时间线 + 身份证 + 防篡改三合一

> 吸收原 F9 (水印) 和原 M3 (Scene Passport)。成为统一的 3D 资产生命周期管理模块。

#### A 维度：用户体验颠覆

**一句话：** 你的 3D 场景不再是一张照片（拍了就定了），而是一部时间日记——每次拍摄都是新的一页，你可以"翻回去"看任何时候的样子。

**用户看到什么：**
```
场景详情页 → 底部有一条时间滑块:
├── 滑到最左: 第一次扫描的样子
├── 滑到中间: 三个月前重新扫描的样子（家具搬了位置）
├── 滑到最右: 最新一次扫描的样子
├── 每个版本都有"身份证": 点击查看 → 显示扫描时间/设备/质量评分/覆盖率
└── "防伪标": 任何人都可以验证这个模型没被PS过

补拍/更新流程:
├── 用户打开旧场景 → 点击"更新"
├── 系统自动加载旧 Gaussian → 用户只拍变化的部分
├── 变化区域: DS conflict 自动检测 → 只更新这些 Gaussian
├── 不变区域: 完全保留，不浪费时间重新优化
├── 生成新版本: 时间线多了一页
└── 旧版本不删除，永远可以回溯

导出分享:
├── 分享整个时间线: 对方看到完整的场景演变历史
├── 分享单个版本: 附带身份证 + 防篡改证明
├── 第三方验证: 一键校验 → "此 3D 资产未被篡改，生成于 2026-02-19 14:30"
└── 用途: 房产交易/保险理赔/建筑验收/文物保护/法庭证据
```

**为什么是颠覆：**
- 竞品：拍一次 = 一个文件，想更新就重新拍
- 我们：每次拍摄 = 时间线新页，增量更新 + 版本管理 + 不可伪造证书
- 类比：竞品是"照片"，我们是"区块链上的 3D 日记"

#### B 维度：X+Y 算法策略

**X（增量更新，别人也在做）:**

| 编号 | 方案 | 来源 | 做什么 |
|------|------|------|--------|
| X₁ | CL-Splats 持续学习 | ICCV 2025 | 新观测更新旧 Gaussian，防灾难性遗忘 |
| X₂ | GaussianUpdate 增量更新 | ICCV 2025 | 只更新变化区域，保留不变区域 |
| X₃ | CodecGS 增量编码 | ICCV 2025 | 变化部分用增量编码，大幅降低更新成本 |

**Y（只有我们能做的三合一）:**

```
Y = Delta Merkle Chain + Ed25519 + Watermark（三合一资产保护）

为什么全球唯一:
├── 我们有 Merkle tree → 每次更新自动追加 leaf → 不可伪造的版本链
├── 我们有 evidence chain → 自动检测变化区域 → 精确增量更新
├── 我们有 Ed25519 → 设备签名 → 证明来源
└── 没有任何 3DGS 方案同时拥有这三样

具体实现:

1. Delta Patch Chain（增量补丁链）:
├── 每次更新 = 一个 delta patch:
│   ├── added_gaussians: 新增的 Gaussian 列表
│   ├── removed_gaussian_ids: 删除的 Gaussian ID 列表
│   ├── modified_gaussians: 修改的 Gaussian (ID + 新属性)
│   └── scaffold_diff: scaffold 拓扑变化
│
├── 版本管理:
│   ├── version_0: 完整快照 (full snapshot)
│   ├── version_1: delta_patch_1 (只存变化)
│   ├── version_2: delta_patch_2 (只存变化)
│   └── ...
│
├── Merkle 追加:
│   ├── 每个 delta_patch → hash → MerkleTree::append()
│   ├── 新版本的 merkle_root 包含所有历史版本的信息
│   ├── consistency_proof: 证明新树包含旧树的所有 leaf
│   └── inclusion_proof: 证明某个版本确实存在于历史中
│
├── 对接现有代码:
│   ├── MerkleTree (merkle_tree.h): append / inclusion_proof / consistency_proof 全部已实现
│   ├── ConsistencyProof (consistency_proof.h): verify() 已实现
│   ├── InclusionProof (inclusion_proof.h): verify / verify_with_leaf_data 已实现
│   ├── hash_leaf / hash_nodes (merkle_tree_hash.h): 已实现
│   └── 需要新增: DeltaPatch 序列化 + canonical JSON

2. 变化区域自动检测:
├── 加载旧 evidence state → EvidenceReplayEngine 恢复
├── 新一轮拍摄开始 → 对同一区域产生新观测
├── 新观测 vs 旧 Gaussian 渲染 → 光度残差
├── DS conflict = dempster_combine(旧 belief, 新 observation).conflict
│   ├── conflict > 0.3 → 标记为"变化区域"
│   └── conflict < 0.1 → 标记为"不变区域"
├── 只对"变化区域"的 Gaussian 做重新优化
└── 对接: DSMassFunction::dempster_combine() (已有) + conflict 字段

3. Scene Passport 自动延伸:
├── 每个版本自动生成新 passport:
│   ├── passport_v0: 第一次扫描的证书
│   ├── passport_v1: 第二次扫描的证书，包含 v0 的 merkle_root 作为 parent
│   └── 形成证书链: v0 → v1 → v2 → ...
├── Ed25519 签名: 每个 passport 独立签名
├── 验证: 验任何一个版本 = 验整条链的完整性
└── 对接: Ed25519 签名流程 (已在 v2.3 M3 中设计)

4. 3D 水印（原 F9，合并进来）:
├── 策略: 不在 SH 系数上嵌入（ShaderML MLP 转换会破坏 SH）
├── 改为: 在 Gaussian 空间分布中嵌入
│   ├── 选取 N 个"锚 Gaussian"（高观测次数，位置稳定）
│   ├── 微调其 position 的最低有效位 → 嵌入设备 ID hash 的 bit
│   ├── 人眼不可见（位置偏移 < 0.1mm）
│   └── 提取: 读取锚 Gaussian position LSB → 恢复 hash → 验证设备 ID
├── 与 Merkle/Ed25519 的分工:
│   ├── Merkle: 验证"完整性"（有没有被改过）
│   ├── Ed25519: 验证"所有权"（谁的设备拍的）
│   └── 水印: 验证"溯源性"（即使 Merkle 数据被剥离，水印仍在模型本身中）
└── go/no-go:
    ├── 水印嵌入后 PSNR 下降 < 0.1dB
    ├── 抵抗 10% 随机 Gaussian 删除后仍可提取
    └── 提取成功率 ≥ 99%

5. 时间滑块渲染:
├── 选择版本 v_k → 重建: snapshot_v0 + Σ(delta_patch_1..k)
├── 版本间过渡动画: Gaussian opacity 渐变 (旧→0, 新→target)
├── 预算: 缓存最近 3 个版本的 Gaussian cloud → 滑块秒切
└── 超过 3 个版本: 按需重建 (< 500ms on iPhone 15 Pro)

6. go/no-go 总览:
├── 增量更新: 变化区域 ≤20% 时，全场景 PSNR 下降 < 0.5dB
├── delta patch 大小: ≤ 变化区域 Gaussian 数 × 1.2（开销系数 ≤ 1.2×）
├── Merkle consistency proof: 100% 验证通过
├── 时间滑块: 版本切换 < 500ms
└── 水印: 嵌入+提取成功率 ≥ 99%，PSNR 下降 < 0.1dB
```

---

### B.4 F6: 幽灵层 — 动态物体运动轨迹可视化

#### A 维度：用户体验颠覆

**一句话：** 拍摄时有人走过、猫跑过、车开过？它们不会被粗暴删除——它们变成"幽灵"，留下运动轨迹，你可以选择看或不看。

**用户看到什么：**
```
扫描一个客厅，你的猫在场景中走来走去:
├── 最终 3D 场景: 干净的客厅，没有猫
├── 但右上角有个"幽灵"图标 + 数字 "1"
├── 点击图标:
│   ├── 猫的"幽灵版"出现: 半透明 + 蓝色辉光
│   ├── 猫走过的路径: 蓝色光带 (轨迹线)
│   ├── 猫停留最久的位置: 蓝色光圈 (热力图)
│   └── 可以拖动时间条: 看猫在不同时刻的位置
├── 关闭图标: 幽灵消失，回到干净场景
└── 实用价值:
    ├── 零售: 顾客动线热力图（最受欢迎的货架在哪里）
    ├── 安防: 人员活动轨迹记录
    └── 有趣: 宠物、小孩的活动轨迹 → 社交媒体分享
```

**为什么是颠覆：**
- 传统 3DGS 对待动态物体：当作噪声删除（所有方案都这么做）
- 我们：动态物体 = 有价值的信息 → 变成可视化的轨迹层
- 从"噪声过滤"到"信息提取"的思维翻转

#### B 维度：X+Y 算法策略

**X（动态物体处理，别人也在做）:**

| 编号 | 方案 | 来源 | 做什么 |
|------|------|------|--------|
| X₁ | WildGS-SLAM 野外鲁棒 SLAM | 2025 | DINOv2 特征 + 语义掩码剔除动态物体 |
| X₂ | SEGS-SLAM 语义 GS-SLAM | arXiv 2025 | 语义分割驱动的动态物体过滤 |
| X₃ | CleanGS 干净背景重建 | CVPR 2025 | 视频恢复技术从瞬态物体中恢复干净背景 |

**Y（只有我们能做的，因为我们有 evidence chain）:**

```
Y = Evidence-Timestamp Ghost Layer（证据时间戳幽灵层）

核心洞察:
├── 别人只知道"这里有个动态物体，删掉"
├── 我们知道: "这里在第 3.2 秒有一个东西，第 5.7 秒没有了，第 8.1 秒又有了"
├── 因为我们的 evidence chain 给每次观测都打了精确时间戳
└── 所以我们能重建运动轨迹，而不只是做二值删除

实现路径:

1. 动态物体检测（用 evidence chain，不用额外神经网络）:
├── 核心判据: DS conflict
│   ├── 对同一个 scaffold_unit，短时间内 belief 剧烈变化
│   ├── 具体: 在 Δt < 2 秒内, |belief(t₂) - belief(t₁)| > 0.4
│   ├── 或: dempster_combine(observation_t₁, observation_t₂).conflict > 0.5
│   └── 标记为: DYNAMIC
│
├── 时间窗口确认（防误杀）:
│   ├── 单次 conflict > 0.5 → 候选
│   ├── 连续 ≥5 帧 conflict > 0.3 → 确认动态
│   ├── 间歇性 conflict (时有时无) → 可能是光照变化，不标记
│   └── 误杀率目标: < 3%（静态物体被误判为动态）
│
├── 对接现有代码:
│   ├── DSMassFunction::dempster_combine() → conflict 值（已有）
│   ├── EvidenceReplayEngine → 每帧的 timestamp_ms（已有）
│   ├── AdmissionController → 已有的 SpamProtection 可辅助判断重复更新（已有）
│   └── 需要新增: DynamicObjectTracker 类

2. 幽灵 Gaussian 隔离:
├── 检测到 DYNAMIC 的 Gaussian → 不删除
├── 移入独立的 ghost_cloud: GaussianCloud (与主 cloud 物理隔离)
├── ghost_cloud 的 Gaussian 属性:
│   ├── 原始属性 (position, SH, opacity, scale) → 保留
│   ├── + ghost_timestamp: 该 Gaussian 的观测时间窗口 [start_ms, end_ms]
│   ├── + ghost_trajectory_id: 同一个移动物体的所有 Gaussian 共享一个 ID
│   └── + ghost_confidence: 该 Gaussian 被多少帧确认为动态
│
├── 主 cloud: 只保留静态世界 → 干净
├── ghost_cloud: 保留所有动态观测 → 完整轨迹信息
└── 两层物理隔离: ghost 不参与 scaffold binding, 不进 Merkle tree, 不影响 Scene Passport

3. 轨迹重建:
├── 同一物体的 ghost Gaussian 按 ghost_timestamp 排序
├── 物体轨迹 = ghost Gaussian 质心的时间序列
│   ├── trajectory[i] = mean(position of ghost gaussians at timestamp_i)
│   └── 平滑: 卡尔曼滤波 / 简单移动平均
├── 热力图 = 轨迹在空间中的停留时间加权密度
│   ├── heatmap(x,y,z) = Σ (停留时间 at (x,y,z)) / 总时间
│   └── 渲染: 3D 半透明热力球 (蓝→红 渐变)
└── 输出:
    ├── trajectory_polyline: vec3[] + timestamp[] → 轨迹线
    ├── heatmap_voxels: (position, intensity)[] → 热力图
    └── ghost_cloud: 完整幽灵 Gaussian → 可选渲染

4. 渲染:
├── 默认: 只渲染主 cloud (静态世界) → 干净
├── 幽灵模式开启:
│   ├── ghost_cloud 渲染: opacity *= 0.3 + 蓝色色调映射
│   ├── 轨迹线: GL_LINE_STRIP + 发光 shader
│   ├── 热力图: 体积渲染 (简化版, 不需要高精度)
│   └── 时间滑块: 只显示 ghost_timestamp ∈ [slider_min, slider_max] 的幽灵
├── 性能预算:
│   ├── ghost_cloud 通常 << 主 cloud (动态物体是少数)
│   ├── 预计额外渲染开销: < 2ms/frame (iPhone 15 Pro)
│   └── 如果超预算: 降低幽灵分辨率 (LOD 降级)

5. go/no-go:
├── 动态物体检测:
│   ├── 召回率 ≥ 90%（10 个动态物体至少检测到 9 个）
│   ├── 误杀率 < 3%（静态物体被误判为动态）
│   └── 测试场景: 1-3 人走动 + 宠物 + 移动家具
├── 幽灵渲染: 额外 GPU 开销 < 2ms/frame
├── 轨迹准确度: 轨迹误差 < 物体尺寸的 20%
└── 主场景质量: 剔除动态物体后，静态区域 PSNR 无下降
```

---

### B.5 核心架构模块 B 维度补充（M1-M7 的 X+Y 策略）

> M1-M7 的 A 维度（体验定义）和技术细节已在正文 v2.2 中完整描述。
> 此处仅补充 B 维度的 X+Y 算法策略。

#### M1: Scaffold-IS-LOD 的 X+Y

```
X₁: LightGaussian SH 蒸馏 (NeurIPS 2024) → 2阶→0阶
X₂: EAGLES 量化 (CVPR 2024) → 定点量化 Gaussian 属性
X₃: Compact3D codebook (arXiv 2024) → 全局 codebook 压缩

Y: Scaffold 三角面积 = 天然 LOD 层级
├── 不需要独立 LOD 系统（减少代码量）
├── LOD 切换边界 = scaffold 三角形边界（无 popping）
├── split 操作同时完成: 几何细化 + LOD 提升 + Gaussian spawn
└── 全球唯一: 无论文将 scaffold 拓扑结构直接作为 LOD 层级
```

#### M2: Trajectory-as-Depth 的 X+Y

```
X₁: UniDepth 单目深度估计 (CVPR 2024) → 粗深度先验
X₂: Depth Pro (Apple 2024) → 零样本深度
X₃: 多帧三角测量 → 经典几何方法

Y: Scaffold ray intersection = 无 ML 依赖的稠密深度
├── 不需要深度相机，不需要神经网络
├── Scaffold 越密 → 深度越准 → Gaussian 越好 → Scaffold 越密 (正反馈环)
├── 复用 SpatialHashTable 做 ray 加速 (已有)
└── MarzulloFusion 加权融合多源深度 (已有)
```

#### M4: Scaffold-as-Tracker Rescue 的 X+Y

```
X₁: iG-SLAM (arXiv 2025) → 即时 GS-SLAM
X₂: LoopSplat 回环检测 (2024) → submap 注册
X₃: ORB-SLAM3 经典跟踪 → 作为 baseline

Y: 已有 Gaussian 模型渲染 → 光度对比 → 位姿修正
├── 触发条件: tracker.tracking_state == kLimited 且 ≥3 帧
├── 1/4 分辨率渲染 → 光度残差 → camera extrinsics 梯度下降
├── 3-5 次迭代, 每次 < 0.5ms
├── 置信度门限: 只在 belief > 0.7 且覆盖率 > 60% 的区域做
└── go/no-go: 单次迭代 > 1ms 或恢复帧数无改善 → 不上线
```

#### M5: Evidence Auction Scheduler 的 X+Y

```
X₁: 均匀分配优化预算 → baseline
X₂: 基于渲染误差的 attention (gsplat 风格) → 误差高的区域多分预算
X₃: 基于视距的优先级 → 近景优先

Y: DS belief 驱动竞价调度
├── expected_gain(unit) = (1 - belief) × plausibility × area
├── 高 expected_gain = 不确定但有潜力 → 优先
├── 低 expected_gain = 已收敛或无望 → 跳过
├── conflict > 0.3 → 可能是动态物体 → 交给 F6 幽灵层
├── 防振荡: hysteresis + min_hold_window + cooldown (已设计)
└── go/no-go: 相同总预算下 PSNR 收敛速度提升 ≥ 10%
```

#### M6: Gaussian Inheritance 的 X+Y (V2)

```
X₁: 标准 pruning → 直接删除低贡献 Gaussian
X₂: LightGaussian 全局蒸馏 → 大模型→小模型
X₃: Mini-Splatting 重要性采样 → 保留关键 Gaussian

Y: 守恒继承
├── prune 时不直接删, 把 SH/opacity/观测统计传给邻居
├── 距离加权: 越近的邻居继承越多
├── 目标: prune 50% 后 PSNR 下降 < 标准 pruning
└── go/no-go: PSNR 提升 < 0.5dB → 放弃
```

#### M7: Counterfactual Quality Field 的 X+Y (V2)

```
X₁: 简单覆盖率统计 → 观测次数 < 阈值 = 低质量
X₂: PUP 3D-GS 不确定性 (CVPR 2025) → per-Gaussian uncertainty

Y: 反事实预测 "如果停拍，各区域质量会降多少"
├── 离线训练: 对已完成采集逐帧截断 → 度量截断后质量
├── 拟合: 截断帧数 × 区域特征 → 预测质量下降
├── 用途: 在拍摄过程中提示用户"再拍 30 秒质量会提升 X%"
└── go/no-go: 预测误差 < 实际下降值的 20% → 上线
```

---

### B.6 模块间依赖关系与整合

```
F1 (时间之镜) 的依赖:
├── ← M1 (Scaffold-IS-LOD): 碎片按 scaffold_unit 分组
├── ← M5 (Evidence Auction): evidence chain 提供 first_observed_ms
├── → F5 (活化石链): 破镜重圆动画可在时间线的每个版本上播放
└── → F6 (幽灵层): 破镜重圆过程中，幽灵 Gaussian 最后出现（标记为半透明）

F3 (可信云存储) 的依赖:
├── ← F5 (活化石链): passport + merkle proof 随模型上传
├── ← M1 (Scaffold-IS-LOD): LOD 分层 → 压缩分层
├── ← M5 (Evidence Auction): DS belief → 压缩激进度决策
└── 独立云端 SDK: 不影响本地核心算法

F5 (活化石链) 的依赖:
├── ← M3 原有 Scene Passport: 作为 v0 基础
├── ← 所有 Merkle 模块: merkle_tree.h / inclusion_proof.h / consistency_proof.h
├── ← M5 (Evidence Auction): DS conflict → 变化区域检测
├── → F1 (时间之镜): 每个版本都有破镜重圆效果
├── → F3 (可信云存储): 每个版本的 passport 随之上传
└── → F6 (幽灵层): 幽灵数据可选择是否纳入版本历史

F6 (幽灵层) 的依赖:
├── ← M5 (Evidence Auction): DS conflict + timestamp → 动态检测
├── ← BindingManager: 动态 Gaussian 脱离 scaffold binding
├── → F1 (时间之镜): 幽灵在破镜重圆中最后出现
└── 物理隔离: ghost_cloud 不进入 Merkle tree（不影响 Scene Passport 完整性）
```

```
实现优先级（时间线）:
├── Phase 1-3: M1/M2/M4/M5 核心算法 + F6 动态检测基础
├── Phase 3: F5 活化石链骨架 (delta patch + Merkle 追加)
├── Phase 4-5: F1 时间之镜渲染 + F3 云存储 SDK + F5 水印
├── Phase 6: 全链路整合 + iOS 首发
└── V2+: M6/M7 研究
```

---

### B.7 最终技术护城河（经 8 轮筛选后验证）

```
√ 1. 手机上 scaffold + Gaussian 同时在线训练          — 无人做过（PocketGS 无 scaffold）
√ 2. 在线增量 mesh + 同时 Gaussian binding             — 无人做过
√ 3. Metal 4 ShaderML 的 3DGS 实现                    — Metal 4 太新
√ 4. Merkle + DS + Ed25519 + 水印 四合一 3D 资产保护   — 无人做过
√ 5. 第一帧就同时运行 scaffold + GS + evidence chain   — 全球唯一
√ 6. 持续学习 + evidence chain + Merkle delta chain     — CL-Splats 无证据链
√ 7. scaffold LOD 做渐进式 3DGS 编解码                 — PCGS/LapisGS 无 scaffold
√ 8. 压缩预算绑定 coverage proof (F3)                   — 全球唯一
√ 9. Merkle delta chain 用于 3D 场景版本管理 (F5)       — 全球唯一
√ 10. [新增] evidence chain 驱动动态物体轨迹提取 (F6)    — 全球唯一（别人只做删除）
√ 11. [新增] 拍摄时间序列作为渲染序列 (F1 时间之镜)      — 全球唯一
```

**护城河结论:** 11 个技术空白。对手要复制单个功能容易，但要复制 scaffold + evidence chain + Merkle + 时间序列渲染 + 幽灵层 + 可信云存储 的完整组合——他们得从头重写整个系统。

---

> 附录 B 结束。4 个颠覆性创新模块 + 7 个核心架构模块，每个都有 A(体验) + B(算法X+Y) 完整定义。

---

> 文档结束。如需进入任何 Phase 的实现阶段，确认即可。
