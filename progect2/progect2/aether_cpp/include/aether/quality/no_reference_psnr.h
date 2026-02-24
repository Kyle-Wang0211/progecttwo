// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_QUALITY_NO_REFERENCE_PSNR_H
#define AETHER_QUALITY_NO_REFERENCE_PSNR_H

#include <cstdint>

#include "aether/core/status.h"

namespace aether {
namespace quality {

struct NoRefPsnrConfig {
    float sharpness_weight{0.35f};
    float noise_weight{0.25f};
    float structure_weight{0.25f};
    float color_weight{0.15f};
    float calibration_offset{0.0f};   ///< scene-specific bias (dB)
};

struct NoRefPsnrResult {
    float estimated_psnr{0.0f};            ///< estimated PSNR in dB
    float sharpness_score{0.0f};           ///< [0,1]
    float noise_score{0.0f};              ///< [0,1] (1 = clean)
    float structural_score{0.0f};          ///< [0,1]
    float color_consistency_score{0.0f};   ///< [0,1]
    float confidence{0.0f};                ///< [0,1]
};

/// Estimate PSNR from a single rendered image without ground-truth.
///
/// Uses a combination of:
///   - Laplacian energy (sharpness, reuses multiscale_image_quality)
///   - MAD noise estimate
///   - Local structure regularity (edge coherence)
///   - Colour consistency (saturation uniformity)
///
/// The four components are mapped to [0,1] via sigmoid, weighted, then
/// linearly regressed to a PSNR estimate calibrated on the training set.
///
/// \param rendered_rgb  pointer to row-major RGB888 pixel data
/// \param width         image width in pixels
/// \param height        image height in pixels
/// \param row_bytes     stride between rows (typically width*3)
/// \param config        weighting configuration
/// \param out           result struct to fill
core::Status estimate_no_reference_psnr(
    const uint8_t* rendered_rgb, int width, int height, int row_bytes,
    const NoRefPsnrConfig& config, NoRefPsnrResult* out);

/// Estimate PSNR from two rendered views of the same scene region.
///
/// Computes per-pixel MSE between the two views as a proxy for
/// reconstruction error.  More accurate than the single-view estimate
/// but requires two renders from nearby viewpoints.
core::Status estimate_multiview_psnr(
    const uint8_t* view_a, const uint8_t* view_b,
    int width, int height, int row_bytes,
    float* out_psnr);

}  // namespace quality
}  // namespace aether

#endif  // AETHER_QUALITY_NO_REFERENCE_PSNR_H
