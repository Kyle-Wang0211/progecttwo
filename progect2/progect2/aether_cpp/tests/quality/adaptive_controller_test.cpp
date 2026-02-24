// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/adaptive_quality_controller.h"

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

static aether::render::GPUCaps make_mid_range_caps() {
    aether::render::GPUCaps caps{};
    caps.backend = aether::render::GraphicsBackend::kMetal;
    caps.max_buffer_size = 256 * 1024 * 1024;
    caps.max_texture_size = 2048;
    caps.max_compute_workgroup_size = 256;
    caps.supports_compute = true;
    caps.supports_half_precision = true;
    caps.simd_width = 32;
    return caps;
}

static void test_create_config_for_device() {
    auto caps = make_mid_range_caps();
    auto cfg = aether::quality::create_config_for_device(
        caps, aether::render::GraphicsBackend::kMetal);

    CHECK(cfg.gaussian_budget_max > 0);
    CHECK(cfg.resolution_scale_min > 0.0f);
    CHECK(cfg.resolution_scale_max <= 1.0f);
}

static void test_nominal_conditions() {
    auto caps = make_mid_range_caps();
    auto cfg = aether::quality::create_config_for_device(
        caps, aether::render::GraphicsBackend::kMetal);
    aether::quality::AdaptiveQualityController ctrl(cfg);

    aether::quality::AdaptiveControllerInput input{};
    input.frame_time_ms = 16.0f;        // 60 fps
    input.psnr_estimate = 35.0f;        // Good quality
    input.available_memory_mb = 2048.0f; // Comfortable
    input.thermal_headroom = 0.8f;      // Plenty
    input.battery_temp = 30.0f;
    input.battery_pct = 80.0f;
    input.chamfer_estimate = 0.01f;
    input.target_fps = 60.0f;

    aether::quality::AdaptiveControllerOutput output{};
    auto status = ctrl.update(input, &output);
    CHECK(aether::core::is_ok(status));
    CHECK(output.gaussian_budget > 0);
    CHECK(output.resolution_scale >= 0.5f);
}

static void test_thermal_degradation() {
    auto caps = make_mid_range_caps();
    auto cfg = aether::quality::create_config_for_device(
        caps, aether::render::GraphicsBackend::kMetal);
    aether::quality::AdaptiveQualityController ctrl(cfg);

    aether::quality::AdaptiveControllerInput input{};
    input.frame_time_ms = 20.0f;
    input.psnr_estimate = 32.0f;
    input.available_memory_mb = 1024.0f;
    input.thermal_headroom = 0.10f;  // Critical thermal state
    input.battery_temp = 42.0f;
    input.battery_pct = 50.0f;
    input.chamfer_estimate = 0.01f;
    input.target_fps = 60.0f;

    aether::quality::AdaptiveControllerOutput output{};
    ctrl.update(input, &output);
    CHECK(output.thermal_tier == aether::quality::ThermalTier::kCritical);
}

static void test_pid_response_high_frame_time() {
    auto caps = make_mid_range_caps();
    auto cfg = aether::quality::create_config_for_device(
        caps, aether::render::GraphicsBackend::kMetal);
    aether::quality::AdaptiveQualityController ctrl(cfg);

    std::uint32_t prev_budget = cfg.gaussian_budget_max;
    bool budget_decreased = false;

    // Feed 20 frames with consistently high frame time.
    for (int i = 0; i < 20; ++i) {
        aether::quality::AdaptiveControllerInput input{};
        input.frame_time_ms = 50.0f;     // Very slow
        input.psnr_estimate = 32.0f;
        input.available_memory_mb = 1024.0f;
        input.thermal_headroom = 0.5f;
        input.battery_temp = 35.0f;
        input.battery_pct = 70.0f;
        input.chamfer_estimate = 0.01f;
        input.target_fps = 60.0f;

        aether::quality::AdaptiveControllerOutput output{};
        ctrl.update(input, &output);
        if (output.gaussian_budget < prev_budget) {
            budget_decreased = true;
        }
        prev_budget = output.gaussian_budget;
    }
    CHECK(budget_decreased);
}

static void test_low_end_device_caps() {
    aether::render::GPUCaps low_caps{};
    low_caps.backend = aether::render::GraphicsBackend::kMetal;
    low_caps.max_buffer_size = 64 * 1024 * 1024;
    low_caps.max_texture_size = 1024;
    low_caps.max_compute_workgroup_size = 128;
    low_caps.supports_compute = true;
    low_caps.supports_half_precision = false;

    auto cfg = aether::quality::create_config_for_device(
        low_caps, aether::render::GraphicsBackend::kMetal);
    CHECK(cfg.gaussian_budget_max <= 50000);
}

static void test_reset_clears_state() {
    auto caps = make_mid_range_caps();
    auto cfg = aether::quality::create_config_for_device(
        caps, aether::render::GraphicsBackend::kMetal);
    aether::quality::AdaptiveQualityController ctrl(cfg);

    aether::quality::AdaptiveControllerInput input{};
    input.frame_time_ms = 40.0f;
    input.thermal_headroom = 0.1f;
    input.available_memory_mb = 1024.0f;
    input.battery_temp = 40.0f;
    input.battery_pct = 50.0f;
    input.psnr_estimate = 30.0f;
    input.chamfer_estimate = 0.02f;
    input.target_fps = 60.0f;

    aether::quality::AdaptiveControllerOutput output{};
    ctrl.update(input, &output);

    ctrl.reset();
    CHECK(ctrl.current_thermal_tier() == aether::quality::ThermalTier::kNominal);
}

int main() {
    test_create_config_for_device();
    test_nominal_conditions();
    test_thermal_degradation();
    test_pid_response_high_frame_time();
    test_low_end_device_caps();
    test_reset_clears_state();

    if (g_failed == 0) {
        std::fprintf(stdout, "adaptive_controller_test: all tests passed\n");
    } else {
        std::fprintf(stderr, "adaptive_controller_test: %d test(s) failed\n", g_failed);
    }
    return g_failed;
}
