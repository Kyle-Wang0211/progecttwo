// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/render/pbr_material.h"

#include <algorithm>
#include <cmath>

namespace aether {
namespace render {

namespace {
constexpr float kPI = 3.14159265358979323846f;
}

// ────────────────────────────────────────────────────────────────────
// Core BRDF functions (mirror GPU shaders in shader_source.cpp)
// ────────────────────────────────────────────────────────────────────

float ggx_ndf(float n_dot_h, float roughness) {
    const float a = roughness * roughness;
    const float a2 = a * a;
    const float cos2 = n_dot_h * n_dot_h;
    const float denom = cos2 * (a2 - 1.0f) + 1.0f;
    return a2 / (kPI * denom * denom + 1e-7f);
}

float smith_ggx_geometry(float n_dot_l, float n_dot_v, float roughness) {
    const float r = (roughness + 1.0f);
    const float k = (r * r) / 8.0f;  // Disney reparameterization
    const float g1_l = n_dot_l / (n_dot_l * (1.0f - k) + k + 1e-7f);
    const float g1_v = n_dot_v / (n_dot_v * (1.0f - k) + k + 1e-7f);
    return g1_l * g1_v;
}

float schlick_fresnel(float f0, float v_dot_h) {
    const float t = 1.0f - v_dot_h;
    const float t2 = t * t;
    const float t5 = t2 * t2 * t;
    return f0 + (1.0f - f0) * t5;
}

float f0_from_ior(float ior) {
    const float ratio = (ior - 1.0f) / (ior + 1.0f);
    return ratio * ratio;
}

float smoothstep(float edge0, float edge1, float x) {
    const float t = std::max(0.0f, std::min(1.0f,
        (x - edge0) / (edge1 - edge0 + 1e-7f)));
    return t * t * (3.0f - 2.0f * t);
}

// ────────────────────────────────────────────────────────────────────
// Evidence → PBR material mapping
// ────────────────────────────────────────────────────────────────────

PBRMaterialParams compute_pbr_from_evidence(
    float display_confidence,
    std::uint8_t /*evidence_state*/,
    const PBRFromEvidenceConfig& config) {

    PBRMaterialParams params;
    const float t = std::max(0.0f, std::min(1.0f, display_confidence));

    // IOR interpolation via smoothstep
    const float ior_t = smoothstep(0.0f, 1.0f, t);
    params.ior = config.ior_s0 + (config.ior_s5 - config.ior_s0) * ior_t;
    params.f0 = f0_from_ior(params.ior);

    // Roughness: interpolate from S0 (roughness_s0=0.6, glossy) to S5 (0.15, mirror-like)
    // Full range from display=0.0 — visible gloss from the very first observation.
    params.roughness = config.roughness_s0 +
        (config.roughness_s5 - config.roughness_s0) *
        smoothstep(0.0f, 0.88f, t);

    // Metallic: from S0 (metallic_s0=0.3, subtle sheen) to S5 (0.7, strong metallic)
    // Full range from display=0.0 — bigger/darker triangles show metallic character.
    params.metallic = config.metallic_s0 +
        (config.metallic_s5 - config.metallic_s0) *
        smoothstep(0.0f, 0.88f, t);

    // Clearcoat: only for high-evidence surfaces
    if (t > config.clearcoat_threshold) {
        const float ct = (t - config.clearcoat_threshold) /
                         (1.0f - config.clearcoat_threshold + 1e-7f);
        params.clearcoat = config.clearcoat_max * ct;
    }

    // Ambient occlusion: reduce for uncertain geometry
    params.ambient_occlusion = smoothstep(0.0f, 0.5f, t);

    return params;
}

// ────────────────────────────────────────────────────────────────────
// Cook-Torrance BRDF evaluation
// ────────────────────────────────────────────────────────────────────

BRDFEvalResult evaluate_cook_torrance(
    const PBRMaterialParams& material,
    const BRDFEvalInput& geom) {

    BRDFEvalResult result;

    if (geom.n_dot_l <= 0.0f || geom.n_dot_v <= 0.0f) {
        result.total = 0.0f;
        return result;
    }

    const float D = ggx_ndf(geom.n_dot_h, material.roughness);
    const float F = schlick_fresnel(material.f0, geom.v_dot_h);
    const float G = smith_ggx_geometry(geom.n_dot_l, geom.n_dot_v,
                                        material.roughness);

    // Cook-Torrance specular
    const float denom = 4.0f * geom.n_dot_l * geom.n_dot_v + 1e-7f;
    result.specular = (D * F * G) / denom;

    // Energy-conserving diffuse: (1 - F) * (1 - metallic) / PI
    const float kd = (1.0f - F) * (1.0f - material.metallic);
    result.diffuse = kd / kPI;

    result.fresnel = F;
    result.ndf = D;
    result.geometry = G;
    result.total = result.specular + result.diffuse;

    // Clearcoat layer (additive)
    if (material.clearcoat > 0.0f) {
        const float D_cc = ggx_ndf(geom.n_dot_h, material.clearcoat_roughness);
        const float F_cc = schlick_fresnel(0.04f, geom.v_dot_h);
        const float G_cc = smith_ggx_geometry(geom.n_dot_l, geom.n_dot_v,
                                               material.clearcoat_roughness);
        result.total += material.clearcoat * (D_cc * F_cc * G_cc) / denom;
    }

    result.energy_conserving = check_energy_conservation(material, 16);
    return result;
}

// ────────────────────────────────────────────────────────────────────
// Energy conservation check
// ────────────────────────────────────────────────────────────────────

bool check_energy_conservation(
    const PBRMaterialParams& material,
    int num_samples) {

    if (num_samples < 4) num_samples = 4;

    // Numerical integration of BRDF * cos(theta) over hemisphere
    // Fixed viewer at 60 degrees from normal
    const float view_theta = 1.0472f;  // 60 degrees
    const float cos_v = std::cos(view_theta);
    const float sin_v = std::sin(view_theta);

    double integral = 0.0;
    const double d_theta = (kPI * 0.5) / num_samples;
    const double d_phi = (2.0 * kPI) / num_samples;

    for (int i = 0; i < num_samples; ++i) {
        const double theta = (static_cast<double>(i) + 0.5) * d_theta;
        const float cos_l = static_cast<float>(std::cos(theta));
        const float sin_l = static_cast<float>(std::sin(theta));

        for (int j = 0; j < num_samples; ++j) {
            const double phi = (static_cast<double>(j) + 0.5) * d_phi;

            // Light direction in local frame
            const float lx = sin_l * static_cast<float>(std::cos(phi));
            const float ly = sin_l * static_cast<float>(std::sin(phi));
            const float lz = cos_l;

            // Half vector = normalize(L + V)
            const float hx = lx + sin_v;
            const float hy = ly;
            const float hz = lz + cos_v;
            const float h_len = std::sqrt(hx * hx + hy * hy + hz * hz);
            if (h_len < 1e-6f) continue;

            BRDFEvalInput geom;
            geom.n_dot_l = cos_l;
            geom.n_dot_v = cos_v;
            geom.n_dot_h = std::max(0.0f, hz / h_len);
            geom.v_dot_h = std::max(0.0f,
                (sin_v * hx + cos_v * hz) / h_len);

            // Simple specular + diffuse (no clearcoat for speed)
            const float D = ggx_ndf(geom.n_dot_h, material.roughness);
            const float F = schlick_fresnel(material.f0, geom.v_dot_h);
            const float G = smith_ggx_geometry(geom.n_dot_l, geom.n_dot_v,
                                                material.roughness);
            const float spec = (D * F * G) /
                (4.0f * geom.n_dot_l * geom.n_dot_v + 1e-7f);
            const float diff = (1.0f - F) * (1.0f - material.metallic) / kPI;
            const float brdf = spec + diff;

            integral += static_cast<double>(brdf) *
                        static_cast<double>(cos_l) *
                        std::sin(theta) * d_theta * d_phi;
        }
    }

    return integral <= 1.05;  // Small tolerance for numerical error
}

}  // namespace render
}  // namespace aether
