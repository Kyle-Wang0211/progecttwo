// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/apollo_mini_optimizer.h"
#include "aether/core/nan_quarantine.h"

#include <cmath>
#include <cstring>

namespace aether {
namespace trainer {

// ---------------------------------------------------------------------------
// Parameter group layout helpers
// ---------------------------------------------------------------------------

void ApolloMiniOptimizer::group_range(ParamGroup group,
                                       std::size_t* offset,
                                       std::size_t* size) {
    switch (group) {
        case ParamGroup::kPosition:
            *offset = kOffsetPosition; *size = kSizePosition; break;
        case ParamGroup::kScale:
            *offset = kOffsetScale; *size = kSizeScale; break;
        case ParamGroup::kOpacity:
            *offset = kOffsetOpacity; *size = kSizeOpacity; break;
        case ParamGroup::kRotation:
            *offset = kOffsetRotation; *size = kSizeRotation; break;
        case ParamGroup::kSHDC:
            *offset = kOffsetSHDC; *size = kSizeSHDC; break;
        case ParamGroup::kSHRest:
            *offset = kOffsetSHRest; *size = kSizeSHRest; break;
        default:
            *offset = 0; *size = 0; break;
    }
}

float ApolloMiniOptimizer::get_lr(ParamGroup group) const {
    switch (group) {
        case ParamGroup::kPosition: return config_.lr_position;
        case ParamGroup::kScale:    return config_.lr_scale;
        case ParamGroup::kOpacity:  return config_.lr_opacity;
        case ParamGroup::kRotation: return config_.lr_rotation;
        case ParamGroup::kSHDC:     return config_.lr_sh;
        case ParamGroup::kSHRest:   return config_.lr_sh;
        default:                    return 0.0f;
    }
}

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------

core::Status ApolloMiniOptimizer::init(const ApolloMiniConfig& config,
                                        std::size_t num_gaussians) {
    if (num_gaussians == 0) {
        return core::Status::kInvalidArgument;
    }

    config_ = config;
    step_ = 0;
    num_gaussians_ = num_gaussians;

    const std::size_t param_count = num_gaussians * kGradientsPerGaussian;
    const std::size_t r_count = num_gaussians * kParamGroupCount;

    m_.assign(param_count, 0.0f);
    r_.assign(r_count, 0.0f);
    grad_clip_.resize(param_count, 0.0f);

    return core::Status::kOk;
}

// ---------------------------------------------------------------------------
// Gradient clipping
// ---------------------------------------------------------------------------

float ApolloMiniOptimizer::clip_gradients(float* grad_copy,
                                           std::size_t count) const {
    // Compute global L2 norm of the gradient buffer
    double norm_sq = 0.0;
    for (std::size_t i = 0; i < count; ++i) {
        const double g = static_cast<double>(grad_copy[i]);
        norm_sq += g * g;
    }
    const float grad_norm = static_cast<float>(std::sqrt(norm_sq));

    if (grad_norm > config_.grad_clip_norm && grad_norm > 0.0f) {
        const float scale = config_.grad_clip_norm / grad_norm;
        for (std::size_t i = 0; i < count; ++i) {
            grad_copy[i] *= scale;
        }
        return scale;
    }
    return 1.0f;
}

// ---------------------------------------------------------------------------
// Optimization step
// ---------------------------------------------------------------------------

core::Status ApolloMiniOptimizer::step(float* parameters,
                                        const float* gradients,
                                        std::size_t num_gaussians) {
    if (parameters == nullptr || gradients == nullptr) {
        return core::Status::kInvalidArgument;
    }
    if (num_gaussians == 0 || num_gaussians > num_gaussians_) {
        return core::Status::kInvalidArgument;
    }

    const std::size_t param_count = num_gaussians * kGradientsPerGaussian;

    // Check for NaN in gradients; skip step if found
    if (core::detect_nan(gradients, param_count)) {
        return core::Status::kOk;  // silently skip corrupted step
    }

    // Copy gradients for clipping
    std::memcpy(grad_clip_.data(), gradients, param_count * sizeof(float));

    // Clip gradients by global norm
    clip_gradients(grad_clip_.data(), param_count);

    // Increment step counter
    ++step_;

    // Bias correction denominators
    const float beta1_pow = std::pow(config_.beta1, static_cast<float>(step_));
    const float beta2_pow = std::pow(config_.beta2, static_cast<float>(step_));
    const float m_correction = 1.0f / (1.0f - beta1_pow);
    const float v_correction = 1.0f / (1.0f - beta2_pow);

    // Process each Gaussian
    for (std::size_t gi = 0; gi < num_gaussians; ++gi) {
        const std::size_t base = gi * kGradientsPerGaussian;
        const std::size_t r_base = gi * kParamGroupCount;

        // Process each parameter group
        for (std::size_t pg = 0; pg < kParamGroupCount; ++pg) {
            const ParamGroup group = static_cast<ParamGroup>(pg);
            std::size_t g_offset = 0;
            std::size_t g_size = 0;
            group_range(group, &g_offset, &g_size);
            if (g_size == 0) {
                continue;
            }

            const float lr = get_lr(group);
            const std::size_t param_start = base + g_offset;

            // Compute group gradient norm for rank-1 auxiliary
            float group_grad_norm = 0.0f;
            for (std::size_t j = 0; j < g_size; ++j) {
                const float g = grad_clip_[param_start + j];
                group_grad_norm += g * g;
            }
            group_grad_norm = std::sqrt(group_grad_norm);

            // Update rank-1 auxiliary: r = beta2 * r + (1-beta2) * |grad_group_norm|
            const std::size_t r_idx = r_base + pg;
            r_[r_idx] = config_.beta2 * r_[r_idx] +
                         (1.0f - config_.beta2) * group_grad_norm;

            // v_approx = r^2
            const float v_approx = r_[r_idx] * r_[r_idx];

            // Bias-corrected second moment approximation
            const float v_hat = v_approx * v_correction;

            // Denominator for parameter update
            const float denom = std::sqrt(v_hat) + config_.eps;

            // Update each parameter in the group
            for (std::size_t j = 0; j < g_size; ++j) {
                const std::size_t idx = param_start + j;

                // Update first moment: m = beta1 * m + (1-beta1) * grad
                m_[idx] = config_.beta1 * m_[idx] +
                          (1.0f - config_.beta1) * grad_clip_[idx];

                // Bias-corrected first moment
                const float m_hat = m_[idx] * m_correction;

                // Parameter update: param -= lr * m_hat / (sqrt(v_hat) + eps)
                parameters[idx] -= lr * m_hat / denom;
            }
        }
    }

    return core::Status::kOk;
}

// ---------------------------------------------------------------------------
// Reset
// ---------------------------------------------------------------------------

void ApolloMiniOptimizer::reset() {
    step_ = 0;
    if (!m_.empty()) {
        std::memset(m_.data(), 0, m_.size() * sizeof(float));
    }
    if (!r_.empty()) {
        std::memset(r_.data(), 0, r_.size() * sizeof(float));
    }
}

// ---------------------------------------------------------------------------
// Memory usage
// ---------------------------------------------------------------------------

std::size_t ApolloMiniOptimizer::memory_bytes() const {
    const std::size_t m_bytes = m_.capacity() * sizeof(float);
    const std::size_t r_bytes = r_.capacity() * sizeof(float);
    const std::size_t clip_bytes = grad_clip_.capacity() * sizeof(float);
    return m_bytes + r_bytes + clip_bytes + sizeof(ApolloMiniOptimizer);
}

}  // namespace trainer
}  // namespace aether
