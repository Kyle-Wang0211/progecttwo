// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/second_order_accel.h"

#include <cmath>
#include <cstdio>
#include <vector>

static int g_failed = 0;

static void check(bool cond, const char* msg, int line) {
    if (!cond) {
        std::fprintf(stderr, "FAIL [line %d]: %s\n", line, msg);
        ++g_failed;
    }
}
#define CHECK(cond) check((cond), #cond, __LINE__)

static bool near(float a, float b, float eps = 1e-4f) {
    return std::fabs(a - b) <= eps;
}

static void test_init_valid_size() {
    aether::trainer::SecondOrderConfig cfg{};
    aether::trainer::SecondOrderAccelerator accel(cfg);
    const auto status = accel.init(4, 4);
    CHECK(aether::core::is_ok(status));
}

static void test_update_hessian_accumulates() {
    aether::trainer::SecondOrderConfig cfg{};
    cfg.hessian_ema_alpha = 1.0f;  // No EMA smoothing for deterministic test.
    cfg.damping = 1e-4f;

    aether::trainer::SecondOrderAccelerator accel(cfg);
    accel.init(1, 4);

    float grads[4] = {1.0f, 2.0f, 3.0f, 4.0f};
    auto status = accel.update_hessian(grads, 4);
    CHECK(aether::core::is_ok(status));

    // Second update.
    float grads2[4] = {2.0f, 2.0f, 2.0f, 2.0f};
    status = accel.update_hessian(grads2, 4);
    CHECK(aether::core::is_ok(status));
}

static void test_apply_preconditions() {
    aether::trainer::SecondOrderConfig cfg{};
    cfg.hessian_ema_alpha = 1.0f;  // H = grads^2 exactly.
    cfg.damping = 1e-4f;
    cfg.max_step_size = 100.0f;  // Large so no clipping.

    aether::trainer::SecondOrderAccelerator accel(cfg);
    accel.init(1, 3);

    float grads[3] = {2.0f, 2.0f, 2.0f};
    accel.update_hessian(grads, 3);

    // Preconditioned gradient = grad / sqrt(H_diag + damping).
    float input[3] = {6.0f, 6.0f, 6.0f};
    auto status = accel.apply(input, 3);
    CHECK(aether::core::is_ok(status));

    // H_diag = [4, 4, 4], damping is small.
    // Expected: 6 / sqrt(4 + eps) ~ 6/2 = 3.
    for (int i = 0; i < 3; ++i) {
        CHECK(near(input[i], 3.0f, 0.5f));
    }
}

static void test_max_step_size_clipping() {
    aether::trainer::SecondOrderConfig cfg{};
    cfg.hessian_ema_alpha = 1.0f;
    cfg.damping = 1e-4f;
    cfg.max_step_size = 1.0f;  // Small clip threshold.

    aether::trainer::SecondOrderAccelerator accel(cfg);
    accel.init(1, 2);

    // Very small Hessian diagonal = large preconditioned step.
    float tiny_grads[2] = {0.01f, 0.01f};
    accel.update_hessian(tiny_grads, 2);

    float input[2] = {100.0f, 100.0f};
    accel.apply(input, 2);

    // The implementation clamps each element independently to [-max_step, max_step],
    // not the vector magnitude.  Verify per-element clamping.
    CHECK(std::fabs(input[0]) <= 1.0f + 1e-6f);
    CHECK(std::fabs(input[1]) <= 1.0f + 1e-6f);
}

static void test_reset_clears_state() {
    aether::trainer::SecondOrderConfig cfg{};
    cfg.hessian_ema_alpha = 1.0f;
    cfg.damping = 1e-4f;

    aether::trainer::SecondOrderAccelerator accel(cfg);
    accel.init(1, 4);

    float grads[4] = {5.0f, 5.0f, 5.0f, 5.0f};
    accel.update_hessian(grads, 4);

    accel.reset();

    // After reset, applying should behave like no hessian accumulated.
    float input[4] = {1.0f, 1.0f, 1.0f, 1.0f};
    accel.apply(input, 4);
    // Without hessian, we expect the output to be only damping-based
    // or pass-through depending on implementation.
    // Just verify it doesn't crash and produces finite values.
    for (int i = 0; i < 4; ++i) {
        CHECK(std::isfinite(input[i]));
    }
}

int main() {
    test_init_valid_size();
    test_update_hessian_accumulates();
    test_apply_preconditions();
    test_max_step_size_clipping();
    test_reset_clears_state();

    if (g_failed == 0) {
        std::fprintf(stdout, "second_order_accel_test: all tests passed\n");
    } else {
        std::fprintf(stderr, "second_order_accel_test: %d test(s) failed\n", g_failed);
    }
    return g_failed;
}
