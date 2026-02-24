// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_UPLOAD_FUSION_SCHEDULER_H
#define AETHER_UPLOAD_FUSION_SCHEDULER_H

#ifdef __cplusplus

#include "aether/core/status.h"

#include <array>
#include <cstdint>

namespace aether {
namespace upload {

struct FusionSchedulerInput {
    std::int64_t queue_length_bytes{0};
    std::int32_t last_chunk_size_bytes{2 * 1024 * 1024};

    double kalman_predicted_bps{0.0};
    std::int32_t kalman_trend{1};  // 0 rising, 1 stable, 2 falling

    double ml_predicted_bps{0.0};
    bool has_ml_prediction{false};

    std::array<double, 5> controller_accuracies{{1.0, 1.0, 1.0, 1.0, 1.0}};

    std::int32_t chunk_size_min_bytes{256 * 1024};
    std::int32_t chunk_size_default_bytes{2 * 1024 * 1024};
    std::int32_t chunk_size_max_bytes{5'242'880};
    std::int32_t chunk_size_step_bytes{512 * 1024};

    double ewma_alpha{0.3};
    double ewma_target_seconds{3.0};
    double ml_norm_bps{10'000'000.0};
    std::int32_t alignment_bytes{16 * 1024};
};

struct FusionSchedulerOutput {
    std::int32_t mpc_size_bytes{0};
    std::int32_t abr_size_bytes{0};
    std::int32_t ewma_size_bytes{0};
    std::int32_t kalman_size_bytes{0};
    std::int32_t ml_size_bytes{0};
    std::int32_t fused_size_bytes{0};
    std::int32_t safe_size_bytes{0};
    std::int32_t final_chunk_size_bytes{0};
};

core::Status fusion_scheduler_decide_chunk_size(
    const FusionSchedulerInput& input,
    FusionSchedulerOutput* out);

}  // namespace upload
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_UPLOAD_FUSION_SCHEDULER_H
