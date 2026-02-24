// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/core/nan_quarantine.h"

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

static void test_detect_nan_clean_buffer() {
    std::vector<float> buf(256, 1.0f);
    CHECK(!aether::core::detect_nan(buf.data(), buf.size()));
}

static void test_detect_nan_with_nan_injected() {
    std::vector<float> buf(256, 1.0f);
    buf[128] = std::numeric_limits<float>::quiet_NaN();
    CHECK(aether::core::detect_nan(buf.data(), buf.size()));
}

static void test_detect_nan_with_inf_injected() {
    std::vector<float> buf(256, 1.0f);
    buf[64] = std::numeric_limits<float>::infinity();
    CHECK(aether::core::detect_nan(buf.data(), buf.size()));

    // Also test negative infinity.
    buf[64] = -std::numeric_limits<float>::infinity();
    CHECK(aether::core::detect_nan(buf.data(), buf.size()));
}

static void test_scan_buffer_counts() {
    std::vector<float> buf(64, 0.5f);
    buf[0] = std::numeric_limits<float>::quiet_NaN();
    buf[1] = std::numeric_limits<float>::quiet_NaN();
    buf[2] = std::numeric_limits<float>::infinity();
    buf[3] = -std::numeric_limits<float>::infinity();
    buf[4] = std::numeric_limits<float>::quiet_NaN();

    aether::core::QuarantineAction result = aether::core::scan_buffer(buf.data(), buf.size());
    CHECK(result.nan_count == 3);
    CHECK(result.inf_count == 2);
    CHECK(result.nan_count + result.inf_count == 5);
    CHECK(result.triggered);
}

static void test_nan_quarantine_triggered() {
    aether::core::NaNQuarantine quarantine;
    CHECK(!quarantine.triggered());

    std::vector<float> clean(64, 1.0f);
    quarantine.check(clean.data(), clean.size(), clean.data(), clean.size());
    CHECK(!quarantine.triggered());

    std::vector<float> bad(64, 1.0f);
    bad[10] = std::numeric_limits<float>::quiet_NaN();
    quarantine.check(bad.data(), bad.size(), clean.data(), clean.size());
    CHECK(quarantine.triggered());
    CHECK(quarantine.quarantine_count() == 1);
}

static void test_scan_buffer_all_clean() {
    std::vector<float> buf(128, 42.0f);
    aether::core::QuarantineAction result = aether::core::scan_buffer(buf.data(), buf.size());
    CHECK(result.nan_count == 0);
    CHECK(result.inf_count == 0);
    CHECK(result.nan_count + result.inf_count == 0);
    CHECK(!result.triggered);
}

static void test_detect_nan_empty_buffer() {
    CHECK(!aether::core::detect_nan(nullptr, 0));
}

int main() {
    test_detect_nan_clean_buffer();
    test_detect_nan_with_nan_injected();
    test_detect_nan_with_inf_injected();
    test_scan_buffer_counts();
    test_nan_quarantine_triggered();
    test_scan_buffer_all_clean();
    test_detect_nan_empty_buffer();

    if (g_failed == 0) {
        std::fprintf(stdout, "nan_quarantine_test: all tests passed\n");
    } else {
        std::fprintf(stderr, "nan_quarantine_test: %d test(s) failed\n", g_failed);
    }
    return g_failed;
}
