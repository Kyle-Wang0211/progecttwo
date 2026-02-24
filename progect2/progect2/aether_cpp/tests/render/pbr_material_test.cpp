// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/render/pbr_material.h"

#include <cassert>
#include <cmath>
#include <cstdio>

using namespace aether::render;

namespace {

void test_f0_from_ior() {
    float f = f0_from_ior(1.5f);
    assert(std::abs(f - 0.04f) < 0.005f);
    f = f0_from_ior(1.0f);
    assert(f < 0.001f);
    f = f0_from_ior(2.5f);
    assert(f > 0.05f);
    (void)f;
    std::printf("  PASS: f0_from_ior (1.5 → %.4f)\n", f0_from_ior(1.5f));
}

void test_ggx_ndf_peak() {
    float d_low = ggx_ndf(1.0f, 0.1f);
    float d_high = ggx_ndf(0.5f, 0.1f);
    assert(d_low > d_high);
    std::printf("  PASS: ggx_ndf_peak (NdotH=1: %.2f > NdotH=0.5: %.2f)\n",
                d_low, d_high);
}

void test_smith_geometry_normal_incidence() {
    float g = smith_ggx_geometry(1.0f, 1.0f, 0.5f);
    assert(g > 0.9f);
    std::printf("  PASS: smith_geometry_normal (G=%.4f)\n", g);
}

void test_schlick_fresnel_extremes() {
    float f_normal = schlick_fresnel(0.04f, 1.0f);
    assert(std::abs(f_normal - 0.04f) < 0.001f);
    float f_grazing = schlick_fresnel(0.04f, 0.0f);
    assert(std::abs(f_grazing - 1.0f) < 0.001f);
    std::printf("  PASS: schlick_fresnel (normal=%.4f, grazing=%.4f)\n",
                f_normal, f_grazing);
}

void test_energy_conservation() {
    PBRMaterialParams mat;
    mat.roughness = 0.5f;
    mat.metallic = 0.0f;
    mat.f0 = 0.04f;
    assert(check_energy_conservation(mat, 32));

    mat.roughness = 0.1f;
    mat.metallic = 1.0f;
    mat.f0 = 0.95f;
    assert(check_energy_conservation(mat, 32));

    std::printf("  PASS: energy_conservation\n");
}

void test_evidence_to_pbr_low() {
    auto p = compute_pbr_from_evidence(0.0f, 0);
    assert(p.roughness > 0.8f);
    assert(p.metallic < 0.1f);
    assert(p.clearcoat < 0.01f);
    std::printf("  PASS: evidence_low (R=%.2f, M=%.2f)\n",
                p.roughness, p.metallic);
}

void test_evidence_to_pbr_high() {
    auto p = compute_pbr_from_evidence(1.0f, 5);
    assert(p.roughness < 0.5f);
    assert(p.metallic > 0.2f);
    assert(p.clearcoat > 0.1f);
    assert(p.f0 > 0.03f);
    std::printf("  PASS: evidence_high (R=%.2f, M=%.2f, CC=%.2f)\n",
                p.roughness, p.metallic, p.clearcoat);
}

void test_cook_torrance_evaluation() {
    PBRMaterialParams mat;
    mat.roughness = 0.3f;
    mat.metallic = 0.5f;
    mat.f0 = 0.04f;

    BRDFEvalInput geom;
    geom.n_dot_l = 0.7f;
    geom.n_dot_v = 0.8f;
    geom.n_dot_h = 0.95f;
    geom.v_dot_h = 0.9f;

    auto r = evaluate_cook_torrance(mat, geom);
    assert(std::isfinite(r.total));
    assert(r.total > 0.0f);
    assert(r.specular >= 0.0f);
    assert(r.diffuse >= 0.0f);
    std::printf("  PASS: cook_torrance (total=%.4f, spec=%.4f, diff=%.4f)\n",
                r.total, r.specular, r.diffuse);
}

void test_zero_ndotl_returns_zero() {
    PBRMaterialParams mat;
    BRDFEvalInput geom;
    geom.n_dot_l = 0.0f;
    geom.n_dot_v = 0.5f;
    auto r = evaluate_cook_torrance(mat, geom);
    assert(r.total == 0.0f);
    (void)r;
    std::printf("  PASS: zero_ndotl_returns_zero\n");
}

}  // namespace

int main() {
    std::printf("pbr_material_test\n");
    test_f0_from_ior();
    test_ggx_ndf_peak();
    test_smith_geometry_normal_incidence();
    test_schlick_fresnel_extremes();
    test_energy_conservation();
    test_evidence_to_pbr_low();
    test_evidence_to_pbr_high();
    test_cook_torrance_evaluation();
    test_zero_ndotl_returns_zero();
    std::printf("All pbr_material tests passed.\n");
    return 0;
}
