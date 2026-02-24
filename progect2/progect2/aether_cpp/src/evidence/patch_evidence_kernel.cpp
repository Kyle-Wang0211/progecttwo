// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/evidence/patch_evidence_kernel.h"

#include <algorithm>
#include <cmath>

namespace aether {
namespace evidence {
namespace {

inline double clamp01(double value) {
    if (!std::isfinite(value)) {
        return 0.0;
    }
    return std::max(0.0, std::min(1.0, value));
}

inline bool is_locked(double evidence, std::int32_t observation_count, double lock_threshold, std::int32_t min_obs) {
    return evidence >= lock_threshold && observation_count >= min_obs;
}

inline double compute_penalty(
    std::int32_t error_streak,
    std::int64_t last_good_update_ms,
    std::int64_t current_time_ms,
    double cooldown_seconds,
    double corpse_protection_seconds,
    double base_penalty_per_observation,
    double max_penalty_per_second,
    double current_frame_rate) {
    if (last_good_update_ms < 0) {
        return 0.0;
    }
    const double age_seconds =
        static_cast<double>(current_time_ms - last_good_update_ms) / 1000.0;
    if (!std::isfinite(age_seconds) || age_seconds < 0.0) {
        return 0.0;
    }
    if (age_seconds > corpse_protection_seconds) {
        return 0.0;
    }
    if (age_seconds < cooldown_seconds) {
        return 0.0;
    }

    const double streak_multiplier =
        std::min(3.0, 1.0 + static_cast<double>(std::max<std::int32_t>(0, error_streak)) * 0.2);
    const double safe_frame_rate =
        (std::isfinite(current_frame_rate) && current_frame_rate > 1e-6)
            ? current_frame_rate
            : 30.0;
    const double max_per_frame = max_penalty_per_second / safe_frame_rate;
    return std::max(0.0, std::min(base_penalty_per_observation * streak_multiplier, max_per_frame));
}

}  // namespace

PatchEvidenceStepResult patch_evidence_step(const PatchEvidenceStepInput& input) {
    PatchEvidenceStepResult out{};

    const double previous_evidence = clamp01(input.previous_evidence);
    double evidence = previous_evidence;
    std::int32_t observation_count = std::max<std::int32_t>(0, input.observation_count);
    std::int32_t error_count = std::max<std::int32_t>(0, input.error_count);
    std::int32_t error_streak = std::max<std::int32_t>(0, input.error_streak);
    std::int32_t suspect_count = std::max<std::int32_t>(0, input.suspect_count);
    std::int64_t last_good_update_ms = input.last_good_update_ms;

    PatchEvidenceVerdict verdict = input.verdict;
    if (verdict == PatchEvidenceVerdict::kUnknown) {
        verdict = PatchEvidenceVerdict::kSuspect;
        out.verdict_was_unknown = true;
    }

    const double lock_threshold = clamp01(input.lock_threshold);
    const std::int32_t min_observations_for_lock =
        std::max<std::int32_t>(0, input.min_observations_for_lock);
    const bool locked_before =
        is_locked(evidence, observation_count, lock_threshold, min_observations_for_lock);

    const double ledger_quality = clamp01(input.ledger_quality);
    const std::int64_t timestamp_ms = input.timestamp_ms;
    const double cooldown_seconds = std::max(0.0, input.cooldown_seconds);
    const double corpse_protection_seconds = std::max(0.0, input.corpse_protection_seconds);
    const double base_penalty_per_observation =
        std::max(0.0, input.base_penalty_per_observation);
    const double max_penalty_per_second = std::max(0.0, input.max_penalty_per_second);
    const double current_frame_rate = input.current_frame_rate;

    if (locked_before) {
        if (verdict == PatchEvidenceVerdict::kGood) {
            if (ledger_quality > evidence) {
                evidence = ledger_quality;
                out.should_update_best_frame = true;
                last_good_update_ms = timestamp_ms;
            }
            error_streak = 0;
        } else {
            suspect_count += 1;
            error_streak = 0;
            if (verdict == PatchEvidenceVerdict::kBad) {
                error_count += 1;
            }
        }
        out.was_updated = evidence > previous_evidence;
        out.is_locked = true;
    } else {
        switch (verdict) {
            case PatchEvidenceVerdict::kGood:
                error_streak = 0;
                last_good_update_ms = timestamp_ms;
                if (ledger_quality > evidence) {
                    evidence = ledger_quality;
                    out.should_update_best_frame = true;
                }
                break;
            case PatchEvidenceVerdict::kSuspect:
                suspect_count += 1;
                break;
            case PatchEvidenceVerdict::kBad: {
                error_streak += 1;
                error_count += 1;
                const double penalty = compute_penalty(
                    error_streak,
                    last_good_update_ms,
                    timestamp_ms,
                    cooldown_seconds,
                    corpse_protection_seconds,
                    base_penalty_per_observation,
                    max_penalty_per_second,
                    current_frame_rate);
                evidence = std::max(0.0, evidence - penalty);
                break;
            }
            case PatchEvidenceVerdict::kUnknown:
                // Unreachable due to normalization above.
                suspect_count += 1;
                break;
        }
        out.was_updated = std::fabs(evidence - previous_evidence) > 1e-12;
        out.is_locked = is_locked(
            evidence,
            static_cast<std::int32_t>(observation_count + 1),
            lock_threshold,
            min_observations_for_lock);
    }

    out.evidence = clamp01(evidence);
    out.last_update_ms = timestamp_ms;
    out.observation_count = observation_count + 1;
    out.error_count = error_count;
    out.error_streak = error_streak;
    out.last_good_update_ms = last_good_update_ms;
    out.suspect_count = suspect_count;
    return out;
}

}  // namespace evidence
}  // namespace aether
