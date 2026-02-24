// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_EVIDENCE_PATCH_EVIDENCE_KERNEL_H
#define AETHER_EVIDENCE_PATCH_EVIDENCE_KERNEL_H

#ifdef __cplusplus

#include <cstdint>

namespace aether {
namespace evidence {

enum class PatchEvidenceVerdict : std::int32_t {
    kGood = 0,
    kSuspect = 1,
    kBad = 2,
    kUnknown = 3,
};

struct PatchEvidenceStepInput {
    double previous_evidence{0.0};
    std::int64_t last_update_ms{0};
    std::int32_t observation_count{0};
    std::int32_t error_count{0};
    std::int32_t error_streak{0};
    std::int64_t last_good_update_ms{-1};  // <0 means none
    std::int32_t suspect_count{0};

    double ledger_quality{0.0};
    PatchEvidenceVerdict verdict{PatchEvidenceVerdict::kUnknown};
    std::int64_t timestamp_ms{0};

    double lock_threshold{0.85};
    std::int32_t min_observations_for_lock{20};
    double cooldown_seconds{0.5};
    double corpse_protection_seconds{10.0};
    double base_penalty_per_observation{0.01};
    double max_penalty_per_second{0.30};
    double current_frame_rate{30.0};
};

struct PatchEvidenceStepResult {
    double evidence{0.0};
    std::int64_t last_update_ms{0};
    std::int32_t observation_count{0};
    std::int32_t error_count{0};
    std::int32_t error_streak{0};
    std::int64_t last_good_update_ms{-1};  // <0 means none
    std::int32_t suspect_count{0};
    bool is_locked{false};
    bool should_update_best_frame{false};
    bool was_updated{false};
    bool verdict_was_unknown{false};
};

PatchEvidenceStepResult patch_evidence_step(const PatchEvidenceStepInput& input);

}  // namespace evidence
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_EVIDENCE_PATCH_EVIDENCE_KERNEL_H
