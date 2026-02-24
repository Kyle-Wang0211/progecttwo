// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
//  NoProgressWarning.swift
//  Aether3D
//
//  PR#5 Quality Pre-check - Milestone 6
//  NoProgressWarning - no progress warning state machine
//

import Foundation
import CAetherNativeBridge

/// NoProgressWarningStateMachine - small state machine for no progress warning
/// States: armed → fired → cooldown
public enum NoProgressWarningState {
    case armed
    case fired
    case cooldown
}

/// NoProgressWarning - manages no progress warning
public class NoProgressWarning {
    private var state: NoProgressWarningState = .armed
    private var armedTime: Int64?
    
    public init() {}
    
    /// Update state based on no progress duration
    /// 2s no white progress → speed must drop
    public func update(noProgressDurationMs: Int64) {
        var nativeState = aether_no_progress_warning_state_t(
            state: state.nativeCode,
            armed_time_ms: armedTime ?? -1
        )
        var warningActive: Int32 = 0
        let rc = aether_quality_no_progress_warning_step(
            &nativeState,
            noProgressDurationMs,
            MonotonicClock.nowMs(),
            QualityPreCheckConstants.NO_PROGRESS_WARNING_MS,
            1000,
            &warningActive
        )
        guard rc == 0 else {
            return
        }

        state = NoProgressWarningState(nativeCode: nativeState.state) ?? .armed
        armedTime = nativeState.armed_time_ms >= 0 ? nativeState.armed_time_ms : nil
        if warningActive != 0 {
            state = .fired
        }
    }
    
    /// Check if warning is active
    public func isWarningActive() -> Bool {
        return state == .fired
    }
}
