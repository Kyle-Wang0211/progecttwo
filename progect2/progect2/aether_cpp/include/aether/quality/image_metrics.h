// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_QUALITY_IMAGE_METRICS_H
#define AETHER_QUALITY_IMAGE_METRICS_H

#ifdef __cplusplus

#include "aether/core/status.h"

#include <cstdint>

namespace aether {
namespace quality {

aether::core::Status laplacian_variance(
    const std::uint8_t* bytes,
    int width,
    int height,
    int row_bytes,
    double* out_variance);

aether::core::Status tenengrad_metric_for_quality(
    int quality_level,
    double tenengrad_threshold,
    double* out_value,
    double* out_confidence,
    double* out_roi_coverage,
    bool* out_skipped);

aether::core::Status tenengrad_metric_from_image(
    const std::uint8_t* bytes,
    int width,
    int height,
    int row_bytes,
    int quality_level,
    double tenengrad_threshold,
    double* out_value,
    double* out_confidence,
    double* out_roi_coverage,
    bool* out_skipped);

aether::core::Status tenengrad_metric_from_image(
    const std::uint8_t* bytes,
    int width,
    int height,
    int row_bytes,
    int quality_level,
    double tenengrad_threshold,
    double* out_value,
    double* out_confidence,
    double* out_roi_coverage,
    bool* out_skipped);

aether::core::Status tenengrad_metric_from_image(
    const std::uint8_t* bytes,
    int width,
    int height,
    int row_bytes,
    int quality_level,
    double tenengrad_threshold,
    double* out_value,
    double* out_confidence,
    double* out_roi_coverage,
    bool* out_skipped);

struct ExposureAnalysis {
    double overexpose_ratio{0.0};
    double underexpose_ratio{0.0};
    bool has_large_blown_region{false};
};

aether::core::Status exposure_analyze(
    const std::uint8_t* bytes,
    int width,
    int height,
    int row_bytes,
    ExposureAnalysis* out_result);

struct TextureAnalysis {
    int feature_count{0};
    double spatial_spread{0.0};
    double entropy{0.0};
    double repetitive_penalty{0.0};
    double fused_score{0.0};
    double confidence{0.0};
};

aether::core::Status texture_analyze(
    const std::uint8_t* bytes,
    int width,
    int height,
    int row_bytes,
    TextureAnalysis* out_result);

aether::core::Status brightness_metric_for_quality(
    int quality_level,
    double* out_value,
    double* out_confidence);

struct MaterialAnalysis {
    double specular_percent{0.0};
    double transparent_percent{0.0};
    double textureless_percent{0.0};
    bool is_non_lambertian{false};
    double confidence{0.0};
    int largest_specular_region{0};
};

aether::core::Status material_analyze_for_quality(
    int quality_level,
    MaterialAnalysis* out_result);

}  // namespace quality
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_QUALITY_IMAGE_METRICS_H
