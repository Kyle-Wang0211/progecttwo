// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

import Foundation
#if canImport(CAetherNativeBridge)
import CAetherNativeBridge
#endif

/// Native bridge for patch-evidence ledger update kernel.
/// Delegates verdict/penalty/locking transitions to C++ SSOT.
enum NativePatchEvidenceBridge {

    static func patchEvidenceStep(
        entry: PatchEntry,
        ledgerQuality: Double,
        verdict: ObservationVerdict,
        timestampMs: Int64
    ) -> aether_patch_evidence_step_result_t? {
        #if canImport(CAetherNativeBridge)
        var input = aether_patch_evidence_step_input_t()
        input.previous_evidence = entry.evidence
        input.last_update_ms = entry.lastUpdateMs
        input.observation_count = Int32(entry.observationCount)
        input.error_count = Int32(entry.errorCount)
        input.error_streak = Int32(entry.errorStreak)
        input.last_good_update_ms = entry.lastGoodUpdateMs ?? -1
        input.suspect_count = Int32(entry.suspectCount)
        input.ledger_quality = ledgerQuality
        input.verdict = verdictCode(verdict)
        input.timestamp_ms = timestampMs
        input.lock_threshold = EvidenceConstants.lockThreshold
        input.min_observations_for_lock = Int32(EvidenceConstants.minObservationsForLock)
        input.cooldown_seconds = FrameRateIndependentPenalty.cooldownSeconds
        input.corpse_protection_seconds = 10.0
        input.base_penalty_per_observation = FrameRateIndependentPenalty.basePenaltyPerObservation
        input.max_penalty_per_second = FrameRateIndependentPenalty.maxPenaltyPerSecond
        input.current_frame_rate = FrameRateIndependentPenalty.currentFrameRate

        var out = aether_patch_evidence_step_result_t()
        let rc = aether_patch_evidence_step(&input, &out)
        return rc == 0 ? out : nil
        #else
        return nil
        #endif
    }

    private static func verdictCode(_ verdict: ObservationVerdict) -> Int32 {
        switch verdict {
        case .good:
            return 0
        case .suspect:
            return 1
        case .bad:
            return 2
        case .unknown:
            return 3
        }
    }
}
