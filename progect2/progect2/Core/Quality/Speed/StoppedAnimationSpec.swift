// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
//  StoppedAnimationSpec.swift
//  Aether3D
//
//  PR#5 Quality Pre-check - Milestone 6
//  StoppedAnimationSpec - stopped breathing animation specification (P8/H2)
//

import Foundation
import CAetherNativeBridge

/// StoppedAnimationSpec - specification for stopped breathing animation
/// P8: 0.5Hz alpha pulse only, no geometry deformation
/// H2: Deterministic animation parameters
public struct StoppedAnimationSpec {
    /// Frequency: 0.5Hz (2 second period)
    public static let frequencyHz: Double = QualityPreCheckConstants.STOPPED_ANIMATION_FREQUENCY_HZ
    
    /// Calculate alpha value for current time
    /// H2: Deterministic calculation based on time, no randomness
    public static func calculateAlpha(timestampMs: Int64) -> Double {
        var alpha = 0.5
        let rc = aether_quality_stopped_animation_alpha(
            timestampMs,
            frequencyHz,
            &alpha
        )
        return rc == 0 ? alpha : 0.5
    }
    
    /// P8/H2: Hard rules
    /// - Only alpha/transparency pulse allowed
    /// - No mesh/triangle/quad geometry deformation
    /// - No vertex movement
    /// - No topology changes
    /// - Continuous time-based cycle, sampled every frame
}
