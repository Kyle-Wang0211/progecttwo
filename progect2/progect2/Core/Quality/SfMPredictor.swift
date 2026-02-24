// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// SfMPredictor.swift
// Aether3D
//
// SfM Predictor - SfM success prediction based on quality metrics
//

import Foundation
import CAetherNativeBridge

/// SfM Predictor
///
/// Predicts SfM (Structure from Motion) success based on quality metrics.
public actor SfMPredictor {
    
    /// Predict SfM success
    /// 
    /// 符合 PR5: SfM success prediction based on quality metrics
    /// - Parameter report: Capture quality report
    /// - Returns: Prediction result
    public func predictSuccess(_ report: CaptureQualityReport) -> SfMPrediction {
        let totalProblemFrames = report.problemSegments.reduce(0) { $0 + ($1.endFrame - $1.startFrame + 1) }
        var native = aether_mobile_sfm_prediction_t()
        let rc = aether_mobile_predict_sfm_success(
            Int32(report.totalFrames),
            Int32(report.acceptableFrames),
            report.overallTier == .rejected ? 1 : 0,
            Int32(totalProblemFrames),
            &native
        )
        guard rc == 0 else {
            return SfMPrediction(
                willSucceed: false,
                confidence: 0.0,
                reason: "Native SfM predictor failed"
            )
        }

        let reason: String
        switch native.reason_code {
        case 1:
            reason = "Too many rejected frames"
        case 2:
            reason = "Insufficient acceptable frames"
        case 3:
            reason = "Too many problem segments"
        default:
            reason = "Quality metrics meet requirements"
        }

        return SfMPrediction(
            willSucceed: native.will_succeed != 0,
            confidence: native.confidence,
            reason: reason
        )
    }
}

/// SfM Prediction
///
/// Prediction result for SfM success.
public struct SfMPrediction: Sendable {
    public let willSucceed: Bool
    public let confidence: Double
    public let reason: String
    
    public init(willSucceed: Bool, confidence: Double, reason: String) {
        self.willSucceed = willSucceed
        self.confidence = confidence
        self.reason = reason
    }
}
