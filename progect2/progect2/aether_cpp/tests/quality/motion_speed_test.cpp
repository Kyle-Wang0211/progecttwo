// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/motion_speed.h"

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

static bool near(double a, double b, double eps = 1e-9) {
    return std::fabs(a - b) <= eps;
}

static void test_zero_motion() {
    const double p0[3] = {0.0, 0.0, 0.0};
    const double p1[3] = {0.0, 0.0, 0.0};
    const double speed = aether::quality::camera_translation_speed(p1, p0, 1.0, 0.0, 1.0 / 240.0);
    CHECK(near(speed, 0.0));
}

static void test_unit_motion() {
    const double p0[3] = {0.0, 0.0, 0.0};
    const double p1[3] = {1.0, 0.0, 0.0};
    const double speed = aether::quality::camera_translation_speed(p1, p0, 1.0, 0.0, 1.0 / 240.0);
    CHECK(near(speed, 1.0));
}

static void test_dt_floor_applied() {
    const double p0[3] = {0.0, 0.0, 0.0};
    const double p1[3] = {0.24, 0.0, 0.0};
    const double speed = aether::quality::camera_translation_speed(p1, p0, 1.0, 1.0, 1.0 / 240.0);
    CHECK(near(speed, 57.6));
}

static void test_invalid_inputs_fail_closed() {
    const double p0[3] = {0.0, 0.0, 0.0};
    const double p1[3] = {NAN, 0.0, 0.0};
    CHECK(near(aether::quality::camera_translation_speed(p1, p0, 1.0, 0.0, 1.0 / 240.0), 0.0));
    CHECK(near(aether::quality::camera_translation_speed(nullptr, p0, 1.0, 0.0, 1.0 / 240.0), 0.0));
}

int main() {
    test_zero_motion();
    test_unit_motion();
    test_dt_floor_applied();
    test_invalid_inputs_fail_closed();

    if (g_failed == 0) {
        std::fprintf(stdout, "motion_speed_test: all tests passed\n");
    }
    return g_failed;
}
