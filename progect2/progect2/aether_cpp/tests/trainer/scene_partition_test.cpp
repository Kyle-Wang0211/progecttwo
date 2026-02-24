// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/scene_partition.h"

#include <cmath>
#include <cstdio>
#include <cstring>
#include <vector>

static int g_failed = 0;

static void check(bool cond, const char* msg, int line) {
    if (!cond) {
        std::fprintf(stderr, "FAIL [line %d]: %s\n", line, msg);
        ++g_failed;
    }
}
#define CHECK(cond) check((cond), #cond, __LINE__)

static void test_should_partition_below_threshold() {
    aether::trainer::ScenePartitionConfig cfg{};
    cfg.budget_threshold = 100000;

    aether::trainer::ScenePartitionManager mgr(cfg);
    CHECK(!mgr.should_partition(50000));
}

static void test_should_partition_above_threshold() {
    aether::trainer::ScenePartitionConfig cfg{};
    cfg.budget_threshold = 100000;

    aether::trainer::ScenePartitionManager mgr(cfg);
    CHECK(mgr.should_partition(200000));
}

static void test_partition_creates_multiple() {
    aether::trainer::ScenePartitionConfig cfg{};
    cfg.budget_threshold = 50;
    cfg.overlap_width_m = 0.5f;

    // Create scattered positions.
    const int count = 200;
    std::vector<aether::innovation::Float3> positions(count);
    for (int i = 0; i < count; ++i) {
        int cluster = i % 4;
        float cx = (cluster & 1) ? 5.0f : -5.0f;
        float cz = (cluster & 2) ? 5.0f : -5.0f;
        positions[i].x = cx + static_cast<float>(i % 10) * 0.01f;
        positions[i].y = 0.0f;
        positions[i].z = cz + static_cast<float>(i % 10) * 0.01f;
    }

    aether::trainer::ScenePartitionManager mgr(cfg);
    std::vector<aether::trainer::ScenePartition> partitions;
    auto status = mgr.partition(positions.data(), count, &partitions);
    CHECK(aether::core::is_ok(status));
    CHECK(partitions.size() > 1);
}

static void test_activate_deactivate() {
    aether::trainer::ScenePartitionConfig cfg{};
    cfg.budget_threshold = 20;
    cfg.overlap_width_m = 0.5f;
    // Use a small max_active so that some partitions are initially inactive.
    cfg.max_active_partitions = 1;

    const int count = 100;
    std::vector<aether::innovation::Float3> positions(count);
    for (int i = 0; i < count; ++i) {
        positions[i].x = static_cast<float>(i % 10);
        positions[i].y = 0.0f;
        positions[i].z = static_cast<float>(i / 10);
    }

    aether::trainer::ScenePartitionManager mgr(cfg);
    std::vector<aether::trainer::ScenePartition> partitions;
    mgr.partition(positions.data(), count, &partitions);

    if (partitions.size() >= 2) {
        // The implementation auto-activates the first max_active_partitions
        // partitions (sorted by size descending).  So partitions[0] is active
        // and partitions beyond max_active_partitions are inactive.
        CHECK(partitions[0].is_active);

        // Find an inactive partition to test activate/deactivate cycle.
        std::uint32_t inactive_pid = 0;
        bool found_inactive = false;
        for (const auto& p : partitions) {
            if (!p.is_active) {
                inactive_pid = p.partition_id;
                found_inactive = true;
                break;
            }
        }
        CHECK(found_inactive);

        // Deactivate the active partition first to make room.
        auto status = mgr.deactivate(partitions[0].partition_id);
        CHECK(aether::core::is_ok(status));

        // Now activate the previously inactive partition.
        status = mgr.activate(inactive_pid);
        CHECK(aether::core::is_ok(status));

        // Verify via the partitions() accessor.
        const auto& parts = mgr.partitions();
        bool found_active = false;
        for (const auto& p : parts) {
            if (p.partition_id == inactive_pid && p.is_active) {
                found_active = true;
            }
        }
        CHECK(found_active);

        // Deactivate the partition we just activated.
        status = mgr.deactivate(inactive_pid);
        CHECK(aether::core::is_ok(status));
    }
}

static void test_single_partition_when_few_gaussians() {
    aether::trainer::ScenePartitionConfig cfg{};
    cfg.budget_threshold = 100;
    cfg.overlap_width_m = 0.5f;

    const int count = 10;
    std::vector<aether::innovation::Float3> positions(count);
    for (int i = 0; i < count; ++i) {
        positions[i].x = static_cast<float>(i) * 0.1f;
        positions[i].y = 0.0f;
        positions[i].z = 0.0f;
    }

    aether::trainer::ScenePartitionManager mgr(cfg);
    std::vector<aether::trainer::ScenePartition> partitions;
    auto status = mgr.partition(positions.data(), count, &partitions);
    CHECK(aether::core::is_ok(status));
    CHECK(partitions.size() == 1);
}

static void test_gaussian_indices_non_empty() {
    aether::trainer::ScenePartitionConfig cfg{};
    cfg.budget_threshold = 30;
    cfg.overlap_width_m = 0.5f;

    const int count = 120;
    std::vector<aether::innovation::Float3> positions(count);
    for (int i = 0; i < count; ++i) {
        positions[i].x = static_cast<float>(i) * 0.1f;
        positions[i].y = 0.0f;
        positions[i].z = 0.0f;
    }

    aether::trainer::ScenePartitionManager mgr(cfg);
    std::vector<aether::trainer::ScenePartition> partitions;
    mgr.partition(positions.data(), count, &partitions);
    CHECK(partitions.size() >= 2);

    // Each partition should have data: active partitions keep gaussian_indices,
    // inactive partitions store data in compressed_data (indices are cleared).
    for (const auto& p : partitions) {
        CHECK(!p.gaussian_indices.empty() || !p.compressed_data.empty());
    }
}

int main() {
    test_should_partition_below_threshold();
    test_should_partition_above_threshold();
    test_partition_creates_multiple();
    test_activate_deactivate();
    test_single_partition_when_few_gaussians();
    test_gaussian_indices_non_empty();

    if (g_failed == 0) {
        std::fprintf(stdout, "scene_partition_test: all tests passed\n");
    } else {
        std::fprintf(stderr, "scene_partition_test: %d test(s) failed\n", g_failed);
    }
    return g_failed;
}
