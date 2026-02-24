// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// BatteryAwareScheduler.swift
// Aether3D
//
// Battery Aware Scheduler - Power-efficient processing
// 符合 Phase 4: Mobile Optimization (iOS)
//

import Foundation
import CAetherNativeBridge

/// Mobile Battery Aware Scheduler
///
/// Power-efficient processing based on battery state.
/// 符合 Phase 4: Mobile Optimization - Battery-Aware Processing
public actor MobileBatteryAwareScheduler {
    
    #if os(iOS)
    /// Check if Low Power Mode is enabled
    /// 
    /// 符合 INV-MOBILE-011: Low Power Mode reduces GPU usage by 40%
    public var isLowPowerModeEnabled: Bool {
        ProcessInfo.processInfo.isLowPowerModeEnabled
    }
    #else
    public var isLowPowerModeEnabled: Bool { false }
    #endif
    
    /// Check if background processing should be allowed
    /// 
    /// 符合 INV-MOBILE-012: Background processing suspended at battery < 10%
    public func shouldAllowBackgroundProcessing() async -> Bool {
        var allow: Int32 = 1
        let rc = aether_mobile_should_allow_background_processing(
            isLowPowerModeEnabled ? 1 : 0,
            &allow
        )
        guard rc == 0 else {
            #if os(iOS)
            return !isLowPowerModeEnabled
            #else
            return true
            #endif
        }
        return allow != 0
    }
    
    /// Get recommended scan quality based on power state
    /// 
    /// 符合 INV-MOBILE-013: Idle power draw < 5% of active scanning
    public func recommendedScanQuality() -> ScanQuality {
        var nativeQuality: Int32 = 1
        let rc = aether_mobile_recommended_scan_quality(
            isLowPowerModeEnabled ? 1 : 0,
            &nativeQuality
        )
        guard rc == 0 else {
            #if os(iOS)
            return isLowPowerModeEnabled ? .efficient : .balanced
            #else
            return .balanced
            #endif
        }
        switch nativeQuality {
        case 0: return .maximum
        case 2: return .efficient
        default: return .balanced
        }
    }
    
    /// Scan quality levels
    public enum ScanQuality: Sendable {
        case maximum    // Full quality, high power
        case balanced   // Good quality, moderate power
        case efficient  // Lower quality, low power
    }
}
