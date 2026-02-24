// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// FramePacingNormalizer.swift
// PR5Capture
//
// PR5 v1.8.1 - PART 0: 传感器和相机管道
// FPS 估计，丢帧检测，时间窗口转换
//

import Foundation
#if canImport(CAetherNativeBridge)
import CAetherNativeBridge
#endif

/// Frame pacing normalizer
///
/// Estimates FPS, detects frame drops, and normalizes timing windows.
/// Ensures consistent frame pacing analysis.
public actor FramePacingNormalizer {
    #if canImport(CAetherNativeBridge)
    private final class NativeRuntimeHandle: @unchecked Sendable {
        let pointer: OpaquePointer
        init(pointer: OpaquePointer) { self.pointer = pointer }
    }
    #endif
    
    // MARK: - Configuration
    
    private let config: ExtremeProfile
    
    // MARK: - State
    
    /// Frame timestamp history
    private var frameTimestamps: [Date] = []
    
    /// Estimated FPS
    private var estimatedFPS: Double?

    /// Native pacing runtime handle
    #if canImport(CAetherNativeBridge)
    private let nativeRuntime: NativeRuntimeHandle?
    #endif
    
    /// Frame drop history
    private var frameDrops: [(timestamp: Date, expectedInterval: TimeInterval, actualInterval: TimeInterval)] = []
    
    // MARK: - Initialization
    
    public init(config: ExtremeProfile) {
        self.config = config
        #if canImport(CAetherNativeBridge)
        var runtime: OpaquePointer?
        if aether_mobile_frame_pacing_create(1.0 / 60.0, 120, &runtime) == 0,
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
    
    // MARK: - Frame Recording
    
    /// Record frame timestamp
    public func recordFrame(_ timestamp: Date) {
        frameTimestamps.append(timestamp)
        
        // Keep only recent history (last 120 frames)
        if frameTimestamps.count > 120 {
            frameTimestamps.removeFirst()
        }
        
        if frameTimestamps.count >= 2 {
            let last = frameTimestamps[frameTimestamps.count - 1]
            let prev = frameTimestamps[frameTimestamps.count - 2]
            let interval = max(1e-6, last.timeIntervalSince(prev))

            #if canImport(CAetherNativeBridge)
            if let nativeRuntime {
                var pacingResult = aether_mobile_frame_pacing_result_t()
                _ = aether_mobile_frame_pacing_record(nativeRuntime.pointer, interval, &pacingResult)
            }
            #endif

            updateFromNativeIntervals()
        }
    }

    private func updateFromNativeIntervals() {
        guard frameTimestamps.count >= 2 else {
            estimatedFPS = nil
            return
        }
        var intervals = [Double]()
        intervals.reserveCapacity(frameTimestamps.count - 1)
        for i in 1..<frameTimestamps.count {
            let interval = frameTimestamps[i].timeIntervalSince(frameTimestamps[i - 1])
            intervals.append(max(1e-6, interval))
        }

        #if canImport(CAetherNativeBridge)
        var analysis = aether_mobile_frame_interval_analysis_t()
        let rc = intervals.withUnsafeBufferPointer { ptr in
            aether_mobile_analyze_frame_intervals(
                ptr.baseAddress,
                Int32(intervals.count),
                &analysis
            )
        }
        guard rc == 0 else {
            estimatedFPS = nil
            return
        }

        estimatedFPS = analysis.fps > 0 ? analysis.fps : nil

        if let lastInterval = intervals.last,
           analysis.average_interval_s > 0,
           lastInterval > analysis.average_interval_s * 1.5 {
            frameDrops.append((
                timestamp: frameTimestamps.last ?? Date(),
                expectedInterval: analysis.average_interval_s,
                actualInterval: lastInterval
            ))
            if frameDrops.count > 50 {
                frameDrops.removeFirst()
            }
        }
        #else
        let mean = intervals.reduce(0.0, +) / Double(intervals.count)
        estimatedFPS = mean > 0 ? (1.0 / mean) : nil
        if let lastInterval = intervals.last,
           mean > 0,
           lastInterval > mean * 1.5 {
            frameDrops.append((
                timestamp: frameTimestamps.last ?? Date(),
                expectedInterval: mean,
                actualInterval: lastInterval
            ))
            if frameDrops.count > 50 {
                frameDrops.removeFirst()
            }
        }
        #endif
    }
    
    // MARK: - Time Window Normalization
    
    /// Normalize time window
    ///
    /// Converts time-based windows to frame-based windows using estimated FPS
    public func normalizeTimeWindow(_ timeWindow: TimeInterval) -> Int? {
        guard let fps = estimatedFPS else { return nil }
        
        let frameCount = Int(timeWindow * fps)
        return max(1, frameCount)  // At least 1 frame
    }
    
    /// Denormalize frame window to time
    ///
    /// Converts frame-based windows to time-based windows
    public func denormalizeFrameWindow(_ frameWindow: Int) -> TimeInterval? {
        guard let fps = estimatedFPS else { return nil }
        
        return Double(frameWindow) / fps
    }
    
    // MARK: - Analysis
    
    /// Get frame pacing analysis
    public func getAnalysis() -> FramePacingAnalysis {
        let dropRate = frameDrops.count > 0 ? Double(frameDrops.count) / Double(frameTimestamps.count) : 0.0
        
        return FramePacingAnalysis(
            estimatedFPS: estimatedFPS,
            frameCount: frameTimestamps.count,
            dropCount: frameDrops.count,
            dropRate: dropRate,
            averageInterval: computeAverageInterval(),
            normalizedWindow: config.sensor.framePacingNormalizationWindow
        )
    }
    
    /// Compute average interval
    private func computeAverageInterval() -> TimeInterval? {
        guard frameTimestamps.count >= 2 else { return nil }
        var intervals = [Double]()
        intervals.reserveCapacity(frameTimestamps.count - 1)
        for i in 1..<frameTimestamps.count {
            intervals.append(max(1e-6, frameTimestamps[i].timeIntervalSince(frameTimestamps[i - 1])))
        }
        #if canImport(CAetherNativeBridge)
        var analysis = aether_mobile_frame_interval_analysis_t()
        let rc = intervals.withUnsafeBufferPointer { ptr in
            aether_mobile_analyze_frame_intervals(ptr.baseAddress, Int32(intervals.count), &analysis)
        }
        return rc == 0 ? analysis.average_interval_s : nil
        #else
        return intervals.reduce(0.0, +) / Double(intervals.count)
        #endif
    }
    
    // MARK: - Queries
    
    /// Get estimated FPS
    public func getEstimatedFPS() -> Double? {
        return estimatedFPS
    }
    
    /// Get frame drop count
    public func getFrameDropCount() -> Int {
        return frameDrops.count
    }
    
    /// Get frame drop history
    public func getFrameDropHistory() -> [(timestamp: Date, expectedInterval: TimeInterval, actualInterval: TimeInterval)] {
        return frameDrops
    }
    
    // MARK: - Result Types
    
    /// Frame pacing analysis result
    public struct FramePacingAnalysis: Sendable {
        public let estimatedFPS: Double?
        public let frameCount: Int
        public let dropCount: Int
        public let dropRate: Double
        public let averageInterval: TimeInterval?
        public let normalizedWindow: TimeInterval
    }
}
