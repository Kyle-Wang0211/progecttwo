# Minimal Whitebox Runtime Inventory

Date: 2026-03-20

Scope:
- `/Users/kaidongwang/Documents/Aether3D/Core/Evidence`
- `/Users/kaidongwang/Documents/Aether3D/Core/TSDF`
- `/Users/kaidongwang/Documents/Aether3D/Core/Quality/PureVision`
- `/Users/kaidongwang/Documents/Aether3D/Core/Quality/Geometry`

Goal:
- classify the remaining runtime support around `Evidence / TSDF / PureVision`
- separate `closed-loop required now` from `historical baggage / next-pass candidate`
- preserve the current minimal whitebox path:
  `Home -> Scan -> Runtime quality review -> Danish 5090 SSH -> Download artifact -> Local 3DGS viewer`

## Already pushed out of the main target

These are no longer in `Aether3DCore`'s main compile boundary and therefore are already out of the current mobile whitebox product path:

- `Core/Compliance`
- `Core/Jobs`
- `Core/MerkleTree`
- `Core/Upload`
- `Core/Replay`
- `Core/TimeAnchoring`
- `Core/Persistence`
- `Evidence/Provenance`
- `Evidence/Grid`
- `Evidence/PR3`
- `Evidence/PRMath`
- `Evidence/Smoothing`
- `Evidence/Tier`
- `Evidence/ViewDiversityTracker`
- `Evidence/TrueDeterministicJSONEncoder`
- `Evidence/EvidenceReplayEngine`
- `Evidence/HealthMonitorWithStrategies`
- `Evidence/IsolatedEvidenceEngine`
- `Evidence/UnifiedAdmissionController`
- `Evidence/NativeAdmissionControllerBridge`
- `Evidence/NativeCoverageEstimatorBridge`
- `Evidence/NativeReplayEngineBridge`
- `Evidence/ObservationReorderBuffer`
- `Evidence/Fusion/MultiLedger`
- `Evidence/Fusion/CoverageEstimator`
- `Evidence/PIZ/PIZGridAnalyzer`
- `Evidence/PIZ/PIZOcclusionFilter`
- `TSDF/CPUIntegrationBackend`
- `TSDF/MarchingCubes`
- `TSDF/TSDFVolume`
- `TSDF/NativeLoopDetector`
- `TSDF/NativeDepthFilter`
- `TSDF/NativeColorCorrector`
- `TSDF/NativeMarchingCubesBridge`
- `TSDF/NativeMeshExtractionSchedulerBridge`
- `TSDF/NativePoseGraphOptimizer`
- `TSDF/NativeRenderStabilityBridge`
- `TSDF/NativeThermalEngineBridge`
- `TSDF/NativeTriTetMappingBridge`
- `TSDF/NativeTSDFRuntimeBridge`
- `TSDF/NativeVolumeController`
- `TSDF/TriTetTSDFMapping`
- `Quality/PureVision/PureVisionRuntimeAuditInputSampler.swift`

Interpretation:
- the blockchain-like / provenance-like / replay / history / multi-job / upload-platform layer is already no longer part of the minimal closed loop
- this directly matches the professor's "先闭环、别平台化、别为了多任务多用户扩模块" guidance

## Closed-loop required now

These pieces still look technical, but they are either directly used by the current app flow or still form the smallest compilable support layer for it.

### Evidence

Keep now:
- `EvidenceState.swift`
  - still owns `PatchEntrySnapshot`
  - `SplitLedger.swift` exports snapshots in this type
- `ColorState.swift`
  - extracted as a tiny standalone enum so `EvidenceState` can survive without old health-monitor code
- `Observation.swift`
  - still defines the evidence observation model shared by remaining evidence structures
- `ObservationVerdict.swift`
  - still used by `SplitLedger` and `PatchEvidenceMap`
- `SplitLedger.swift`
  - still the smallest surviving evidence container
- `PatchEvidenceMap.swift`
  - backing store for `SplitLedger`
- `PatchDisplayMap.swift`
  - still part of the reduced evidence data model
- `PatchWeightComputer.swift`
  - used by `PatchEvidenceMap`
- `DynamicWeights.swift`
  - used by `SplitLedger`
- `ClampedEvidence.swift`
  - property-wrapper dependency used in retained evidence types
- `BucketedAmortizedAggregator.swift`
  - still used inside `PatchEvidenceMap`
- `MemoryPressureHandler.swift`
  - still referenced by retained ledger pruning code
- `TriTetEvidenceMetadata.swift`
  - still referenced from `Observation`
- `Fusion/DSMassFusion.swift`
  - still compiled as part of the surviving evidence math layer
- `EvidenceLogger.swift`
  - lightweight helper, still referenced by retained files

Why they stay:
- they are no longer product-facing features
- but they still form a reduced shared data layer under the remaining quality/runtime code

### TSDF

Keep now:
- `NativePoseStabilizerBridge.swift`
  - directly used by `ScanViewModel`
- `TSDFConstants.swift`
  - still referenced by retained TSDF data structures and app-side TSDF helpers
- `BlockIndex.swift`
- `VoxelBlock.swift`
- `VoxelBlockPool.swift`
- `ManagedVoxelStorage.swift`
- `SpatialHashTable.swift`
- `TSDFTypes.swift`
- `TSDFMathTypes.swift`
- `VoxelTypes.swift`
- `AdaptiveResolution.swift`
- `MeshOutput.swift`
- `TSDFIntegrationBackend.swift`
- `NativeICPRefiner.swift`

Why they stay:
- the current app tree still imports `Aether3DCore` from TSDF-adjacent capture/render code
- `App/TSDF/MetalTSDFIntegrator.swift` still relies on the reduced TSDF type layer even though the old TSDF-heavy product UI is gone
- `ScanViewModel` still uses the pose stabilizer bridge directly

### PureVision / Geometry

Keep now:
- `GeometryMLFusionEngine.swift`
- `NativePureVisionRuntimeBridge.swift`
- `CrossValidationFusion.swift`
- `PureVisionRuntimeProfile.swift`
- `ZeroFabricationPolicyKernel.swift`
- `PureVisionRuntimeGateEvaluator.swift`
- `DeterministicTriangulator.swift`
- `ScanTriangle.swift`
- `TriTetConsistencyEngine.swift`
- `SIMDHelpers.swift`

Why they stay:
- `QualityAnalyzer.runtimeAuditCaptureSignals()` returns `GeometryMLCaptureSignals`
- `PureVisionRuntimeGateEvaluator` still pulls together runtime metrics, zero-fabrication policy, cross-validation, and tri-tet consistency
- `TriTetConsistencyEngine` depends on `PureVisionRuntimeProfile`
- this is still the cleanest remaining "runtime quality + admission" layer after the big evidence stack was pushed out

## Not product-critical, but still compiled because of dependency drag

These are the most likely "next round" candidates. They are not obviously part of the user-visible whitebox loop, but deleting them now would require one more dependency cleanup pass.

### Evidence candidates

Likely next-pass candidates:
- `PatchDisplayMap.swift`
- `PatchWeightComputer.swift`
- `BucketedAmortizedAggregator.swift`
- `MemoryPressureHandler.swift`
- `Fusion/DSMassFusion.swift`
- `TriTetEvidenceMetadata.swift`

Why they are candidates:
- they look like leftover internal scoring / bookkeeping rather than product-critical flow
- they are not driving current user navigation or Danish SSH training
- they survive mainly because `SplitLedger`, `Observation`, and the retained quality models still reference them

What must happen before removal:
- either replace `SplitLedger` with a simpler transport-friendly snapshot model
- or remove the remaining evidence bookkeeping from the runtime quality path

### TSDF candidates

Likely next-pass candidates:
- `NativeICPRefiner.swift`
- `MeshOutput.swift`
- `AdaptiveResolution.swift`
- `SpatialHashTable.swift`
- `ManagedVoxelStorage.swift`
- `VoxelBlockPool.swift`
- `VoxelBlock.swift`
- `BlockIndex.swift`
- `TSDFIntegrationBackend.swift`

Why they are candidates:
- the current minimal app no longer presents TSDF mesh extraction UI
- these types are still hanging around mostly because `App/TSDF/MetalTSDFIntegrator.swift` and retained TSDF abstractions still compile against them

What must happen before removal:
- either remove `App/TSDF` from the active mobile build path
- or replace the remaining TSDF-backed capture plumbing with a thinner pure capture/runtime-quality abstraction

Hard keep for now:
- `NativePoseStabilizerBridge.swift`

### PureVision candidates

Likely next-pass candidates:
- `CrossValidationFusion.swift`
- `NativePureVisionRuntimeBridge.swift`
- parts of `GeometryMLFusionEngine.swift`

Why they are candidates:
- they are still richer than the professor's minimum "runtime quality review" requirement
- some of this stack looks like a generalized quality/admission framework rather than a ruthlessly small mobile closed loop

What must happen before removal:
- decide whether the product really needs the current pure-vision gate framework
- if not, compress runtime review down to a thinner capture-quality bundle:
  blur, exposure, motion, coverage prompt, and a single upload-admission verdict

Hard keep for now:
- `PureVisionRuntimeGateEvaluator.swift`
- `PureVisionRuntimeProfile.swift`
- `ZeroFabricationPolicyKernel.swift`
- `TriTetConsistencyEngine.swift`

## Practical reading of the professor's strategy

If we follow the professor literally, the product priority is:

1. capture
2. runtime quality review
3. remote training on the Danish golden box
4. download the result package
5. local 3DGS interactive viewing

Everything outside those five items should be treated as guilty until proven necessary.

That means the most suspicious remaining groups are:
- evidence bookkeeping that exists only to support richer internal scoring histories
- TSDF storage/extraction abstractions that no longer surface in the product UI
- generalized pure-vision validation layers that exceed the minimum runtime review requirement

## Recommended next cut order

To stay safe, the next cuts should happen in this order:

1. Thin `Evidence` again
   - target `PatchDisplayMap`, `PatchWeightComputer`, `BucketedAmortizedAggregator`, `MemoryPressureHandler`, `DSMassFusion`
2. Decide whether `App/TSDF` is still part of the mobile shipping path
   - if not, remove the TSDF storage/integration support layer behind it
3. Compress `PureVision`
   - keep one runtime gate bundle
   - remove anything that only exists for generalized audit or extended cross-validation

## Current conclusion

The important news is positive:
- the history / provenance / merkle / replay / multi-job / upload-platform layer is already out of the main closed-loop target
- the remaining runtime support is much smaller than before

But there is still one more meaningful slimming pass available:
- `Evidence` can likely be reduced to a much thinner snapshot model
- `TSDF` can likely be reduced to pose stabilization plus the smallest shared math/types layer
- `PureVision` can likely be compressed to a minimal runtime-review gate set
