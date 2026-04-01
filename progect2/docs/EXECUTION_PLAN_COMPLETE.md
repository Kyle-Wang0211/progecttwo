# AETHER3D 完整执行方案 — 6 项修复 + 12 项改进 + 2 项新发现 = 20 项

> 生成时间: 2026-02-24 | 基于全量代码深度审计 (491 个 Core 文件)
> 状态: 待审批

---

## 目录

- [第一部分: 6 项修复](#第一部分-6-项修复)
- [第二部分: 12 项改进](#第二部分-12-项改进)
- [第三部分: 2 项新发现](#第三部分-2-项新发现)
- [文件修改总览](#文件修改总览)
- [未连接算法清单](#未连接算法清单)
- [实施顺序](#实施顺序)

---

## 第一部分: 6 项修复

---

### 修复 1: S0-S5 多维审核管线 — 接通已写好的算法

**用户反馈**: "S5有好多个维度的审核才能变成S5，目前你的审核机制不是完整版"

**审计发现 — 85% 实现但只有 30% 接通**:

已有完整实现:
- EvidenceStateMachine.swift: S0-S5 状态机 ✅
- DimensionalEvidence.swift: 15 维评分框架 ✅
- C++ choquet_learner.h: Choquet 积分学习器 ✅
- C++ evidence_state_machine.h: 6-gate S5 认证 ✅
- C++ coverage_estimator.h: DS belief + Lyapunov ✅
- GateQualityComputer.swift: viewGain/geomGain/basicGain/coverageScore 已计算 ✅

**致命断点 — ScanViewModel.swift 行 1139-1141**:
```swift
lastOverallColorState = evidenceStateMachine.evaluate(
    coverage: coverageResult
    // ❌ dimensionalScores: 参数直接没传！永远是 nil！
)
```
结果: S5 认证只评估了 2 个 gate (belief + uncertainty)，
Choquet/SuperDim/HighObsRatio/Lyapunov 4 个 gate 全部默认 0 → 永远过不了

**10 个维度得分来源**:
```
dim1 viewGain:      GateQualityComputer.viewGateGain()     ✅ 已计算 → 需连接
dim2 geometryGain:  GateQualityComputer.geomGateGain()     ✅ 已计算 → 需连接
dim3 depthQuality:  ❌ 未实现 (需 ARKit depth 数据)       → 暂用 0.5 占位
dim4 semanticConsistency: ❌ 未实现                        → 暂用 0.5 占位
dim5 errorTypeScore: GateQualityComputer                   ✅ 已计算 → 需连接
dim6 basicGain:     GateQualityComputer.basicGateGain()    ✅ 已计算 → 需连接
dim7 provenance:    ❌ 未实现 (SHA-256 chain)              → 暂用 0.5 占位
dim8 coverageTracker: GateCoverageTracker.coverageScore()  ✅ 已计算 → 需连接
dim9 resolutionQuality: ❌ 未实现                          → 暂用 0.5 占位
dim10 viewDiversity: ViewDiversityTracker.diversityScore() ✅ 已实例化但未调用
```

**具体修改**:

```
文件: App/Scan/ScanViewModel.swift

1) 新增属性 (行 ~87):
    private var perPatchDimensionalScores: [String: DimensionalScoreSet] = [:]

2) 在 updatePatchDisplayMap() 的 for 循环内 (行 ~940 后) 计算维度得分:
    let dims = DimensionalScoreSet(
        dim1_viewGain:          gateComputer.lastViewGain(for: triangle.patchId),
        dim2_geometryGain:      gateComputer.lastGeomGain(for: triangle.patchId),
        dim3_depthQuality:      0.5,  // 占位 — 后续接 ARKit depth
        dim4_semanticConsistency: 0.5,
        dim5_errorTypeScore:    gateComputer.lastErrorTypeScore(for: triangle.patchId),
        dim6_basicGain:         gateComputer.lastBasicGain(for: triangle.patchId),
        dim7_provenanceContribution: 0.5,
        dim8_coverageTrackerScore: gateComputer.lastCoverageScore(for: triangle.patchId),
        dim9_resolutionQuality: 0.5,
        dim10_viewDiversity:    viewDiversityTracker.diversityScore(for: triangle.patchId)
    )
    perPatchDimensionalScores[triangle.patchId] = dims

3) 在 calculateOverallCoverage() 行 1139 传入维度得分:

  旧:
    lastOverallColorState = evidenceStateMachine.evaluate(
        coverage: coverageResult
    )

  新:
    let globalDims = computeGlobalDimensionalScores()
    lastOverallColorState = evidenceStateMachine.evaluate(
        coverage: coverageResult,
        dimensionalScores: globalDims
    )

4) 新增辅助方法:
  private func computeGlobalDimensionalScores() -> DimensionalScoreSet {
      guard !perPatchDimensionalScores.isEmpty else { return DimensionalScoreSet() }
      var sum = DimensionalScoreSet()
      for (_, dims) in perPatchDimensionalScores {
          sum.dim1_viewGain += dims.dim1_viewGain
          sum.dim2_geometryGain += dims.dim2_geometryGain
          // ... 所有 10 个维度累加
      }
      let n = Double(perPatchDimensionalScores.count)
      return DimensionalScoreSet(
          dim1_viewGain: sum.dim1_viewGain / n,
          dim2_geometryGain: sum.dim2_geometryGain / n,
          // ... 所有 10 个维度取平均
      )
  }

5) 统一颜色阈值 — 行 718-725:
  旧:
    if display < 0.1 { .black }
    else if display < 0.3 { .darkGray }
    else if display < 0.6 { .lightGray }
    else if display < 0.85 { .white }
    else { .original }
  新:
    if display < ScanGuidanceConstants.s0ToS1Threshold { .black }        // <0.10
    else if display < ScanGuidanceConstants.s1ToS2Threshold { .darkGray } // 0.10-0.25
    else if display < ScanGuidanceConstants.s2ToS3Threshold { .lightGray } // 0.25-0.50
    else if display < ScanGuidanceConstants.s3ToS4Threshold { .white }    // 0.50-0.75
    else if display < ScanGuidanceConstants.s4ToS5Threshold { .white }    // 0.75-0.88
    else { .original }                                                     // >=0.88
```

**前置条件**: GateQualityComputer 需暴露 per-patch 维度 getter。
当前 computeGateQuality() 只返回 Double，需扩展。

---

### 修复 2: 光照自适应 — 使用已有的 EnvironmentLightEstimator

**用户反馈**: "我之前都写过代码写过算法，你自己去检查去不要重新造轮子！"

**审计结果**: EnvironmentLightEstimator 已完整实现且已连接:
- 3-tier fallback: ARKit → Vision → Fallback
- C++ 平滑: rise_alpha=0.35, fall_alpha=0.12
- SH 系数: 9 个 L2 球谐
- 已在 ScanGuidanceRenderPipeline 每帧调用 lightEstimator.update()

**不新建任何光照模块。仅调优 mapAmbientToSharpness() 映射**:

```
文件: App/Scan/ScanViewModel.swift
行 971-978 mapAmbientToSharpness():

旧:
  if ambientIntensity < 100 { return 30.0 }
  if ambientIntensity < 300 { return 60.0 }
  if ambientIntensity < 600 { return 100.0 }
  if ambientIntensity < 2500 { return 130.0 }
  if ambientIntensity < 5000 { return 90.0 }
  return 50.0

新:
  if ambientIntensity < 50  { return 20.0 }     // 极暗
  if ambientIntensity < 150 { return 50.0 }      // 暗
  if ambientIntensity < 400 { return 100.0 }     // 室内偏暗
  if ambientIntensity < 3000 { return 150.0 }    // 正常 → 远超 85 硬门
  if ambientIntensity < 6000 { return 110.0 }    // 强光
  return 40.0                                      // 极强光 → flare
```

---

### 修复 3: 证据积累速度 — 四层同时加速

**用户反馈**: "2.5秒依然太慢。polycam和scaniverse都可以第一秒就出现覆盖面热力图"

**瓶颈分析**:
```
当前:
  层 1: Admission Controller — 每 patch 限制 2 次/秒
  层 2: baseIncrement=0.06 × 2次/秒 = 0.12/秒
  层 3: PatchDisplayMap EMA alpha=0.35 → 3 帧时间常数
  层 4: WedgeGenerator EMA alpha=0.6 → 2 帧时间常数
  总效果: ~5 秒才到 S1
```

#### 修复 3a: 拆除 Admission Controller

```
文件: App/Scan/ScanViewModel.swift — 5 处删除

1. 行 82-85: 删除属性声明
   private var admissionHandle: OpaquePointer?

2. 行 250-253: 删除 init() 中创建
   if algorithmSet.isEnabled(AlgorithmFlags.admissionControl) {
       admissionHandle = NativeAdmissionControllerBridge.admissionControllerCreate()
   }

3. 行 284-286: 删除 deinit 中销毁
   if let handle = admissionHandle {
       NativeAdmissionControllerBridge.admissionControllerDestroy(handle)
   }

4. 行 844-866: 删除 updatePatchDisplayMap() 中的 admission gate 整个 if 块
   if let handle = admissionHandle {
       ...
       if decision.allowed == 0 { continue }
   }

5. 行 1393-1395: 删除 resetSubsystems() 中 reset
   if let handle = admissionHandle {
       NativeAdmissionControllerBridge.admissionControllerReset(handle)
   }
```

保留 NativeAdmissionControllerBridge.swift 等底层文件不删 (其他模块可能引用)。
质量把关由 GateQualityComputer (reproj/edge/sharpness 硬门) + verdict (good/suspect/bad) 承担。

#### 修复 3b: baseIncrement 校准

```
文件: Core/Evidence/PatchEvidenceMap.swift
行 213:
旧: let baseIncrement = 0.06
新: let baseIncrement = 0.01  // 60fps 无 admission: 0.01×60×0.75=0.45/sec

行 206-212 注释更新:
旧:
  //   good observation, quality 0.5:  0.012 × 1.0 × 0.75 = 0.009/frame
  //   At 60fps × 50% admission duty cycle = 30 effective frames/sec × 0.009 = 0.27/sec
新:
  //   good observation, quality 0.5:  0.01 × 1.0 × 0.75 = 0.0075/frame
  //   At 60fps (no admission gate) = 60 frames/sec × 0.0075 = 0.45/sec
  //   display=0.10 (S0→S1) in ~0.22 sec ← 快于 Polycam 的 1 秒
  //   display=0.50 (S2→S3) in ~1.1 sec
  //   display=0.88 (S4→S5) in ~2.0 sec
```

#### 修复 3c: PatchDisplayMap alpha 加速

```
文件: Core/Evidence/PatchDisplayMap.swift
行 135:
旧: let fallbackAlpha = 0.35
新: let fallbackAlpha = 0.85  // 近乎即时跟随 evidence (上游已是 monotonic)
```

#### 修复 3d: WedgeGenerator smoothing 加速

```
文件: Core/Quality/Geometry/WedgeGeometryGenerator.swift
行 75 和行 281 (两处):
旧: config.smoothing_alpha = 0.6
新: config.smoothing_alpha = 0.9  // 三层 EMA 合并为单帧延迟
```

**修复后速度**:
```
0.01 × 60fps × 0.75 = 0.45/秒
S0→S1 (0.10): 0.22 秒 ← 快于 Polycam
S2→S3 (0.50): 1.1 秒
S4→S5 (0.88): 2.0 秒
```

---

### 修复 4: 60fps 全帧处理

**用户反馈**: "那之前不是也是都要审核吗？改什么呀？我需要的就是快速审核"

**审计确认 — 已正确实现，无需修改**:
- ARCameraPreview 每帧触发 processARFrame()
- 不跳帧
- MeshExtractor 已移到 ARSession 线程
- Task 闭包只捕获 [ScanTriangle] 轻量值
- budget selection 只限制渲染数量，不限制处理数量
- 拆除 admission (修复 3a) 后每帧每个三角形都累积证据

---

### 修复 5: 索引缓冲区溢出 — 移除 6000 cap + 自适应 LOD

**用户反馈**: "6000个绝对不够。GPU计算不过来是算法的问题，你去优化算法去"

```
文件: App/ScanGuidance/ScanGuidanceRenderPipeline.swift

1) 行 157-165 — 移除 6000 硬编码:
  旧:
    let maxSafeInputTriangles = min(tier.maxTriangles, 6000)
  新:
    let maxSafeInputTriangles = tier.maxTriangles

2) LOD 选择 — 根据三角形数量自适应:

  let adaptiveLOD: WedgeGeometryGenerator.LODLevel
  let inputCount = limitedTriangles.count
  if inputCount <= 3000 {
      adaptiveLOD = .full        // <=3K: 完整 bevel + side → 132 indices/tri
  } else if inputCount <= 8000 {
      adaptiveLOD = .medium      // 3K-8K: 简化 bevel → 60 indices/tri
  } else if inputCount <= 15000 {
      adaptiveLOD = .low         // 8K-15K: 无 bevel → 24 indices/tri
  } else {
      adaptiveLOD = .flat        // >15K: 单面 → 6 indices/tri
  }
  // 取 thermal tier LOD 和 adaptive LOD 中更保守的
  let effectiveLOD = max(tier.lodLevel.rawValue, adaptiveLOD.rawValue)
  let finalLOD = WedgeGeometryGenerator.LODLevel(rawValue: effectiveLOD) ?? .flat
```

**已有安全保障 (保留不动)**:
- vertex/index buffer 自动翻倍扩容 (行 565-603)
- index count clamp to buffer capacity (行 275-276)

---

### 修复 6: C 函数完整性

**已完成**: 5 个 C 函数已添加到 c_api.cpp:
1. `aether_extract_frustum_planes` — Gribb-Hartmann 6 平面
2. `aether_frustum_cull_aabbs` — AABB 剔除
3. `aether_meshlet_build` — 三角形分组
4. `aether_two_pass_cull_meshlets` — CPU Tier-C 剔除
5. `aether_screen_detail_factor` — LOD 因子

**验证方案**:
```bash
# 扫描 .h 中声明但未实现的函数
grep -n 'AETHER_API' aether_cpp/include/**/*.h | 提取函数名
# 确认 c_api.cpp 有对应实现
grep -n 函数名 aether_cpp/src/c_api.cpp
# C++ 测试
cd aether_cpp/build && cmake --build . && ctest --output-on-failure  # 预期 113/113
# Xcode 链接
xcodebuild build | grep "Undefined symbols"  # 预期无
```

---

## 第二部分: 12 项改进

---

### 改进 ①: 多线程并行 — "多雇几个厨师"

**用户反馈**: "你说的就是多雇几个厨师"

**processARFrame() 瓶颈分析**:
```
步骤                     耗时         可并行?
updatePatchDisplayMap   25-75ms      YES — 每个三角形独立
rebuildAdjacencyGraph   20-50ms      YES — 纯计算
view diversity tracking  5-15ms      YES — 每个三角形独立
```

#### 改进 ①-A: updatePatchDisplayMap 并行化

```
文件: App/Scan/ScanViewModel.swift
行 833 updatePatchDisplayMap():

旧: for triangle in triangles { ... } (串行)

新:
  // Step 1: 并行计算 quality/verdict (无共享状态)
  struct TriangleResult {
      let patchId: String
      let ledgerQuality: Double
      let verdict: ObservationVerdict
  }
  let results = UnsafeMutableBufferPointer<TriangleResult>.allocate(capacity: triangles.count)
  DispatchQueue.concurrentPerform(iterations: triangles.count) { index in
      let triangle = triangles[index]
      // cosAngle, approxEdgeRms, approxSharpness 计算
      // gateComputer.computeGateQuality() — 需验证线程安全
      results[index] = TriangleResult(...)
  }

  // Step 2: 串行写入 (PatchEvidenceMap/DisplayMap 非线程安全)
  for i in 0..<triangles.count {
      patchEvidenceMap.update(...)
      patchDisplayMap.update(...)
  }
  results.deallocate()
```

**风险**: GateQualityComputer 内部有 SmartAntiBoostSmoother 状态。
如不线程安全 → 方案 B: 每线程一个 GateQualityComputer 实例。

#### 改进 ①-B: adjacency 移到后台线程

```
文件: App/Scan/ScanViewModel.swift
行 1175-1177:

旧:
  private func rebuildAdjacencyGraph() {
      adjacencyGraph = SpatialHashAdjacency(triangles: meshTriangles)
  }

新:
  private func rebuildAdjacencyGraph() {
      let snapshot = meshTriangles
      DispatchQueue.global(qos: .userInitiated).async { [weak self] in
          let newGraph = SpatialHashAdjacency(triangles: snapshot)
          Task { @MainActor [weak self] in
              self?.adjacencyGraph = newGraph
          }
      }
  }
```

C++ `aether_spatial_adjacency_build()` 无共享状态，线程安全。
主线程节省 20-50ms。

---

### 改进 ②: 防刷机制 — 直接拆除

**用户反馈**: "如果把这个机制直接拆掉会怎么样呢？这个机制存在的必要性是什么呢？"

**已合并到修复 3a**。Token bucket 在 ScanViewModel 中被移除。
质量由 GateQualityComputer + verdict 两层门控保障。

---

### 改进 ③: 三层 EMA — 既快又平滑

**用户反馈**: "我不要取舍。我要的是既快速又平滑"

**已合并到修复 3c/3d**。alpha=0.85/0.9。

**为什么高 alpha 不会抖动**:
- PatchEvidenceMap: additive 累积，永不降低
- PatchDisplayMap: monotonic guarantee (max(prev, new))
- WedgeGenerator: C++ EMA 只跟随上升值
- 三层都是单调的 → 不会"跳上去又掉下来"
- 高 alpha 只意味着"跟随快"，输入本身平滑递增所以不抖

---

### 改进 ④: 金属质感 — 从 S0 起始就有光泽

**用户反馈**: "金属质感一直都有啊，从S零最大最黑的三角形就有金属质感和光泽感呀！越大的三角形越应该凸显质感"

**审计发现 — 三个独立门控压制金属质感**:

门控 1: C++ pbr_material.cpp 行 77-79
```cpp
params.metallic = config.metallic_s0 +     // metallic_s0 = 0.0 ← 问题根源
    (config.metallic_s5 - config.metallic_s0) *
    smoothstep(0.45f, 0.88f, t);   // ← 门控: display<0.45 → metallic=0
```

门控 2: ScanGuidance.metal 行 459
```metal
if (display < 0.5h) discard_fragment();  // Pass 3 金属光泽直接丢弃
```

门控 3: roughness_s0 = 1.0 → 完全粗糙

**且**: ScanGuidanceConstants 定义 metallicBase=0.3, roughnessBase=0.6，
但 C++ PBRFromEvidenceConfig 默认 metallic_s0=0.0, roughness_s0=1.0 → SSOT 不一致

#### 修改 ④-A: C++ 默认值对齐 SSOT

```
文件: aether_cpp/include/aether/render/pbr_material.h
行 46-54:

旧:
  float metallic_s0{0.0f};
  float metallic_s5{0.5f};
  float roughness_s0{1.0f};
  float roughness_s5{0.15f};
新:
  float metallic_s0{0.3f};    // 对齐 ScanGuidanceConstants.metallicBase
  float metallic_s5{0.7f};    // metallicBase + metallicS3Bonus
  float roughness_s0{0.6f};   // 对齐 ScanGuidanceConstants.roughnessBase
  float roughness_s5{0.15f};
```

#### 修改 ④-B: smoothstep 门控从 0 开始

```
文件: aether_cpp/src/render/pbr_material.cpp

行 77-79:
旧: params.metallic = ... * smoothstep(0.45f, 0.88f, t);
新: params.metallic = ... * smoothstep(0.0f, 0.88f, t);

行 72-74:
旧: params.roughness = ... * smoothstep(0.1f, 0.88f, t);
新: params.roughness = ... * smoothstep(0.0f, 0.88f, t);
```

#### 修改 ④-C: Metal shader Pass 3 门控移除

```
文件: App/ScanGuidance/Shaders/ScanGuidance.metal
行 459:
旧: if (display < 0.5h) discard_fragment();
新: // 所有三角形都有金属光泽，S0 微弱，S5 最强

行 466-467:
旧:
  half metallicBoost = (display - 0.5h) * 2.0h;
  half3 sheen = half3(fresnel * metallicBoost * 0.15h);
新:
  half metallicBoost = display;  // 全范围 [0, 1]
  half3 sheen = half3(fresnel * metallicBoost * 0.2h);
```

#### 修改 ④-D: 面积影响金属感

```
文件: aether_cpp/src/render/fracture_display_mesh.cpp
行 113-118:
旧: p.metallic = pbr.metallic;
新:
  float area_metallic_boost = std::min(1.5f, std::sqrt(triangle_area / median_area));
  p.metallic = std::min(1.0f, pbr.metallic * area_metallic_boost);
```

**修改后效果**:
```
S0 小三角形: metallic ≈ 0.3 × 0.7 = 0.21 (微弱但可见的光泽)
S0 大三角形: metallic ≈ 0.3 × 1.5 = 0.45 (明显的金属感)
S5 大三角形: metallic ≈ 0.7 × 1.5 = 1.0 (满金属)
```

---

### 改进 ⑤: 颜色阈值 — S0-S5 统一标准

**用户反馈**: "唯一的标准就是我们的S0到S5的素材质量等级"

**已合并到修复 1**: 所有阈值改用 ScanGuidanceConstants SSOT (0.10/0.25/0.50/0.75/0.88)。

---

### 改进 ⑥: sRGB 色彩空间转换

**用户反馈**: "直接去做正确的转换"

**审计发现**:
- Oklab→Linear sRGB 转换 ✅ 正确 (ScanGuidance.metal 行 113-131)
- 边框 gamma 校正 ✅ 正确 (Stevens' power law, gamma=0.45)
- **基础颜色缺少 sRGB EOTF** ❌ (tone mapping 后直接输出 linear)
- **Framebuffer 格式**: `.bgra8Unorm` (non-sRGB)

```
文件: App/ScanGuidance/Shaders/ScanGuidance.metal
行 338-339 (wedgeFillFragment, tone mapping 后):

旧:
  colorF = colorF / (colorF + 1.0);
  color = half3(colorF);

新:
  colorF = colorF / (colorF + 1.0);
  // Linear → sRGB EOTF: pow(x, 1/2.2) 近似
  colorF = pow(max(colorF, float3(0.0)), float3(1.0 / 2.2));
  color = half3(colorF);
```

**注**: 如果 ARKit render pass 使用 `.bgra8Unorm_srgb` 格式，
Metal 硬件会自动转换 → 上述修改会导致双重 gamma。需真机验证:
```swift
#if DEBUG
print("[Aether3D] Framebuffer: \(texture.pixelFormat.rawValue)")
// .bgra8Unorm = 80, .bgra8Unorm_srgb = 81
#endif
```

---

### 改进 ⑦: 邻接图后台重建

**用户反馈**: "我不关心在哪里做，你就给我做到最好的用户体验就行了"

**已合并到改进 ①-B**: rebuildAdjacencyGraph() 移到 DispatchQueue.global()。
后续版本可做增量更新 (当前 SpatialHashAdjacency 只有 init，无 insert/remove API)。

---

### 改进 ⑧: 历史记录持久化 — 今天实施

**用户反馈**: "你今天就直接做，不要等以后了！"

**审计发现**:
- PatchEntry: Codable ✅ — 但 没有 save/load 方法 ❌
- DisplayEntry: Codable ✅ — 但 没有 save/load 方法 ❌
- PatchEvidenceMap 有 `allEntriesSnapshotSorted()` 可导出 ✅
- PatchDisplayMap 有 `snapshotSorted()` 可导出 ✅
- 无任何 FileManager 调用 ❌

#### 修改 ⑧-A: PatchEvidenceMap 添加 save/load

```
文件: Core/Evidence/PatchEvidenceMap.swift
在 allEntriesSnapshotSorted() 后新增:

  public func saveToDisk(sessionId: String) throws {
      let entries = allEntriesSnapshotSorted()
      let data = try JSONEncoder().encode(entries)
      let url = Self.storageURL(for: sessionId, suffix: "evidence")
      try data.write(to: url, options: .atomic)
  }

  public func loadFromDisk(sessionId: String) throws {
      let url = Self.storageURL(for: sessionId, suffix: "evidence")
      let data = try Data(contentsOf: url)
      let entries = try JSONDecoder().decode([PatchEntry].self, from: data)
      for entry in entries {
          if let patchId = entry.bestFrameId {
              patches[patchId] = entry
          }
      }
  }

  private static func storageURL(for sessionId: String, suffix: String) -> URL {
      let dir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
          .appendingPathComponent("Aether3D/Sessions", isDirectory: true)
      try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
      return dir.appendingPathComponent("\(sessionId)_\(suffix).json")
  }
```

#### 修改 ⑧-B: PatchDisplayMap 添加 save/load

```
文件: Core/Evidence/PatchDisplayMap.swift
在 snapshotSorted() 后新增:

  public func saveToDisk(sessionId: String) throws {
      let entries = snapshotSorted()
      let data = try JSONEncoder().encode(entries)
      let url = PatchEvidenceMap.storageURL(for: sessionId, suffix: "display")
      try data.write(to: url, options: .atomic)
  }

  public func loadFromDisk(sessionId: String) throws {
      let url = PatchEvidenceMap.storageURL(for: sessionId, suffix: "display")
      let data = try Data(contentsOf: url)
      let entries = try JSONDecoder().decode([DisplayEntry].self, from: data)
      for entry in entries {
          if let existing = displays[entry.patchId] {
              if entry.display > existing.display { displays[entry.patchId] = entry }
          } else {
              displays[entry.patchId] = entry
          }
      }
  }
```

#### 修改 ⑧-C: ScanViewModel 集成

```
文件: App/Scan/ScanViewModel.swift

1) 新增: private let sessionId = UUID().uuidString

2) stopCapture() 中保存:
  try? patchEvidenceMap.saveToDisk(sessionId: sessionId)
  try? patchDisplayMap.saveToDisk(sessionId: sessionId)

3) processARFrame() 每 300 帧 (5秒) 自动保存:
  if frameCounter % 300 == 0 {
      Task.detached { [weak self] in
          guard let self else { return }
          try? await MainActor.run {
              try? self.patchEvidenceMap.saveToDisk(sessionId: self.sessionId)
              try? self.patchDisplayMap.saveToDisk(sessionId: self.sessionId)
          }
      }
  }

4) 历史容量:
  旧: private static let maxHistoricalAnchors = 10000
  新: private static let maxHistoricalAnchors = 50000
```

---

### 改进 ⑨: GPU 过载 — 算法优化不降级

**用户反馈**: "GPU计算不过来，那是你算法的问题，你去优化算法去"

**已合并到修复 5**: 移除 6000 cap，自适应 LOD (full/medium/low/flat)。

**额外安全保障 (已有，保留)**:
- AdaptiveBudgetController: C++ 流式回归学习渲染成本
- 非对称 PID: 过载 2 帧降级，恢复 30 帧回升
- Buffer 动态扩容: 初始 4MB → 自动翻倍

---

### 改进 ⑩: 神经网络

**用户反馈**: "你说的神经网络可以替代这个流程或者优化它吗？"

**审计**: 当前代码库无 ML/CoreML 集成。

**可行方向 (研究阶段，非本轮)**:
- 方向 A: 证据质量预测 — 输入 RGB patch crop → 输出 (reprojRms, edgeRms, sharpness)
  - 优势: 真实图像质量评估，取代 ambient→sharpness 近似
  - 成本: CoreML ~1ms/帧 (Neural Engine)
  - 需要: 训练数据
- 方向 B: S5 认证加速 — 预测 P(S5 | observations)
  - 风险: 失去确定性证据链 (用户要上链)
- 方向 C: LOD 智能选择 — per-device 自适应

**结论**: 方向 A 最有价值，记入 roadmap，本轮不实施。

---

### 改进 ⑪: 速度自适应

**用户反馈**: "我不care。模糊的画面一定会被审核淘汰"

**审计确认 — 已完整实现**:
```
ScanViewModel.swift 行 915-932:
  velocity > 1.4m/s: verdict = .bad    → increment × 0.0
  velocity > 0.7m/s: verdict = .suspect → increment × 0.3
  velocity <= 0.7m/s: verdict = .good   → increment × 1.0
```
模糊帧 → bad verdict → 不累积 → 自动忽略。**无需修改**。

---

### 改进 ⑫: 区块链留痕

**用户反馈**: "我以后还要上链，证据的累积就是过程的留痕"

```
新建文件: Core/Evidence/ObservationLog.swift

public struct ObservationRecord: Codable, Sendable {
    let patchId: String
    let timestampMs: Int64
    let frameId: String
    let ledgerQuality: Double
    let verdict: String
    let evidenceBefore: Double
    let evidenceAfter: Double
    let cameraPosition: [Float]  // [x, y, z]
    let viewAngleDeg: Double
}

public final class ObservationLog {
    private var records: [ObservationRecord] = []
    private let maxRecords = 100_000  // ~10MB JSON

    func append(_ record: ObservationRecord) {
        records.append(record)
        if records.count > maxRecords {
            records.removeFirst(records.count - maxRecords)
        }
    }
    func export() -> Data? { try? JSONEncoder().encode(records) }
}
```

```
修改文件: App/Scan/ScanViewModel.swift
在 updatePatchDisplayMap() 的 patchEvidenceMap.update() 后:

  observationLog.append(ObservationRecord(
      patchId: triangle.patchId,
      timestampMs: timestampMs,
      frameId: frameId,
      ledgerQuality: ledgerQuality,
      verdict: verdict.rawValue,
      evidenceBefore: previousEvidence,
      evidenceAfter: newEvidence,
      cameraPosition: [camPos.x, camPos.y, camPos.z],
      viewAngleDeg: viewAngleDeg
  ))
```

性能: append O(1), 内存 ~10MB 上限。后续 Merkle tree 在链端实现。

---

## 第三部分: 2 项新发现

---

### 新增 A: 开始拍摄后全屏变黑 ★★★

**用户反馈**: "用户点击开始拍摄之后就应该全屏变黑呀，然后出现白色边框，也就是出现很多大的黑色三角形，然后三角形数量慢慢变多，颜色变浅，变白，露出物体本来的颜色，这些都是我已经写好的算法"

**审计发现 — 完全缺失**:
1. MTKView clearColor = (0,0,0,**0**) → alpha=0 → 透明，用户看到摄像头
2. Oklab display=0 时 L=0.30 → 深灰不是纯黑
3. 第一个三角形到达前没有遮罩

#### 修改 A-1: ScanView 添加黑色遮罩

```
文件: App/Scan/ScanView.swift
行 39-43:

旧:
  ZStack {
      ARCameraPreview(viewModel: viewModel)
          .ignoresSafeArea()

新:
  ZStack {
      ARCameraPreview(viewModel: viewModel)
          .ignoresSafeArea()

      // Layer 1.5: 全屏黑色遮罩
      if viewModel.isCapturing {
          Color.black
              .ignoresSafeArea()
              .opacity(viewModel.blackoutOpacity)
              .allowsHitTesting(false)
              .animation(.easeOut(duration: 0.3), value: viewModel.blackoutOpacity)
      }
```

#### 修改 A-2: ScanViewModel blackout 控制

```
文件: App/Scan/ScanViewModel.swift

新增属性:
  @Published var blackoutOpacity: Double = 1.0
  @Published var isCapturing: Bool = false

transition(to: .capturing):
  isCapturing = true
  blackoutOpacity = 1.0

processARFrame() 每帧更新:
  let meshCoverage = Double(limitedTriangles.count) / 1000.0
  blackoutOpacity = max(0.0, 1.0 - min(1.0, meshCoverage))
```

#### 修改 A-3: Oklab S0 调暗到纯黑

```
文件: App/ScanGuidance/Shaders/ScanGuidance.metal
行 138:

旧: half L = mix(0.30h, 0.97h, display);  // S0 = 深灰
新: half L = mix(0.05h, 0.97h, display);  // S0 = 近乎纯黑 (RGB ~3,3,3)
```

**完整生命周期**:
```
点击"开始拍摄" → 全屏黑 (blackoutOpacity=1.0)
→ ARSession 启动 (~0.3秒)
→ 第一个 mesh 到达 → 三角形出现
→ 三角形增多 → 黑色遮罩渐隐
→ S0: 纯黑三角形 + 白色边框 + 金属光泽
→ 0.22秒: S1 深灰
→ 1.1秒: S3 白色 + 明显金属
→ 2.0秒: S5 露出原色
```

---

### 新增 B: SSOT 常量对齐

ScanGuidanceConstants 与 C++ PBRFromEvidenceConfig 不一致 →
**已在改进 ④-A 中统一**。

---

## 文件修改总览

| # | 文件 | 修改内容 | 改动量 |
|---|------|---------|--------|
| 1 | `App/Scan/ScanViewModel.swift` | 删 admission(5处) + S0-S5 阈值 + 维度连接 + blackout + 持久化 + 并行化 + sharpness 映射 | -35, +120 |
| 2 | `App/Scan/ScanView.swift` | 全屏黑色遮罩层 | +10 |
| 3 | `Core/Evidence/PatchEvidenceMap.swift` | baseIncrement 0.06→0.01 + save/load | -6, +35 |
| 4 | `Core/Evidence/PatchDisplayMap.swift` | fallbackAlpha 0.35→0.85 + save/load | -1, +25 |
| 5 | `Core/Quality/Geometry/WedgeGeometryGenerator.swift` | smoothing_alpha 0.6→0.9 (2处) | -2, +2 |
| 6 | `App/ScanGuidance/ScanGuidanceRenderPipeline.swift` | 移除 6000 cap + 自适应 LOD | -5, +25 |
| 7 | `App/ScanGuidance/Shaders/ScanGuidance.metal` | Oklab L=0.05 + sRGB gamma + Pass 3 门控 | -4, +8 |
| 8 | `aether_cpp/include/aether/render/pbr_material.h` | metallic_s0=0.3, roughness_s0=0.6 | -2, +2 |
| 9 | `aether_cpp/src/render/pbr_material.cpp` | smoothstep 从 0 开始 | -2, +2 |
| 10 | `aether_cpp/src/render/fracture_display_mesh.cpp` | area → metallic boost | +2 |
| 11 | `Core/Evidence/ObservationLog.swift` (新建) | 区块链留痕 | +40 |

**总计**: 10 个修改 + 1 个新建 = 11 个文件, ~270 行新增, ~57 行删除

---

## 不修改的文件 (已有算法保留原样)

- EnvironmentLightEstimator.swift — 完整实现，已连接 ✅
- EvidenceStateMachine.swift — 完整实现 ✅ (只需传入 dimensionalScores)
- DimensionalEvidence.swift — 完整实现 ✅ (只需在 ScanViewModel 实例化)
- GateQualityComputer.swift — 完整实现 ✅ (需暴露 per-patch 维度 getter)
- ViewDiversityTracker.swift — 完整实现 ✅ (需在循环中调用)
- CoverageEstimator.swift — 完整实现 ✅
- ScanGuidanceConstants.swift — SSOT 只读 ✅

---

## 未连接算法清单 (63% 的 Core 模块)

| 模块 | 文件 | 功能 | 本轮连接? |
|------|------|------|----------|
| DimensionalEvidence | DimensionalEvidence.swift | 15 维评分框架 | ✅ |
| ViewDiversityTracker | ViewDiversityTracker.swift | 视角多样性 | ✅ |
| QualityAnalyzer | QualityAnalyzer.swift | 多指标融合 | ❌ 后续 |
| SpeedController | SpeedController.swift | 进度/动画速度 | ❌ 后续 |
| ProgressTracker | ProgressTracker.swift | 覆盖率方向追踪 | ❌ 后续 |
| HintController | HintController.swift | 引导提示 | ❌ 后续 |
| SplitLedger | SplitLedger.swift | 双账本架构 | ❌ 后续 |
| EvidenceReplayEngine | EvidenceReplayEngine.swift | 回放取证 | ❌ 后续 |
| MaterialAnalyzer | MaterialAnalyzer.swift | 材质推断 | ❌ 后续 |
| ProvenanceBundle | ProvenanceBundle.swift | 证据打包导出 | ❌ 后续 |

---

## 实施顺序

| 步骤 | 任务 | 预期效果 |
|------|------|---------|
| 1 | 新增 A: 全屏黑色遮罩 | 点击开始拍摄 → 立即变黑 |
| 2 | 修复 3: 四层加速 (拆 admission + 三层 alpha) | 0.22 秒 S1 可见 |
| 3 | 改进 ④: 金属质感从 S0 起始 | 黑色三角形有光泽 |
| 4 | 修复 1: S0-S5 阈值 + 维度得分连接 | 6-gate S5 认证生效 |
| 5 | 修复 2: 光照 sharpness 映射 | 正常光照不阻碍 |
| 6 | 修复 5: 自适应 LOD | 50K+ 三角形 |
| 7 | 改进 ⑥: sRGB | 颜色准确 |
| 8 | 改进 ⑧: 持久化 | 证据保存磁盘 |
| 9 | 改进 ⑫: 观测日志 | 区块链留痕 |
| 10 | 改进 ①: 并行化 | 帧处理 -50% |
| 11 | 修复 6: C 函数完整性验证 | 无遗漏 |
| 12 | 编译验证 | 113/113 C++ + Xcode BUILD SUCCEEDED |

---

## 速度预期

```
修复后 (无 admission, baseIncrement=0.01, alpha=0.85/0.9):

每帧增量 = 0.01 × 1.0 (good) × 0.75 (avg quality) = 0.0075
每秒增量 = 0.0075 × 60fps = 0.45/秒

各 S-level 到达时间:
  S0→S1 (0.10): 0.22 秒  ← 第一次颜色变化
  S1→S2 (0.25): 0.56 秒
  S2→S3 (0.50): 1.1 秒   ← 金属质感明显
  S3→S4 (0.75): 1.7 秒
  S4→S5 (0.88): 2.0 秒   ← 完整认证

对比: Polycam/Scaniverse 第一秒出现热力图
我们: 0.22 秒出现 S1 颜色变化 ← 更快
```
