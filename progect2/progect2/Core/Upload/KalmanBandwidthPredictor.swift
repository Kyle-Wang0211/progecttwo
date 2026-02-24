// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// CONSTITUTIONAL CONTRACT - DO NOT EDIT WITHOUT RFC
// Contract Version: PR9-NETWORK-1.0
// Module: Upload Infrastructure - Kalman Bandwidth Predictor
// Cross-Platform: macOS + Linux (pure Foundation)

import Foundation
import CAetherNativeBridge

/// Bandwidth prediction result.
public struct BandwidthPrediction: Sendable {
    public let predictedBps: Double
    public let confidenceInterval95: (low: Double, high: Double)
    public let trend: BandwidthTrend
    public let isReliable: Bool       // trace(P) convergence check
    public let source: PredictionSource
}

/// Bandwidth trend indicator.
public enum BandwidthTrend: String, Sendable {
    case rising
    case stable
    case falling
}

/// Prediction source.
public enum PredictionSource: String, Sendable {
    case kalman
    case ml
    case ensemble
}

/// Bandwidth estimator protocol.
public protocol BandwidthEstimator: Sendable {
    func addSample(bytesTransferred: Int64, durationSeconds: TimeInterval) async
    func predict() async -> BandwidthPrediction
    func reset() async
}

/// 4D Kalman filter for bandwidth prediction.
///
/// **State vector (4D):** `[bandwidth, d_bandwidth/dt, d2_bandwidth/dt2, variance]`
///
/// **Key parameters:**
/// - Process noise Q: adaptive (10x increase on NWPathMonitor network change events)
/// - Measurement noise R: dynamic based on last 10 samples' variance
/// - Initial covariance P0: `diag(100, 10, 1, 50)`
/// - Anomaly threshold: Mahalanobis distance > 2.5σ → reduce sample weight
/// - Convergence indicator: `trace(P) < 5.0` → mark "estimate reliable"
public actor KalmanBandwidthPredictor: BandwidthEstimator {
    private var nativeState = aether_kalman_bandwidth_state_t()
    private var nativeEnabled = false
    private var totalSamples = 0
    private let networkPathObserver: NetworkPathObserver?

    public init(networkPathObserver: NetworkPathObserver? = nil) {
        self.networkPathObserver = networkPathObserver
        _ = networkPathObserver
        nativeEnabled = aether_bandwidth_kalman_reset(&nativeState) == 0
    }

    public func addSample(bytesTransferred: Int64, durationSeconds: TimeInterval) async {
        guard durationSeconds.isFinite, durationSeconds > 0 else { return }
        guard nativeEnabled else { return }

        var nativeOut = aether_kalman_bandwidth_output_t(
            predicted_bps: 0,
            ci_low: 0,
            ci_high: 0,
            trend: 1,
            reliable: 0
        )
        let rc = aether_bandwidth_kalman_step(
            &nativeState,
            bytesTransferred,
            durationSeconds,
            &nativeOut
        )
        if rc == 0 {
            totalSamples += 1
        } else {
            nativeEnabled = false
        }
    }

    public func predict() async -> BandwidthPrediction {
        guard nativeEnabled else {
            return Self.failClosedPrediction()
        }
        var nativeOut = aether_kalman_bandwidth_output_t(
            predicted_bps: 0,
            ci_low: 0,
            ci_high: 0,
            trend: 1,
            reliable: 0
        )
        let rc = aether_bandwidth_kalman_predict(&nativeState, &nativeOut)
        guard rc == 0 else {
            nativeEnabled = false
            return Self.failClosedPrediction()
        }

        return BandwidthPrediction(
            predictedBps: nativeOut.predicted_bps,
            confidenceInterval95: (
                low: max(0.0, nativeOut.ci_low),
                high: max(nativeOut.ci_low, nativeOut.ci_high)
            ),
            trend: nativeTrend(nativeOut.trend),
            isReliable: nativeOut.reliable != 0,
            source: .kalman
        )
    }

    public func reset() async {
        totalSamples = 0
        nativeState = aether_kalman_bandwidth_state_t()
        nativeEnabled = aether_bandwidth_kalman_reset(&nativeState) == 0
    }

    private static func failClosedPrediction() -> BandwidthPrediction {
        BandwidthPrediction(
            predictedBps: 0,
            confidenceInterval95: (low: 0, high: 0),
            trend: .stable,
            isReliable: false,
            source: .kalman
        )
    }

    private func nativeTrend(_ raw: Int32) -> BandwidthTrend {
        switch raw {
        case 0:
            return .rising
        case 2:
            return .falling
        default:
            return .stable
        }
    }
}
