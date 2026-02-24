// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/tsdf_gaussian_bridge.h"

#include <cmath>
#include <cstring>

namespace {

constexpr float kInvSqrt4Pi = 1.0f / 3.5449077018110318f;  // 1/sqrt(4*pi)
constexpr float kEpsilon = 1e-7f;

inline float clamp_f(float v, float lo, float hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

inline float abs_f(float v) {
    return v < 0.0f ? -v : v;
}

}  // namespace

namespace aether {
namespace trainer {

using innovation::Float3;
using innovation::make_float3;
using innovation::dot;
using innovation::cross;
using innovation::mul;
using innovation::sub;

TSDFGaussianBridge::TSDFGaussianBridge(const BridgeConfig& config)
    : config_(config) {}

// ---------------------------------------------------------------------------
// Static helpers
// ---------------------------------------------------------------------------

void TSDFGaussianBridge::compute_sh_dc_from_rgb(const std::uint8_t rgb[3],
                                                  float sh_dc[3]) {
    // sh_dc[c] = (rgb[c] / 255.0f) / sqrt(4 * pi)
    sh_dc[0] = (static_cast<float>(rgb[0]) / 255.0f) * kInvSqrt4Pi;
    sh_dc[1] = (static_cast<float>(rgb[1]) / 255.0f) * kInvSqrt4Pi;
    sh_dc[2] = (static_cast<float>(rgb[2]) / 255.0f) * kInvSqrt4Pi;
}

Float3 TSDFGaussianBridge::compute_sdf_gradient(const float* sdf_neighborhood_6) {
    // Neighbors in order: +x, -x, +y, -y, +z, -z
    // Central difference: grad_x = (sdf[+x] - sdf[-x]) / 2
    const float gx = (sdf_neighborhood_6[0] - sdf_neighborhood_6[1]) * 0.5f;
    const float gy = (sdf_neighborhood_6[2] - sdf_neighborhood_6[3]) * 0.5f;
    const float gz = (sdf_neighborhood_6[4] - sdf_neighborhood_6[5]) * 0.5f;
    return make_float3(gx, gy, gz);
}

Float3 TSDFGaussianBridge::compute_anisotropic_scale(
    const Float3& normal,
    float voxel_size,
    float normal_ratio) {
    // The scale along the normal direction is compressed,
    // while tangent directions keep the full voxel size.
    // We compute a tangent basis using Gram-Schmidt and return
    // the scale vector in world-aligned form as (sx, sy, sz) where
    // the components represent the magnitude along each principal axis.
    //
    // For the Gaussian representation, we express scale in the local frame:
    //   - tangent1 direction: voxel_size
    //   - tangent2 direction: voxel_size
    //   - normal direction:   voxel_size * normal_ratio
    //
    // We return scale in the local frame as (tangent1, tangent2, normal).
    const float tangent_scale = voxel_size;
    const float normal_scale = voxel_size * normal_ratio;

    // Construct tangent basis via Gram-Schmidt
    // Choose initial vector not parallel to the normal
    Float3 up = make_float3(0.0f, 1.0f, 0.0f);
    if (abs_f(dot(normal, up)) > 0.99f) {
        up = make_float3(1.0f, 0.0f, 0.0f);
    }

    // tangent1 = normalize(up - dot(up, normal) * normal)
    const float d = dot(up, normal);
    Float3 tangent1 = make_float3(
        up.x - d * normal.x,
        up.y - d * normal.y,
        up.z - d * normal.z);
    const float t1_len = std::sqrt(dot(tangent1, tangent1));
    if (t1_len > kEpsilon) {
        tangent1 = mul(tangent1, 1.0f / t1_len);
    }

    // We return the scale in the local Gaussian frame:
    // (tangent1_scale, tangent2_scale, normal_scale)
    return make_float3(tangent_scale, tangent_scale, normal_scale);
}

// ---------------------------------------------------------------------------
// extract_seeds
// ---------------------------------------------------------------------------

core::Status TSDFGaussianBridge::extract_seeds(
    const TSDFVoxelSeed* voxels,
    std::size_t count,
    std::vector<GaussianSeed>* seeds) {
    if (seeds == nullptr) {
        return core::Status::kInvalidArgument;
    }
    if (count > 0 && voxels == nullptr) {
        return core::Status::kInvalidArgument;
    }

    seeds->clear();
    seeds->reserve(count / 2);  // rough estimate: ~half pass the filter

    for (std::size_t i = 0; i < count; ++i) {
        const TSDFVoxelSeed& voxel = voxels[i];

        // Filter: |SDF| must be below truncation threshold
        if (abs_f(voxel.sdf_value) >= config_.truncation_threshold) {
            continue;
        }

        // Filter: confidence must exceed minimum
        if (voxel.confidence < config_.min_confidence) {
            continue;
        }

        GaussianSeed seed{};

        // Position: directly from voxel center
        seed.position = voxel.position;

        // Normal: use the voxel's stored normal. If it's zero-length, skip.
        Float3 normal = voxel.normal;
        const float normal_len = std::sqrt(dot(normal, normal));
        if (normal_len < kEpsilon) {
            // Degenerate normal; skip this voxel
            continue;
        }
        normal = mul(normal, 1.0f / normal_len);

        // Anisotropic scale
        seed.scale = compute_anisotropic_scale(
            normal, voxel.voxel_size, config_.normal_scale_ratio);

        // Opacity: clamp(confidence * opacity_scale, opacity_min, opacity_max)
        seed.opacity = clamp_f(
            voxel.confidence * config_.opacity_scale,
            config_.opacity_min,
            config_.opacity_max);

        // SH DC coefficients from RGB
        compute_sh_dc_from_rgb(voxel.rgb, seed.sh_dc);

        // Binding: soft by default; host to be set externally
        seed.binding = innovation::BindingState::kSoft;
        seed.host = 0;

        seeds->push_back(seed);
    }

    return core::Status::kOk;
}

}  // namespace trainer
}  // namespace aether
