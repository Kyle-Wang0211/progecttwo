// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
//  DecisionPolicy.swift
//  Aether3D
//
//  PR#5 Quality Pre-check - Milestone 3
//  DecisionPolicy - single source of truth for Gray→White decisions (P10/P19/H2)
//

import Foundation
import CAetherNativeBridge

/// DecisionPolicy - single source of truth for state transition decisions
/// P19: Only component that can decide Gray→White
/// H2: Failure semantics (Safety > Consistency > UX, uncertainty blocks White)
public struct DecisionPolicy {
    /// Check if transition is allowed
    /// P10: FPS White Policy locked - ONLY Full tier allows Gray→White
    /// Degraded and Emergency tiers BLOCK Gray→White
    /// H2: Uncertainty blocks White
    public static func canTransition(
        from: VisualState,
        to: VisualState,
        fpsTier: FpsTier,
        criticalMetrics: CriticalMetricBundle?,
        stability: Double?
    ) -> (allowed: Bool, reason: String?) {
        let hasCriticalMetrics = criticalMetrics != nil
        let brightnessConfidence = criticalMetrics?.brightness.confidence ?? 0.0
        let laplacianConfidence = criticalMetrics?.laplacian.confidence ?? 0.0
        let hasStability = stability != nil
        let stabilityValue = stability ?? 0.0

        var nativeResult = aether_quality_transition_result_t()
        let rc = aether_quality_can_transition(
            from.nativeCode,
            to.nativeCode,
            fpsTier.nativeCode,
            hasCriticalMetrics ? 1 : 0,
            brightnessConfidence,
            laplacianConfidence,
            hasStability ? 1 : 0,
            stabilityValue,
            QualityPreCheckConstants.CONFIDENCE_THRESHOLD_FULL,
            QualityPreCheckConstants.FULL_WHITE_STABILITY_MAX,
            &nativeResult
        )
        guard rc == 0 else {
            return (false, "Native transition evaluation failed")
        }
        if nativeResult.allowed != 0 {
            return (true, nil)
        }
        return (false, nativeTransitionReasonMessage(reason: nativeResult.reason, fpsTier: fpsTier))
    }

    // MARK: - Profile-Aware Thresholds (PR5-QUALITY-2.0)

    /// Get effective Laplacian threshold for given capture profile
    /// Different profiles have different sharpness requirements
    public static func getEffectiveLaplacianThreshold(
        for profile: CaptureProfile
    ) -> Double {
        let base = FrameQualityConstants.blurThresholdLaplacian

        switch profile {
        case .standard:
            return base  // 200
        case .smallObjectMacro:
            return base * FrameQualityConstants.LAPLACIAN_MULTIPLIER_PRO_MACRO  // 250
        case .largeScene:
            return base * FrameQualityConstants.LAPLACIAN_MULTIPLIER_LARGE_SCENE  // 180
        case .proMacro:
            return base * FrameQualityConstants.LAPLACIAN_MULTIPLIER_PRO_MACRO  // 250
        case .cinematicScene:
            return base * FrameQualityConstants.LAPLACIAN_MULTIPLIER_CINEMATIC  // 180
        }
    }

    /// Get effective minimum ORB feature count for given capture profile
    public static func getEffectiveMinFeatureCount(
        for profile: CaptureProfile
    ) -> Int {
        let base = FrameQualityConstants.MIN_ORB_FEATURES_FOR_SFM

        switch profile {
        case .standard, .largeScene:
            return base  // 500
        case .smallObjectMacro, .proMacro:
            return Int(Double(base) * FrameQualityConstants.FEATURE_MULTIPLIER_PRO_MACRO)  // 600
        case .cinematicScene:
            return Int(Double(base) * FrameQualityConstants.FEATURE_MULTIPLIER_CINEMATIC)  // 350
        }
    }

    /// Get effective Tenengrad threshold for given capture profile
    public static func getEffectiveTenengradThreshold(
        for profile: CaptureProfile
    ) -> Double {
        let base = FrameQualityConstants.TENENGRAD_THRESHOLD

        switch profile {
        case .standard, .largeScene, .cinematicScene:
            return base  // 50
        case .smallObjectMacro, .proMacro:
            return base * 1.2  // 60 (sharper required for macro)
        }
    }
}
