// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

import Foundation
import CAetherNativeBridge

extension VisualState {
    var nativeCode: Int32 {
        switch self {
        case .black: return Int32(AETHER_QUALITY_VISUAL_STATE_BLACK)
        case .gray: return Int32(AETHER_QUALITY_VISUAL_STATE_GRAY)
        case .white: return Int32(AETHER_QUALITY_VISUAL_STATE_WHITE)
        case .clear: return Int32(AETHER_QUALITY_VISUAL_STATE_CLEAR)
        }
    }

    init?(nativeCode: Int32) {
        switch nativeCode {
        case Int32(AETHER_QUALITY_VISUAL_STATE_BLACK): self = .black
        case Int32(AETHER_QUALITY_VISUAL_STATE_GRAY): self = .gray
        case Int32(AETHER_QUALITY_VISUAL_STATE_WHITE): self = .white
        case Int32(AETHER_QUALITY_VISUAL_STATE_CLEAR): self = .clear
        default: return nil
        }
    }
}

extension FpsTier {
    var nativeCode: Int32 {
        switch self {
        case .full: return Int32(AETHER_QUALITY_FPS_TIER_FULL)
        case .degraded: return Int32(AETHER_QUALITY_FPS_TIER_DEGRADED)
        case .emergency: return Int32(AETHER_QUALITY_FPS_TIER_EMERGENCY)
        }
    }

    var displayName: String {
        switch self {
        case .full: return "Full"
        case .degraded: return "Degraded"
        case .emergency: return "Emergency"
        }
    }
}

extension SpeedTier {
    init?(nativeCode: Int32) {
        switch nativeCode {
        case Int32(AETHER_QUALITY_SPEED_TIER_EXCELLENT): self = .excellent
        case Int32(AETHER_QUALITY_SPEED_TIER_GOOD): self = .good
        case Int32(AETHER_QUALITY_SPEED_TIER_MODERATE): self = .moderate
        case Int32(AETHER_QUALITY_SPEED_TIER_POOR): self = .poor
        case Int32(AETHER_QUALITY_SPEED_TIER_STOPPED): self = .stopped
        default: return nil
        }
    }
}

extension NoProgressWarningState {
    var nativeCode: Int32 {
        switch self {
        case .armed: return Int32(AETHER_NO_PROGRESS_WARNING_STATE_ARMED)
        case .fired: return Int32(AETHER_NO_PROGRESS_WARNING_STATE_FIRED)
        case .cooldown: return Int32(AETHER_NO_PROGRESS_WARNING_STATE_COOLDOWN)
        }
    }

    init?(nativeCode: Int32) {
        switch nativeCode {
        case Int32(AETHER_NO_PROGRESS_WARNING_STATE_ARMED): self = .armed
        case Int32(AETHER_NO_PROGRESS_WARNING_STATE_FIRED): self = .fired
        case Int32(AETHER_NO_PROGRESS_WARNING_STATE_COOLDOWN): self = .cooldown
        default: return nil
        }
    }
}

func nativeTransitionReasonMessage(reason: Int32, fpsTier: FpsTier) -> String {
    switch reason {
    case Int32(AETHER_QUALITY_TRANSITION_REASON_ONLY_FULL_TIER):
        return "Only Full tier allows Gray→White; \(fpsTier.displayName) tier blocks Gray→White"
    case Int32(AETHER_QUALITY_TRANSITION_REASON_MISSING_CRITICAL_METRICS):
        return "Missing critical metrics"
    case Int32(AETHER_QUALITY_TRANSITION_REASON_MISSING_STABILITY):
        return "Missing stability value"
    case Int32(AETHER_QUALITY_TRANSITION_REASON_CONFIDENCE_THRESHOLD_NOT_MET):
        return "Confidence threshold not met"
    case Int32(AETHER_QUALITY_TRANSITION_REASON_STABILITY_THRESHOLD_EXCEEDED):
        return "Stability threshold exceeded"
    case Int32(AETHER_QUALITY_TRANSITION_REASON_CANNOT_RETREAT):
        return "Cannot retreat visual state"
    case Int32(AETHER_QUALITY_TRANSITION_REASON_INVALID_VISUAL_STATE):
        return "Invalid visual state"
    default:
        return "Unknown transition reason"
    }
}
