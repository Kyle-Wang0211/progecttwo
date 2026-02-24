// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/upload/fusion_scheduler.h"

#include <cstdint>
#include <cstdio>
#include <limits>

static int g_failed = 0;

static void check(bool cond, const char* msg, int line) {
    if (!cond) {
        std::fprintf(stderr, "FAIL [line %d]: %s\n", line, msg);
        ++g_failed;
    }
}
#define CHECK(cond) check((cond), #cond, __LINE__)

static aether::upload::FusionSchedulerInput make_default_input() {
    aether::upload::FusionSchedulerInput in{};
    in.queue_length_bytes = 2 * 1024 * 1024;
    in.last_chunk_size_bytes = 2 * 1024 * 1024;
    in.kalman_predicted_bps = 16'000'000.0;
    in.kalman_trend = 1;
    in.ml_predicted_bps = 16'000'000.0;
    in.has_ml_prediction = false;
    in.controller_accuracies = {{1.0, 1.0, 1.0, 1.0, 1.0}};
    in.chunk_size_min_bytes = 256 * 1024;
    in.chunk_size_default_bytes = 2 * 1024 * 1024;
    in.chunk_size_max_bytes = 5'242'880;
    in.chunk_size_step_bytes = 512 * 1024;
    in.ewma_alpha = 0.3;
    in.ewma_target_seconds = 3.0;
    in.ml_norm_bps = 10'000'000.0;
    in.alignment_bytes = 16 * 1024;
    return in;
}

static void test_null_output() {
    const aether::upload::FusionSchedulerInput in = make_default_input();
    CHECK(
        aether::upload::fusion_scheduler_decide_chunk_size(in, nullptr) ==
        aether::core::Status::kInvalidArgument);
}

static void test_final_size_bounds_and_alignment() {
    const aether::upload::FusionSchedulerInput in = make_default_input();
    aether::upload::FusionSchedulerOutput out{};
    const aether::core::Status status =
        aether::upload::fusion_scheduler_decide_chunk_size(in, &out);
    CHECK(status == aether::core::Status::kOk);
    CHECK(out.final_chunk_size_bytes >= in.chunk_size_min_bytes);
    CHECK(out.final_chunk_size_bytes <= in.chunk_size_max_bytes);
    CHECK((out.final_chunk_size_bytes % in.alignment_bytes) == 0);
}

static void test_abr_regimes() {
    aether::upload::FusionSchedulerInput in = make_default_input();
    aether::upload::FusionSchedulerOutput out{};

    in.queue_length_bytes = 512 * 1024;
    CHECK(aether::upload::fusion_scheduler_decide_chunk_size(in, &out) == aether::core::Status::kOk);
    CHECK(out.abr_size_bytes == in.chunk_size_max_bytes);

    in.queue_length_bytes = 5 * 1024 * 1024;
    CHECK(aether::upload::fusion_scheduler_decide_chunk_size(in, &out) == aether::core::Status::kOk);
    CHECK(out.abr_size_bytes == in.chunk_size_default_bytes);

    in.queue_length_bytes = 64 * 1024 * 1024;
    CHECK(aether::upload::fusion_scheduler_decide_chunk_size(in, &out) == aether::core::Status::kOk);
    CHECK(out.abr_size_bytes == in.chunk_size_min_bytes);
}

static void test_kalman_trend_step() {
    aether::upload::FusionSchedulerInput in = make_default_input();
    aether::upload::FusionSchedulerOutput out{};
    in.last_chunk_size_bytes = in.chunk_size_default_bytes;

    in.kalman_trend = 0;
    CHECK(aether::upload::fusion_scheduler_decide_chunk_size(in, &out) == aether::core::Status::kOk);
    CHECK(out.kalman_size_bytes == in.chunk_size_default_bytes + in.chunk_size_step_bytes);

    in.kalman_trend = 2;
    CHECK(aether::upload::fusion_scheduler_decide_chunk_size(in, &out) == aether::core::Status::kOk);
    CHECK(out.kalman_size_bytes == in.chunk_size_default_bytes - in.chunk_size_step_bytes);
}

static void test_ml_candidate_enabled() {
    aether::upload::FusionSchedulerInput in = make_default_input();
    in.has_ml_prediction = true;
    in.ml_predicted_bps = 60'000'000.0;
    aether::upload::FusionSchedulerOutput out{};
    CHECK(aether::upload::fusion_scheduler_decide_chunk_size(in, &out) == aether::core::Status::kOk);
    CHECK(out.ml_size_bytes >= in.chunk_size_default_bytes);
}

static void test_non_finite_inputs_fail_closed() {
    aether::upload::FusionSchedulerInput in = make_default_input();
    in.kalman_predicted_bps = std::numeric_limits<double>::quiet_NaN();
    in.ml_predicted_bps = std::numeric_limits<double>::infinity();
    in.ewma_alpha = std::numeric_limits<double>::infinity();
    in.ewma_target_seconds = -1.0;
    in.ml_norm_bps = 0.0;
    in.chunk_size_min_bytes = -1;
    in.chunk_size_default_bytes = -1;
    in.chunk_size_max_bytes = -1;
    in.chunk_size_step_bytes = -1;
    in.alignment_bytes = -1;
    aether::upload::FusionSchedulerOutput out{};
    CHECK(aether::upload::fusion_scheduler_decide_chunk_size(in, &out) == aether::core::Status::kOk);
    CHECK(out.final_chunk_size_bytes > 0);
}

int main() {
    test_null_output();
    test_final_size_bounds_and_alignment();
    test_abr_regimes();
    test_kalman_trend_step();
    test_ml_candidate_enabled();
    test_non_finite_inputs_fail_closed();
    if (g_failed != 0) {
        std::fprintf(stderr, "%d fusion scheduler test(s) failed\n", g_failed);
    }
    return g_failed == 0 ? 0 : 1;
}
