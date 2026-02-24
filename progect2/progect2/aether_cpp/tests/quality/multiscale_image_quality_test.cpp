// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/multiscale_image_quality.h"

#include <cassert>
#include <cmath>
#include <cstdio>
#include <vector>

using namespace aether::quality;

namespace {

void test_uniform_image() {
    const int w = 64, h = 64;
    std::vector<std::uint8_t> img(w * h, 128);
    MultiscaleConfig cfg;
    MultiscaleImageResult result;
    int rc = multiscale_image_quality(img.data(), w, h, w, cfg, &result);
    assert(rc == 0);
    assert(result.per_level_energy[0] < 1.0);
    assert(result.noise_estimate < 1.0);
    assert(result.levels_computed >= 2);
    (void)rc;
    std::printf("  PASS: uniform_image (energy=%.2f, noise=%.2f)\n",
                result.per_level_energy[0], result.noise_estimate);
}

void test_high_frequency_image() {
    const int w = 64, h = 64;
    std::vector<std::uint8_t> img(w * h);
    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w; ++x) {
            img[y * w + x] = ((x + y) % 2) ? 255 : 0;  // Checkerboard
        }
    }
    MultiscaleConfig cfg;
    MultiscaleImageResult result;
    int rc = multiscale_image_quality(img.data(), w, h, w, cfg, &result);
    assert(rc == 0);
    assert(result.per_level_energy[0] > 100.0);
    assert(result.sharpness_profile > 1.0);
    (void)rc;
    std::printf("  PASS: high_frequency_image (energy=%.2f, profile=%.2f)\n",
                result.per_level_energy[0], result.sharpness_profile);
}

void test_quality_level_skip() {
    const int w = 32, h = 32;
    std::vector<std::uint8_t> img(w * h, 128);
    MultiscaleConfig cfg;
    cfg.quality_level = 2;
    MultiscaleImageResult result;
    int rc = multiscale_image_quality(img.data(), w, h, w, cfg, &result);
    assert(rc == 0);
    assert(result.confidence < 0.01);
    (void)rc;
    std::printf("  PASS: quality_level_skip\n");
}

void test_small_image() {
    const int w = 8, h = 8;
    std::vector<std::uint8_t> img(w * h, 100);
    MultiscaleConfig cfg;
    MultiscaleImageResult result;
    int rc = multiscale_image_quality(img.data(), w, h, w, cfg, &result);
    assert(rc == 0);
    assert(result.levels_computed >= 1);
    (void)rc;
    std::printf("  PASS: small_image (levels=%d)\n", result.levels_computed);
}

void test_null_safety() {
    MultiscaleConfig cfg;
    MultiscaleImageResult result;
    assert(multiscale_image_quality(nullptr, 64, 64, 64, cfg, &result) < 0);
    std::uint8_t dummy[4] = {};
    assert(multiscale_image_quality(dummy, 2, 2, 2, cfg, &result) < 0);
    (void)cfg;
    (void)result;
    (void)dummy;
    std::printf("  PASS: null_safety\n");
}

void test_large_image_four_levels() {
    const int w = 256, h = 256;
    std::vector<std::uint8_t> img(w * h);
    for (int i = 0; i < w * h; ++i) {
        img[i] = static_cast<std::uint8_t>(i % 256);
    }
    MultiscaleConfig cfg;
    cfg.max_levels = 4;
    MultiscaleImageResult result;
    int rc = multiscale_image_quality(img.data(), w, h, w, cfg, &result);
    assert(rc == 0);
    assert(result.levels_computed == 4);
    assert(std::isfinite(result.composite_quality));
    (void)rc;
    std::printf("  PASS: large_image_four_levels (quality=%.3f)\n",
                result.composite_quality);
}

}  // namespace

int main() {
    std::printf("multiscale_image_quality_test\n");
    test_uniform_image();
    test_high_frequency_image();
    test_quality_level_skip();
    test_small_image();
    test_null_safety();
    test_large_image_four_levels();
    std::printf("All multiscale_image_quality tests passed.\n");
    return 0;
}
