// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// OverlapEstimator.swift
// Aether3D
//
// Overlap Estimator - Frame overlap estimation
//

import Foundation
import CAetherNativeBridge

/// Overlap Estimator
///
/// Estimates frame overlap for photogrammetry.
public actor OverlapEstimator {
    
    /// Estimate frame overlap
    /// 
    /// 符合 PR5-02: Research-backed thresholds (FRAME_OVERLAP_FORWARD: 0.80, FRAME_OVERLAP_SIDE: 0.65)
    /// - Parameters:
    ///   - frame1: First frame
    ///   - frame2: Second frame
    ///   - direction: Motion direction
    /// - Returns: Overlap ratio (0.0 to 1.0)
    public func estimateOverlap(frame1: FrameData, frame2: FrameData, direction: MotionDirection) async -> Double {
        let dt = abs(frame2.timestamp.timeIntervalSince(frame1.timestamp))
        let directionCode: Int32
        switch direction {
        case .forward: directionCode = 0
        case .side: directionCode = 1
        case .backward: directionCode = 2
        }

        var native = aether_mobile_overlap_result_t()
        let rc = frame1.imageData.withUnsafeBytes { frame1Buffer in
            frame2.imageData.withUnsafeBytes { frame2Buffer in
                aether_mobile_estimate_overlap(
                    frame1Buffer.bindMemory(to: UInt8.self).baseAddress,
                    Int32(frame1.imageData.count),
                    frame2Buffer.bindMemory(to: UInt8.self).baseAddress,
                    Int32(frame2.imageData.count),
                    directionCode,
                    dt,
                    &native
                )
            }
        }
        guard rc == 0, native.overlap_ratio.isFinite else {
            // Fail-closed: return SSOT baseline threshold for requested direction.
            switch direction {
            case .forward, .backward:
                return QualityThresholds.frameOverlapForward
            case .side:
                return QualityThresholds.frameOverlapSide
            }
        }

        return max(0.0, min(1.0, native.overlap_ratio))
    }
}

/// Motion Direction
public enum MotionDirection: Sendable {
    case forward
    case side
    case backward
}
