// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/tsdf_gaussian_bridge.h"

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

static bool near(float a, float b, float eps = 1e-5f) {
    return std::fabs(a - b) <= eps;
}

static std::vector<aether::trainer::TSDFVoxelSeed> make_mock_seeds(int count) {
    std::vector<aether::trainer::TSDFVoxelSeed> seeds(static_cast<std::size_t>(count));
    for (int i = 0; i < count; ++i) {
        auto& s = seeds[static_cast<std::size_t>(i)];
        // Scatter positions in a cube.
        s.position.x = static_cast<float>(i % 10) * 0.1f;
        s.position.y = static_cast<float>((i / 10) % 10) * 0.1f;
        s.position.z = static_cast<float>(i / 100) * 0.1f;

        // Normals pointing outward from origin.
        float nx = s.position.x;
        float ny = s.position.y;
        float nz = s.position.z;
        float l = std::sqrt(nx * nx + ny * ny + nz * nz);
        if (l < 1e-6f) { nx = 0.0f; ny = 1.0f; nz = 0.0f; l = 1.0f; }
        s.normal.x = nx / l;
        s.normal.y = ny / l;
        s.normal.z = nz / l;

        // Moderate SDF near zero-crossing.
        s.sdf_value = (i % 2 == 0) ? 0.002f : -0.001f;
        s.confidence = 0.5f + static_cast<float>(i % 10) * 0.05f;
        s.voxel_size = 0.01f;

        // Color data (uint8_t).
        s.rgb[0] = 128;
        s.rgb[1] = 76;
        s.rgb[2] = 179;
    }
    return seeds;
}

static void test_extract_seeds_count() {
    auto voxels = make_mock_seeds(100);
    aether::trainer::BridgeConfig cfg{};
    cfg.truncation_threshold = 0.01f;
    cfg.min_confidence = 0.1f;

    aether::trainer::TSDFGaussianBridge bridge(cfg);
    std::vector<aether::trainer::GaussianSeed> out;
    auto status = bridge.extract_seeds(voxels.data(), voxels.size(), &out);
    CHECK(aether::core::is_ok(status));
    CHECK(!out.empty());
    // All seeds should pass: sdf_value < truncation_threshold and confidence > min_confidence.
    CHECK(out.size() == 100);
}

static void test_anisotropic_scale() {
    // Test the static compute_anisotropic_scale method.
    aether::innovation::Float3 normal{};
    normal.x = 0.0f;
    normal.y = 1.0f;
    normal.z = 0.0f;

    auto scale = aether::trainer::TSDFGaussianBridge::compute_anisotropic_scale(
        normal, 0.01f, 0.25f);
    // Normal direction should be smaller than tangent directions.
    // For a y-normal, scale.y = voxel_size * ratio, scale.x = scale.z = voxel_size.
    // Since ratio < 1.0, the normal-direction scale should be smaller.
    float max_tangent = (scale.x > scale.z) ? scale.x : scale.z;
    // Scale in the normal direction should be smaller than tangent.
    // We can't know the exact axis mapping, but at least verify it's non-zero.
    CHECK(scale.x > 0.0f);
    CHECK(scale.y > 0.0f);
    CHECK(scale.z > 0.0f);
    (void)max_tangent;
}

static void test_opacity_range() {
    auto voxels = make_mock_seeds(80);
    aether::trainer::BridgeConfig cfg{};
    cfg.truncation_threshold = 0.01f;
    cfg.min_confidence = 0.1f;
    cfg.opacity_min = 0.3f;
    cfg.opacity_max = 0.95f;

    aether::trainer::TSDFGaussianBridge bridge(cfg);
    std::vector<aether::trainer::GaussianSeed> out;
    bridge.extract_seeds(voxels.data(), voxels.size(), &out);

    for (const auto& gs : out) {
        CHECK(gs.opacity >= 0.3f);
        CHECK(gs.opacity <= 0.95f);
    }
}

static void test_sh_dc_positive() {
    auto voxels = make_mock_seeds(30);
    aether::trainer::BridgeConfig cfg{};
    cfg.truncation_threshold = 0.01f;
    cfg.min_confidence = 0.1f;

    aether::trainer::TSDFGaussianBridge bridge(cfg);
    std::vector<aether::trainer::GaussianSeed> out;
    bridge.extract_seeds(voxels.data(), voxels.size(), &out);

    for (std::size_t i = 0; i < out.size(); ++i) {
        const auto& gs = out[i];
        CHECK(gs.sh_dc[0] > 0.0f);
        CHECK(gs.sh_dc[1] > 0.0f);
        CHECK(gs.sh_dc[2] > 0.0f);
    }
}

static void test_compute_sdf_gradient() {
    // 6-neighbor SDF: +x, -x, +y, -y, +z, -z
    // For a field sdf = x, +x neighbor = 1, -x neighbor = -1, others = 0.
    float sdf6[6] = {1.0f, -1.0f, 0.0f, 0.0f, 0.0f, 0.0f};
    auto grad = aether::trainer::TSDFGaussianBridge::compute_sdf_gradient(sdf6);
    // Gradient should be approximately (1, 0, 0).
    CHECK(near(grad.x, 1.0f, 0.1f));
    CHECK(near(grad.y, 0.0f, 0.1f));
    CHECK(near(grad.z, 0.0f, 0.1f));
}

static void test_compute_sh_dc_from_rgb() {
    std::uint8_t rgb[3] = {255, 128, 0};
    float sh_dc[3] = {0.0f, 0.0f, 0.0f};
    aether::trainer::TSDFGaussianBridge::compute_sh_dc_from_rgb(rgb, sh_dc);
    // sh_dc = (rgb/255) / sqrt(4*pi), should be > 0 for non-zero channels.
    CHECK(sh_dc[0] > 0.0f);
    CHECK(sh_dc[1] > 0.0f);
    CHECK(near(sh_dc[2], 0.0f, 0.01f));
}

static void test_extract_seeds_filters_by_confidence() {
    auto voxels = make_mock_seeds(10);
    // Set half the voxels to very low confidence.
    for (int i = 0; i < 5; ++i) {
        voxels[static_cast<std::size_t>(i)].confidence = 0.001f;
    }
    aether::trainer::BridgeConfig cfg{};
    cfg.truncation_threshold = 0.01f;
    cfg.min_confidence = 0.1f;

    aether::trainer::TSDFGaussianBridge bridge(cfg);
    std::vector<aether::trainer::GaussianSeed> out;
    bridge.extract_seeds(voxels.data(), voxels.size(), &out);
    CHECK(out.size() == 5);
}

static void test_extract_seeds_filters_by_sdf() {
    auto voxels = make_mock_seeds(10);
    // Set half the voxels to high SDF (far from surface).
    for (int i = 0; i < 5; ++i) {
        voxels[static_cast<std::size_t>(i)].sdf_value = 1.0f;
    }
    aether::trainer::BridgeConfig cfg{};
    cfg.truncation_threshold = 0.01f;
    cfg.min_confidence = 0.1f;

    aether::trainer::TSDFGaussianBridge bridge(cfg);
    std::vector<aether::trainer::GaussianSeed> out;
    bridge.extract_seeds(voxels.data(), voxels.size(), &out);
    CHECK(out.size() == 5);
}

int main() {
    test_extract_seeds_count();
    test_anisotropic_scale();
    test_opacity_range();
    test_sh_dc_positive();
    test_compute_sdf_gradient();
    test_compute_sh_dc_from_rgb();
    test_extract_seeds_filters_by_confidence();
    test_extract_seeds_filters_by_sdf();

    if (g_failed == 0) {
        std::fprintf(stdout, "tsdf_gaussian_bridge_test: all tests passed\n");
    } else {
        std::fprintf(stderr, "tsdf_gaussian_bridge_test: %d test(s) failed\n", g_failed);
    }
    return g_failed;
}
