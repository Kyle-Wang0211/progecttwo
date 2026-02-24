// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/scene_partition.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <unordered_map>
#include <vector>

namespace aether {
namespace trainer {
namespace {

constexpr float kEps = 1e-7f;

/// 3D grid cell index.
struct CellIndex {
    std::int32_t x{0};
    std::int32_t y{0};
    std::int32_t z{0};

    bool operator==(const CellIndex& other) const {
        return x == other.x && y == other.y && z == other.z;
    }
};

/// Hash for CellIndex to use in unordered_map.
struct CellIndexHash {
    std::size_t operator()(const CellIndex& c) const {
        // FNV-1a inspired spatial hash.
        std::size_t h = 14695981039346656037ULL;
        h ^= static_cast<std::size_t>(static_cast<std::uint32_t>(c.x));
        h *= 1099511628211ULL;
        h ^= static_cast<std::size_t>(static_cast<std::uint32_t>(c.y));
        h *= 1099511628211ULL;
        h ^= static_cast<std::size_t>(static_cast<std::uint32_t>(c.z));
        h *= 1099511628211ULL;
        return h;
    }
};

/// Compute which grid cell a position falls into.
CellIndex position_to_cell(
    const innovation::Float3& pos,
    const innovation::Float3& scene_min,
    float cell_size) {

    const float inv = 1.0f / std::max(cell_size, kEps);
    return CellIndex{
        static_cast<std::int32_t>(std::floor((pos.x - scene_min.x) * inv)),
        static_cast<std::int32_t>(std::floor((pos.y - scene_min.y) * inv)),
        static_cast<std::int32_t>(std::floor((pos.z - scene_min.z) * inv))
    };
}

/// Check if a position falls within an AABB (with overlap expansion).
bool point_in_aabb(
    const innovation::Float3& pos,
    const float bounds_min[3],
    const float bounds_max[3]) {

    return pos.x >= bounds_min[0] && pos.x <= bounds_max[0] &&
           pos.y >= bounds_min[1] && pos.y <= bounds_max[1] &&
           pos.z >= bounds_min[2] && pos.z <= bounds_max[2];
}

/// Compute the number of active partitions in a list.
std::size_t count_active(const std::vector<ScenePartition>& partitions) {
    std::size_t count = 0;
    for (const auto& p : partitions) {
        if (p.is_active) {
            ++count;
        }
    }
    return count;
}

/// Simple compression: store the gaussian indices as raw bytes.
/// This is a minimal implementation; a production system would use
/// LZ4 or similar.
std::vector<std::uint8_t> compress_indices(
    const std::vector<std::uint32_t>& indices) {

    const std::size_t byte_count = indices.size() * sizeof(std::uint32_t);
    std::vector<std::uint8_t> data(byte_count);
    if (byte_count > 0) {
        std::memcpy(data.data(), indices.data(), byte_count);
    }
    return data;
}

/// Decompress indices from raw bytes.
std::vector<std::uint32_t> decompress_indices(
    const std::vector<std::uint8_t>& data) {

    const std::size_t count = data.size() / sizeof(std::uint32_t);
    std::vector<std::uint32_t> indices(count);
    if (count > 0) {
        std::memcpy(indices.data(), data.data(), count * sizeof(std::uint32_t));
    }
    return indices;
}

}  // namespace

ScenePartitionManager::ScenePartitionManager(const ScenePartitionConfig& config)
    : config_(config) {
    if (config_.budget_threshold == 0) {
        config_.budget_threshold = 30000;
    }
    if (config_.overlap_width_m < 0.0f) {
        config_.overlap_width_m = 0.5f;
    }
    if (config_.max_active_partitions == 0) {
        config_.max_active_partitions = 2;
    }
}

bool ScenePartitionManager::should_partition(std::size_t gaussian_count) const {
    return gaussian_count > config_.budget_threshold;
}

core::Status ScenePartitionManager::partition(
    const innovation::Float3* positions,
    std::size_t count,
    std::vector<ScenePartition>* out_partitions) {

    if (positions == nullptr || out_partitions == nullptr) {
        return core::Status::kInvalidArgument;
    }

    if (count == 0) {
        out_partitions->clear();
        return core::Status::kOk;
    }

    // ── Step 1: Compute scene bounding box ──
    innovation::Float3 scene_min{
        std::numeric_limits<float>::max(),
        std::numeric_limits<float>::max(),
        std::numeric_limits<float>::max()};
    innovation::Float3 scene_max{
        std::numeric_limits<float>::lowest(),
        std::numeric_limits<float>::lowest(),
        std::numeric_limits<float>::lowest()};

    for (std::size_t i = 0; i < count; ++i) {
        const auto& p = positions[i];
        scene_min.x = std::min(scene_min.x, p.x);
        scene_min.y = std::min(scene_min.y, p.y);
        scene_min.z = std::min(scene_min.z, p.z);
        scene_max.x = std::max(scene_max.x, p.x);
        scene_max.y = std::max(scene_max.y, p.y);
        scene_max.z = std::max(scene_max.z, p.z);
    }

    // ── Step 2: Determine grid cell size ──
    // Target: each partition holds roughly budget_threshold gaussians.
    // Estimate number of partitions needed.
    const std::size_t target_partitions = std::max(
        static_cast<std::size_t>(1),
        (count + config_.budget_threshold - 1) / config_.budget_threshold);

    // Cube root to get approximate cells per axis.
    const float cells_per_axis_f = std::cbrt(
        static_cast<float>(target_partitions));
    const std::uint32_t cells_per_axis = std::max(
        1u, static_cast<std::uint32_t>(std::ceil(cells_per_axis_f)));

    const float scene_extent_x = scene_max.x - scene_min.x + kEps;
    const float scene_extent_y = scene_max.y - scene_min.y + kEps;
    const float scene_extent_z = scene_max.z - scene_min.z + kEps;
    const float max_extent = std::max({scene_extent_x, scene_extent_y, scene_extent_z});
    const float cell_size = max_extent / static_cast<float>(cells_per_axis);

    // ── Step 3: Assign each gaussian to a grid cell ──
    using CellMap = std::unordered_map<CellIndex, std::vector<std::uint32_t>, CellIndexHash>;
    CellMap cell_map;
    cell_map.reserve(target_partitions * 2);

    for (std::size_t i = 0; i < count; ++i) {
        const CellIndex cell = position_to_cell(positions[i], scene_min, cell_size);
        cell_map[cell].push_back(static_cast<std::uint32_t>(i));
    }

    // ── Step 4: Create partitions from cells ──
    // Each non-empty cell becomes a partition.  Adjacent cells could be
    // merged for efficiency, but for simplicity we use one partition per cell.
    std::vector<ScenePartition> new_partitions;
    new_partitions.reserve(cell_map.size());

    for (const auto& entry : cell_map) {
        const CellIndex& cell = entry.first;
        const auto& indices = entry.second;

        ScenePartition partition;
        partition.partition_id = next_partition_id_++;

        // Compute tight bounds from the cell grid coordinates.
        partition.bounds_min[0] = scene_min.x + static_cast<float>(cell.x) * cell_size;
        partition.bounds_min[1] = scene_min.y + static_cast<float>(cell.y) * cell_size;
        partition.bounds_min[2] = scene_min.z + static_cast<float>(cell.z) * cell_size;
        partition.bounds_max[0] = partition.bounds_min[0] + cell_size;
        partition.bounds_max[1] = partition.bounds_min[1] + cell_size;
        partition.bounds_max[2] = partition.bounds_min[2] + cell_size;

        partition.gaussian_indices = indices;
        partition.is_active = false;

        new_partitions.push_back(std::move(partition));
    }

    // ── Step 5: Expand bounds by overlap and include boundary gaussians ──
    for (auto& partition : new_partitions) {
        // Expand bounds by overlap width.
        float expanded_min[3];
        float expanded_max[3];
        for (int d = 0; d < 3; ++d) {
            expanded_min[d] = partition.bounds_min[d] - config_.overlap_width_m;
            expanded_max[d] = partition.bounds_max[d] + config_.overlap_width_m;
        }

        // Find all gaussians in the expanded bounds that aren't already
        // in this partition.
        // Use a boolean flag array for fast lookup.
        std::vector<bool> already_in(count, false);
        for (std::uint32_t idx : partition.gaussian_indices) {
            if (idx < count) {
                already_in[idx] = true;
            }
        }

        for (std::size_t i = 0; i < count; ++i) {
            if (!already_in[i] &&
                point_in_aabb(positions[i], expanded_min, expanded_max)) {
                partition.gaussian_indices.push_back(
                    static_cast<std::uint32_t>(i));
            }
        }

        // Update bounds to the expanded version.
        for (int d = 0; d < 3; ++d) {
            partition.bounds_min[d] = expanded_min[d];
            partition.bounds_max[d] = expanded_max[d];
        }
    }

    // ── Step 6: Activate the first max_active_partitions (by size) ──
    // Sort by partition size descending to activate the largest first.
    std::sort(new_partitions.begin(), new_partitions.end(),
        [](const ScenePartition& a, const ScenePartition& b) {
            return a.gaussian_indices.size() > b.gaussian_indices.size();
        });

    for (std::size_t i = 0; i < new_partitions.size(); ++i) {
        if (i < config_.max_active_partitions) {
            new_partitions[i].is_active = true;
        } else {
            // Compress inactive partition data.
            new_partitions[i].compressed_data =
                compress_indices(new_partitions[i].gaussian_indices);
            new_partitions[i].gaussian_indices.clear();
            new_partitions[i].is_active = false;
        }
    }

    partitions_ = std::move(new_partitions);
    *out_partitions = partitions_;

    return core::Status::kOk;
}

core::Status ScenePartitionManager::activate(std::uint32_t partition_id) {
    // Find the partition.
    ScenePartition* target = nullptr;
    for (auto& p : partitions_) {
        if (p.partition_id == partition_id) {
            target = &p;
            break;
        }
    }

    if (target == nullptr) {
        return core::Status::kInvalidArgument;
    }

    if (target->is_active) {
        return core::Status::kOk;  // Already active.
    }

    // Check if activating would exceed the limit.
    const std::size_t active_count = count_active(partitions_);
    if (active_count >= config_.max_active_partitions) {
        return core::Status::kResourceExhausted;
    }

    // Decompress data.
    if (!target->compressed_data.empty()) {
        target->gaussian_indices = decompress_indices(target->compressed_data);
        target->compressed_data.clear();
    }

    target->is_active = true;
    return core::Status::kOk;
}

core::Status ScenePartitionManager::deactivate(std::uint32_t partition_id) {
    // Find the partition.
    ScenePartition* target = nullptr;
    for (auto& p : partitions_) {
        if (p.partition_id == partition_id) {
            target = &p;
            break;
        }
    }

    if (target == nullptr) {
        return core::Status::kInvalidArgument;
    }

    if (!target->is_active) {
        return core::Status::kOk;  // Already inactive.
    }

    // Compress and release live data.
    target->compressed_data = compress_indices(target->gaussian_indices);
    target->gaussian_indices.clear();
    target->is_active = false;

    return core::Status::kOk;
}

const std::vector<ScenePartition>& ScenePartitionManager::partitions() const {
    return partitions_;
}

}  // namespace trainer
}  // namespace aether
