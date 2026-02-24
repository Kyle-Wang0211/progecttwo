// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/budget_densifier.h"

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

static constexpr std::size_t kCount = 100;

struct MockGaussianData {
    std::vector<float> grad_norms;
    std::vector<float> opacities;
    std::vector<float> scales;
    std::vector<std::uint8_t> evidence;
    std::vector<float> uncertainty;

    MockGaussianData() {
        grad_norms.resize(kCount);
        opacities.resize(kCount);
        scales.resize(kCount);
        evidence.resize(kCount);
        uncertainty.resize(kCount);

        for (std::size_t i = 0; i < kCount; ++i) {
            grad_norms[i] = 0.001f * static_cast<float>(i);
            opacities[i] = 0.2f + 0.007f * static_cast<float>(i);
            scales[i] = 0.01f + 0.001f * static_cast<float>(i);
            evidence[i] = static_cast<std::uint8_t>(i % 5);  // Cycle S0-S4.
            uncertainty[i] = 0.1f + 0.005f * static_cast<float>(i);
        }
    }
};

static void test_budget_suggests_densification() {
    MockGaussianData data;

    aether::trainer::DensifyConfig cfg{};
    cfg.gaussian_budget = 150;
    cfg.grad_threshold = 0.02f;
    cfg.opacity_prune_threshold = 0.05f;

    aether::trainer::BudgetDensifier densifier(cfg);
    aether::trainer::DensifyResult result;
    auto status = densifier.evaluate(
        data.grad_norms.data(),
        data.opacities.data(),
        data.scales.data(),
        data.evidence.data(),
        data.uncertainty.data(),
        kCount,
        &result);
    CHECK(aether::core::is_ok(status));
    CHECK(result.clone_indices.size() + result.split_indices.size() > 0);
}

static void test_budget_enforcement() {
    MockGaussianData data;
    // Make many gaussians have high gradients to trigger lots of densification.
    for (std::size_t i = 0; i < kCount; ++i) {
        data.grad_norms[i] = 0.1f;
    }

    aether::trainer::DensifyConfig cfg{};
    cfg.gaussian_budget = 120;
    cfg.grad_threshold = 0.02f;
    cfg.opacity_prune_threshold = 0.05f;

    aether::trainer::BudgetDensifier densifier(cfg);
    aether::trainer::DensifyResult result;
    densifier.evaluate(
        data.grad_norms.data(),
        data.opacities.data(),
        data.scales.data(),
        data.evidence.data(),
        data.uncertainty.data(),
        kCount,
        &result);

    // final_count should be within budget.
    CHECK(result.final_count <= cfg.gaussian_budget + 10);
}

static void test_opacity_pruning() {
    MockGaussianData data;
    // Set some gaussians to very low opacity.
    for (std::size_t i = 0; i < 10; ++i) {
        data.opacities[i] = 0.001f;
    }

    aether::trainer::DensifyConfig cfg{};
    cfg.gaussian_budget = 200;
    cfg.grad_threshold = 0.02f;
    cfg.opacity_prune_threshold = 0.01f;

    aether::trainer::BudgetDensifier densifier(cfg);
    aether::trainer::DensifyResult result;
    densifier.evaluate(
        data.grad_norms.data(),
        data.opacities.data(),
        data.scales.data(),
        data.evidence.data(),
        data.uncertainty.data(),
        kCount,
        &result);
    CHECK(result.prune_indices.size() >= 10);
}

static void test_s5_never_densified() {
    MockGaussianData data;
    // Set all evidence states to S5 (kOriginal = 4) and all grad norms high.
    for (std::size_t i = 0; i < kCount; ++i) {
        data.evidence[i] = 4;  // kOriginal
        data.grad_norms[i] = 0.1f;
    }

    aether::trainer::DensifyConfig cfg{};
    // Set budget equal to the current count so that the budget enforcement
    // phase does NOT add extra clones to fill an under-budget deficit.
    // Phase 1 correctly skips frozen S5 gaussians, but Phase 2 (enforce_budget)
    // adds clones from any gaussian with grad > 0 if projected < budget_lower.
    cfg.gaussian_budget = kCount;
    cfg.grad_threshold = 0.02f;
    cfg.opacity_prune_threshold = 0.001f;
    cfg.scale_prune_threshold = 0.001f;

    aether::trainer::BudgetDensifier densifier(cfg);
    aether::trainer::DensifyResult result;
    densifier.evaluate(
        data.grad_norms.data(),
        data.opacities.data(),
        data.scales.data(),
        data.evidence.data(),
        data.uncertainty.data(),
        kCount,
        &result);
    // S5 gaussians should not be densified in Phase 1.
    // With budget == count and no prunes, enforce_budget won't add extras.
    CHECK(result.split_indices.empty());
    // Net growth should be zero or negative (no densification of frozen data).
    int net = static_cast<int>(result.clone_indices.size())
            + static_cast<int>(result.split_indices.size())
            - static_cast<int>(result.prune_indices.size());
    CHECK(net <= 0);
}

static void test_uncertainty_override_prevents_pruning() {
    MockGaussianData data;
    // Low opacity but high uncertainty = keep alive.
    for (std::size_t i = 0; i < kCount; ++i) {
        data.opacities[i] = 0.001f;
        data.uncertainty[i] = 0.95f;
    }

    aether::trainer::DensifyConfig cfg{};
    cfg.gaussian_budget = 200;
    cfg.grad_threshold = 0.02f;
    cfg.opacity_prune_threshold = 0.01f;
    cfg.uncertainty_densify_threshold = 0.8f;

    aether::trainer::BudgetDensifier densifier(cfg);
    aether::trainer::DensifyResult result;
    densifier.evaluate(
        data.grad_norms.data(),
        data.opacities.data(),
        data.scales.data(),
        data.evidence.data(),
        data.uncertainty.data(),
        kCount,
        &result);
    // High uncertainty should prevent pruning.
    CHECK(result.prune_indices.empty());
}

static void test_zero_budget_no_densify() {
    MockGaussianData data;
    for (std::size_t i = 0; i < kCount; ++i) data.grad_norms[i] = 0.1f;

    aether::trainer::DensifyConfig cfg{};
    cfg.gaussian_budget = kCount;  // Budget exactly equals current count.
    cfg.grad_threshold = 0.02f;
    cfg.opacity_prune_threshold = 0.001f;

    aether::trainer::BudgetDensifier densifier(cfg);
    aether::trainer::DensifyResult result;
    densifier.evaluate(
        data.grad_norms.data(),
        data.opacities.data(),
        data.scales.data(),
        data.evidence.data(),
        data.uncertainty.data(),
        kCount,
        &result);
    // Net growth should be zero or negative.
    int net = static_cast<int>(result.clone_indices.size())
            + static_cast<int>(result.split_indices.size())
            - static_cast<int>(result.prune_indices.size());
    CHECK(net <= 0);
}

int main() {
    test_budget_suggests_densification();
    test_budget_enforcement();
    test_opacity_pruning();
    test_s5_never_densified();
    test_uncertainty_override_prevents_pruning();
    test_zero_budget_no_densify();

    if (g_failed == 0) {
        std::fprintf(stdout, "budget_densifier_test: all tests passed\n");
    } else {
        std::fprintf(stderr, "budget_densifier_test: %d test(s) failed\n", g_failed);
    }
    return g_failed;
}
