// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
//  SpeedController.swift
//  Aether3D
//
//  PR#5 Quality Pre-check - Milestone 6
//  SpeedController - separated progress speed and animation speed (PART 7, H2)
//

import Foundation
import CAetherNativeBridge

/// SpeedController - manages speed feedback
/// Separates progress speed (0-100%) and animation speed (5-100%)
public class SpeedController {
    private var currentProgressSpeed: Double = 0.0
    private var currentAnimationSpeed: Double = 5.0  // Never stops (minimum 5%)
    private var currentTier: SpeedTier = .stopped
    
    public init() {}
    
    /// Update speed based on metrics and no progress duration
    /// H2: Deterministic calculation, no randomness
    public func updateSpeed(
        whiteCoverageIncrement: Int,
        noProgressDurationMs: Int64
    ) {
        var native = aether_speed_feedback_result_t()
        let rc = aether_quality_speed_feedback(
            Int32(whiteCoverageIncrement),
            noProgressDurationMs,
            currentAnimationSpeed,
            QualityPreCheckConstants.SPEED_SMOOTHING_WINDOW_MS,
            QualityPreCheckConstants.SPEED_MAX_CHANGE_RATE,
            QualityPreCheckConstants.SPEED_SMOOTHING_WINDOW_MS,
            QualityPreCheckConstants.NO_PROGRESS_WARNING_MS,
            &native
        )
        guard rc == 0 else {
            return
        }

        currentProgressSpeed = native.progress_speed
        currentAnimationSpeed = native.animation_speed
        currentTier = SpeedTier(nativeCode: native.tier) ?? .stopped
    }
    
    /// Get current speed tier
    public func getCurrentTier() -> SpeedTier {
        return currentTier
    }
    
    /// Get progress speed (0-100%)
    public func getProgressSpeed() -> Double {
        return currentProgressSpeed
    }
    
    /// Get animation speed (5-100%)
    public func getAnimationSpeed() -> Double {
        return currentAnimationSpeed
    }
}
