// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/gaussian_gradient_buffer.h"

#include <cmath>
#include <cstdio>
#include <cstring>

static int g_failed = 0;

static void check(bool cond, const char* msg, int line) {
    if (!cond) {
        std::fprintf(stderr, "FAIL [line %d]: %s\n", line, msg);
        ++g_failed;
    }
}
#define CHECK(cond) check((cond), #cond, __LINE__)

static void test_init_valid_size() {
    aether::trainer::GaussianGradientBuffer buf;
    const auto status = buf.init(1024);
    CHECK(aether::core::is_ok(status));
    CHECK(buf.max_gaussians() == 1024);
}

static void test_clear_write_buffer() {
    aether::trainer::GaussianGradientBuffer buf;
    buf.init(64);
    buf.clear_write_buffer();

    // Write data via write_slice to ensure data is accessible
    // Then check the buffer is zeroed after clear
    const std::size_t count = buf.buffer_size_bytes() / sizeof(float);
    // Access raw write pointer (non-const)
    float* data = buf.write_data();
    bool all_zero = true;
    for (std::size_t i = 0; i < count; ++i) {
        if (data[i] != 0.0f) { all_zero = false; break; }
    }
    CHECK(all_zero);
}

static void test_swap_switches_buffers() {
    aether::trainer::GaussianGradientBuffer buf;
    buf.init(32);

    const float* before_read = buf.read_data();
    float* before_write = buf.write_data();
    buf.swap();
    const float* after_read = buf.read_data();
    float* after_write = buf.write_data();

    CHECK(before_read == after_write);
    CHECK(before_write == after_read);
}

static void test_write_slice_returns_valid_pointers() {
    aether::trainer::GaussianGradientBuffer buf;
    buf.init(100);

    auto slice0 = buf.write_slice(0);
    auto slice10 = buf.write_slice(10);
    CHECK(slice0.position != nullptr);
    CHECK(slice10.position != nullptr);
    CHECK(slice10.position > slice0.position);
}

static void test_buffer_size_bytes() {
    aether::trainer::GaussianGradientBuffer buf;
    buf.init(256);
    const std::size_t expected = 256 * aether::trainer::kGradientsPerGaussian * sizeof(float);
    CHECK(buf.buffer_size_bytes() == expected);
}

int main() {
    test_init_valid_size();
    test_clear_write_buffer();
    test_swap_switches_buffers();
    test_write_slice_returns_valid_pointers();
    test_buffer_size_bytes();

    if (g_failed == 0) {
        std::fprintf(stdout, "gradient_buffer_test: all tests passed\n");
    } else {
        std::fprintf(stderr, "gradient_buffer_test: %d test(s) failed\n", g_failed);
    }
    return g_failed;
}
