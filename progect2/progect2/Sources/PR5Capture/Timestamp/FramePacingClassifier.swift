// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// FramePacingClassifier.swift
// PR5Capture
//
// PR5 v1.8.1 - PART B: 时间戳和同步
// 帧率分类（24/30/60fps），节奏类别分析
//

import Foundation
#if canImport(CAetherNativeBridge)
import CAetherNativeBridge
#endif

/// Frame pacing classifier
///
/// Classifies frame rate (24/30/60fps) and analyzes pacing rhythm.
/// Detects frame drops and irregular pacing patterns.
public actor FramePacingClassifier {
    
    // MARK: - Configuration
    
    private let config: ExtremeProfile
    
    // MARK: - Frame Rate Types
    
    public enum FrameRate: String, Codable, Sendable, CaseIterable {
        case fps24 = "24fps"
        case fps30 = "30fps"
        case fps60 = "60fps"
        case variable = "variable"
        case unknown = "unknown"
    }
    
    // MARK: - Pacing Rhythm Types
    
    public enum PacingRhythm: String, Codable, Sendable, CaseIterable {
        case regular      // Consistent frame intervals
        case irregular    // Variable frame intervals
        case dropped      // Missing frames detected
        case stuttering   // Frequent frame drops
    }
    
    // MARK: - State
    
    /// Frame timestamp history
    private var frameTimestamps: [Date] = []
    
    /// Classified frame rate
    private var classifiedFrameRate: FrameRate = .unknown
    
    /// Classified pacing rhythm
    private var classifiedRhythm: PacingRhythm = .regular
    
    // MARK: - Initialization
    
    public init(config: ExtremeProfile) {
        self.config = config
    }
    
    // MARK: - Frame Recording
    
    /// Record frame timestamp
    public func recordFrame(_ timestamp: Date) {
        frameTimestamps.append(timestamp)
        
        // Keep only recent history (last 120 frames, ~2 seconds at 60fps)
        if frameTimestamps.count > 120 {
            frameTimestamps.removeFirst()
        }
        
        // Re-classify when we have enough samples
        if frameTimestamps.count >= 10 {
            classifyFrameRate()
            classifyPacingRhythm()
        }
    }
    
    // MARK: - Classification
    
    /// Classify frame rate
    private func classifyFrameRate() {
        let native = nativeClassify()
        switch native?.frameRateCode {
        case 1: classifiedFrameRate = .fps24
        case 2: classifiedFrameRate = .fps30
        case 3: classifiedFrameRate = .fps60
        case 4: classifiedFrameRate = .variable
        default: classifiedFrameRate = .unknown
        }
    }
    
    /// Classify pacing rhythm
    private func classifyPacingRhythm() {
        let native = nativeClassify()
        switch native?.rhythmCode {
        case 1: classifiedRhythm = .irregular
        case 2: classifiedRhythm = .dropped
        case 3: classifiedRhythm = .stuttering
        default: classifiedRhythm = .regular
        }
    }

    private func nativeClassify() -> (frameRateCode: Int, rhythmCode: Int)? {
        guard frameTimestamps.count >= 2 else {
            return nil
        }
        var intervals = [Double]()
        intervals.reserveCapacity(frameTimestamps.count - 1)
        for i in 1..<frameTimestamps.count {
            intervals.append(max(1e-6, frameTimestamps[i].timeIntervalSince(frameTimestamps[i - 1])))
        }
        #if canImport(CAetherNativeBridge)
        var analysis = aether_mobile_frame_interval_analysis_t()
        let analysisRC = intervals.withUnsafeBufferPointer { ptr in
            aether_mobile_analyze_frame_intervals(
                ptr.baseAddress,
                Int32(intervals.count),
                &analysis
            )
        }
        guard analysisRC == 0 else {
            return nil
        }
        var classification = aether_mobile_frame_pacing_classification_t()
        let classifyRC = aether_mobile_classify_frame_pacing(
            &analysis,
            Int32(intervals.count),
            &classification
        )
        guard classifyRC == 0 else { return nil }
        return (Int(classification.frame_rate_code), Int(classification.rhythm_code))
        #else
        let avgInterval = intervals.reduce(0.0, +) / Double(intervals.count)
        let fps = avgInterval > 0 ? (1.0 / avgInterval) : 0.0
        let variance = intervals.map { pow($0 - avgInterval, 2) }.reduce(0.0, +) / Double(intervals.count)
        let stdDev = sqrt(variance)
        let cv = avgInterval > 0 ? (stdDev / avgInterval) : 0.0
        let dropCount = intervals.filter { $0 > avgInterval * 1.5 }.count

        let frameRateCode: Int
        if abs(fps - 24.0) < 2.0 {
            frameRateCode = 1
        } else if abs(fps - 30.0) < 2.0 {
            frameRateCode = 2
        } else if abs(fps - 60.0) < 2.0 {
            frameRateCode = 3
        } else if cv > 0.10 {
            frameRateCode = 4
        } else {
            frameRateCode = 0
        }

        let rhythmCode: Int
        if dropCount >= intervals.count / 4 {
            rhythmCode = 3
        } else if dropCount > 0 {
            rhythmCode = 2
        } else if cv > 0.15 {
            rhythmCode = 1
        } else {
            rhythmCode = 0
        }
        return (frameRateCode, rhythmCode)
        #endif
    }
    
    // MARK: - Queries
    
    /// Get classified frame rate
    public func getClassifiedFrameRate() -> FrameRate {
        return classifiedFrameRate
    }
    
    /// Get classified pacing rhythm
    public func getClassifiedPacingRhythm() -> PacingRhythm {
        return classifiedRhythm
    }
    
    /// Get frame rate analysis result
    public func getAnalysisResult() -> FrameRateAnalysisResult {
        return FrameRateAnalysisResult(
            frameRate: classifiedFrameRate,
            pacingRhythm: classifiedRhythm,
            sampleCount: frameTimestamps.count
        )
    }
    
    // MARK: - Result Types
    
    /// Frame rate analysis result
    public struct FrameRateAnalysisResult: Sendable {
        public let frameRate: FrameRate
        public let pacingRhythm: PacingRhythm
        public let sampleCount: Int
    }
}
