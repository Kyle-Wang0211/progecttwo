// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_RENDER_PBR_MATERIAL_H
#define AETHER_RENDER_PBR_MATERIAL_H

#ifdef __cplusplus

#include <cstdint>

namespace aether {
namespace render {

/// PBR material parameters for Cook-Torrance BRDF.
struct PBRMaterialParams {
    float metallic{0.0f};
    float roughness{1.0f};
    float f0{0.04f};
    float ior{1.5f};
    float clearcoat{0.0f};
    float clearcoat_roughness{0.1f};
    float ambient_occlusion{1.0f};
};

/// Input geometry for BRDF evaluation.
struct BRDFEvalInput {
    float n_dot_l{0.0f};
    float n_dot_v{0.0f};
    float n_dot_h{0.0f};
    float v_dot_h{0.0f};
};

/// Result of BRDF evaluation.
struct BRDFEvalResult {
    float specular{0.0f};
    float diffuse{0.0f};
    float fresnel{0.0f};
    float ndf{0.0f};
    float geometry{0.0f};
    float total{0.0f};
    bool energy_conserving{true};
};

/// Configuration for evidence-to-PBR mapping.
struct PBRFromEvidenceConfig {
    float ior_s0{1.0f};
    float ior_s5{1.5f};
    float roughness_s0{0.6f};   // Match ScanGuidanceConstants.roughnessBase — glossy from S0
    float roughness_s5{0.15f};
    float metallic_s0{0.3f};   // Match ScanGuidanceConstants.metallicBase — metallic sheen from S0
    float metallic_s5{0.7f};   // Strong metallic at full evidence
    float clearcoat_threshold{0.75f};
    float clearcoat_max{0.3f};
};

/// Compute PBR material parameters from evidence state.
PBRMaterialParams compute_pbr_from_evidence(
    float display_confidence,
    std::uint8_t evidence_state,
    const PBRFromEvidenceConfig& config = {});

/// Evaluate Cook-Torrance BRDF.
BRDFEvalResult evaluate_cook_torrance(
    const PBRMaterialParams& material,
    const BRDFEvalInput& geom);

/// GGX Normal Distribution Function (mirrors GPU shader).
float ggx_ndf(float n_dot_h, float roughness);

/// Smith Geometry with GGX and Disney reparameterization.
float smith_ggx_geometry(float n_dot_l, float n_dot_v, float roughness);

/// Schlick Fresnel approximation.
float schlick_fresnel(float f0, float v_dot_h);

/// Fresnel base reflectance from index of refraction.
float f0_from_ior(float ior);

/// Smoothstep interpolation.
float smoothstep(float edge0, float edge1, float x);

/// Energy conservation check: hemisphere integral of BRDF * cos(theta).
bool check_energy_conservation(
    const PBRMaterialParams& material,
    int num_samples = 32);

}  // namespace render
}  // namespace aether

#endif  // __cplusplus
#endif  // AETHER_RENDER_PBR_MATERIAL_H
