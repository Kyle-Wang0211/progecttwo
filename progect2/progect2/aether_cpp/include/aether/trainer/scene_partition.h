// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_TRAINER_SCENE_PARTITION_H
#define AETHER_TRAINER_SCENE_PARTITION_H

#ifdef __cplusplus

#include "aether/core/status.h"
#include "aether/innovation/core_types.h"

#include <cstddef>
#include <cstdint>
#include <vector>

namespace aether {
namespace trainer {

/// Configuration for scene partitioning.
struct ScenePartitionConfig {
    /// Gaussian count threshold above which partitioning is triggered.
    std::size_t budget_threshold{30000};

    /// Overlap width in meters between adjacent partitions.
    /// Ensures smooth blending at partition boundaries.
    float overlap_width_m{0.5f};

    /// Maximum number of simultaneously active partitions.
    /// Inactive partitions have their data compressed/swapped out.
    std::size_t max_active_partitions{2};
};

/// A single spatial partition of the scene.
struct ScenePartition {
    /// Unique partition identifier.
    std::uint32_t partition_id{0};

    /// Axis-aligned bounding box minimum corner (meters).
    float bounds_min[3]{0.0f, 0.0f, 0.0f};

    /// Axis-aligned bounding box maximum corner (meters).
    float bounds_max[3]{0.0f, 0.0f, 0.0f};

    /// Indices of gaussians that belong to this partition
    /// (including overlap-region duplicates).
    std::vector<std::uint32_t> gaussian_indices;

    /// Whether this partition is currently active (loaded in memory).
    bool is_active{false};

    /// Compressed representation of gaussian data for inactive partitions.
    /// For active partitions, this is empty — the live data is used directly.
    std::vector<std::uint8_t> compressed_data;
};

/// Large scene partitioning manager for memory management.
///
/// Divides the scene into spatial partitions using a uniform grid,
/// with configurable overlap for seamless boundary blending.  Only
/// a limited number of partitions can be active simultaneously;
/// inactive partitions store compressed index data to reduce
/// memory pressure.
///
/// Thread safety: NOT thread-safe.  Caller must synchronize.
class ScenePartitionManager {
public:
    explicit ScenePartitionManager(const ScenePartitionConfig& config);

    /// Returns true if the gaussian count exceeds the budget threshold
    /// and partitioning should be performed.
    bool should_partition(std::size_t gaussian_count) const;

    /// Partition the scene based on gaussian positions.
    ///
    /// Computes a spatial hash grid over the scene bounding box,
    /// assigns each gaussian to a cell, groups adjacent cells into
    /// partitions, and expands partition bounds by overlap_width_m
    /// to include boundary gaussians.
    ///
    /// @param positions  Array of gaussian positions.
    /// @param count      Number of gaussians.
    /// @param partitions Output partition list (replaced, not appended).
    /// @return kOk on success.
    core::Status partition(
        const innovation::Float3* positions,
        std::size_t count,
        std::vector<ScenePartition>* partitions);

    /// Activate a partition (decompress data, mark as active).
    /// If max_active_partitions would be exceeded, returns kResourceExhausted.
    core::Status activate(std::uint32_t partition_id);

    /// Deactivate a partition (compress data, mark as inactive).
    core::Status deactivate(std::uint32_t partition_id);

    /// Read-only access to the current partition list.
    const std::vector<ScenePartition>& partitions() const;

private:
    ScenePartitionConfig config_;
    std::vector<ScenePartition> partitions_;
    std::uint32_t next_partition_id_{0};
};

}  // namespace trainer
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_TRAINER_SCENE_PARTITION_H
