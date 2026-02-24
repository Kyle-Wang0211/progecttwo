// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
//  SpeedSmoothing.swift
//  Aether3D
//
//  PR#5 Quality Pre-check - Milestone 6
//  SpeedSmoothing - smooth speed transitions
//

import Foundation
import CAetherNativeBridge

/// SpeedSmoothing - smooth speed transitions
/// Max change rate: 30% per 200ms
public struct SpeedSmoothing {
    /// Smooth speed change
    /// Returns smoothed speed value
    public static func smooth(
        currentSpeed: Double,
        targetSpeed: Double,
        timeDeltaMs: Int64
    ) -> Double {
        var outSpeed = currentSpeed
        let rc = aether_quality_smooth_speed(
            currentSpeed,
            targetSpeed,
            timeDeltaMs,
            QualityPreCheckConstants.SPEED_MAX_CHANGE_RATE,
            QualityPreCheckConstants.SPEED_SMOOTHING_WINDOW_MS,
            &outSpeed
        )
        return rc == 0 ? outSpeed : currentSpeed
    }
}
