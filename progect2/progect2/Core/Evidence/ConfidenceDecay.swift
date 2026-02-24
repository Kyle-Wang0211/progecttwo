// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// ConfidenceDecay.swift
// Aether3D
//
// PR2 Patch V4 - Confidence Decay
// ONLY affects aggregation weight, NEVER modifies evidence
//

import Foundation
import CAetherNativeBridge

/// Confidence decay for stale patches
///
/// INVARIANT: This function NEVER modifies PatchEntry.evidence
/// It only returns a weight to be used in totalEvidence() computation
public enum ConfidenceDecay {
    
    /// Half-life in seconds
    public static let halfLifeSec: Double = EvidenceConstants.confidenceHalfLifeSec
    
    /// Compute WEIGHT for aggregation (NOT evidence value)
    ///
    /// Uses exponential decay: w(t) = 0.5 ^ (age / halfLife)
    ///
    /// - Parameters:
    ///   - lastUpdateMs: Last update timestamp in milliseconds
    ///   - currentTimeMs: Current timestamp in milliseconds
    /// - Returns: Decay weight [0, 1] where 1.0 = no decay, 0.0 = fully decayed
    public static func aggregationWeight(
        lastUpdateMs: Int64,
        currentTimeMs: Int64
    ) -> Double {
        var out: Double = 1.0
        let rc = aether_confidence_aggregation_weight(
            lastUpdateMs,
            currentTimeMs,
            halfLifeSec,
            &out
        )
        if rc == 0 {
            return out
        }
        return 1.0
    }
    
    /// Compute weight from TimeInterval (convenience)
    public static func aggregationWeight(
        lastUpdate: TimeInterval,
        currentTime: TimeInterval
    ) -> Double {
        let lastUpdateMs = Int64(lastUpdate * 1000.0)
        let currentTimeMs = Int64(currentTime * 1000.0)
        return aggregationWeight(lastUpdateMs: lastUpdateMs, currentTimeMs: currentTimeMs)
    }
}
