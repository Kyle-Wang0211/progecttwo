// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// TimestampJitterAnalyzer.swift
// PR5Capture
//
// PR5 v1.8.1 - PART B: 时间戳和同步
// 时间戳抖动分析，相机/IMU 时间戳方差检测
//

import Foundation
#if canImport(CAetherNativeBridge)
import CAetherNativeBridge
#endif

/// Timestamp jitter analyzer
///
/// Analyzes timestamp jitter and variance between camera and IMU timestamps.
/// Detects timing inconsistencies that could affect quality metrics.
public actor TimestampJitterAnalyzer {
    
    // MARK: - Configuration
    
    private let config: ExtremeProfile
    
    // MARK: - State
    
    /// Camera timestamp history
    private var cameraTimestamps: [TimeInterval] = []
    
    /// IMU timestamp history
    private var imuTimestamps: [TimeInterval] = []
    
    /// Timestamp pairs (camera, IMU)
    private var timestampPairs: [(camera: TimeInterval, imu: TimeInterval, delta: TimeInterval)] = []
    
    // MARK: - Initialization
    
    public init(config: ExtremeProfile) {
        self.config = config
    }
    
    // MARK: - Timestamp Recording
    
    /// Record camera timestamp
    public func recordCameraTimestamp(_ timestamp: TimeInterval) {
        cameraTimestamps.append(timestamp)
        
        // Keep only recent history (last 100 timestamps)
        if cameraTimestamps.count > 100 {
            cameraTimestamps.removeFirst()
        }
    }
    
    /// Record IMU timestamp
    public func recordIMUTimestamp(_ timestamp: TimeInterval) {
        imuTimestamps.append(timestamp)
        
        // Keep only recent history (last 100 timestamps)
        if imuTimestamps.count > 100 {
            imuTimestamps.removeFirst()
        }
    }
    
    /// Record timestamp pair (camera and IMU)
    public func recordTimestampPair(camera: TimeInterval, imu: TimeInterval) {
        let delta = abs(camera - imu)
        timestampPairs.append((camera: camera, imu: imu, delta: delta))
        
        // Keep only recent pairs (last 100)
        if timestampPairs.count > 100 {
            timestampPairs.removeFirst()
        }
    }
    
    // MARK: - Jitter Analysis
    
    /// Analyze timestamp jitter
    ///
    /// Computes variance and detects jitter exceeding thresholds
    public func analyzeJitter() -> JitterAnalysisResult {
        // Analyze camera timestamp jitter
        let cameraJitter = computeJitter(cameraTimestamps)
        
        // Analyze IMU timestamp jitter
        let imuJitter = computeJitter(imuTimestamps)
        
        // Analyze timestamp pair deltas
        let deltaJitter = computeDeltaJitter()
        
        // Get threshold from config
        let threshold = PR5CaptureConstants.getValue(
            PR5CaptureConstants.Timestamp.maxJitterMs,
            profile: config.profile
        )
        
        // Check if jitter exceeds threshold
        let cameraExceeds = cameraJitter.varianceMs >= threshold
        let imuExceeds = imuJitter.varianceMs >= threshold
        let deltaExceeds = deltaJitter?.varianceMs ?? 0 >= threshold
        
        let hasExcessiveJitter = cameraExceeds || imuExceeds || deltaExceeds
        
        return JitterAnalysisResult(
            cameraJitter: cameraJitter,
            imuJitter: imuJitter,
            deltaJitter: deltaJitter,
            threshold: threshold,
            hasExcessiveJitter: hasExcessiveJitter
        )
    }
    
    /// Compute jitter statistics for a timestamp array
    private func computeJitter(_ timestamps: [TimeInterval]) -> JitterStats {
        guard timestamps.count >= 2 else {
            return JitterStats(
                meanMs: 0.0,
                varianceMs: 0.0,
                stdDevMs: 0.0,
                minMs: 0.0,
                maxMs: 0.0
            )
        }
        
        var intervalsSeconds: [Double] = []
        intervalsSeconds.reserveCapacity(timestamps.count - 1)
        for i in 1..<timestamps.count {
            intervalsSeconds.append(Swift.max(1e-6, timestamps[i] - timestamps[i - 1]))
        }

        let intervalsMs = intervalsSeconds.map { $0 * 1000.0 }
        let minValue = intervalsMs.min() ?? 0.0
        let maxValue = intervalsMs.max() ?? 0.0
        #if canImport(CAetherNativeBridge)
        var analysis = aether_mobile_frame_interval_analysis_t()
        let rc = intervalsSeconds.withUnsafeBufferPointer { ptr in
            aether_mobile_analyze_frame_intervals(ptr.baseAddress, Int32(intervalsSeconds.count), &analysis)
        }
        let meanMs = rc == 0 ? analysis.average_interval_s * 1000.0 : (intervalsMs.reduce(0.0, +) / Double(intervalsMs.count))
        let stdDevMs = rc == 0 ? (analysis.coefficient_of_variation * meanMs) : 0.0
        let varianceMs = stdDevMs * stdDevMs
        #else
        let meanMs = intervalsMs.reduce(0.0, +) / Double(intervalsMs.count)
        let varianceMs = intervalsMs.map { pow($0 - meanMs, 2) }.reduce(0.0, +) / Double(intervalsMs.count)
        let stdDevMs = sqrt(varianceMs)
        #endif
        
        return JitterStats(
            meanMs: meanMs,
            varianceMs: varianceMs,
            stdDevMs: stdDevMs,
            minMs: minValue,
            maxMs: maxValue
        )
    }
    
    /// Compute delta jitter between camera and IMU timestamps
    private func computeDeltaJitter() -> JitterStats? {
        guard timestampPairs.count >= 2 else { return nil }

        let deltasSec = timestampPairs.map { Swift.max(1e-6, $0.delta) }
        let deltasMs = deltasSec.map { $0 * 1000.0 }
        let minValue = deltasMs.min() ?? 0.0
        let maxValue = deltasMs.max() ?? 0.0
        #if canImport(CAetherNativeBridge)
        var analysis = aether_mobile_frame_interval_analysis_t()
        let rc = deltasSec.withUnsafeBufferPointer { ptr in
            aether_mobile_analyze_frame_intervals(ptr.baseAddress, Int32(deltasSec.count), &analysis)
        }
        let meanMs = rc == 0 ? analysis.average_interval_s * 1000.0 : (deltasMs.reduce(0.0, +) / Double(deltasMs.count))
        let stdDevMs = rc == 0 ? (analysis.coefficient_of_variation * meanMs) : 0.0
        let varianceMs = stdDevMs * stdDevMs
        #else
        let meanMs = deltasMs.reduce(0.0, +) / Double(deltasMs.count)
        var varianceAccum = 0.0
        for value in deltasMs {
            let diff = value - meanMs
            varianceAccum += diff * diff
        }
        let varianceMs = varianceAccum / Double(deltasMs.count)
        let stdDevMs = sqrt(varianceMs)
        #endif
        
        return JitterStats(
            meanMs: meanMs,
            varianceMs: varianceMs,
            stdDevMs: stdDevMs,
            minMs: minValue,
            maxMs: maxValue
        )
    }
    
    // MARK: - Result Types
    
    /// Jitter statistics
    public struct JitterStats: Sendable {
        public let meanMs: Double
        public let varianceMs: Double
        public let stdDevMs: Double
        public let minMs: Double
        public let maxMs: Double
    }
    
    /// Jitter analysis result
    public struct JitterAnalysisResult: Sendable {
        public let cameraJitter: JitterStats
        public let imuJitter: JitterStats
        public let deltaJitter: JitterStats?
        public let threshold: Double
        public let hasExcessiveJitter: Bool
    }
}
