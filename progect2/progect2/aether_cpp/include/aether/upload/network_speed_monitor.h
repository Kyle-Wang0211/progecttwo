// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_UPLOAD_NETWORK_SPEED_MONITOR_H
#define AETHER_UPLOAD_NETWORK_SPEED_MONITOR_H

#ifdef __cplusplus

#include "aether/core/status.h"

#include <array>
#include <cstdint>

namespace aether {
namespace upload {

enum class NetworkSpeedClass : std::int32_t {
    kSlow = 0,
    kNormal = 1,
    kFast = 2,
    kUltraFast = 3,
    kUnknown = 4,
};

struct NetworkSpeedSample {
    std::int64_t bytes_transferred{0};
    double duration_seconds{0.0};
    double timestamp_seconds{0.0};
};

struct NetworkSpeedState {
    std::array<NetworkSpeedSample, 64> samples{};
    std::int32_t head{0};
    std::int32_t count{0};
    std::int32_t max_samples{20};
    double window_seconds{30.0};
    double current_speed_mbps{0.0};
    NetworkSpeedClass current_class{NetworkSpeedClass::kUnknown};
    bool reliable{false};
};

struct NetworkSpeedSnapshot {
    NetworkSpeedClass speed_class{NetworkSpeedClass::kUnknown};
    double speed_mbps{0.0};
    std::int32_t sample_count{0};
    bool reliable{false};
};

struct NetworkSpeedStatistics {
    double min_mbps{0.0};
    double max_mbps{0.0};
    double avg_mbps{0.0};
    double stddev_mbps{0.0};
    std::int32_t sample_count{0};
};

enum class ChunkSizingStrategy : std::int32_t {
    kFixed = 0,
    kAdaptive = 1,
    kAggressive = 2,
};

void network_speed_reset(
    NetworkSpeedState* state,
    std::int32_t max_samples,
    double window_seconds);

core::Status network_speed_record_sample(
    NetworkSpeedState* state,
    std::int64_t bytes_transferred,
    double duration_seconds,
    double timestamp_seconds);

core::Status network_speed_snapshot(
    NetworkSpeedState* state,
    double now_seconds,
    NetworkSpeedSnapshot* out);

core::Status network_speed_statistics(
    NetworkSpeedState* state,
    double now_seconds,
    NetworkSpeedStatistics* out);

std::int32_t network_speed_recommended_chunk_size(
    NetworkSpeedClass speed_class,
    std::int32_t chunk_size_min,
    std::int32_t chunk_size_default,
    std::int32_t chunk_size_max);

std::int32_t network_speed_recommended_parallel_count(
    NetworkSpeedClass speed_class,
    std::int32_t max_parallel_uploads);

std::int32_t network_speed_calculate_chunk_size(
    ChunkSizingStrategy strategy,
    NetworkSpeedClass speed_class,
    std::int32_t recommended_chunk_size,
    std::int32_t chunk_size_default,
    std::int32_t chunk_size_max);

std::int32_t network_speed_calculate_chunk_size_for_file(
    std::int32_t base_chunk_size,
    std::int32_t chunk_size_min,
    std::int64_t file_size_bytes);

}  // namespace upload
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_UPLOAD_NETWORK_SPEED_MONITOR_H
