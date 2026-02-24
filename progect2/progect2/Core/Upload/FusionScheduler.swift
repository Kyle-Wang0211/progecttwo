// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// CONSTITUTIONAL CONTRACT - DO NOT EDIT WITHOUT RFC
// Contract Version: PR9-SCHEDULER-1.0
// Module: Upload Infrastructure - Fusion Scheduler
// Cross-Platform: macOS + Linux (pure Foundation)

import Foundation
import CAetherNativeBridge

public actor FusionScheduler {

    private let kalmanPredictor: KalmanBandwidthPredictor
    private let mlPredictor: MLBandwidthPredictor?

    // MPC, ABR, EWMA, Kalman, ML
    private var controllerAccuracies: [Double] = [1.0, 1.0, 1.0, 1.0, 1.0]
    private var queueLength: Int64 = 0
    private var lastChunkSize: Int = UploadConstants.CHUNK_SIZE_DEFAULT_BYTES
    private var nativeEnabled = true

    public init(
        kalmanPredictor: KalmanBandwidthPredictor,
        mlPredictor: MLBandwidthPredictor? = nil
    ) {
        self.kalmanPredictor = kalmanPredictor
        self.mlPredictor = mlPredictor
    }

    public func decideChunkSize() async -> Int {
        let kalmanPrediction = await kalmanPredictor.predict()
        let mlPrediction = await (mlPredictor?.predict() ?? kalmanPrediction)
        guard nativeEnabled else {
            let failClosed = UploadConstants.CHUNK_SIZE_DEFAULT_BYTES
            lastChunkSize = failClosed
            return failClosed
        }

        var input = aether_upload_fusion_scheduler_input_t()
        input.queue_length_bytes = queueLength
        input.last_chunk_size_bytes = Int32(clamping: lastChunkSize)
        input.kalman_predicted_bps = sanitizeBps(kalmanPrediction.predictedBps)
        input.kalman_trend = nativeTrendCode(kalmanPrediction.trend)
        input.ml_predicted_bps = sanitizeBps(mlPrediction.predictedBps)
        input.has_ml_prediction = mlPredictor == nil ? 0 : 1
        input.controller_accuracy_mpc = controllerAccuracy(at: 0)
        input.controller_accuracy_abr = controllerAccuracy(at: 1)
        input.controller_accuracy_ewma = controllerAccuracy(at: 2)
        input.controller_accuracy_kalman = controllerAccuracy(at: 3)
        input.controller_accuracy_ml = controllerAccuracy(at: 4)
        input.chunk_size_min_bytes = Int32(clamping: UploadConstants.CHUNK_SIZE_MIN_BYTES)
        input.chunk_size_default_bytes = Int32(clamping: UploadConstants.CHUNK_SIZE_DEFAULT_BYTES)
        input.chunk_size_max_bytes = Int32(clamping: UploadConstants.CHUNK_SIZE_MAX_BYTES)
        input.chunk_size_step_bytes = Int32(clamping: UploadConstants.CHUNK_SIZE_STEP_BYTES)
        input.ewma_alpha = 0.3
        input.ewma_target_seconds = 3.0
        input.ml_norm_bps = 10_000_000.0
        input.alignment_bytes = 16 * 1024

        var output = aether_upload_fusion_scheduler_output_t()
        let rc = aether_upload_fusion_decide_chunk_size(&input, &output)
        guard rc == 0 else {
            nativeEnabled = false
            let failClosed = UploadConstants.CHUNK_SIZE_DEFAULT_BYTES
            lastChunkSize = failClosed
            return failClosed
        }

        let nextChunkSize = clampAndAlign(Int(output.final_chunk_size_bytes))
        lastChunkSize = nextChunkSize
        return nextChunkSize
    }

    public func updateQueueLength(_ length: Int64) {
        queueLength = max(0, length)
    }

    public func updateLastChunkSize(_ size: Int) {
        lastChunkSize = clampAndAlign(size)
    }

    private func controllerAccuracy(at index: Int) -> Double {
        guard controllerAccuracies.indices.contains(index) else { return 1.0 }
        let value = controllerAccuracies[index]
        return value.isFinite ? value : 1.0
    }

    private func sanitizeBps(_ value: Double) -> Double {
        guard value.isFinite, value > 0 else { return 0.0 }
        return value
    }

    private func nativeTrendCode(_ trend: BandwidthTrend) -> Int32 {
        switch trend {
        case .rising:
            return 0
        case .stable:
            return 1
        case .falling:
            return 2
        }
    }

    private func clampAndAlign(_ size: Int) -> Int {
        let minSize = UploadConstants.CHUNK_SIZE_MIN_BYTES
        let maxSize = UploadConstants.CHUNK_SIZE_MAX_BYTES
        let clamped = max(minSize, min(maxSize, size))
        let aligned = (clamped / 16384) * 16384
        return max(minSize, min(maxSize, aligned))
    }
}
