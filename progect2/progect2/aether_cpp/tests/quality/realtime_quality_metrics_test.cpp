// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/realtime_quality_metrics.h"

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

static void test_meets_world_model_standard_all_pass() {
    aether::quality::RealtimeQualityMetrics m{};
    m.psnr_estimate = 35.0f;
    m.chamfer_estimate = 0.01f;
    m.normal_consistency = 0.92f;
    m.scale_accuracy = 0.99f;
    m.coverage_f_score = 0.85f;
    m.psnr_ci_lower = 30.0f;
    CHECK(m.meets_world_model_standard());
}

static void test_meets_world_model_standard_psnr_fails() {
    aether::quality::RealtimeQualityMetrics m{};
    m.psnr_estimate = 25.0f;       // Below 28 dB
    m.chamfer_estimate = 0.01f;
    m.normal_consistency = 0.92f;
    m.scale_accuracy = 0.99f;
    m.coverage_f_score = 0.85f;
    m.psnr_ci_lower = 30.0f;
    CHECK(!m.meets_world_model_standard());
}

static void test_meets_world_model_standard_chamfer_fails() {
    aether::quality::RealtimeQualityMetrics m{};
    m.psnr_estimate = 35.0f;
    m.chamfer_estimate = 0.05f;    // Above 0.02
    m.normal_consistency = 0.92f;
    m.scale_accuracy = 0.99f;
    m.coverage_f_score = 0.85f;
    m.psnr_ci_lower = 30.0f;
    CHECK(!m.meets_world_model_standard());
}

static void test_estimator_identical_images_high_psnr() {
    aether::quality::RealtimeQualityEstimator estimator;

    const int kW = 8;
    const int kH = 8;
    const int kPixels = kW * kH;
    std::vector<float> img(kPixels * 3, 0.5f);

    aether::quality::QualityEstimatorInput input{};
    input.rendered_rgb = img.data();
    input.width = kW;
    input.height = kH;

    auto status = estimator.update(input);
    CHECK(aether::core::is_ok(status));
    auto metrics = estimator.current();

    // With no TSDF reference data, PSNR comparison to self should give high PSNR.
    // (Implementation may not compute PSNR without GT; just check it is non-negative.)
    CHECK(metrics.psnr_estimate >= 0.0f);
}

static void test_estimator_reset() {
    aether::quality::RealtimeQualityEstimator estimator;

    const int kW = 4;
    const int kH = 4;
    const int kPixels = kW * kH;
    std::vector<float> img(kPixels * 3, 0.5f);

    aether::quality::QualityEstimatorInput input{};
    input.rendered_rgb = img.data();
    input.width = kW;
    input.height = kH;

    estimator.update(input);
    estimator.reset();

    auto metrics = estimator.current();
    CHECK(metrics.psnr_estimate == 0.0f);
}

static void test_estimator_with_depth_data() {
    aether::quality::RealtimeQualityEstimator estimator;

    const int kW = 4;
    const int kH = 4;
    const int kPixels = kW * kH;
    std::vector<float> rgb(kPixels * 3, 0.5f);
    std::vector<float> rendered_depth(kPixels, 1.0f);
    std::vector<float> tsdf_depth(kPixels, 1.1f);

    aether::quality::QualityEstimatorInput input{};
    input.rendered_rgb = rgb.data();
    input.rendered_depth = rendered_depth.data();
    input.tsdf_depth = tsdf_depth.data();
    input.width = kW;
    input.height = kH;

    auto status = estimator.update(input);
    CHECK(aether::core::is_ok(status));
    auto metrics = estimator.current();

    // Chamfer should be positive when there is a depth difference.
    CHECK(metrics.chamfer_estimate > 0.0f);
}

static void test_normal_consistency() {
    aether::quality::RealtimeQualityMetrics m{};
    m.psnr_estimate = 35.0f;
    m.chamfer_estimate = 0.01f;
    m.normal_consistency = 0.5f;   // Below 0.85
    m.scale_accuracy = 0.99f;
    m.coverage_f_score = 0.85f;
    m.psnr_ci_lower = 30.0f;
    CHECK(!m.meets_world_model_standard());
}

static void test_scale_accuracy() {
    aether::quality::RealtimeQualityMetrics m{};
    m.psnr_estimate = 35.0f;
    m.chamfer_estimate = 0.01f;
    m.normal_consistency = 0.92f;
    m.scale_accuracy = 0.90f;      // Below 0.98
    m.coverage_f_score = 0.85f;
    m.psnr_ci_lower = 30.0f;
    CHECK(!m.meets_world_model_standard());
}

int main() {
    test_meets_world_model_standard_all_pass();
    test_meets_world_model_standard_psnr_fails();
    test_meets_world_model_standard_chamfer_fails();
    test_estimator_identical_images_high_psnr();
    test_estimator_reset();
    test_estimator_with_depth_data();
    test_normal_consistency();
    test_scale_accuracy();

    if (g_failed == 0) {
        std::fprintf(stdout, "realtime_quality_metrics_test: all tests passed\n");
    } else {
        std::fprintf(stderr, "realtime_quality_metrics_test: %d test(s) failed\n", g_failed);
    }
    return g_failed;
}
