// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_TRAINER_TSDF_GAUSSIAN_BRIDGE_H
#define AETHER_TRAINER_TSDF_GAUSSIAN_BRIDGE_H

#ifdef __cplusplus

#include "aether/core/status.h"
#include "aether/innovation/core_types.h"

#include <cstddef>
#include <cstdint>
#include <vector>

namespace aether {
namespace trainer {

// ---------------------------------------------------------------------------
// TSDFVoxelSeed: input from the TSDF volume
// ---------------------------------------------------------------------------

struct TSDFVoxelSeed {
    innovation::Float3 position;
    innovation::Float3 normal;
    float sdf_value;
    float confidence;
    float voxel_size;
    std::uint8_t rgb[3];
};

// ---------------------------------------------------------------------------
// GaussianSeed: output Gaussian initialization
// ---------------------------------------------------------------------------

struct GaussianSeed {
    innovation::Float3 position;
    innovation::Float3 scale;
    float opacity;
    float sh_dc[3];
    innovation::BindingState binding;
    innovation::ScaffoldUnitId host;
};

// ---------------------------------------------------------------------------
// BridgeConfig: tunable parameters for TSDF-to-Gaussian conversion
// ---------------------------------------------------------------------------

struct BridgeConfig {
    float truncation_threshold{0.02f};   // |SDF| must be below this
    float min_confidence{0.1f};          // voxel confidence threshold
    float opacity_scale{0.8f};           // confidence-to-opacity multiplier
    float opacity_min{0.3f};             // clamp lower bound
    float opacity_max{0.95f};            // clamp upper bound
    float normal_scale_ratio{0.25f};     // normal direction = voxel_size * ratio
};

// ---------------------------------------------------------------------------
// TSDFGaussianBridge: converts TSDF voxels into initial Gaussian seeds
// ---------------------------------------------------------------------------

class TSDFGaussianBridge {
public:
    explicit TSDFGaussianBridge(const BridgeConfig& config);

    /// Extract Gaussian seeds from a set of TSDF voxels.
    /// Filters by SDF truncation and confidence thresholds.
    core::Status extract_seeds(const TSDFVoxelSeed* voxels,
                               std::size_t count,
                               std::vector<GaussianSeed>* seeds);

    /// Compute anisotropic scale: tangent dirs = voxel_size, normal = voxel_size * ratio.
    static innovation::Float3 compute_anisotropic_scale(
        const innovation::Float3& normal,
        float voxel_size,
        float normal_ratio);

    /// Convert sRGB [0,255] to SH DC coefficients: sh_dc = (rgb/255) / sqrt(4*pi).
    static void compute_sh_dc_from_rgb(const std::uint8_t rgb[3], float sh_dc[3]);

    /// Compute SDF gradient from 6 neighbors in order: +x, -x, +y, -y, +z, -z.
    static innovation::Float3 compute_sdf_gradient(const float* sdf_neighborhood_6);

private:
    BridgeConfig config_;
};

}  // namespace trainer
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_TRAINER_TSDF_GAUSSIAN_BRIDGE_H
