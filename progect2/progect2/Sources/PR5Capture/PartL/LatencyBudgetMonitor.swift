// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// LatencyBudgetMonitor.swift
// PR5Capture
//
// PR5 v1.8.1 - PART L: 性能预算
// 延迟预算监控，帧时间追踪
//

import Foundation
#if canImport(CAetherNativeBridge)
import CAetherNativeBridge
#endif

/// Latency budget monitor
///
/// Monitors latency budgets and tracks frame times.
/// Ensures frame processing stays within budget.
public actor LatencyBudgetMonitor {
    private final class NativeRuntimeHandle: @unchecked Sendable {
        let pointer: OpaquePointer
        init(pointer: OpaquePointer) { self.pointer = pointer }
    }
    
    // MARK: - Configuration
    
    private let config: ExtremeProfile
    
    // MARK: - State
    
    /// Frame time history
    private var frameTimes: [TimeInterval] = []
    
    /// Budget violations
    private var violations: [(timestamp: Date, frameTime: TimeInterval, budget: TimeInterval)] = []
    
    /// Target frame time (e.g., 16.67ms for 60fps)
    private let targetFrameTime: TimeInterval = 0.01667
    #if canImport(CAetherNativeBridge)
    private let nativeRuntime: NativeRuntimeHandle?
    #endif
    
    // MARK: - Initialization
    
    public init(config: ExtremeProfile) {
        self.config = config
        #if canImport(CAetherNativeBridge)
        var runtime: OpaquePointer?
        if aether_mobile_frame_pacing_create(1.0 / 60.0, 100, &runtime) == 0,
           let runtime {
            self.nativeRuntime = NativeRuntimeHandle(pointer: runtime)
        } else {
            self.nativeRuntime = nil
        }
        #endif
    }

    deinit {
        #if canImport(CAetherNativeBridge)
        if let nativeRuntime {
            _ = aether_mobile_frame_pacing_destroy(nativeRuntime.pointer)
        }
        #endif
    }
    
    // MARK: - Monitoring
    
    /// Record frame time
    public func recordFrameTime(_ time: TimeInterval) -> MonitoringResult {
        frameTimes.append(time)
        
        // Keep only recent history (last 100)
        if frameTimes.count > 100 {
            frameTimes.removeFirst()
        }
        
        var withinBudget = time <= targetFrameTime
        #if canImport(CAetherNativeBridge)
        if let nativeRuntime {
            var pacing = aether_mobile_frame_pacing_result_t()
            if aether_mobile_frame_pacing_record(nativeRuntime.pointer, max(1e-6, time), &pacing) == 0 {
                withinBudget = pacing.p95 <= targetFrameTime
            }
        }
        #endif

        if !withinBudget {
            violations.append((timestamp: Date(), frameTime: time, budget: targetFrameTime))
            
            // Keep only recent violations (last 50)
            if violations.count > 50 {
                violations.removeFirst()
            }
        }
        
        return MonitoringResult(
            frameTime: time,
            withinBudget: withinBudget,
            budget: targetFrameTime,
            violationCount: violations.count
        )
    }
    
    /// Get average frame time
    public func getAverageFrameTime() -> TimeInterval? {
        guard !frameTimes.isEmpty else { return nil }
        return frameTimes.reduce(0.0, +) / Double(frameTimes.count)
    }
    
    // MARK: - Result Types
    
    /// Monitoring result
    public struct MonitoringResult: Sendable {
        public let frameTime: TimeInterval
        public let withinBudget: Bool
        public let budget: TimeInterval
        public let violationCount: Int
    }
}
