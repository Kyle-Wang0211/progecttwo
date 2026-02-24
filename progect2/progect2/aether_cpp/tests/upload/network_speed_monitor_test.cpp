// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/upload/network_speed_monitor.h"

#include <cmath>
#include <cstdio>

static int g_failed = 0;

static void check(bool cond, const char* msg, int line) {
    if (!cond) {
        std::fprintf(stderr, "FAIL [line %d]: %s\n", line, msg);
        ++g_failed;
    }
}
#define CHECK(cond) check((cond), #cond, __LINE__)

static void test_reset_and_invalid_input() {
    aether::upload::NetworkSpeedState state{};
    aether::upload::network_speed_reset(&state, 30, 60.0);
    CHECK(state.max_samples == 30);
    CHECK(state.window_seconds == 60.0);
    CHECK(aether::upload::network_speed_record_sample(
              nullptr, 1000, 1.0, 1.0) == aether::core::Status::kInvalidArgument);
    CHECK(aether::upload::network_speed_record_sample(
              &state, 0, 1.0, 1.0) == aether::core::Status::kInvalidArgument);
}

static void test_classification_and_reliability() {
    aether::upload::NetworkSpeedState state{};
    aether::upload::network_speed_reset(&state, 30, 60.0);
    for (int i = 0; i < 5; ++i) {
        CHECK(aether::upload::network_speed_record_sample(
                  &state, 250000, 1.0, 100.0 + static_cast<double>(i)) ==
              aether::core::Status::kOk);
    }
    aether::upload::NetworkSpeedSnapshot snapshot{};
    CHECK(aether::upload::network_speed_snapshot(
              &state, 105.0, &snapshot) == aether::core::Status::kOk);
    CHECK(snapshot.reliable);
    CHECK(snapshot.speed_class == aether::upload::NetworkSpeedClass::kSlow);
    CHECK(snapshot.sample_count == 5);
}

static void test_statistics() {
    aether::upload::NetworkSpeedState state{};
    aether::upload::network_speed_reset(&state, 30, 60.0);
    CHECK(aether::upload::network_speed_record_sample(&state, 1'000'000, 1.0, 10.0) == aether::core::Status::kOk);
    CHECK(aether::upload::network_speed_record_sample(&state, 2'000'000, 1.0, 11.0) == aether::core::Status::kOk);
    aether::upload::NetworkSpeedStatistics stats{};
    CHECK(aether::upload::network_speed_statistics(&state, 12.0, &stats) == aether::core::Status::kOk);
    CHECK(stats.sample_count == 2);
    CHECK(stats.max_mbps >= stats.min_mbps);
    CHECK(stats.avg_mbps > 0.0);
    CHECK(std::isfinite(stats.stddev_mbps));
}

static void test_recommendations() {
    CHECK(aether::upload::network_speed_recommended_chunk_size(
              aether::upload::NetworkSpeedClass::kSlow, 256 * 1024, 2 * 1024 * 1024, 5'242'880) ==
          256 * 1024);
    CHECK(aether::upload::network_speed_recommended_chunk_size(
              aether::upload::NetworkSpeedClass::kFast, 256 * 1024, 2 * 1024 * 1024, 5'242'880) ==
          4 * 1024 * 1024);
    CHECK(aether::upload::network_speed_recommended_parallel_count(
              aether::upload::NetworkSpeedClass::kNormal, 6) == 3);
    CHECK(aether::upload::network_speed_recommended_parallel_count(
              aether::upload::NetworkSpeedClass::kUltraFast, 6) == 6);
}

int main() {
    test_reset_and_invalid_input();
    test_classification_and_reliability();
    test_statistics();
    test_recommendations();
    if (g_failed != 0) {
        std::fprintf(stderr, "%d network speed monitor test(s) failed\n", g_failed);
    }
    return g_failed == 0 ? 0 : 1;
}
