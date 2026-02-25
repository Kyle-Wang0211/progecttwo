// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// PatchDisplayMap.swift
// Aether3D
//
// PR2 Patch V4 - Patch Display Map
// Monotonic display evidence per patch with EMA smoothing
//

import Foundation
#if canImport(CAetherNativeBridge)
import CAetherNativeBridge
#endif

/// Display entry for a patch
public struct DisplayEntry: Codable, Sendable {
    /// Patch identifier
    public let patchId: String

    /// Current display evidence [0, 1] (monotonic, never decreases)
    @ClampedEvidence public var display: Double

    /// EMA state [0, 1]
    @ClampedEvidence public var ema: Double

    /// Observation count
    public var observationCount: Int

    /// Last update timestamp (milliseconds)
    public var lastUpdateMs: Int64

    /// Ghost display high-water mark — records the peak display value before
    /// eviction so the patch can warm-start from its historical peak when
    /// re-observed. This enables the "no visual regression" guarantee:
    /// patches resume from where they left off after the camera moves away.
    public var displayHighWater: Double = 0.0

    public init(
        patchId: String,
        display: Double = 0.0,
        ema: Double = 0.0,
        observationCount: Int = 0,
        lastUpdateMs: Int64 = 0,
        displayHighWater: Double = 0.0
    ) {
        self.patchId = patchId
        self._display = ClampedEvidence(wrappedValue: display)
        self._ema = ClampedEvidence(wrappedValue: ema)
        self.observationCount = observationCount
        self.lastUpdateMs = lastUpdateMs
        self.displayHighWater = displayHighWater
    }
}

/// Patch display evidence storage
///
/// INVARIANTS:
/// - Display evidence NEVER decreases per patch
/// - Uses core C++ patch-display kernel (EMA + lock acceleration) as SSOT
/// - Locked patches accelerate display growth (but remain monotonic)
public final class PatchDisplayMap {

    /// Patch ID → Display Entry storage
    private var displays: [String: DisplayEntry] = [:]

    public init() {}
    
    /// Update display evidence for a patch
    ///
    /// - Parameters:
    ///   - patchId: Patch identifier
    ///   - target: Target evidence value [0, 1]
    ///   - timestampMs: Current timestamp in milliseconds
    ///   - isLocked: Whether patch is locked (affects acceleration)
    ///   - constants: Evidence constants (defaults to EvidenceConstants)
    /// - Returns: Updated display entry
    @discardableResult
    public func update(
        patchId: String,
        target: Double,
        timestampMs: Int64,
        isLocked: Bool,
        constants: EvidenceConstants.Type = EvidenceConstants.self
    ) -> DisplayEntry {
        var entry = displays[patchId] ?? DisplayEntry(
            patchId: patchId,
            lastUpdateMs: timestampMs
        )
        
        let prevDisplay = entry.display
        let prevEma = entry.ema
        let ghostHW = entry.displayHighWater

        // Clamp target to [0, 1]
        let clampedTarget = max(0.0, min(1.0, target))

        // Core-layer SSOT path for display evolution.
        // Pass ghost_display_high_water so the C++ kernel can warm-start
        // evicted patches from their historical peak (no visual regression).
        let nextDisplay: Double
        let nextEma: Double
        if let step = NativePatchDisplayBridge.patchDisplayStep(
            previousDisplay: prevDisplay,
            previousEMA: prevEma,
            observationCount: entry.observationCount,
            target: clampedTarget,
            isLocked: isLocked,
            config: nil,
            ghostDisplayHighWater: ghostHW
        ) {
            nextDisplay = max(prevDisplay, min(1.0, step.display))
            nextEma = max(0.0, min(1.0, step.ema))
            // Layer 6.7: Only increment observation count on successful bridge call.
            // Was unconditionally incremented, inflating count even when bridge failed.
            entry.observationCount += 1
        } else {
            // Layer 6.10: Software fallback when native bridge is unavailable.
            // Uses FASTER EMA (alpha=0.25) to track the evidence target.
            // The evidence from PatchEvidenceMap now grows additively (not
            // converging to a ceiling), so the display map can afford to
            // follow it more aggressively. Monotonic guarantee preserved via
            // max(prevDisplay, ...).
            //
            // Also applies ghost warm-start: if the patch was previously evicted
            // but had accumulated display, resume from the historical peak.
            let effectiveTarget: Double
            if prevDisplay <= 0.001 && ghostHW > 0.01 {
                // Ghost warm-start: resume from historical peak
                effectiveTarget = max(clampedTarget, ghostHW)
            } else {
                effectiveTarget = clampedTarget
            }
            let fallbackAlpha = 0.85  // Near-instant tracking: admission removed, no double-smoothing needed
            let emaUpdate = prevEma + fallbackAlpha * (effectiveTarget - prevEma)
            nextDisplay = max(prevDisplay, min(1.0, emaUpdate))
            nextEma = max(0.0, min(1.0, emaUpdate))
            entry.observationCount += 1
        }

        // Update entry — maintain ghost high-water mark for future warm-starts
        entry.display = nextDisplay
        entry.ema = nextEma
        entry.displayHighWater = max(entry.displayHighWater, nextDisplay)
        entry.lastUpdateMs = timestampMs
        
        displays[patchId] = entry
        
        return entry
    }
    
    /// Get display evidence for a patch
    public func display(for patchId: String) -> Double {
        return displays[patchId]?.display ?? 0.0
    }
    
    /// Compute color evidence using hybrid formula (Rule F)
    ///
    /// Formula: colorEvidence = local * 0.7 + global * 0.3
    ///
    /// - Parameters:
    ///   - patchId: Patch identifier
    ///   - globalDisplay: Global display evidence [0, 1]
    ///   - constants: Evidence constants (defaults to EvidenceConstants)
    /// - Returns: Color evidence [0, 1]
    public func colorEvidence(
        for patchId: String,
        globalDisplay: Double,
        constants: EvidenceConstants.Type = EvidenceConstants.self
    ) -> Double {
        let local = display(for: patchId)
        let clampedGlobal = max(0.0, min(1.0, globalDisplay))
        
        if let color = NativePatchDisplayBridge.patchColorEvidence(
            localDisplay: local,
            globalDisplay: clampedGlobal,
            config: nil
        ) {
            return max(0.0, min(1.0, color))
        }
        // Layer 6.8: Fallback hybrid formula (Rule F) when bridge fails.
        // Was returning 0.0 which makes patches appear unscanned on bridge failure.
        let fallbackColor = local * 0.7 + clampedGlobal * 0.3
        return max(0.0, min(1.0, fallbackColor))
    }
    
    /// Get all entries sorted by patch ID (deterministic)
    public func snapshotSorted() -> [DisplayEntry] {
        return displays.sorted { $0.key < $1.key }.map { $0.value }
    }
    
    /// Reset all displays
    public func reset() {
        displays.removeAll()
    }

    // MARK: - Persistence (save/load)

    /// Save display state to disk. DisplayEntry conforms to Codable.
    /// - Parameter url: File URL to write the JSON data.
    /// - Throws: Encoding or I/O errors.
    public func saveToDisk(url: URL) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let data = try encoder.encode(displays)
        try data.write(to: url, options: [.atomic])
    }

    /// Load display state from disk. Merges by taking higher display per patch
    /// (monotonic guarantee — loaded state never regresses current state).
    /// - Parameter url: File URL to read the JSON data from.
    /// - Throws: Decoding or I/O errors.
    public func loadFromDisk(url: URL) throws {
        let data = try Data(contentsOf: url)
        let decoder = JSONDecoder()
        let loaded = try decoder.decode([String: DisplayEntry].self, from: data)
        for (patchId, loadedEntry) in loaded {
            if let existing = displays[patchId] {
                // Monotonic merge — take higher display
                if loadedEntry.display > existing.display {
                    displays[patchId] = loadedEntry
                }
            } else {
                displays[patchId] = loadedEntry
            }
        }
    }
}
