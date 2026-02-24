// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_QUALITY_MULTISCALE_IMAGE_QUALITY_H
#define AETHER_QUALITY_MULTISCALE_IMAGE_QUALITY_H

#ifdef __cplusplus

#include <cstdint>

namespace aether {
namespace quality {

struct MultiscaleImageResult {
    double composite_quality{0.0};
    double confidence{0.0};
    double per_level_energy[4]{};
    double noise_estimate{0.0};
    double sharpness_profile{0.0};
    int levels_computed{0};
};

struct MultiscaleConfig {
    int max_levels{4};
    int quality_level{0};
    double noise_mad_scale{0.6745};
    int min_dimension{8};
};

/// Compute multi-scale image quality from grayscale buffer.
/// Returns 0 on success, negative on error.
int multiscale_image_quality(
    const std::uint8_t* bytes,
    int width,
    int height,
    int row_bytes,
    const MultiscaleConfig& config,
    MultiscaleImageResult* out_result);

}  // namespace quality
}  // namespace aether

#endif  // __cplusplus
#endif  // AETHER_QUALITY_MULTISCALE_IMAGE_QUALITY_H
