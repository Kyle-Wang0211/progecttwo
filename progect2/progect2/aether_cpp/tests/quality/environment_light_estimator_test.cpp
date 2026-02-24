// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/environment_light_estimator.h"

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

static bool near(float a, float b, float eps = 1e-5f) {
    return std::fabs(a - b) <= eps;
}

static void test_default_fallback_state() {
    aether::quality::EnvironmentLightEstimator estimator;
    const auto& state = estimator.state();
    CHECK(state.tier == 2);
    CHECK(near(state.direction[0], 0.0f));
    CHECK(near(state.direction[1], 1.0f));
    CHECK(near(state.direction[2], 0.0f));
    CHECK(near(state.intensity, 1.0f));
    for (int i = 0; i < 27; ++i) {
        CHECK(std::isfinite(state.sh_coeffs_rgb[i]));
    }
}

static void test_initial_observation_bootstrap() {
    aether::quality::EnvironmentLightEstimator estimator;
    aether::quality::EnvironmentLightObservation obs{};
    obs.source_tier = 0;
    obs.has_direction = 1;
    obs.direction[0] = 0.6f;
    obs.direction[1] = 0.8f;
    obs.direction[2] = 0.0f;
    obs.intensity = 2.2f;

    const auto& state = estimator.step(&obs, 10.0);
    CHECK(state.tier == 0);
    CHECK(state.intensity > 2.1f && state.intensity < 2.3f);
    CHECK(std::fabs(state.direction[0]) > 0.59f);
    CHECK(std::fabs(state.direction[1]) > 0.79f);
}

static void test_missing_samples_decay_without_jump() {
    aether::quality::EnvironmentLightConfig cfg;
    cfg.max_missing_hold_s = 0.2f;
    aether::quality::EnvironmentLightEstimator estimator(cfg);
    aether::quality::EnvironmentLightObservation obs{};
    obs.source_tier = 0;
    obs.intensity = 3.0f;
    const float s0_intensity = estimator.step(&obs, 0.0).intensity;
    CHECK(s0_intensity > 2.9f);

    const float s1_intensity = estimator.step(nullptr, 1.0 / 60.0).intensity;
    CHECK(s1_intensity < s0_intensity);
    CHECK(s1_intensity > 1.0f);

    const auto& s2 = estimator.step(nullptr, 1.0);
    CHECK(s2.tier == 2);
    CHECK(s2.intensity < s1_intensity);
}

static void test_invalid_direction_falls_back() {
    aether::quality::EnvironmentLightEstimator estimator;
    aether::quality::EnvironmentLightObservation obs{};
    obs.source_tier = 1;
    obs.has_direction = 1;
    obs.direction[0] = NAN;
    obs.direction[1] = NAN;
    obs.direction[2] = NAN;
    obs.intensity = 1.5f;
    const auto& s = estimator.step(&obs, 5.0);
    CHECK(near(s.direction[0], 0.0f));
    CHECK(s.direction[1] > 0.99f);
    CHECK(near(s.direction[2], 0.0f));
}

static void test_sh_input_path() {
    aether::quality::EnvironmentLightEstimator estimator;
    aether::quality::EnvironmentLightObservation obs{};
    obs.source_tier = 0;
    obs.has_sh = 1;
    obs.intensity = 0.8f;
    for (int i = 0; i < 27; ++i) {
        obs.sh_coeffs_rgb[i] = 0.001f * static_cast<float>(i + 1);
    }

    const auto& state = estimator.step(&obs, 0.0);
    CHECK(near(state.sh_coeffs_rgb[0], obs.sh_coeffs_rgb[0]));
    CHECK(near(state.sh_coeffs_rgb[26], obs.sh_coeffs_rgb[26]));
}

int main() {
    test_default_fallback_state();
    test_initial_observation_bootstrap();
    test_missing_samples_decay_without_jump();
    test_invalid_direction_falls_back();
    test_sh_input_path();

    if (g_failed == 0) {
        std::fprintf(stdout, "environment_light_estimator_test: all tests passed\n");
    }
    return g_failed;
}
