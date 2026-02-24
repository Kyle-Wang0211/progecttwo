// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// InformationGainCalculator.swift
// Aether3D
//
// PR#1 C-Class SOFT/HARD LIMIT - Information Gain Calculator
//
// Deterministic implementation with bounds [0,1]
//

import Foundation
import CAetherNativeBridge

/// Information gain calculator interface.
/// MUST enforce bounds [0,1].
///
/// **v2.3b Sealed:**
/// - Returns MUST be in [0,1] range
/// - Must satisfy monotonic constraints
public protocol InformationGainCalculator {
    /// Compute information gain for a patch candidate
    /// Returns: Double in [0,1], bounded
    func computeInfoGain(
        patch: PatchCandidate,
        existingCoverage: CoverageGrid
    ) -> Double
    
    /// Compute novelty for a patch candidate
    /// Returns: Double in [0,1], bounded
    func computeNovelty(
        patch: PatchCandidate,
        existingPatches: [PatchCandidate]
    ) -> Double
}

/// Deterministic information-gain calculator.
///
/// Uses three replay-stable signals:
/// - Coverage state at candidate cell (uncovered > gray > white)
/// - Local frontier score (boundary cells preferred over fully saturated interior)
/// - Candidate novelty vs existing accepted patches
public struct DeterministicInformationGainCalculator: InformationGainCalculator {
    public init() {}
    
    public func computeInfoGain(
        patch: PatchCandidate,
        existingCoverage: CoverageGrid
    ) -> Double {
        guard let native = computeInfoGainNative(patch: patch, existingCoverage: existingCoverage) else {
            return 0.0
        }
        return native
    }
    
    public func computeNovelty(
        patch: PatchCandidate,
        existingPatches: [PatchCandidate]
    ) -> Double {
        guard let native = computeNoveltyNative(patch: patch, existingPatches: existingPatches) else {
            return 0.0
        }
        return native
    }

    private func computeInfoGainNative(
        patch: PatchCandidate,
        existingCoverage: CoverageGrid
    ) -> Double? {
        var nativePatch = toNativePatchDescriptor(patch)
        let gridSize = CoverageGrid.gridSize
        var states = [UInt8](repeating: 0, count: CoverageGrid.totalCellCount)
        for row in 0..<gridSize {
            for col in 0..<gridSize {
                let idx = CoverageGrid.cellIndex(row: row, col: col)
                states[idx] = existingCoverage.getState(row: row, col: col).rawValue
            }
        }

        var value: Double = 0.0
        let rc = states.withUnsafeBufferPointer { ptr in
            aether_pr1_compute_info_gain(
                &nativePatch,
                ptr.baseAddress,
                Int32(gridSize),
                &value
            )
        }
        guard rc == 0, value.isFinite else {
            return nil
        }
        return clamp01(value)
    }

    private func computeNoveltyNative(
        patch: PatchCandidate,
        existingPatches: [PatchCandidate]
    ) -> Double? {
        var nativePatch = toNativePatchDescriptor(patch)
        let nativeExisting = existingPatches.map(toNativePatchDescriptor)
        var value: Double = 0.0
        let rc = nativeExisting.withUnsafeBufferPointer { ptr in
            aether_pr1_compute_novelty(
                &nativePatch,
                ptr.baseAddress,
                Int32(ptr.count),
                CapacityLimitConstants.POSE_EPS,
                &value
            )
        }
        guard rc == 0, value.isFinite else {
            return nil
        }
        return clamp01(value)
    }

    private func toNativePatchDescriptor(_ patch: PatchCandidate) -> aether_pr1_patch_descriptor_t {
        var native = aether_pr1_patch_descriptor_t()
        native.pose_x = patch.pose.x
        native.pose_y = patch.pose.y
        native.pose_z = patch.pose.z
        native.coverage_x = Int32(patch.coverageCell.x)
        native.coverage_y = Int32(patch.coverageCell.y)
        native.radiance_x = patch.radiance.x
        native.radiance_y = patch.radiance.y
        native.radiance_z = patch.radiance.z
        return native
    }

    private func clamp01(_ value: Double) -> Double {
        min(1.0, max(0.0, value))
    }
}
