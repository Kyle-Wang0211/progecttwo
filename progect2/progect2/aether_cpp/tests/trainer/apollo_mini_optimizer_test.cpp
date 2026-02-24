// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/apollo_mini_optimizer.h"

#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>
#include <vector>

static int g_failed = 0;

static void check(bool cond, const char* msg, int line) {
    if (!cond) {
        std::fprintf(stderr, "FAIL [line %d]: %s\n", line, msg);
        ++g_failed;
    }
}
#define CHECK(cond) check((cond), #cond, __LINE__)

static void test_init_valid_config() {
    aether::trainer::ApolloMiniConfig cfg{};
    cfg.lr_position = 1e-3f;
    cfg.beta1 = 0.9f;
    cfg.beta2 = 0.999f;
    cfg.eps = 1e-8f;
    cfg.grad_clip_norm = 1.0f;

    aether::trainer::ApolloMiniOptimizer opt;
    const auto status = opt.init(cfg, 100);
    CHECK(aether::core::is_ok(status));
    CHECK(opt.current_step() == 0);
}

static void test_quadratic_convergence() {
    // Minimize f(x) = sum(x_i^2) using a buffer sized for 1 gaussian.
    // We use kGradientsPerGaussian floats as the parameter block.
    const std::size_t n_params = aether::trainer::kGradientsPerGaussian;
    aether::trainer::ApolloMiniConfig cfg{};
    cfg.lr_position = 0.05f;
    cfg.lr_scale = 0.05f;
    cfg.lr_opacity = 0.05f;
    cfg.lr_sh = 0.05f;
    cfg.lr_rotation = 0.05f;
    cfg.beta1 = 0.9f;
    cfg.beta2 = 0.999f;
    cfg.eps = 1e-8f;
    cfg.grad_clip_norm = 10.0f;

    aether::trainer::ApolloMiniOptimizer opt;
    opt.init(cfg, 1);

    std::vector<float> params(n_params, 5.0f);
    float prev_loss = 0.0f;
    for (std::size_t i = 0; i < n_params; ++i) prev_loss += params[i] * params[i];

    for (int iter = 0; iter < 100; ++iter) {
        std::vector<float> grads(n_params);
        for (std::size_t i = 0; i < n_params; ++i) grads[i] = 2.0f * params[i];
        opt.step(params.data(), grads.data(), 1);
    }
    // After 100 steps the final loss should be smaller than the initial loss,
    // verifying overall convergence.  The optimizer does not guarantee strict
    // monotonic decrease at every single step due to momentum and rank-1
    // auxiliary updates.
    float final_loss = 0.0f;
    for (std::size_t i = 0; i < n_params; ++i) final_loss += params[i] * params[i];
    CHECK(final_loss < prev_loss);
    CHECK(final_loss < 25.0f * static_cast<float>(n_params));
}

static void test_gradient_clipping() {
    aether::trainer::ApolloMiniConfig cfg{};
    cfg.lr_position = 0.01f;
    cfg.beta1 = 0.9f;
    cfg.beta2 = 0.999f;
    cfg.eps = 1e-8f;
    cfg.grad_clip_norm = 0.5f;

    aether::trainer::ApolloMiniOptimizer opt;
    opt.init(cfg, 1);

    const std::size_t n_params = aether::trainer::kGradientsPerGaussian;
    std::vector<float> params(n_params, 1.0f);
    std::vector<float> grads(n_params, 100.0f);  // Very large gradient.

    float param_before = params[0];
    opt.step(params.data(), grads.data(), 1);

    // The step should be bounded by the clipping; param change should be small.
    float delta = std::fabs(params[0] - param_before);
    CHECK(delta < 10.0f);
}

static void test_memory_bytes() {
    aether::trainer::ApolloMiniConfig cfg{};
    cfg.lr_position = 1e-3f;
    cfg.beta1 = 0.9f;
    cfg.beta2 = 0.999f;
    cfg.eps = 1e-8f;
    cfg.grad_clip_norm = 1.0f;

    aether::trainer::ApolloMiniOptimizer opt;
    opt.init(cfg, 200);

    const std::size_t mem = opt.memory_bytes();
    // Must store at least first moment per parameter.
    const std::size_t min_expected = 200 * aether::trainer::kGradientsPerGaussian * sizeof(float);
    CHECK(mem >= min_expected);
}

static void test_step_counter_increments() {
    aether::trainer::ApolloMiniConfig cfg{};
    cfg.lr_position = 0.01f;

    aether::trainer::ApolloMiniOptimizer opt;
    opt.init(cfg, 1);

    CHECK(opt.current_step() == 0);

    const std::size_t n_params = aether::trainer::kGradientsPerGaussian;
    std::vector<float> params(n_params, 1.0f);
    std::vector<float> grads(n_params, 0.1f);

    opt.step(params.data(), grads.data(), 1);
    CHECK(opt.current_step() == 1);

    opt.step(params.data(), grads.data(), 1);
    CHECK(opt.current_step() == 2);
}

static void test_reset_clears_state() {
    aether::trainer::ApolloMiniConfig cfg{};
    cfg.lr_position = 0.01f;

    aether::trainer::ApolloMiniOptimizer opt;
    opt.init(cfg, 1);

    const std::size_t n_params = aether::trainer::kGradientsPerGaussian;
    std::vector<float> params(n_params, 1.0f);
    std::vector<float> grads(n_params, 0.1f);

    opt.step(params.data(), grads.data(), 1);
    CHECK(opt.current_step() == 1);

    opt.reset();
    CHECK(opt.current_step() == 0);
}

int main() {
    test_init_valid_config();
    test_quadratic_convergence();
    test_gradient_clipping();
    test_memory_bytes();
    test_step_counter_increments();
    test_reset_clears_state();

    if (g_failed == 0) {
        std::fprintf(stdout, "apollo_mini_optimizer_test: all tests passed\n");
    } else {
        std::fprintf(stderr, "apollo_mini_optimizer_test: %d test(s) failed\n", g_failed);
    }
    return g_failed;
}
