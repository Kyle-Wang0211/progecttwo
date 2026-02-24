// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// CoverageEstimator.swift
// Aether3D
//
// PR6 Evidence Grid System - Coverage Estimator
// D-S based coverage calculation with EMA smoothing and anti-jitter limiter
//

import Foundation
import CAetherNativeBridge

/// **Rule ID:** PR6_GRID_COVERAGE_001
/// Coverage Estimator: computes coverage from EvidenceGrid cells
public final class CoverageEstimator: @unchecked Sendable {
    private var nativeEstimator: OpaquePointer?
    private var lastCoverage: Double = 0.0

    public init() {
        var config = NativeCoverageEstimatorBridge.defaultConfig()
        // Keep capture-mode monotonic behavior.
        config.monotonic_mode = 1
        nativeEstimator = NativeCoverageEstimatorBridge.create(config: config)
    }

    deinit {
        if let nativeEstimator {
            NativeCoverageEstimatorBridge.destroy(nativeEstimator)
        }
    }

    /// **Rule ID:** PR6_GRID_COVERAGE_002
    /// Update coverage from EvidenceGrid cells
    ///
    /// - Parameter grid: EvidenceGrid instance
    /// - Returns: CoverageResult with breakdown and explainability
    public func update(grid: EvidenceGrid) async -> CoverageResult {
        let cells = await grid.allActiveCells()

        guard let nativeEstimator else {
            return CoverageResult(
                coveragePercentage: 0.0,
                breakdownCounts: Array(repeating: 0, count: 7),
                weightedSumComponents: Array(repeating: 0.0, count: 7),
                excludedAreaSqM: 0.0
            )
        }

        var observations = [aether_coverage_cell_observation_t](
            repeating: aether_coverage_cell_observation_t(),
            count: cells.count
        )
        for (index, cell) in cells.enumerated() {
            observations[index].level = cell.level.rawValue
            observations[index].occupied = cell.dsMass.occupied
            observations[index].free_mass = cell.dsMass.free
            observations[index].unknown = cell.dsMass.unknown
            observations[index].area_weight = 1.0
            observations[index].excluded = 0
            // Leave view_count unspecified; native core infers minimum count by level.
            observations[index].view_count = 0
        }

        let timestampMs = MonotonicClock.nowMs()
        let nativeResult = observations.withUnsafeBufferPointer { buffer in
            NativeCoverageEstimatorBridge.update(
                nativeEstimator,
                cells: buffer.baseAddress,
                cellCount: Int32(cells.count),
                monotonicTimestampMs: timestampMs
            )
        }

        guard let nativeResult else {
            return CoverageResult(
                coveragePercentage: lastCoverage,
                breakdownCounts: Array(repeating: 0, count: 7),
                weightedSumComponents: Array(repeating: 0.0, count: 7),
                excludedAreaSqM: 0.0
            )
        }

        let breakdownTuple = nativeResult.breakdown_counts
        let breakdownCounts: [Int] = [
            Int(breakdownTuple.0),
            Int(breakdownTuple.1),
            Int(breakdownTuple.2),
            Int(breakdownTuple.3),
            Int(breakdownTuple.4),
            Int(breakdownTuple.5),
            Int(breakdownTuple.6)
        ]
        let weightedTuple = nativeResult.weighted_sum_components
        let weightedSumComponents: [Double] = [
            weightedTuple.0,
            weightedTuple.1,
            weightedTuple.2,
            weightedTuple.3,
            weightedTuple.4,
            weightedTuple.5,
            weightedTuple.6
        ]
        lastCoverage = nativeResult.coverage

        return CoverageResult(
            coveragePercentage: nativeResult.coverage,
            breakdownCounts: breakdownCounts,
            weightedSumComponents: weightedSumComponents,
            excludedAreaSqM: nativeResult.excluded_area_weight,
            beliefCoverage: nativeResult.belief_coverage,
            plausibilityCoverage: nativeResult.plausibility_coverage,
            uncertaintyWidth: nativeResult.uncertainty_width,
            highObservationRatio: nativeResult.high_observation_ratio,
            lyapunovRate: nativeResult.lyapunov_rate,
            meanFisherInfo: nativeResult.mean_fisher_info
        )
    }

    /// Get last coverage value
    public func getLastCoverage() -> Double {
        if let nativeEstimator, let last = NativeCoverageEstimatorBridge.lastCoverage(nativeEstimator) {
            lastCoverage = last
        }
        return lastCoverage
    }

    /// Reset estimator
    public func reset() {
        lastCoverage = 0.0
        if let nativeEstimator {
            NativeCoverageEstimatorBridge.reset(nativeEstimator)
        }
    }
}
