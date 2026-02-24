// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/upload/network_speed_monitor.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace aether {
namespace upload {
namespace {

constexpr double kSlowMbps = 3.0;
constexpr double kNormalMbps = 30.0;
constexpr double kUltraFastMbps = 200.0;
constexpr std::int32_t kMinReliableSamples = 5;

inline double clamp_positive(double value, double fallback) {
    if (std::isfinite(value) && value > 0.0) {
        return value;
    }
    return fallback;
}

inline std::int32_t clamp_positive_i32(std::int32_t value, std::int32_t fallback) {
    if (value > 0) {
        return value;
    }
    return fallback;
}

inline std::size_t ring_capacity() {
    return NetworkSpeedState{}.samples.size();
}

inline std::size_t ring_start(const NetworkSpeedState& state) {
    const std::size_t cap = ring_capacity();
    const std::int32_t head = std::max<std::int32_t>(0, state.head);
    const std::int32_t count = std::max<std::int32_t>(0, std::min<std::int32_t>(state.count, static_cast<std::int32_t>(cap)));
    return static_cast<std::size_t>((head - count + static_cast<std::int32_t>(cap)) % static_cast<std::int32_t>(cap));
}

inline NetworkSpeedClass classify_speed(double speed_mbps) {
    const double epsilon = 1e-9;
    if (speed_mbps + epsilon < kSlowMbps) {
        return NetworkSpeedClass::kSlow;
    }
    if (speed_mbps + epsilon < kNormalMbps) {
        return NetworkSpeedClass::kNormal;
    }
    if (speed_mbps + epsilon < kUltraFastMbps) {
        return NetworkSpeedClass::kFast;
    }
    return NetworkSpeedClass::kUltraFast;
}

inline double sample_speed_mbps(const NetworkSpeedSample& sample) {
    if (!(sample.duration_seconds > 0.0) || !std::isfinite(sample.duration_seconds)) {
        return 0.0;
    }
    const double bps = static_cast<double>(sample.bytes_transferred) / sample.duration_seconds;
    if (!std::isfinite(bps) || !(bps > 0.0)) {
        return 0.0;
    }
    return (bps * 8.0) / 1'000'000.0;
}

void recalc_locked(NetworkSpeedState* state, double now_seconds, NetworkSpeedSnapshot* out_snapshot) {
    if (state == nullptr) {
        return;
    }
    const double now = std::isfinite(now_seconds) ? now_seconds : 0.0;
    const double window_seconds = clamp_positive(state->window_seconds, 30.0);
    state->window_seconds = window_seconds;

    const std::size_t cap = ring_capacity();
    const std::int32_t count = std::max<std::int32_t>(0, std::min<std::int32_t>(state->count, static_cast<std::int32_t>(cap)));
    const std::size_t start = ring_start(*state);

    double weighted_sum = 0.0;
    double weight_sum = 0.0;
    std::int32_t valid_count = 0;

    for (std::int32_t i = 0; i < count; ++i) {
        const std::size_t idx = (start + static_cast<std::size_t>(i)) % cap;
        const NetworkSpeedSample& sample = state->samples[idx];
        double age = now - sample.timestamp_seconds;
        if (!std::isfinite(age)) {
            continue;
        }
        if (age < 0.0) {
            age = 0.0;
        }
        if (age > window_seconds) {
            continue;
        }

        const double speed_mbps = sample_speed_mbps(sample);
        if (!(speed_mbps > 0.0) || !std::isfinite(speed_mbps)) {
            continue;
        }

        const double weight = std::max(0.1, 1.0 - (age / window_seconds));
        weighted_sum += speed_mbps * weight;
        weight_sum += weight;
        valid_count += 1;
    }

    if (valid_count >= kMinReliableSamples && weight_sum > 0.0) {
        state->current_speed_mbps = weighted_sum / weight_sum;
        state->current_class = classify_speed(state->current_speed_mbps);
        state->reliable = true;
    } else {
        state->current_speed_mbps = 0.0;
        state->current_class = NetworkSpeedClass::kUnknown;
        state->reliable = false;
    }

    if (out_snapshot != nullptr) {
        out_snapshot->speed_class = state->current_class;
        out_snapshot->speed_mbps = state->current_speed_mbps;
        out_snapshot->sample_count = valid_count;
        out_snapshot->reliable = state->reliable;
    }
}

}  // namespace

void network_speed_reset(
    NetworkSpeedState* state,
    std::int32_t max_samples,
    double window_seconds) {
    if (state == nullptr) {
        return;
    }
    *state = NetworkSpeedState{};
    state->max_samples = std::min<std::int32_t>(
        static_cast<std::int32_t>(ring_capacity()),
        clamp_positive_i32(max_samples, 20));
    state->window_seconds = clamp_positive(window_seconds, 30.0);
}

core::Status network_speed_record_sample(
    NetworkSpeedState* state,
    std::int64_t bytes_transferred,
    double duration_seconds,
    double timestamp_seconds) {
    if (state == nullptr) {
        return core::Status::kInvalidArgument;
    }
    if (bytes_transferred <= 0 || !(duration_seconds > 0.0) ||
        !std::isfinite(duration_seconds) || !std::isfinite(timestamp_seconds)) {
        return core::Status::kInvalidArgument;
    }

    const std::size_t cap = ring_capacity();
    const std::int32_t max_samples = std::max<std::int32_t>(
        1,
        std::min<std::int32_t>(
            static_cast<std::int32_t>(cap),
            clamp_positive_i32(state->max_samples, 20)));
    state->max_samples = max_samples;

    const std::size_t write_idx = static_cast<std::size_t>(
        std::max<std::int32_t>(0, state->head) % static_cast<std::int32_t>(cap));
    state->samples[write_idx] = NetworkSpeedSample{
        bytes_transferred,
        duration_seconds,
        timestamp_seconds,
    };
    state->head = (state->head + 1) % static_cast<std::int32_t>(cap);
    state->count = std::min<std::int32_t>(max_samples, state->count + 1);

    recalc_locked(state, timestamp_seconds, nullptr);
    return core::Status::kOk;
}

core::Status network_speed_snapshot(
    NetworkSpeedState* state,
    double now_seconds,
    NetworkSpeedSnapshot* out) {
    if (state == nullptr || out == nullptr || !std::isfinite(now_seconds)) {
        return core::Status::kInvalidArgument;
    }
    recalc_locked(state, now_seconds, out);
    return core::Status::kOk;
}

core::Status network_speed_statistics(
    NetworkSpeedState* state,
    double now_seconds,
    NetworkSpeedStatistics* out) {
    if (state == nullptr || out == nullptr || !std::isfinite(now_seconds)) {
        return core::Status::kInvalidArgument;
    }

    const std::size_t cap = ring_capacity();
    const std::int32_t count = std::max<std::int32_t>(0, std::min<std::int32_t>(state->count, static_cast<std::int32_t>(cap)));
    const std::size_t start = ring_start(*state);
    const double window_seconds = clamp_positive(state->window_seconds, 30.0);
    const double now = now_seconds;

    std::int32_t valid_count = 0;
    double min_speed = 0.0;
    double max_speed = 0.0;
    double sum_speed = 0.0;

    for (std::int32_t i = 0; i < count; ++i) {
        const std::size_t idx = (start + static_cast<std::size_t>(i)) % cap;
        const NetworkSpeedSample& sample = state->samples[idx];
        double age = now - sample.timestamp_seconds;
        if (!std::isfinite(age)) {
            continue;
        }
        if (age < 0.0) {
            age = 0.0;
        }
        if (age > window_seconds) {
            continue;
        }

        const double speed_mbps = sample_speed_mbps(sample);
        if (!(speed_mbps > 0.0) || !std::isfinite(speed_mbps)) {
            continue;
        }

        if (valid_count == 0) {
            min_speed = speed_mbps;
            max_speed = speed_mbps;
        } else {
            min_speed = std::min(min_speed, speed_mbps);
            max_speed = std::max(max_speed, speed_mbps);
        }
        sum_speed += speed_mbps;
        valid_count += 1;
    }

    if (valid_count < 2) {
        return core::Status::kOutOfRange;
    }

    const double mean = sum_speed / static_cast<double>(valid_count);
    double variance = 0.0;
    for (std::int32_t i = 0; i < count; ++i) {
        const std::size_t idx = (start + static_cast<std::size_t>(i)) % cap;
        const NetworkSpeedSample& sample = state->samples[idx];
        double age = now - sample.timestamp_seconds;
        if (!std::isfinite(age)) {
            continue;
        }
        if (age < 0.0) {
            age = 0.0;
        }
        if (age > window_seconds) {
            continue;
        }
        const double speed_mbps = sample_speed_mbps(sample);
        if (!(speed_mbps > 0.0) || !std::isfinite(speed_mbps)) {
            continue;
        }
        const double delta = speed_mbps - mean;
        variance += delta * delta;
    }
    variance /= static_cast<double>(valid_count);

    out->min_mbps = min_speed;
    out->max_mbps = max_speed;
    out->avg_mbps = mean;
    out->stddev_mbps = std::sqrt(std::max(0.0, variance));
    out->sample_count = valid_count;
    return core::Status::kOk;
}

std::int32_t network_speed_recommended_chunk_size(
    NetworkSpeedClass speed_class,
    std::int32_t chunk_size_min,
    std::int32_t chunk_size_default,
    std::int32_t chunk_size_max) {
    const std::int32_t min_size = std::max<std::int32_t>(1, chunk_size_min);
    const std::int32_t max_size = std::max(min_size, chunk_size_max);
    const std::int32_t default_size = std::max(min_size, std::min(max_size, chunk_size_default));
    const std::int32_t fast_size = std::max(min_size, std::min(max_size, 4 * 1024 * 1024));
    switch (speed_class) {
    case NetworkSpeedClass::kSlow:
        return min_size;
    case NetworkSpeedClass::kNormal:
        return default_size;
    case NetworkSpeedClass::kFast:
        return fast_size;
    case NetworkSpeedClass::kUltraFast:
        return max_size;
    case NetworkSpeedClass::kUnknown:
    default:
        return default_size;
    }
}

std::int32_t network_speed_recommended_parallel_count(
    NetworkSpeedClass speed_class,
    std::int32_t max_parallel_uploads) {
    const std::int32_t max_parallel = std::max<std::int32_t>(1, max_parallel_uploads);
    switch (speed_class) {
    case NetworkSpeedClass::kSlow:
        return 2;
    case NetworkSpeedClass::kNormal:
        return 3;
    case NetworkSpeedClass::kFast:
    case NetworkSpeedClass::kUltraFast:
        return max_parallel;
    case NetworkSpeedClass::kUnknown:
    default:
        return 2;
    }
}

std::int32_t network_speed_calculate_chunk_size(
    ChunkSizingStrategy strategy,
    NetworkSpeedClass speed_class,
    std::int32_t recommended_chunk_size,
    std::int32_t chunk_size_default,
    std::int32_t chunk_size_max) {
    const std::int32_t safe_default = std::max<std::int32_t>(1, chunk_size_default);
    const std::int32_t safe_max = std::max(safe_default, chunk_size_max);
    const std::int32_t safe_recommended = std::max<std::int32_t>(1, recommended_chunk_size);

    switch (strategy) {
    case ChunkSizingStrategy::kFixed:
        return safe_default;
    case ChunkSizingStrategy::kAdaptive:
        return safe_recommended;
    case ChunkSizingStrategy::kAggressive:
        if (speed_class == NetworkSpeedClass::kFast || speed_class == NetworkSpeedClass::kUltraFast) {
            return safe_max;
        }
        return safe_recommended;
    default:
        return safe_recommended;
    }
}

std::int32_t network_speed_calculate_chunk_size_for_file(
    std::int32_t base_chunk_size,
    std::int32_t chunk_size_min,
    std::int64_t file_size_bytes) {
    const std::int32_t safe_min = std::max<std::int32_t>(1, chunk_size_min);
    const std::int32_t safe_base = std::max(safe_min, base_chunk_size);
    if (file_size_bytes < static_cast<std::int64_t>(safe_base) * 2) {
        const std::int64_t half = std::max<std::int64_t>(0, file_size_bytes / 2);
        return static_cast<std::int32_t>(std::max<std::int64_t>(safe_min, half));
    }
    return safe_base;
}

}  // namespace upload
}  // namespace aether
