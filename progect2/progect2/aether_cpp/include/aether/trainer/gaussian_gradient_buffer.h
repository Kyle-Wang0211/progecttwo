// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_TRAINER_GAUSSIAN_GRADIENT_BUFFER_H
#define AETHER_TRAINER_GAUSSIAN_GRADIENT_BUFFER_H

#ifdef __cplusplus

#include "aether/core/status.h"

#include <cstddef>
#include <cstdint>
#include <vector>

namespace aether {
namespace trainer {

// ---------------------------------------------------------------------------
// Layout: 38 floats per Gaussian
// ---------------------------------------------------------------------------
// position(3) + scale(3) + opacity(1) + rotation(4) + sh_dc(3) + sh_rest(24)
static constexpr std::size_t kGradientsPerGaussian = 38;

// Offsets within the per-gaussian gradient block
static constexpr std::size_t kOffsetPosition = 0;
static constexpr std::size_t kOffsetScale    = 3;
static constexpr std::size_t kOffsetOpacity  = 6;
static constexpr std::size_t kOffsetRotation = 7;
static constexpr std::size_t kOffsetSHDC     = 11;
static constexpr std::size_t kOffsetSHRest   = 14;

// Component sizes
static constexpr std::size_t kSizePosition = 3;
static constexpr std::size_t kSizeScale    = 3;
static constexpr std::size_t kSizeOpacity  = 1;
static constexpr std::size_t kSizeRotation = 4;
static constexpr std::size_t kSizeSHDC     = 3;
static constexpr std::size_t kSizeSHRest   = 24;

// ---------------------------------------------------------------------------
// GradientSlice: convenience pointers into a flat gradient buffer
// ---------------------------------------------------------------------------

struct GradientSlice {
    float* position;   // 3 floats
    float* scale;      // 3 floats
    float* opacity;    // 1 float
    float* rotation;   // 4 floats
    float* sh_dc;      // 3 floats
    float* sh_rest;    // 24 floats
};

// ---------------------------------------------------------------------------
// GaussianGradientBuffer: double-buffered gradient accumulation
// ---------------------------------------------------------------------------

class GaussianGradientBuffer {
public:
    GaussianGradientBuffer() = default;

    /// Allocate two buffers for the given maximum number of Gaussians.
    core::Status init(std::size_t max_gaussians);

    /// Swap read and write buffers.
    void swap();

    /// Zero out the current write buffer.
    void clear_write_buffer();

    /// Raw pointer to the current write buffer.
    float* write_data();

    /// Raw pointer to the current read buffer.
    const float* read_data() const;

    /// Get a GradientSlice for a specific gaussian in the write buffer.
    /// Returns a slice with all null pointers if the index is out of range.
    GradientSlice write_slice(std::size_t gaussian_index);

    /// Maximum number of Gaussians this buffer can hold.
    std::size_t max_gaussians() const { return max_gaussians_; }

    /// Total size in bytes of a single buffer.
    std::size_t buffer_size_bytes() const {
        return max_gaussians_ * kGradientsPerGaussian * sizeof(float);
    }

private:
    std::vector<float> buffer_a_{};
    std::vector<float> buffer_b_{};
    std::size_t current_write_{0};  // 0 = buffer_a_ is write, 1 = buffer_b_
    std::size_t max_gaussians_{0};
};

}  // namespace trainer
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_TRAINER_GAUSSIAN_GRADIENT_BUFFER_H
