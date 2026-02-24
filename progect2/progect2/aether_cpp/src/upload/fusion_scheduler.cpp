// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/upload/fusion_scheduler.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <utility>
#include <vector>

namespace aether {
namespace upload {
namespace {

constexpr std::int64_t kAbrLowThresholdBytes = 1024 * 1024;
constexpr std::int64_t kAbrHighThresholdBytes = 10 * 1024 * 1024;

std::int64_t clamp_i64(std::int64_t value, std::int64_t lo, std::int64_t hi) {
    return std::max(lo, std::min(hi, value));
}

std::int64_t sanitize_positive_i64(std::int32_t value, std::int64_t fallback) {
    if (value > 0) {
        return static_cast<std::int64_t>(value);
    }
    return fallback;
}

double sanitize_positive_double(double value, double fallback) {
    if (std::isfinite(value) && value > 0.0) {
        return value;
    }
    return fallback;
}

double sanitize_unit_interval(double value, double fallback) {
    if (!std::isfinite(value)) {
        return fallback;
    }
    return std::max(0.0, std::min(1.0, value));
}

std::int64_t align_down(std::int64_t value, std::int64_t alignment) {
    if (alignment <= 1) {
        return value;
    }
    if (value < 0) {
        return 0;
    }
    return (value / alignment) * alignment;
}

std::int64_t weighted_trimmed_mean(
    const std::vector<std::int64_t>& candidates,
    const std::array<double, 5>& accuracies,
    std::size_t count,
    std::int64_t fallback) {
    if (candidates.empty() || count == 0u) {
        return fallback;
    }
    const std::size_t used = std::min<std::size_t>(std::min<std::size_t>(count, candidates.size()), accuracies.size());
    if (used == 0u) {
        return fallback;
    }

    std::vector<std::pair<std::int64_t, double>> pairs;
    pairs.reserve(used);
    for (std::size_t i = 0u; i < used; ++i) {
        const double w = accuracies[i];
        pairs.emplace_back(candidates[i], std::isfinite(w) ? w : 0.0);
    }
    std::sort(pairs.begin(), pairs.end(), [](const auto& lhs, const auto& rhs) {
        return lhs.first < rhs.first;
    });

    if (pairs.size() <= 2u) {
        return pairs.front().first;
    }

    long double weighted_sum = 0.0L;
    long double total_weight = 0.0L;
    for (std::size_t i = 1u; i + 1u < pairs.size(); ++i) {
        const double weight = pairs[i].second;
        if (!(weight > 0.0)) {
            continue;
        }
        weighted_sum += static_cast<long double>(pairs[i].first) * static_cast<long double>(weight);
        total_weight += static_cast<long double>(weight);
    }

    if (!(total_weight > 0.0L)) {
        return fallback;
    }
    return static_cast<std::int64_t>(weighted_sum / total_weight);
}

}  // namespace

core::Status fusion_scheduler_decide_chunk_size(
    const FusionSchedulerInput& input,
    FusionSchedulerOutput* out) {
    if (out == nullptr) {
        return core::Status::kInvalidArgument;
    }

    const std::int64_t min_chunk_size = sanitize_positive_i64(input.chunk_size_min_bytes, 256 * 1024);
    const std::int64_t max_chunk_size = std::max(
        min_chunk_size,
        sanitize_positive_i64(input.chunk_size_max_bytes, 5'242'880));
    const std::int64_t default_chunk_size = clamp_i64(
        sanitize_positive_i64(input.chunk_size_default_bytes, 2 * 1024 * 1024),
        min_chunk_size,
        max_chunk_size);
    const std::int64_t step_chunk_size = std::max<std::int64_t>(
        1,
        sanitize_positive_i64(input.chunk_size_step_bytes, 512 * 1024));
    const std::int64_t alignment_bytes = std::max<std::int64_t>(
        1,
        sanitize_positive_i64(input.alignment_bytes, 16 * 1024));

    const double ewma_alpha = sanitize_unit_interval(input.ewma_alpha, 0.3);
    const double ewma_target_seconds = sanitize_positive_double(input.ewma_target_seconds, 3.0);
    const double ml_norm_bps = sanitize_positive_double(input.ml_norm_bps, 10'000'000.0);

    const std::int64_t queue_length = std::max<std::int64_t>(0, input.queue_length_bytes);
    const std::int64_t last_chunk_size = clamp_i64(
        static_cast<std::int64_t>(input.last_chunk_size_bytes),
        min_chunk_size,
        max_chunk_size);
    const double fallback_bps =
        (static_cast<double>(default_chunk_size) * 8.0) / std::max(1e-6, ewma_target_seconds);
    const double kalman_predicted_bps = sanitize_positive_double(input.kalman_predicted_bps, fallback_bps);
    const double ml_predicted_bps = sanitize_positive_double(input.ml_predicted_bps, kalman_predicted_bps);

    const std::int64_t mpc_size = default_chunk_size;
    std::int64_t abr_size = min_chunk_size;
    if (queue_length < kAbrLowThresholdBytes) {
        abr_size = max_chunk_size;
    } else if (queue_length < kAbrHighThresholdBytes) {
        abr_size = default_chunk_size;
    }

    double ewma_target_bytes = (kalman_predicted_bps / 8.0) * ewma_target_seconds;
    if (!std::isfinite(ewma_target_bytes)) {
        ewma_target_bytes = static_cast<double>(default_chunk_size);
    }
    double ewma_size_double =
        static_cast<double>(last_chunk_size) * (1.0 - ewma_alpha) + ewma_target_bytes * ewma_alpha;
    if (!std::isfinite(ewma_size_double)) {
        ewma_size_double = static_cast<double>(default_chunk_size);
    }
    const std::int64_t ewma_size = static_cast<std::int64_t>(ewma_size_double);

    std::int64_t kalman_size = last_chunk_size;
    if (input.kalman_trend == 0) {
        kalman_size = std::min(max_chunk_size, last_chunk_size + step_chunk_size);
    } else if (input.kalman_trend == 2) {
        kalman_size = std::max(min_chunk_size, last_chunk_size - step_chunk_size);
    }

    double ml_size_double = static_cast<double>(default_chunk_size) * (ml_predicted_bps / ml_norm_bps);
    if (!std::isfinite(ml_size_double)) {
        ml_size_double = static_cast<double>(default_chunk_size);
    }
    const std::int64_t ml_size = static_cast<std::int64_t>(ml_size_double);

    std::vector<std::int64_t> candidates;
    candidates.reserve(input.has_ml_prediction ? 5u : 4u);
    candidates.push_back(mpc_size);
    candidates.push_back(abr_size);
    candidates.push_back(ewma_size);
    candidates.push_back(kalman_size);
    if (input.has_ml_prediction) {
        candidates.push_back(ml_size);
    }

    const std::int64_t fused_size = weighted_trimmed_mean(
        candidates,
        input.controller_accuracies,
        candidates.size(),
        default_chunk_size);
    const std::int64_t safe_size = fused_size;  // Placeholder for Lyapunov DPP constraints.
    const std::int64_t aligned_size = align_down(safe_size, alignment_bytes);
    const std::int64_t final_chunk_size = clamp_i64(aligned_size, min_chunk_size, max_chunk_size);

    out->mpc_size_bytes = static_cast<std::int32_t>(clamp_i64(mpc_size, min_chunk_size, max_chunk_size));
    out->abr_size_bytes = static_cast<std::int32_t>(clamp_i64(abr_size, min_chunk_size, max_chunk_size));
    out->ewma_size_bytes = static_cast<std::int32_t>(clamp_i64(ewma_size, min_chunk_size, max_chunk_size));
    out->kalman_size_bytes = static_cast<std::int32_t>(clamp_i64(kalman_size, min_chunk_size, max_chunk_size));
    out->ml_size_bytes = static_cast<std::int32_t>(clamp_i64(ml_size, min_chunk_size, max_chunk_size));
    out->fused_size_bytes = static_cast<std::int32_t>(clamp_i64(fused_size, min_chunk_size, max_chunk_size));
    out->safe_size_bytes = static_cast<std::int32_t>(clamp_i64(safe_size, min_chunk_size, max_chunk_size));
    out->final_chunk_size_bytes = static_cast<std::int32_t>(final_chunk_size);
    return core::Status::kOk;
}

}  // namespace upload
}  // namespace aether
