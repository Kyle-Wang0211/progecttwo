// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_TRAINER_APOLLO_MINI_OPTIMIZER_H
#define AETHER_TRAINER_APOLLO_MINI_OPTIMIZER_H

#ifdef __cplusplus

#include "aether/core/status.h"
#include "aether/trainer/gaussian_gradient_buffer.h"

#include <cstddef>
#include <cstdint>
#include <vector>

namespace aether {
namespace trainer {

// ---------------------------------------------------------------------------
// Parameter groups for per-group learning rates
// ---------------------------------------------------------------------------

enum class ParamGroup : std::uint8_t {
    kPosition = 0,
    kScale    = 1,
    kOpacity  = 2,
    kRotation = 3,
    kSHDC     = 4,
    kSHRest   = 5,
    kCount    = 6,
};

static constexpr std::size_t kParamGroupCount =
    static_cast<std::size_t>(ParamGroup::kCount);

// ---------------------------------------------------------------------------
// ApolloMiniConfig: optimizer hyperparameters
// ---------------------------------------------------------------------------

struct ApolloMiniConfig {
    // Per-group learning rates
    float lr_position{1.6e-4f};
    float lr_opacity{5e-2f};
    float lr_scale{5e-3f};
    float lr_sh{2.5e-3f};
    float lr_rotation{1e-3f};

    // Adam-style momentum parameters
    float beta1{0.9f};
    float beta2{0.999f};
    float eps{1e-8f};

    // APOLLO-Mini specific
    std::uint32_t rank{1};

    // Gradient clipping
    float grad_clip_norm{1.0f};
    float max_lr_scale{10.0f};
};

// ---------------------------------------------------------------------------
// ApolloMiniOptimizer: rank-1 APOLLO-Mini optimizer for 3DGS parameters
// ---------------------------------------------------------------------------
// Memory-efficient variant of Adam that maintains:
//   - Full first moment (m) vector
//   - Rank-1 auxiliary scalar (r) per parameter group per gaussian
//
// This reduces second-moment memory from O(params) to O(groups * gaussians).

class ApolloMiniOptimizer {
public:
    ApolloMiniOptimizer() = default;

    /// Initialize the optimizer for a given number of Gaussians.
    core::Status init(const ApolloMiniConfig& config, std::size_t num_gaussians);

    /// Perform one optimization step, updating parameters in-place.
    core::Status step(float* parameters,
                      const float* gradients,
                      std::size_t num_gaussians);

    /// Reset optimizer state (moments, step counter).
    void reset();

    /// Current optimization step count.
    std::uint32_t current_step() const { return step_; }

    /// Total memory used by optimizer state in bytes.
    std::size_t memory_bytes() const;

private:
    /// Clip gradients by global norm, returning the scale factor applied.
    float clip_gradients(float* grad_copy, std::size_t count) const;

    /// Get the learning rate for a given parameter group.
    float get_lr(ParamGroup group) const;

    /// Get the offset and size for a parameter group within a single Gaussian.
    static void group_range(ParamGroup group,
                            std::size_t* offset,
                            std::size_t* size);

    ApolloMiniConfig config_{};
    std::uint32_t step_{0};
    std::size_t num_gaussians_{0};

    // First moment: same shape as parameters (num_gaussians * kGradientsPerGaussian)
    std::vector<float> m_{};

    // Rank-1 auxiliary: one scalar per group per gaussian
    // Layout: [gaussian_0_group_0, gaussian_0_group_1, ..., gaussian_N_group_5]
    std::vector<float> r_{};

    // Temporary buffer for clipped gradients
    std::vector<float> grad_clip_{};
};

}  // namespace trainer
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_TRAINER_APOLLO_MINI_OPTIMIZER_H
