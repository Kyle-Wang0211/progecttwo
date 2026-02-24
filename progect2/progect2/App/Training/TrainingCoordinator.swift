// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
//  TrainingCoordinator.swift
//  Aether3D
//
//  Coordinates the 3DGS training pipeline lifecycle from Swift.
//  Bridges between ARKit frame flow and C++ TrainingPipeline via C API.
//

import Foundation
#if canImport(CAetherNativeBridge)
import CAetherNativeBridge
#endif
#if canImport(QuartzCore)
import QuartzCore
#endif

/// Coordinates the 3DGS training pipeline lifecycle from Swift.
///
/// Architecture:
///   ARFrame (30fps) -> TrainingCoordinator -> C API -> C++ TrainingPipeline
///     -> aether_training_pipeline_micro_step / full_step
///     -> @Published state -> SwiftUI rerender
///
/// Pattern: @MainActor + ObservableObject + @Published
/// (consistent with ScanViewModel and PipelineDemoViewModel)
@MainActor
final class TrainingCoordinator: ObservableObject {

    // MARK: - Phase

    enum Phase: Int, Sendable {
        case idle = 0
        case capturing = 1
        case initializing = 2
        case training = 3
        case complete = 4
        case failed = 5

        var displayName: String {
            switch self {
            case .idle:          return "Idle"
            case .capturing:     return "Capturing"
            case .initializing:  return "Initializing"
            case .training:      return "Training"
            case .complete:      return "Complete"
            case .failed:        return "Failed"
            }
        }
    }

    // MARK: - Quality Metrics

    struct QualityMetrics: Sendable {
        let psnrEstimate: Float
        let ssimEstimate: Float
        let chamferEstimate: Float
        let normalConsistency: Float
        let scaleAccuracy: Float
        let coverageFScore: Float
        let psnrCILower: Float
        let psnrCIUpper: Float
        let meetsWorldModelStandard: Bool

        static let zero = QualityMetrics(
            psnrEstimate: 0, ssimEstimate: 0, chamferEstimate: 0,
            normalConsistency: 0, scaleAccuracy: 0, coverageFScore: 0,
            psnrCILower: 0, psnrCIUpper: 0, meetsWorldModelStandard: false
        )
    }

    // MARK: - Step Result

    struct StepResult: Sendable {
        let loss: Float
        let psnrEstimate: Float
        let gaussiansActive: UInt32
        let trainingStep: UInt32
        let phase: Phase
        let checkpointSaved: Bool
    }

    // MARK: - Published State

    @Published private(set) var phase: Phase = .idle
    @Published private(set) var currentQuality: QualityMetrics = .zero
    @Published private(set) var trainingStep: UInt32 = 0
    @Published private(set) var gaussianCount: UInt32 = 0
    @Published private(set) var lastLoss: Float = 0

    // MARK: - Private State

    private var pipeline: aether_training_pipeline_t?
    private let config: aether_pipeline_config_t
    private let checkpointDir: String

    // SLO tracking
    private var captureStartTime: CFTimeInterval = 0
    private var firstS5Time: CFTimeInterval = 0
    private var frameCount: UInt64 = 0
    private var jankFrameCount: UInt64 = 0
    private var lastFrameTime: CFTimeInterval = 0

    // Quality floor thresholds (document spec)
    static let qualityFloorPSNR: Float = 28.0
    static let qualityFloorSSIM: Float = 0.85
    static let qualityFloorCoverage: Float = 0.90
    static let qualityFloorNormal: Float = 0.80

    // MARK: - Init / Deinit

    init(
        config: aether_pipeline_config_t? = nil,
        checkpointDir: String = NSTemporaryDirectory()
    ) {
        self.config = config ?? aether_training_default_config()
        self.checkpointDir = checkpointDir
    }

    deinit {
        if let pipeline = pipeline {
            aether_training_pipeline_destroy(pipeline)
        }
    }

    // MARK: - Lifecycle

    /// Create the native training pipeline. Must be called before any other operation.
    func createPipeline() {
        var cfg = config
        pipeline = aether_training_pipeline_create(&cfg)
        phase = .idle
        trainingStep = 0
        gaussianCount = 0
        lastLoss = 0
        currentQuality = .zero
    }

    /// Destroy the native pipeline and reset state.
    func destroyPipeline() {
        if let pipeline = pipeline {
            aether_training_pipeline_destroy(pipeline)
        }
        pipeline = nil
        phase = .idle
        trainingStep = 0
        gaussianCount = 0
        lastLoss = 0
        currentQuality = .zero
    }

    /// Initialize training from TSDF voxel seeds.
    /// Transitions: idle -> initializing -> training (or failed).
    /// - Parameter seeds: Array of voxel seeds from TSDF reconstruction.
    /// - Returns: `true` if initialization succeeded.
    @discardableResult
    func initialize(seeds: [aether_voxel_seed_t]) -> Bool {
        guard let pipeline = pipeline else { return false }
        guard !seeds.isEmpty else {
            phase = .failed
            return false
        }

        phase = .initializing

        let status = seeds.withUnsafeBufferPointer { buffer in
            aether_training_pipeline_initialize(
                pipeline, buffer.baseAddress, buffer.count)
        }

        if status == AETHER_OK {
            phase = .training
            gaussianCount = aether_training_pipeline_gaussian_count(pipeline)
            trainingStep = 0
            return true
        }

        phase = .failed
        return false
    }

    // MARK: - Training Steps

    /// Execute a micro training step (forward pass + optimizer, no densification).
    /// Lightweight step suitable for per-frame execution at capture rate.
    /// - Parameter frame: Camera frame with RGB/depth data.
    /// - Returns: Step result, or `nil` if pipeline is not in training phase.
    func microStep(frame: aether_camera_frame_t) -> StepResult? {
        guard let pipeline = pipeline else { return nil }

        var cFrame = frame
        var result = aether_training_result_t()
        let status = aether_training_pipeline_micro_step(
            pipeline, &cFrame, &result)
        guard status == AETHER_OK else { return nil }

        trackFrameTiming()
        return convertResult(result)
    }

    /// Execute a full training step (forward + densification + quality update).
    /// Heavier step that includes densification evaluation and quality metric refresh.
    /// Should be called at a lower rate (e.g., every 5th frame or on demand).
    /// - Parameter frame: Camera frame with RGB/depth data.
    /// - Returns: Step result, or `nil` if pipeline is not in training phase.
    func fullStep(frame: aether_camera_frame_t) -> StepResult? {
        guard let pipeline = pipeline else { return nil }

        var cFrame = frame
        var result = aether_training_result_t()
        let status = aether_training_pipeline_full_step(
            pipeline, &cFrame, &result)
        guard status == AETHER_OK else { return nil }

        trackFrameTiming()
        updatePublishedState(result)
        return convertResult(result)
    }

    // MARK: - Quality

    /// Refresh quality metrics from the native pipeline.
    /// Updates `currentQuality` published property.
    func refreshQuality() {
        guard let pipeline = pipeline else { return }

        var metrics = aether_quality_metrics_t()
        let status = aether_training_pipeline_get_quality(pipeline, &metrics)
        guard status == AETHER_OK else { return }

        currentQuality = QualityMetrics(
            psnrEstimate: metrics.psnr_estimate,
            ssimEstimate: metrics.ssim_estimate,
            chamferEstimate: metrics.chamfer_estimate,
            normalConsistency: metrics.normal_consistency,
            scaleAccuracy: metrics.scale_accuracy,
            coverageFScore: metrics.coverage_f_score,
            psnrCILower: metrics.psnr_ci_lower,
            psnrCIUpper: metrics.psnr_ci_upper,
            meetsWorldModelStandard: metrics.meets_world_model_standard != 0
        )

        // Record first S5 achievement
        if currentQuality.meetsWorldModelStandard {
            recordFirstS5()
        }
    }

    // MARK: - Phase Query

    /// Query the current phase from the native pipeline.
    /// Useful for verifying Swift-side state matches C++ state.
    func refreshPhase() {
        guard let pipeline = pipeline else { return }
        let nativePhase = aether_training_pipeline_get_phase(pipeline)
        phase = Phase(rawValue: Int(nativePhase.rawValue)) ?? .failed
    }

    // MARK: - Checkpoint

    /// Save a training checkpoint to disk.
    /// - Returns: `true` if checkpoint was saved successfully.
    @discardableResult
    func saveCheckpoint() -> Bool {
        guard let pipeline = pipeline else { return false }
        let path = (checkpointDir as NSString)
            .appendingPathComponent("training_checkpoint.bin")
        return aether_training_pipeline_save_checkpoint(pipeline, path) == AETHER_OK
    }

    /// Load a training checkpoint from disk.
    /// - Returns: `true` if checkpoint was loaded successfully.
    @discardableResult
    func loadCheckpoint() -> Bool {
        guard let pipeline = pipeline else { return false }
        let path = (checkpointDir as NSString)
            .appendingPathComponent("training_checkpoint.bin")
        let status = aether_training_pipeline_load_checkpoint(pipeline, path)
        guard status == AETHER_OK else { return false }

        // Sync published state after load
        gaussianCount = aether_training_pipeline_gaussian_count(pipeline)
        trainingStep = aether_training_pipeline_training_step(pipeline)
        refreshPhase()
        refreshQuality()
        return true
    }

    // MARK: - SLO Metrics

    /// Jank rate: fraction of frames that exceeded 2x target interval.
    var jankRate: Float {
        guard frameCount > 0 else { return 0 }
        return Float(jankFrameCount) / Float(frameCount)
    }

    /// Time-To-First-S5: seconds from capture start to first world-model-standard
    /// quality achievement. Returns -1 if not yet achieved.
    var ttfs5: CFTimeInterval {
        guard firstS5Time > 0, captureStartTime > 0 else { return -1 }
        return firstS5Time - captureStartTime
    }

    /// Total frames processed since capture start.
    var totalFrames: UInt64 { frameCount }

    /// Total jank frames since capture start.
    var totalJankFrames: UInt64 { jankFrameCount }

    /// Mark the start of a capture session for SLO timing.
    func startCapture() {
        captureStartTime = CACurrentMediaTime()
        firstS5Time = 0
        frameCount = 0
        jankFrameCount = 0
        lastFrameTime = 0
        phase = .capturing
    }

    /// Record the first time world-model-standard (S5) quality is achieved.
    func recordFirstS5() {
        if firstS5Time == 0 {
            firstS5Time = CACurrentMediaTime()
        }
    }

    // MARK: - Computed State

    /// Whether the pipeline is actively training.
    var isTraining: Bool { phase == .training }

    /// Whether training has completed successfully.
    var isComplete: Bool { phase == .complete }

    /// Whether the pipeline has encountered an error.
    var hasFailed: Bool { phase == .failed }

    /// Whether the pipeline is ready to accept training steps.
    var isReady: Bool { pipeline != nil && (phase == .training || phase == .idle) }

    // MARK: - Private Helpers

    private func trackFrameTiming() {
        let now = CACurrentMediaTime()
        if lastFrameTime > 0 {
            let dt = now - lastFrameTime
            // Target: 60fps -> 16.67ms per frame
            // Jank threshold: 2x target = 33.33ms
            let targetInterval = 1.0 / 60.0
            if dt > 2.0 * targetInterval {
                jankFrameCount += 1
            }
        }
        lastFrameTime = now
        frameCount += 1
    }

    private func updatePublishedState(_ result: aether_training_result_t) {
        trainingStep = result.training_step
        gaussianCount = result.gaussians_active
        lastLoss = result.loss
        phase = Phase(rawValue: Int(result.phase.rawValue)) ?? .failed

        // Auto-refresh quality after full step
        refreshQuality()
    }

    private func convertResult(_ result: aether_training_result_t) -> StepResult {
        StepResult(
            loss: result.loss,
            psnrEstimate: result.psnr_estimate,
            gaussiansActive: result.gaussians_active,
            trainingStep: result.training_step,
            phase: Phase(rawValue: Int(result.phase.rawValue)) ?? .failed,
            checkpointSaved: result.checkpoint_saved != 0
        )
    }
}
