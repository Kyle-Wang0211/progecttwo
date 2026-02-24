// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
//  TrendConfirmation.swift
//  Aether3D
//
//  PR#5 Quality Pre-check - Milestone 3
//  TrendConfirmation - 300ms window stability check (PART 2.4, P6/H2)
//

import Foundation
import CAetherNativeBridge

/// TrendConfirmation - O(1) stability check using RingBuffer
/// P6/H2: Uses MonotonicClock for time windows
public class TrendConfirmation {
    private var metricHistory: RingBuffer<Double>
    private let windowMs: Int64 = QualityPreCheckConstants.TREND_WINDOW_MS
    private var timestamps: RingBuffer<Int64>
    
    public init() {
        self.metricHistory = RingBuffer<Double>(maxCapacity: QualityPreCheckConstants.MAX_TREND_BUFFER_SIZE)
        self.timestamps = RingBuffer<Int64>(maxCapacity: QualityPreCheckConstants.MAX_TREND_BUFFER_SIZE)
    }
    
    /// Add metric value with timestamp
    /// H2: Uses MonotonicClock, not Date()
    public func addValue(_ value: Double, timestamp: Int64) {
        metricHistory.append(value)
        timestamps.append(timestamp)
    }
    
    /// Check stability within window
    /// Returns stability value (variance) or nil if insufficient data
    public func checkStability() -> Double? {
        let now = MonotonicClock.nowMs()
        let allValues = metricHistory.getAll()
        let allTimestamps = timestamps.getAll()

        guard allValues.count == allTimestamps.count else {
            return nil
        }

        var variance = 0.0
        var hasValue: Int32 = 0
        let rc = allValues.withUnsafeBufferPointer { valuesPtr in
            allTimestamps.withUnsafeBufferPointer { timePtr in
                aether_quality_trend_variance(
                    valuesPtr.baseAddress,
                    timePtr.baseAddress,
                    Int32(allValues.count),
                    now,
                    windowMs,
                    &variance,
                    &hasValue
                )
            }
        }
        guard rc == 0, hasValue != 0 else {
            return nil
        }
        return variance
    }
}
