# Minimal Whitebox Constants And Native Inventory

Date: 2026-03-21

Scope:
- `/Users/kaidongwang/Documents/Aether3D/Core/Constants`
- `/Users/kaidongwang/Documents/Aether3D/aether_cpp`

Goal:
- distinguish constants still used by the current minimal whitebox loop from historical PR compatibility shells
- distinguish native subsystems that are still required by the current mobile whitebox path from those that are compiled but can be removed in the next pruning pass

## 1. Core/Constants

### 1.1 Keep now: directly useful to the current whitebox loop

- `CaptureRecordingConstants.swift`
  - active in the current capture path
  - used by `/Users/kaidongwang/Documents/Aether3D/App/Capture/CameraSession.swift`
  - used by `/Users/kaidongwang/Documents/Aether3D/App/Capture/RecordingController.swift`
  - used by `/Users/kaidongwang/Documents/Aether3D/App/Capture/InterruptionHandler.swift`

- `ThermalConstants.swift`
  - active in the current mobile capture runtime
  - used by `/Users/kaidongwang/Documents/Aether3D/App/Capture/ThermalMonitor.swift`

- `PipelineTimeoutConstants.swift`
  - active in the current SSH remote-training loop
  - used by `/Users/kaidongwang/Documents/Aether3D/Core/Pipeline/PipelineRunner.swift`

### 1.2 Keep for now, but these are secondary runtime scaffolds rather than whitebox-core

- `QualityThresholds.swift`
  - used by the current Swift quality helper stack
  - examples:
    - `/Users/kaidongwang/Documents/Aether3D/Core/Quality/QualityAnalyzer.swift`
    - `/Users/kaidongwang/Documents/Aether3D/Core/Quality/Metrics/BlurDetector.swift`
    - `/Users/kaidongwang/Documents/Aether3D/Core/Quality/Metrics/TextureAnalyzer.swift`
    - `/Users/kaidongwang/Documents/Aether3D/Core/Quality/QualityFeedback.swift`
    - `/Users/kaidongwang/Documents/Aether3D/Core/Quality/OverlapEstimator.swift`

- `QualityPreCheckConstants.swift`
  - still feeds the quality hint / direction / degradation helpers
  - examples:
    - `/Users/kaidongwang/Documents/Aether3D/Core/Quality/Hints/HintController.swift`
    - `/Users/kaidongwang/Documents/Aether3D/Core/Quality/Direction/DirectionManager.swift`
    - `/Users/kaidongwang/Documents/Aether3D/Core/Quality/Degradation/DegradationController.swift`

- `FrameQualityConstants.swift`
  - still used by the local photometric/material analyzers and the slim PureVision gate layer
  - examples:
    - `/Users/kaidongwang/Documents/Aether3D/Core/Quality/Metrics/PhotometricConsistencyChecker.swift`
    - `/Users/kaidongwang/Documents/Aether3D/Core/Quality/Metrics/MaterialAnalyzer.swift`
    - `/Users/kaidongwang/Documents/Aether3D/Core/Quality/PureVision/PureVisionRuntimeGateEvaluator.swift`

- `PureVisionRuntimeConstants.swift`
  - still used by the remaining PureVision gate surface
  - examples:
    - `/Users/kaidongwang/Documents/Aether3D/Core/Quality/PureVision/PureVisionRuntimeGateEvaluator.swift`
    - also referenced by dormant-but-still-on-disk files such as `GeometryMLFusionEngine.swift`

### 1.3 Historical or compatibility shells for the current whitebox loop

- `SSOT.swift`
  - no live `SSOT.` call sites outside the constants layer itself
  - effectively a compatibility facade now

- `SSOTVersion.swift`
- `SSOTRegistry.swift`
- `SSOTValidation.swift`
- `SSOTHelpers.swift`
- `SSOTError.swift`
- `SSOTErrorCode.swift`
- `SSOTErrorRecord.swift`
- `SSOTLogEvent.swift`
- `SSOTTypes.swift`
- `SSOTKeys.swift`
- `SSOTRegistry.swift`
  - these mainly support the old SSOT/registry architecture, not the current minimal whitebox user path

- `CoreConstantsManifest.swift`
  - currently has no external references outside its own file
  - pure compatibility shell at the moment

- `ScanGuidanceConstants.swift`
  - current code references it only in comments and archive notes
  - live guidance values were already inlined elsewhere

- `UploadConstants.swift`
- `BundleConstants.swift`
- `APIContractConstants.swift`
  - still referenced by `/Users/kaidongwang/Documents/Aether3D/Core/Upload` and `/Users/kaidongwang/Documents/Aether3D/Core/Network`
  - but not part of the current SSH-to-Denmark whitebox loop

- `RetryConstants.swift`
- `StorageConstants.swift`
- `SystemConstants.swift`
- `ErrorCodes.swift`
- `ErrorDomain.swift`
  - still mostly consumed through the old SSOT compatibility surface

- `CoverageVisualizationConstants.swift`
- `CoveragePolicy.swift`
- `PatchPolicy.swift`
- `EvidenceBudgetPolicy.swift`
- `CaptureProfile.swift`
- `GridResolutionPolicy.swift`
  - mostly policy-table infrastructure and digest/spec machinery
  - not currently wired into the shipping whitebox capture -> SSH -> viewer loop

- `ObservationConstants.swift`
  - used by `/Users/kaidongwang/Documents/Aether3D/Core/Models/ObservationModel.swift`
  - but that whole observation-model track currently has no live app call site

- `PIZThresholds.swift`
- `PIZConstants.swift`
  - now mostly serve compatibility / canonicalization code
  - not used by the current whitebox user flow

- `ComplianceConstants.swift`
- `AuditProtocols.swift`
- `UserFacingOutputContract.swift`
  - archival or non-whitebox policy surface

## 2. Native aether_cpp

Reference build boundary:
- `/Users/kaidongwang/Documents/Aether3D/Package.swift`

Current reality:
- `innovation`, `geo`, `merkle`, `upload` are already out of the compiled source list
- the next shrink target is inside the still-compiled `evidence`, `quality`, `render`, and `tsdf` buckets

### 2.1 Must keep now for the minimal whitebox loop

- `src/pipeline/*`
  - current scan pipeline and coordinator
  - `/Users/kaidongwang/Documents/Aether3D/App/Scan/ScanViewModel.swift`
  - `/Users/kaidongwang/Documents/Aether3D/Core/Pipeline/PipelineCoordinatorBridge.swift`

- `src/splat/splat_render_engine.cpp`
- `src/splat/splat_c_api.cpp`
- `src/splat/spz_decoder.cpp`
  - current 3DGS local viewer path
  - `/Users/kaidongwang/Documents/Aether3D/App/GaussianSplatting/GaussianSplatViewController.swift`
  - `/Users/kaidongwang/Documents/Aether3D/Core/Render/NativeSplatEngineBridge.swift`

- `src/render/metal_gpu_device.mm`
- `src/render/metal_c_api.mm`
- `src/render/shader_source.cpp`
  - required to create the Metal-backed GPU device and shader pipeline used by the splat viewer

- `src/tsdf/pose_stabilizer.cpp`
  - directly used by `/Users/kaidongwang/Documents/Aether3D/Core/TSDF/NativePoseStabilizerBridge.swift`
  - active call sites in `/Users/kaidongwang/Documents/Aether3D/App/Scan/ScanViewModel.swift`

- `src/tsdf_volume.cpp`
- `src/tsdf/spatial_hash_table.cpp`
  - still used internally by `pipeline_coordinator.cpp`
  - `PipelineCoordinator` owns `std::unique_ptr<tsdf::TSDFVolume>`

- `src/evidence/smart_anti_boost_smoother.cpp`
  - still used internally by `pipeline_coordinator.cpp`
  - current use is loss smoothing during global training convergence

- `src/training/gaussian_training_engine.cpp`
- `src/trainer/da3_depth_fuser.cpp`
- `src/trainer/noise_aware_trainer.cpp`
  - still feed the current mobile training / depth fusion path

### 2.2 Safe next-cut candidates: compiled now, but only kept alive by dormant C API surface

#### Evidence

- `src/evidence/admission_controller.cpp`
- `src/evidence/coverage_estimator.cpp`
- `src/evidence/deterministic_json.cpp`
- `src/evidence/ds_mass_function.cpp`
- `src/evidence/patch_display_kernel.cpp`
- `src/evidence/pr1_admission_kernel.cpp`
- `src/evidence/pr1_information_gain.cpp`
- `src/evidence/pr_math.cpp`
- `src/evidence/replay_engine.cpp`
- `src/evidence/evidence_state_machine.cpp`

Reason:
- no current pipeline-internal dependency except `smart_anti_boost_smoother.cpp`
- the rest are primarily exposed through `c_api.cpp` for old evidence/admission/replay flows

#### Quality

- `src/quality/deterministic_triangulator.cpp`
- `src/quality/image_metrics.cpp`
- `src/quality/motion_analyzer.cpp`
- `src/quality/photometric_checker.cpp`
- `src/quality/geometry_ml_fusion.cpp`
- `src/quality/pure_vision_runtime.cpp`
- `src/quality/spatial_hash_adjacency.cpp`
- `src/quality/zero_fabrication_policy.cpp`
- `src/quality/quality_c_api.cpp`

Reason:
- current C++ pipeline code does not include these modules internally
- they survive mainly because `c_api.cpp` still exports old native quality helpers
- Swift-side direct native bridges are already unused or attached only to dormant files:
  - `/Users/kaidongwang/Documents/Aether3D/Core/Quality/NativeImageMetricsBridge.swift`
  - `/Users/kaidongwang/Documents/Aether3D/Core/Quality/NativeMotionAnalyzerBridge.swift`
  - `/Users/kaidongwang/Documents/Aether3D/Core/Quality/NativePhotometricCheckerBridge.swift`
  - `/Users/kaidongwang/Documents/Aether3D/Core/Quality/NativeRenderQualityBridge.swift`
  - `/Users/kaidongwang/Documents/Aether3D/Core/Quality/PureVision/NativePureVisionRuntimeBridge.swift`

#### Render

- `src/render/confidence_decay.cpp`
- `src/render/color_correction.cpp`
- `src/render/dgrut_renderer.cpp`
- `src/render/flip_animation.cpp`
- `src/render/flip_animation_gpu.cpp`
- `src/render/frustum_culler.cpp`
- `src/render/meshlet_builder.cpp`
- `src/render/ripple_propagation.cpp`
- `src/render/screen_detail_selector.cpp`
- `src/render/two_pass_culler.cpp`
- `src/render/tri_tet_splat_projector.cpp`
- `src/render/wedge_geometry.cpp`

Reason:
- current product viewer path uses `SplatRenderEngine`, not the older DGRUT / meshlet / wedge / flip / ripple / render-stability path
- most of these are only reachable through unused Swift bridges or dead C API exports

#### TSDF

- `src/tsdf/isotropic_remesher.cpp`
- `src/tsdf/marching_cubes.cpp`
- `src/tsdf/mesh_extraction_scheduler.cpp`
- `src/tsdf/mesh_fiedler.cpp`
- `src/tsdf/mesh_topology.cpp`
- `src/tsdf/depth_filter.cpp`
- `src/tsdf/icp_registration.cpp`
- `src/tsdf/loop_detector.cpp`
- `src/tsdf/pose_graph.cpp`
- `src/tsdf/soft_eviction.cpp`
- `src/tsdf/spatial_quantizer.cpp`
- `src/tsdf/thermal_engine.cpp`
- `src/tsdf/tri_tet_consistency.cpp`
- `src/tsdf/tri_tet_mapping.cpp`
- `src/tsdf/volume_controller.cpp`

Reason:
- current shipping scan flow only needs pose stabilization plus the coordinator-owned `TSDFVolume`
- these extra TSDF modules mainly support dormant bridges and old mesh / render-stability / tri-tet / thermal-control experiments

### 2.3 Native bridges that strongly indicate removable C++ debt

These Swift files still exist on disk, but they currently have no active app call site:

- `/Users/kaidongwang/Documents/Aether3D/Core/Quality/NativeImageMetricsBridge.swift`
- `/Users/kaidongwang/Documents/Aether3D/Core/Quality/NativeMotionAnalyzerBridge.swift`
- `/Users/kaidongwang/Documents/Aether3D/Core/Quality/NativePhotometricCheckerBridge.swift`
- `/Users/kaidongwang/Documents/Aether3D/Core/Render/NativeDGRUTSelectorBridge.swift`
- `/Users/kaidongwang/Documents/Aether3D/Core/Render/NativeMeshletBuilderBridge.swift`
- `/Users/kaidongwang/Documents/Aether3D/Core/Render/NativeTwoPassCullerBridge.swift`
- `/Users/kaidongwang/Documents/Aether3D/Core/TSDF/NativeMarchingCubesBridge.swift`
- `/Users/kaidongwang/Documents/Aether3D/Core/TSDF/NativeMeshExtractionSchedulerBridge.swift`
- `/Users/kaidongwang/Documents/Aether3D/Core/TSDF/NativeThermalEngineBridge.swift`
- `/Users/kaidongwang/Documents/Aether3D/Core/TSDF/NativeTriTetMappingBridge.swift`

Implication:
- once the corresponding dormant Swift files are excluded or deleted, the matching C++ sources can be removed from `CAetherNativeBridge` with low product risk

## 3. Recommended next pruning order

1. `Core/Constants`
   - delete or exclude the pure shells first:
   - `SSOT*`, `CoreConstantsManifest`, `ScanGuidanceConstants`, `UploadConstants`, `BundleConstants`, `APIContractConstants`

2. `aether_cpp`
   - keep only:
   - `pipeline`
   - `splat`
   - Metal GPU device
   - `pose_stabilizer`
   - `tsdf_volume`
   - `spatial_hash_table`
   - `smart_anti_boost_smoother`
   - training/depth-fusion pieces

3. after that
   - cut the dormant render bridges and TSDF bridges from Swift
   - then remove the matching `render/*`, `quality/*`, and `tsdf/*` source files from `CAetherNativeBridge`
