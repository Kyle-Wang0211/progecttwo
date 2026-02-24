// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/gaussian_loss.h"

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

static bool near(float a, float b, float eps = 1e-4f) {
    return std::fabs(a - b) <= eps;
}

// Image dimensions must exceed 2 * SSIM_RADIUS (=5) = 10 per axis,
// otherwise the SSIM computation falls back to 0.0 for tiny images.
// Using 16x16 ensures the SSIM sliding window has valid centers.
static constexpr int kW = 16;
static constexpr int kH = 16;
static constexpr int kPixels = kW * kH;

static void test_rgb_loss_nonzero_when_different() {
    std::vector<float> rendered(kPixels * 3, 0.0f);
    std::vector<float> gt(kPixels * 3, 1.0f);

    aether::trainer::LossConfig cfg{};
    aether::trainer::GaussianLoss loss_fn(cfg);

    aether::trainer::LossInput input{};
    std::memset(&input, 0, sizeof(input));
    input.rendered_rgb = rendered.data();
    input.gt_rgb = gt.data();
    input.width = kW;
    input.height = kH;
    input.current_step = 0;
    input.total_steps = 1000;
    input.num_gaussians = 0;

    aether::trainer::LossResult result{};
    loss_fn.compute(input, &result);
    CHECK(result.loss_rgb > 0.0f);
}

static void test_rgb_loss_zero_when_identical() {
    std::vector<float> img(kPixels * 3, 0.5f);

    aether::trainer::LossConfig cfg{};
    aether::trainer::GaussianLoss loss_fn(cfg);

    aether::trainer::LossInput input{};
    std::memset(&input, 0, sizeof(input));
    input.rendered_rgb = img.data();
    input.gt_rgb = img.data();
    input.width = kW;
    input.height = kH;
    input.current_step = 0;
    input.total_steps = 1000;
    input.num_gaussians = 0;

    aether::trainer::LossResult result{};
    loss_fn.compute(input, &result);
    // L_rgb = (1-lambda)*L1 + lambda*(1-SSIM).  For identical images L1=0
    // and SSIM~=1, but floating-point rounding in the SSIM window can
    // introduce small residuals.
    CHECK(near(result.loss_rgb, 0.0f, 1e-3f));
}

static void test_depth_loss_with_difference() {
    std::vector<float> rgb(kPixels * 3, 0.5f);
    std::vector<float> rendered_depth(kPixels, 1.0f);
    std::vector<float> gt_depth(kPixels, 2.0f);
    std::vector<float> confidence(kPixels, 0.9f);

    aether::trainer::LossConfig cfg{};
    aether::trainer::GaussianLoss loss_fn(cfg);

    aether::trainer::LossInput input{};
    std::memset(&input, 0, sizeof(input));
    input.rendered_rgb = rgb.data();
    input.gt_rgb = rgb.data();
    input.rendered_depth = rendered_depth.data();
    input.tsdf_depth = gt_depth.data();
    input.depth_confidence = confidence.data();
    input.width = kW;
    input.height = kH;
    input.current_step = 0;
    input.total_steps = 1000;
    input.num_gaussians = 0;

    aether::trainer::LossResult result{};
    loss_fn.compute(input, &result);
    CHECK(result.loss_depth > 0.0f);
}

static void test_normal_loss_perpendicular() {
    // Rendered normals pointing up, GT normals pointing right = perpendicular.
    std::vector<float> rendered_normals(kPixels * 3, 0.0f);
    std::vector<float> gt_normals(kPixels * 3, 0.0f);
    for (int i = 0; i < kPixels; ++i) {
        rendered_normals[i * 3 + 1] = 1.0f;  // (0, 1, 0)
        gt_normals[i * 3 + 0] = 1.0f;        // (1, 0, 0)
    }

    std::vector<float> rgb(kPixels * 3, 0.5f);

    aether::trainer::LossConfig cfg{};
    aether::trainer::GaussianLoss loss_fn(cfg);

    aether::trainer::LossInput input{};
    std::memset(&input, 0, sizeof(input));
    input.rendered_rgb = rgb.data();
    input.gt_rgb = rgb.data();
    input.rendered_normal = rendered_normals.data();
    input.tsdf_normal = gt_normals.data();
    input.width = kW;
    input.height = kH;
    input.current_step = 0;
    input.total_steps = 1000;
    input.num_gaussians = 0;

    aether::trainer::LossResult result{};
    loss_fn.compute(input, &result);
    CHECK(result.loss_normal > 0.5f);
}

static void test_normal_loss_parallel() {
    // Same normals = low loss.
    std::vector<float> normals(kPixels * 3, 0.0f);
    for (int i = 0; i < kPixels; ++i) {
        normals[i * 3 + 1] = 1.0f;  // (0, 1, 0)
    }

    std::vector<float> rgb(kPixels * 3, 0.5f);

    aether::trainer::LossConfig cfg{};
    aether::trainer::GaussianLoss loss_fn(cfg);

    aether::trainer::LossInput input{};
    std::memset(&input, 0, sizeof(input));
    input.rendered_rgb = rgb.data();
    input.gt_rgb = rgb.data();
    input.rendered_normal = normals.data();
    input.tsdf_normal = normals.data();
    input.width = kW;
    input.height = kH;
    input.current_step = 0;
    input.total_steps = 1000;
    input.num_gaussians = 0;

    aether::trainer::LossResult result{};
    loss_fn.compute(input, &result);
    CHECK(result.loss_normal < 0.1f);
}

static void test_effective_beta_decays() {
    std::vector<float> rgb(kPixels * 3, 0.5f);
    std::vector<float> gt(kPixels * 3, 0.7f);

    aether::trainer::LossConfig cfg{};
    aether::trainer::GaussianLoss loss_fn(cfg);

    aether::trainer::LossInput input_early{};
    std::memset(&input_early, 0, sizeof(input_early));
    input_early.rendered_rgb = rgb.data();
    input_early.gt_rgb = gt.data();
    input_early.width = kW;
    input_early.height = kH;
    input_early.current_step = 0;
    input_early.total_steps = 1000;
    input_early.num_gaussians = 0;

    aether::trainer::LossInput input_late = input_early;
    input_late.current_step = 900;

    aether::trainer::LossResult result_early{};
    loss_fn.compute(input_early, &result_early);

    aether::trainer::LossResult result_late{};
    loss_fn.compute(input_late, &result_late);

    // The effective depth weight (beta) should decay over time.
    CHECK(result_early.effective_beta >= result_late.effective_beta);
}

static void test_total_loss_non_negative() {
    std::vector<float> rendered(kPixels * 3, 0.3f);
    std::vector<float> gt(kPixels * 3, 0.7f);

    aether::trainer::LossConfig cfg{};
    aether::trainer::GaussianLoss loss_fn(cfg);

    aether::trainer::LossInput input{};
    std::memset(&input, 0, sizeof(input));
    input.rendered_rgb = rendered.data();
    input.gt_rgb = gt.data();
    input.width = kW;
    input.height = kH;
    input.current_step = 100;
    input.total_steps = 1000;
    input.num_gaussians = 0;

    aether::trainer::LossResult result{};
    loss_fn.compute(input, &result);

    CHECK(result.total_loss >= 0.0f);
    CHECK(result.loss_rgb >= 0.0f);
}

int main() {
    test_rgb_loss_nonzero_when_different();
    test_rgb_loss_zero_when_identical();
    test_depth_loss_with_difference();
    test_normal_loss_perpendicular();
    test_normal_loss_parallel();
    test_effective_beta_decays();
    test_total_loss_non_negative();

    if (g_failed == 0) {
        std::fprintf(stdout, "gaussian_loss_test: all tests passed\n");
    } else {
        std::fprintf(stderr, "gaussian_loss_test: %d test(s) failed\n", g_failed);
    }
    return g_failed;
}
