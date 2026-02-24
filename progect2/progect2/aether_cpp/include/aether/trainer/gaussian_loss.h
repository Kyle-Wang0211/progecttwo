// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_TRAINER_GAUSSIAN_LOSS_H
#define AETHER_TRAINER_GAUSSIAN_LOSS_H

#ifdef __cplusplus

#include <cstddef>
#include <cstdint>

#include "aether/core/status.h"

namespace aether {
namespace trainer {

// ═══════════════════════════════════════════════════════════════════════
// LossConfig: Hyperparameters for multi-component 3DGS training loss
// ═══════════════════════════════════════════════════════════════════════
// L_total = alpha * L_rgb + beta(t) * L_depth + gamma * L_normal
//         + delta * L_pbr + epsilon * L_reg
//
// beta(t) decays exponentially: beta(t) = weight_depth * exp(-decay_rate * t / T)

struct LossConfig {
    float weight_rgb{1.0f};              // alpha
    float weight_depth{0.5f};            // beta (initial, before decay)
    float weight_normal{0.2f};           // gamma
    float weight_pbr{0.1f};             // delta
    float weight_regularize{0.01f};      // epsilon
    float ssim_lambda{0.2f};            // weight of SSIM within L_rgb
    float depth_weight_decay_rate{0.005f};  // exponential decay rate for beta
    float scale_reg_weight{0.01f};       // regularization on gaussian scale
    float opacity_reg_weight{0.001f};    // regularization on gaussian opacity
    float scaffold_binding_weight{0.01f}; // penalty for gaussian-scaffold dist
};

// ═══════════════════════════════════════════════════════════════════════
// LossInput: All data required for a single loss computation
// ═══════════════════════════════════════════════════════════════════════

struct LossInput {
    // Rendered vs ground truth images (width * height * 3 floats, RGB)
    const float* rendered_rgb;
    const float* gt_rgb;
    std::uint32_t width;
    std::uint32_t height;

    // Depth supervision (width * height floats)
    const float* rendered_depth;
    const float* tsdf_depth;

    // Normal supervision (width * height * 3 floats)
    const float* rendered_normal;
    const float* tsdf_normal;

    // Per-pixel depth confidence / noise-aware weight (width * height floats)
    const float* depth_confidence;

    // Gaussian regularization data (num_gaussians * 3 floats each)
    const float* gaussian_scales;      // [num_gaussians * 3]
    const float* gaussian_opacities;   // [num_gaussians]
    const float* gaussian_positions;   // [num_gaussians * 3]
    const float* scaffold_positions;   // [num_gaussians * 3] (anchor points)
    const std::uint32_t* gaussian_scaffold_bindings;  // [num_gaussians]

    std::uint32_t num_gaussians;
    std::uint32_t current_step;
    std::uint32_t total_steps;
};

// ═══════════════════════════════════════════════════════════════════════
// LossResult: Computed loss values per component
// ═══════════════════════════════════════════════════════════════════════

struct LossResult {
    float total_loss;
    float loss_rgb;
    float loss_depth;
    float loss_normal;
    float loss_pbr;
    float loss_regularize;
    float effective_beta;  // decayed depth weight at current step
};

// ═══════════════════════════════════════════════════════════════════════
// GaussianLoss: Multi-component loss function for 3DGS training
// ═══════════════════════════════════════════════════════════════════════
// Computes the weighted sum of:
//   L_rgb:   (1-lambda)*L1 + lambda*(1-SSIM)
//   L_depth: noise-aware weighted depth residual
//   L_normal: cosine distance between rendered and TSDF normals
//   L_pbr:   placeholder (zero) for future PBR inference
//   L_reg:   scale + opacity + scaffold binding regularization

class GaussianLoss {
public:
    explicit GaussianLoss(const LossConfig& config);

    /// Compute all loss components from the given inputs.
    /// \p result must not be null.
    core::Status compute(const LossInput& input, LossResult* result);

private:
    LossConfig config_;

    // Internal helpers
    float compute_l1_loss(const float* rendered, const float* gt,
                          std::size_t pixel_count) const;
    float compute_ssim_loss(const float* rendered, const float* gt,
                            std::uint32_t width, std::uint32_t height) const;
    float compute_depth_loss(const float* rendered_depth,
                             const float* tsdf_depth,
                             const float* confidence,
                             std::size_t pixel_count) const;
    float compute_normal_loss(const float* rendered_normal,
                              const float* tsdf_normal,
                              std::size_t pixel_count) const;
    float compute_regularization_loss(const LossInput& input) const;
};

}  // namespace trainer
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_TRAINER_GAUSSIAN_LOSS_H
