// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/evidence_gated_trainer.h"

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

static bool near(float a, float b, float eps = 1e-4f) {
    return std::fabs(a - b) <= eps;
}

static void test_s5_original_no_train() {
    aether::trainer::EGTConfig cfg{};
    auto decision = aether::trainer::compute_egt_strategy(
        aether::evidence::ColorState::kOriginal,  // S5
        0.5f,   // choquet_score
        0.1f,   // uncertainty
        30.0f,  // psnr_local
        cfg);
    CHECK(!decision.should_train);
    CHECK(near(decision.lr_scale, 0.0f));
}

static void test_s4_white_low_lr() {
    aether::trainer::EGTConfig cfg{};
    auto decision = aether::trainer::compute_egt_strategy(
        aether::evidence::ColorState::kWhite,  // S4
        0.5f,   // choquet_score
        0.1f,   // uncertainty
        30.0f,  // psnr_local
        cfg);
    CHECK(decision.should_train);
    CHECK(near(decision.lr_scale, cfg.s4_lr_scale));
}

static void test_s3_light_gray_normal_lr() {
    aether::trainer::EGTConfig cfg{};
    auto decision = aether::trainer::compute_egt_strategy(
        aether::evidence::ColorState::kLightGray,  // S3
        0.5f,   // choquet_score
        0.1f,   // uncertainty
        30.0f,  // psnr_local
        cfg);
    CHECK(decision.should_train);
    CHECK(near(decision.lr_scale, cfg.s3_lr_scale));
}

static void test_s0_black_boosted() {
    aether::trainer::EGTConfig cfg{};
    auto decision = aether::trainer::compute_egt_strategy(
        aether::evidence::ColorState::kBlack,  // S0
        0.5f,   // choquet_score
        0.1f,   // uncertainty
        30.0f,  // psnr_local
        cfg);
    CHECK(decision.should_train);
    CHECK(near(decision.lr_scale, cfg.s012_lr_scale));
    CHECK(near(decision.iter_multiplier, cfg.s012_iter_multiplier));
}

static void test_s1_dark_gray() {
    aether::trainer::EGTConfig cfg{};
    auto decision = aether::trainer::compute_egt_strategy(
        aether::evidence::ColorState::kDarkGray,  // S1/S2
        0.5f,   // choquet_score
        0.1f,   // uncertainty
        30.0f,  // psnr_local
        cfg);
    CHECK(decision.should_train);
    CHECK(decision.lr_scale >= 1.0f);
}

static void test_high_choquet_reduces_lr() {
    aether::trainer::EGTConfig cfg{};
    auto decision = aether::trainer::compute_egt_strategy(
        aether::evidence::ColorState::kLightGray,  // S3
        0.95f,  // High Choquet = already well-certified
        0.1f,   // uncertainty
        30.0f,  // psnr_local
        cfg);
    CHECK(decision.should_train);
    CHECK(decision.lr_scale < cfg.s3_lr_scale);
}

static void test_s5_no_densify() {
    aether::trainer::EGTConfig cfg{};
    auto decision = aether::trainer::compute_egt_strategy(
        aether::evidence::ColorState::kOriginal,  // S5
        0.9f,   // choquet_score
        0.1f,   // uncertainty
        35.0f,  // psnr_local
        cfg);
    CHECK(!decision.allow_densify);
}

static void test_s0_prioritize_densify() {
    aether::trainer::EGTConfig cfg{};
    auto decision = aether::trainer::compute_egt_strategy(
        aether::evidence::ColorState::kBlack,  // S0
        0.3f,   // choquet_score
        0.5f,   // uncertainty
        25.0f,  // psnr_local
        cfg);
    CHECK(decision.prioritize_densify);
}

int main() {
    test_s5_original_no_train();
    test_s4_white_low_lr();
    test_s3_light_gray_normal_lr();
    test_s0_black_boosted();
    test_s1_dark_gray();
    test_high_choquet_reduces_lr();
    test_s5_no_densify();
    test_s0_prioritize_densify();

    if (g_failed == 0) {
        std::fprintf(stdout, "evidence_gated_trainer_test: all tests passed\n");
    } else {
        std::fprintf(stderr, "evidence_gated_trainer_test: %d test(s) failed\n", g_failed);
    }
    return g_failed;
}
