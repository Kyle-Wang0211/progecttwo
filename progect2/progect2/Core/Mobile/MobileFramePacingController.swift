// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// FramePacingController.swift
// Aether3D
//
// Frame Pacing Controller - Consistent frame delivery
// 符合 Phase 4: Mobile Optimization (iOS)
//

import Foundation
import CAetherNativeBridge

/// Mobile Frame Pacing Controller
///
/// Manages consistent frame delivery for smooth rendering.
/// 符合 Phase 4: Mobile Optimization - Frame Pacing & Smoothness
public actor MobileFramePacingController {
    private struct NativeRuntimeHandle: @unchecked Sendable {
        let pointer: OpaquePointer
    }

    private let nativeRuntime: NativeRuntimeHandle?

    public init() {
        var runtime: OpaquePointer?
        let rc = aether_mobile_frame_pacing_create(1.0 / 60.0, 30, &runtime)
        if rc == 0, let runtime {
            self.nativeRuntime = NativeRuntimeHandle(pointer: runtime)
        } else {
            self.nativeRuntime = nil
        }
    }

    deinit {
        if let nativeRuntime {
            _ = aether_mobile_frame_pacing_destroy(nativeRuntime.pointer)
        }
    }
    
    /// Record frame time and get pacing advice
    /// 
    /// 符合 INV-MOBILE-008: Frame time variance < 2ms for 95th percentile
    /// 符合 INV-MOBILE-009: Frame drops < 1% in steady state
    /// 符合 INV-MOBILE-010: Adaptive frame rate (60→30→24) based on load
    public func recordFrameTime(_ frameTime: TimeInterval) async -> FramePacingAdvice {
        guard let nativeRuntime else {
            return .maintain
        }
        var native = aether_mobile_frame_pacing_result_t()
        let rc = aether_mobile_frame_pacing_record(nativeRuntime.pointer, frameTime, &native)
        guard rc == 0 else {
            return .maintain
        }
        switch native.advice {
        case 1:
            return .reduceQuality
        case 2:
            return .enableSmoothing
        case 3:
            return .increaseQuality
        default:
            return .maintain
        }
    }
    
    /// Frame pacing advice
    public enum FramePacingAdvice: Sendable {
        case maintain
        case reduceQuality
        case enableSmoothing
        case increaseQuality
    }
}
