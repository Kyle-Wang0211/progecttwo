# Aether3DApp True-Source Whitelist

Date: 2026-03-21

## Goal

This whitelist defines the files and targets that belong to the current minimal whitebox production path:

`Home -> Capture -> Real-time Review -> Denmark 5090 -> Download -> Local 3DGS Viewer`

Anything outside this list is a cleanup candidate and should not remain in the active `Aether3DApp` run chain.

## 1. App Target Whitelist

### Entry

- `App/Aether3DApp.swift`

### Home

- `App/Home/HomePage.swift`
- `App/Home/HomeViewModel.swift`
- `App/Home/ScanRecord.swift`
- `App/Home/ScanRecordCell.swift`
- `App/Home/ScanRecordStore.swift`

### Capture

- `App/Capture/ARKitSessionManager.swift`
- `App/Capture/CameraCapabilities.swift`
- `App/Capture/CameraConfig.swift`
- `App/Capture/CameraSession.swift`
- `App/Capture/CameraSessionDelegate.swift`
- `App/Capture/CameraSessionError.swift`
- `App/Capture/CaptureMetadata.swift`
- `App/Capture/ClockProvider.swift`
- `App/Capture/IMUDataCollector.swift`
- `App/Capture/InterruptionHandler.swift`
- `App/Capture/LiDARDepthProcessor.swift`
- `App/Capture/RecordingConfig.swift`
- `App/Capture/RecordingController.swift`
- `App/Capture/RecordingResult.swift`
- `App/Capture/ThermalMonitor.swift`
- `App/Capture/TimerScheduler.swift`

### Scan

- `App/Scan/ARCameraPreview.swift`
- `App/Scan/ScanState.swift`
- `App/Scan/ScanView.swift`
- `App/Scan/ScanViewModel.swift`

### Scan Guidance

- `App/ScanGuidance/GuidanceHapticEngine.swift`
- `App/ScanGuidance/GuidanceHints.swift`
- `App/ScanGuidance/GuidanceToastPresenter.swift`
- `App/ScanGuidance/PointCloudOIRPipeline.swift`
- `App/ScanGuidance/ScanCaptureControls.swift`
- `App/ScanGuidance/ScanCompletionBridge.swift`

### Viewer

- `App/Viewer/SplatViewerView.swift`
- `App/GaussianSplatting/GaussianSplatViewController.swift`

### Shader and Model Resources

- `App/Shaders/GaussianSplat.metal`
- `App/Shaders/GaussianTraining.metal`
- `App/Shaders/PointCloudRender.metal`
- `App/Shaders/OIRSplatRender.metal`
- `App/Shaders/QualityMetrics.metal`
- `App/Shaders/QualityOverlay.metal`
- `DepthAnythingV2Small.mlmodelc`
- `DepthAnythingV2Large.mlmodelc`
- `Assets.xcassets`

## 2. Core SwiftPM Whitelist

### Products

- `Aether3DCore`

### Targets

- `CSQLite`
- `CAetherNativeBridge`
- `SharedSecurity`
- `Aether3DCore`

## 3. Native C++ Whitelist

- `aether_cpp/src/core/canonicalize.cpp`
- `aether_cpp/src/core/numeric_guard.cpp`
- `aether_cpp/src/crypto/sha256.cpp`
- `aether_cpp/src/evidence/smart_anti_boost_smoother.cpp`
- `aether_cpp/src/memory/arena.cpp`
- `aether_cpp/src/render/shader_source.cpp`
- `aether_cpp/src/render/metal_gpu_device.mm`
- `aether_cpp/src/render/metal_c_api.mm`
- `aether_cpp/src/trainer/da3_depth_fuser.cpp`
- `aether_cpp/src/trainer/noise_aware_trainer.cpp`
- `aether_cpp/src/training/gaussian_training_engine.cpp`
- `aether_cpp/src/tsdf/pose_stabilizer.cpp`
- `aether_cpp/src/tsdf/spatial_hash_table.cpp`
- `aether_cpp/src/tsdf_volume.cpp`
- `aether_cpp/src/mobile_whitebox_c_api.cpp`
- `aether_cpp/src/splat/spz_decoder.cpp`
- `aether_cpp/src/splat/splat_render_engine.cpp`
- `aether_cpp/src/splat/splat_c_api.cpp`
- `aether_cpp/src/pipeline/streaming_pipeline.cpp`
- `aether_cpp/src/pipeline/streaming_c_api.cpp`
- `aether_cpp/src/pipeline/pipeline_coordinator.cpp`
- `aether_cpp/src/pipeline/coordinator_c_api.cpp`
- `aether_cpp/src/pipeline/depth_inference_coreml.mm`
- `aether_cpp/src/thermal/thermal_predictor.cpp`

## 4. Cleanup Candidates Removed In This Pass

- `App/ScanGuidance/EnvironmentLightEstimator.swift`
- `App/ScanGuidance/EvidenceRenderer.swift`
- `App/ScanGuidance/ScanGuidanceVertexDescriptor.swift`
- Package products and test targets outside the minimal whitebox chain

## 5. Note About Remaining Schemes

Even after cleanup, Xcode may still show dependency-generated schemes such as Crypto- and SSH-related schemes. These come from active third-party package dependencies and are not part of the app’s own source whitelist.
