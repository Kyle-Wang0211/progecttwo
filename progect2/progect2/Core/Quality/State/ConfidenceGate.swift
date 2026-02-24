// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
//  ConfidenceGate.swift
//  Aether3D
//
//  PR#5 Quality Pre-check - Milestone 3
//  ConfidenceGate - pure helper for confidence checks (P19/P23)
//

import Foundation
import CAetherNativeBridge

/// ConfidenceGate - pure helper for confidence threshold checks
/// P19/P23: Cannot be used directly for final Gray→White decisions
/// Must be called only from within DecisionPolicy
public struct ConfidenceGate {
    /// Check Black→Gray confidence
    /// Threshold: 0.7 (exposure OR focus either passes)
    public static func checkBlackToGray(
        brightness: MetricResult?,
        laplacian: MetricResult?,
        focus: MetricResult?
    ) -> Bool {
        _ = laplacian
        let hasBrightness = brightness != nil
        let brightnessConfidence = brightness?.confidence ?? 0.0
        let hasFocus = focus != nil
        let focusConfidence = focus?.confidence ?? 0.0

        var outPass: Int32 = 0
        let rc = aether_quality_check_black_to_gray(
            hasBrightness ? 1 : 0,
            brightnessConfidence,
            hasFocus ? 1 : 0,
            focusConfidence,
            0.7,
            &outPass
        )
        return rc == 0 && outPass != 0
    }
    
    // NOTE: checkGrayToWhite has been moved to DecisionPolicy as private nested helper
    // This ensures compile-time sealing - cannot be called outside DecisionPolicy.swift
}
