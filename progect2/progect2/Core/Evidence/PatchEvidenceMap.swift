// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// PatchEvidenceMap.swift
// Aether3D
//
// PR2 Patch V4 - Patch Evidence Map (Complete Implementation)
// Per-patch ledger storage with deterministic updates
//

import Foundation

/// Patch evidence entry
public struct PatchEntry: Codable, Sendable {
    /// Current evidence value [0, 1] (clamped)
    @ClampedEvidence public var evidence: Double
    
    /// Last update timestamp (milliseconds)
    public var lastUpdateMs: Int64
    
    /// Observation count (for weight calculation)
    public var observationCount: Int
    
    /// Best observation frame ID
    public var bestFrameId: String?
    
    /// Total error count (for analytics)
    public var errorCount: Int
    
    /// Consecutive error streak (for penalty calculation)
    public var errorStreak: Int
    
    /// Last good (non-error) update timestamp (milliseconds, optional)
    public var lastGoodUpdateMs: Int64?
    
    /// Suspect observation count
    public var suspectCount: Int
    
    /// Whether patch is locked (computed property, not stored)
    public var isLocked: Bool {
        return EvidenceLocking.isLocked(
            evidence: evidence,
            observationCount: observationCount
        )
    }
    
    public init(
        evidence: Double = 0.0,
        lastUpdateMs: Int64 = 0,
        observationCount: Int = 0,
        bestFrameId: String? = nil,
        errorCount: Int = 0,
        errorStreak: Int = 0,
        lastGoodUpdateMs: Int64? = nil,
        suspectCount: Int = 0
    ) {
        self._evidence = ClampedEvidence(wrappedValue: evidence)
        self.lastUpdateMs = lastUpdateMs
        self.observationCount = observationCount
        self.bestFrameId = bestFrameId
        self.errorCount = errorCount
        self.errorStreak = errorStreak
        self.lastGoodUpdateMs = lastGoodUpdateMs
        self.suspectCount = suspectCount
    }
}

/// Patch entry update result
public struct PatchEntryUpdateResult: Sendable {
    /// Whether evidence was updated
    public let wasUpdated: Bool
    
    /// Previous evidence value
    public let previousEvidence: Double
    
    /// New evidence value
    public let newEvidence: Double
    
    /// Whether patch is now locked
    public let isLocked: Bool
}

/// Total evidence snapshot
public struct TotalEvidenceSnapshot: Sendable {
    public let totalEvidence: Double
    public let patchCount: Int
    public let weightedSum: Double
    public let totalWeight: Double
}

/// Patch-level evidence storage
public final class PatchEvidenceMap {
    
    /// Patch ID → Entry storage
    private var patches: [String: PatchEntry] = [:]
    
    /// Bucketed aggregator for O(k) total computation
    private var aggregator: BucketedAmortizedAggregator
    
    public init() {
        self.aggregator = BucketedAmortizedAggregator()
    }
    
    // MARK: - Read
    
    /// Get evidence for a specific patch
    public func evidence(for patchId: String) -> Double {
        return patches[patchId]?.evidence ?? 0.0
    }
    
    /// Get entry for patch
    public func entry(for patchId: String) -> PatchEntry? {
        return patches[patchId]
    }
    
    /// Get entry for patch (alias for compatibility)
    public func getEntry(for patchId: String) -> PatchEntry? {
        return entry(for: patchId)
    }
    
    /// Get all patch IDs (deterministic sorted order)
    public var allPatchIds: [String] {
        return patches.keys.sorted()
    }
    
    /// Get all entries sorted by patch ID (deterministic)
    public func allEntriesSnapshotSorted() -> [PatchEntry] {
        return patches.sorted { $0.key < $1.key }.map { $0.value }
    }
    
    // MARK: - Write
    
    /// Update patch evidence with gradual penalty for errors
    /// - Parameters:
    ///   - patchId: Patch identifier
    ///   - ledgerQuality: Quality from Gate/Soft (NOT observation.quality)
    ///   - verdict: Observation verdict (good/suspect/bad/unknown)
    ///   - frameId: Source frame ID
    ///   - timestampMs: Current timestamp in milliseconds
    ///   - errorType: Error type (if applicable)
    /// - Returns: Update result
    @discardableResult
    public func update(
        patchId: String,
        ledgerQuality: Double,
        verdict: ObservationVerdict,
        frameId: String,
        timestampMs: Int64,
        errorType: ObservationErrorType? = nil
    ) -> PatchEntryUpdateResult {
        var entry = patches[patchId] ?? PatchEntry(lastUpdateMs: timestampMs)
        
        let previousEvidence = entry.evidence

        if let native = NativePatchEvidenceBridge.patchEvidenceStep(
            entry: entry,
            ledgerQuality: ledgerQuality,
            verdict: verdict,
            timestampMs: timestampMs
        ) {
            if native.verdict_was_unknown != 0 {
                EvidenceLogger.warn("Unknown verdict treated as suspect for patch \(patchId)")
            }
            entry.evidence = native.evidence
            entry.lastUpdateMs = native.last_update_ms
            entry.observationCount = max(0, Int(native.observation_count))
            entry.errorCount = max(0, Int(native.error_count))
            entry.errorStreak = max(0, Int(native.error_streak))
            entry.lastGoodUpdateMs = native.last_good_update_ms >= 0 ? native.last_good_update_ms : nil
            entry.suspectCount = max(0, Int(native.suspect_count))
            if native.should_update_best_frame != 0 {
                entry.bestFrameId = frameId
            }
            patches[patchId] = entry

            let weight = computeBaseWeight(entry: entry, timestampMs: timestampMs)
            aggregator.updatePatch(
                patchId: patchId,
                evidence: entry.evidence,
                baseWeight: weight,
                timestamp: Double(timestampMs) / 1000.0
            )

            return PatchEntryUpdateResult(
                wasUpdated: native.was_updated != 0,
                previousEvidence: previousEvidence,
                newEvidence: entry.evidence,
                isLocked: native.is_locked != 0
            )
        }
        
        // Layer 6.9: Software-only fallback when C++ bridge is unavailable.
        //
        // CRITICAL FIX: Uses ADDITIVE accumulation, not EMA convergence.
        //
        // OLD BUG: `evidence += alpha * (ledgerQuality - evidence)` treats
        // ledgerQuality as a CEILING. If quality=0.3, evidence can NEVER
        // exceed 0.3 — no matter how many frames you observe. The user scans
        // for 30 seconds and the triangles stay permanently dark.
        //
        // NEW: Each observation ADDS a quality-modulated increment. Evidence
        // grows toward 1.0 as observations accumulate. Good observations
        // add more, suspect observations add less. Target: ~10-15 seconds of
        // good observations to reach 1.0.
        //
        // Rate: baseIncrement × verdictMultiplier × qualityModifier
        //   good observation, quality 0.5:  0.012 × 1.0 × 0.75 = 0.009/frame
        //   At 60fps × 50% admission duty cycle = 30 effective frames/sec × 0.009 = 0.27/sec
        //   display=0.3 (visually noticeable) in ~1.1 seconds ✓
        //   display=0.7 (bright) in ~2.6 seconds ✓
        //   display=1.0 (full) in ~3.7 seconds ✓
        let baseIncrement = 0.01  // Admission removed: 0.01×60fps×0.75(quality)=0.45/sec → display=0.3 in ~0.7s, display=1.0 in ~2.2s
        let verdictMultiplier: Double
        switch verdict {
        case .good:
            verdictMultiplier = 1.0     // Full speed
        case .suspect:
            verdictMultiplier = 0.3     // 30% speed — still makes progress
        case .bad:
            verdictMultiplier = 0.0     // No growth, penalty below
        case .unknown:
            verdictMultiplier = 0.15    // Minimal growth
        }

        if verdict == .bad {
            // Penalty: fixed decrement (never below 0), with streak tracking.
            // Bad observations still penalize, but don't erase all progress.
            let penaltyAmount = min(0.02, 0.008 * Double(1 + entry.errorStreak))
            entry.evidence = max(0.0, entry.evidence - penaltyAmount)
            entry.errorCount += 1
            entry.errorStreak += 1
        } else {
            // ADDITIVE growth: quality modulates the rate, NOT the ceiling.
            // qualityModifier has a floor of 0.3 — even low-quality observations
            // contribute, just slower. This ensures distant/small triangles still
            // change color over time (the user's core complaint).
            let qualityModifier = max(0.3, min(1.0, ledgerQuality * 1.5))
            let increment = baseIncrement * verdictMultiplier * qualityModifier
            entry.evidence = min(1.0, entry.evidence + increment)
            entry.errorStreak = 0
            entry.lastGoodUpdateMs = timestampMs
            if verdict == .suspect {
                entry.suspectCount += 1
            }
        }
        entry.observationCount += 1
        entry.lastUpdateMs = timestampMs

        // Track best frame on quality improvement
        if verdict == .good && ledgerQuality > previousEvidence {
            entry.bestFrameId = frameId
        }

        _ = errorType  // Reserved for future per-error-type penalty curves
        EvidenceLogger.warn("Native patch-evidence kernel unavailable for patch \(patchId); software fallback (evidence: \(String(format: "%.4f", previousEvidence)) → \(String(format: "%.4f", entry.evidence)))")
        patches[patchId] = entry

        let weight = computeBaseWeight(entry: entry, timestampMs: timestampMs)
        aggregator.updatePatch(
            patchId: patchId,
            evidence: entry.evidence,
            baseWeight: weight,
            timestamp: Double(timestampMs) / 1000.0
        )

        return PatchEntryUpdateResult(
            wasUpdated: true,
            previousEvidence: previousEvidence,
            newEvidence: entry.evidence,
            isLocked: entry.isLocked
        )
    }
    
    /// Convenience update using TimeInterval
    public func update(
        patchId: String,
        ledgerQuality: Double,
        verdict: ObservationVerdict,
        frameId: String,
        timestamp: TimeInterval,
        errorType: ObservationErrorType? = nil
    ) -> PatchEntryUpdateResult {
        let timestampMs = Int64(timestamp * 1000.0)
        return update(
            patchId: patchId,
            ledgerQuality: ledgerQuality,
            verdict: verdict,
            frameId: frameId,
            timestampMs: timestampMs,
            errorType: errorType
        )
    }
    
    // MARK: - Aggregation
    
    /// Compute weighted totals with decay
    /// Uses BucketedAmortizedAggregator for O(k) performance
    public func weightedTotals(
        nowMs: Int64,
        decay: ConfidenceDecay.Type = ConfidenceDecay.self,
        weightComputer: PatchWeightComputer.Type = PatchWeightComputer.self,
        aggregator: BucketedAmortizedAggregator? = nil
    ) -> TotalEvidenceSnapshot {
        let agg = aggregator ?? self.aggregator
        
        // Recalibrate periodically (every 60s or when bucket count exceeds max).
        // Current strategy keeps aggregator state stable within a single session.
        
        let totalEvidence = agg.totalEvidence
        
        return TotalEvidenceSnapshot(
            totalEvidence: totalEvidence,
            patchCount: patches.count,
            weightedSum: 0.0,  // Not exposed by aggregator
            totalWeight: 0.0   // Not exposed by aggregator
        )
    }
    
    /// Total evidence (convenience method)
    public func totalEvidence(currentTime: TimeInterval) -> Double {
        let nowMs = Int64(currentTime * 1000.0)
        return weightedTotals(nowMs: nowMs).totalEvidence
    }
    
    // MARK: - Pruning
    
    /// Prune patches based on strategy
    public func prunePatches(keepCount: Int, strategy: MemoryPressureHandler.TrimPriority) {
        guard patches.count > keepCount else { return }
        
        let sortedEntries: [(String, PatchEntry)]
        
        switch strategy {
        case .lowestEvidence:
            sortedEntries = patches.sorted { $0.value.evidence < $1.value.evidence }
        case .oldestLastUpdate:
            sortedEntries = patches.sorted { $0.value.lastUpdateMs < $1.value.lastUpdateMs }
        case .lowestDiversity:
            // Default to lowest evidence if diversity not available
            sortedEntries = patches.sorted { $0.value.evidence < $1.value.evidence }
        case .notLocked:
            // First remove non-locked, then by evidence
            sortedEntries = patches.sorted { entry1, entry2 in
                let locked1 = entry1.value.isLocked
                let locked2 = entry2.value.isLocked
                if locked1 != locked2 {
                    return !locked1  // Non-locked first
                }
                return entry1.value.evidence < entry2.value.evidence
            }
        }
        
        // Keep top N
        let toKeep = sortedEntries.suffix(keepCount)
        let keepIds = Set(toKeep.map { $0.0 })
        
        // Remove others
        patches = patches.filter { keepIds.contains($0.key) }
        
        // Recalibrate aggregator
        let nowMs = Int64(Date().timeIntervalSince1970 * 1000)
        let patchesForRecal = allPatchesForRecalibration(currentTimeMs: nowMs)
        aggregator.recalibrate(patches: patchesForRecal, currentTime: Double(nowMs) / 1000.0)
    }
    
    /// Get all patches for recalibration
    public func allPatchesForRecalibration(currentTimeMs: Int64) -> [(patchId: String, evidence: Double, weight: Double, lastUpdate: TimeInterval)] {
        var result: [(patchId: String, evidence: Double, weight: Double, lastUpdate: TimeInterval)] = []
        
        for (patchId, entry) in patches {
            let weight = computeBaseWeight(entry: entry, timestampMs: currentTimeMs)
            result.append((
                patchId: patchId,
                evidence: entry.evidence,
                weight: weight,
                lastUpdate: Double(entry.lastUpdateMs) / 1000.0
            ))
        }
        
        return result
    }
    
    // MARK: - Private Helpers
    
    /// Compute base weight (frequency cap only, decay applied separately)
    private func computeBaseWeight(entry: PatchEntry, timestampMs: Int64) -> Double {
        // Frequency cap: min(1, observationCount / 8)
        let frequencyWeight = min(1.0, Double(entry.observationCount) / EvidenceConstants.weightCapDenominator)
        
        // Note: Decay is applied by BucketedAmortizedAggregator, not here
        return frequencyWeight
    }
    
    /// Compute penalty using FrameRateIndependentPenalty
    private func computePenalty(
        errorStreak: Int,
        lastGoodUpdateMs: Int64?,
        currentTimeMs: Int64
    ) -> Double {
        guard let lastGood = lastGoodUpdateMs else {
            return 0.0  // No good update, don't penalize
        }
        
        // Check cooldown (time-based, frame-rate independent)
        let lastGoodTime = Double(lastGood) / 1000.0
        let currentTime = Double(currentTimeMs) / 1000.0
        let age = currentTime - lastGoodTime
        
        // CORPSE PROTECTION: If patch hasn't been updated in a long time (>10s),
        // don't penalize it (it's effectively "dead" and shouldn't be penalized retroactively)
        let corpseProtectionThreshold: Double = 10.0  // seconds
        if age > corpseProtectionThreshold {
            return 0.0  // Patch is stale, don't penalize
        }
        
        // Check cooldown window
        if !FrameRateIndependentPenalty.isCooldownElapsed(
            lastPenaltyTime: lastGoodTime,
            currentTime: currentTime
        ) {
            return 0.0  // Still in cooldown
        }
        
        // Scale penalty by streak (but cap per-second rate)
        let streakMultiplier = min(3.0, 1.0 + Double(errorStreak) * 0.2)
        let basePenalty = FrameRateIndependentPenalty.basePenaltyPerObservation
        let maxPerFrame = FrameRateIndependentPenalty.maxPenaltyPerSecond / FrameRateIndependentPenalty.currentFrameRate
        
        return min(basePenalty * streakMultiplier, maxPerFrame)
    }
    
    /// Reset all patches
    public func reset() {
        patches.removeAll()
        aggregator = BucketedAmortizedAggregator()
    }

    // MARK: - Persistence (save/load)

    /// Save evidence state to disk. PatchEntry conforms to Codable.
    /// - Parameter url: File URL to write the JSON data.
    /// - Throws: Encoding or I/O errors.
    public func saveToDisk(url: URL) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let data = try encoder.encode(patches)
        try data.write(to: url, options: [.atomic])
    }

    /// Load evidence state from disk. Merges by taking higher evidence per patch
    /// (monotonic guarantee — loaded state never regresses current state).
    /// - Parameter url: File URL to read the JSON data from.
    /// - Throws: Decoding or I/O errors.
    public func loadFromDisk(url: URL) throws {
        let data = try Data(contentsOf: url)
        let decoder = JSONDecoder()
        let loaded = try decoder.decode([String: PatchEntry].self, from: data)
        for (patchId, loadedEntry) in loaded {
            if let existing = patches[patchId] {
                // Monotonic merge — take higher evidence
                if loadedEntry.evidence > existing.evidence {
                    patches[patchId] = loadedEntry
                }
            } else {
                patches[patchId] = loadedEntry
            }
        }
    }
}

/// Evidence locking configuration
public enum EvidenceLocking {
    
    /// Evidence threshold for locking
    public static let lockThreshold: Double = EvidenceConstants.lockThreshold
    
    /// Minimum observations for locking
    public static let minObservationsForLock: Int = EvidenceConstants.minObservationsForLock
    
    /// Check if patch should be locked
    public static func isLocked(evidence: Double, observationCount: Int) -> Bool {
        return evidence >= lockThreshold && observationCount >= minObservationsForLock
    }
}
