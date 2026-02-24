// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/render/gaussian_forward_pass.h"
#include "aether/render/gpu_device.h"
#include "aether/innovation/packed_gaussian.h"

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

static void test_init_succeeds() {
    aether::render::NullGPUDevice device;
    aether::render::GaussianForwardPass pass;

    aether::render::ForwardPassConfig cfg{};
    cfg.render_width = 1920;
    cfg.render_height = 1080;
    cfg.max_gaussians = 50000;

    auto status = pass.init(&device, cfg);
    CHECK(aether::core::is_ok(status));
}

static void test_upload_gaussians() {
    aether::render::NullGPUDevice device;
    aether::render::GaussianForwardPass pass;

    aether::render::ForwardPassConfig cfg{};
    cfg.render_width = 512;
    cfg.render_height = 512;
    cfg.max_gaussians = 1000;
    pass.init(&device, cfg);

    // Create mock packed gaussian data.
    std::vector<aether::innovation::PackedGaussian> data(1000);
    std::memset(data.data(), 0, data.size() * sizeof(aether::innovation::PackedGaussian));
    auto status = pass.upload_gaussians(data.data(), data.size());
    CHECK(aether::core::is_ok(status));
}

static void test_render_targets_valid() {
    aether::render::NullGPUDevice device;
    aether::render::GaussianForwardPass pass;

    aether::render::ForwardPassConfig cfg{};
    cfg.render_width = 640;
    cfg.render_height = 480;
    cfg.max_gaussians = 200;
    pass.init(&device, cfg);

    auto color_handle = pass.color_target();
    auto depth_handle = pass.depth_target();
    CHECK(color_handle.valid());
    CHECK(depth_handle.valid());
}

static void test_destroy_cleans_up() {
    aether::render::NullGPUDevice device;
    aether::render::GaussianForwardPass pass;

    aether::render::ForwardPassConfig cfg{};
    cfg.render_width = 320;
    cfg.render_height = 240;
    cfg.max_gaussians = 100;
    pass.init(&device, cfg);

    pass.destroy();
    // After destroy, re-init should succeed.
    auto status = pass.init(&device, cfg);
    CHECK(aether::core::is_ok(status));
    pass.destroy();
}

int main() {
    test_init_succeeds();
    test_upload_gaussians();
    test_render_targets_valid();
    test_destroy_cleans_up();

    if (g_failed == 0) {
        std::fprintf(stdout, "gaussian_forward_pass_test: all tests passed\n");
    } else {
        std::fprintf(stderr, "gaussian_forward_pass_test: %d test(s) failed\n", g_failed);
    }
    return g_failed;
}
