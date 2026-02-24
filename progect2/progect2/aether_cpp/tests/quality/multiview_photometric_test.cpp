// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/multiview_photometric.h"

#include <cassert>
#include <cmath>
#include <cstdio>

using namespace aether::quality;

namespace {

ViewRadianceSample make_sample(double r, double g, double b,
                                double vx, double vy, double vz,
                                double nx, double ny, double nz,
                                double lum) {
    ViewRadianceSample s;
    s.rgb[0] = r; s.rgb[1] = g; s.rgb[2] = b;
    s.view_dir[0] = vx; s.view_dir[1] = vy; s.view_dir[2] = vz;
    s.normal[0] = nx; s.normal[1] = ny; s.normal[2] = nz;
    s.luminance = lum;
    s.lab.l = lum * 100.0;
    s.lab.a = (r - g) * 50.0;
    s.lab.b = (g - b) * 50.0;
    s.timestamp_ms = 0;
    return s;
}

void test_single_view_returns_default() {
    MultiViewPhotometricValidator v;
    v.add_observation(1, make_sample(0.5, 0.5, 0.5,  0, 0, 1,  0, 0, 1,  0.5));
    auto r = v.evaluate_patch(1);
    assert(r.is_consistent);
    assert(r.confidence < 0.01);
    assert(r.pair_count == 0);
    (void)r;
    std::printf("  PASS: single_view_returns_default\n");
}

void test_identical_views_consistent() {
    MultiViewPhotometricValidator v;
    auto s1 = make_sample(0.5, 0.5, 0.5,  0, 0, 1,  0, 0, 1,  0.5);
    auto s2 = make_sample(0.5, 0.5, 0.5,  0.1, 0, 0.995,  0, 0, 1,  0.5);
    v.add_observation(1, s1);
    v.add_observation(1, s2);
    auto r = v.evaluate_patch(1);
    assert(r.is_consistent);
    assert(r.mean_cross_view_delta_e < 1.0);
    assert(r.pair_count == 1);
    (void)r;
    std::printf("  PASS: identical_views_consistent\n");
}

void test_different_views_flag_inconsistency() {
    MultiViewPhotometricValidator v;
    // Very different colors from same surface → inconsistent
    auto s1 = make_sample(0.9, 0.1, 0.1,  0, 0, 1,  0, 0, 1,  0.3);
    auto s2 = make_sample(0.1, 0.1, 0.9,  0.1, 0, 0.995,  0, 0, 1,  0.3);
    v.add_observation(1, s1);
    v.add_observation(1, s2);
    auto r = v.evaluate_patch(1);
    // Large delta_e expected
    assert(r.mean_cross_view_delta_e > 5.0);
    assert(r.pair_count == 1);
    (void)r;
    std::printf("  PASS: different_views_flag_inconsistency\n");
}

void test_grazing_angle_skipped() {
    MultiViewPhotometricConfig cfg;
    cfg.grazing_angle_cos_min = 0.15;
    MultiViewPhotometricValidator v(cfg);
    // View direction nearly perpendicular to normal → cos ≈ 0
    auto s1 = make_sample(0.5, 0.5, 0.5,  1, 0, 0,  0, 0, 1,  0.5);
    auto s2 = make_sample(0.5, 0.5, 0.5,  0, 1, 0,  0, 0, 1,  0.5);
    v.add_observation(1, s1);
    v.add_observation(1, s2);
    auto r = v.evaluate_patch(1);
    // Both views should be skipped (cos ≈ 0 for both)
    assert(r.pair_count == 0);
    assert(r.confidence < 0.01);
    (void)r;
    std::printf("  PASS: grazing_angle_skipped\n");
}

void test_max_views_cap() {
    MultiViewPhotometricConfig cfg;
    cfg.max_views_per_patch = 4;
    MultiViewPhotometricValidator v(cfg);
    for (int i = 0; i < 10; ++i) {
        v.add_observation(1,
            make_sample(0.5, 0.5, 0.5,  0, 0, 1,  0, 0, 1,  0.5));
    }
    // Should cap at 4 samples → C(4,2)=6 pairs max
    auto r = v.evaluate_patch(1);
    assert(r.pair_count <= 6);
    (void)r;
    std::printf("  PASS: max_views_cap\n");
}

void test_evaluate_all_aggregates() {
    MultiViewPhotometricValidator v;
    // Patch 1: consistent
    v.add_observation(1, make_sample(0.5, 0.5, 0.5,  0, 0, 1,  0, 0, 1,  0.5));
    v.add_observation(1, make_sample(0.5, 0.5, 0.5,  0.1, 0, 0.995,  0, 0, 1,  0.5));
    // Patch 2: also consistent
    v.add_observation(2, make_sample(0.3, 0.3, 0.3,  0, 0, 1,  0, 0, 1,  0.3));
    v.add_observation(2, make_sample(0.3, 0.3, 0.3,  0.1, 0, 0.995,  0, 0, 1,  0.3));

    auto r = v.evaluate_all();
    assert(r.is_consistent);
    assert(r.pair_count >= 2);
    assert(v.patch_count() == 2);
    (void)r;
    std::printf("  PASS: evaluate_all_aggregates\n");
}

void test_unknown_patch_returns_default() {
    MultiViewPhotometricValidator v;
    auto r = v.evaluate_patch(999);
    assert(r.is_consistent);
    assert(r.pair_count == 0);
    assert(r.confidence < 0.01);
    (void)r;
    std::printf("  PASS: unknown_patch_returns_default\n");
}

void test_reset_clears_state() {
    MultiViewPhotometricValidator v;
    v.add_observation(1, make_sample(0.5, 0.5, 0.5,  0, 0, 1,  0, 0, 1,  0.5));
    assert(v.patch_count() == 1);
    v.reset();
    assert(v.patch_count() == 0);
    std::printf("  PASS: reset_clears_state\n");
}

}  // namespace

int main() {
    std::printf("multiview_photometric_test\n");
    test_single_view_returns_default();
    test_identical_views_consistent();
    test_different_views_flag_inconsistency();
    test_grazing_angle_skipped();
    test_max_views_cap();
    test_evaluate_all_aggregates();
    test_unknown_patch_returns_default();
    test_reset_clears_state();
    std::printf("All multiview_photometric tests passed.\n");
    return 0;
}
