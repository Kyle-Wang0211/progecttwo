// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/second_order_accel.h"

#include <algorithm>
#include <cmath>
#include <cstddef>

namespace aether {
namespace trainer {

SecondOrderAccelerator::SecondOrderAccelerator(const SecondOrderConfig& config)
    : config_(config) {
    // Sanitize configuration.
    if (config_.hessian_ema_alpha <= 0.0f || config_.hessian_ema_alpha > 1.0f) {
        config_.hessian_ema_alpha = 0.1f;
    }
    if (config_.damping < 0.0f) {
        config_.damping = 1e-4f;
    }
    if (config_.max_step_size <= 0.0f) {
        config_.max_step_size = 0.1f;
    }
}

core::Status SecondOrderAccelerator::init(
    std::size_t num_gaussians,
    std::size_t params_per_gaussian) {

    if (num_gaussians == 0 || params_per_gaussian == 0) {
        return core::Status::kInvalidArgument;
    }

    total_params_ = num_gaussians * params_per_gaussian;
    hessian_diag_.assign(total_params_, 0.0f);
    initialized_ = true;

    return core::Status::kOk;
}

core::Status SecondOrderAccelerator::update_hessian(
    const float* gradients,
    std::size_t count) {

    if (gradients == nullptr) {
        return core::Status::kInvalidArgument;
    }
    if (!initialized_ || count != total_params_) {
        return core::Status::kInvalidArgument;
    }

    // EMA update of diagonal Hessian estimate:
    //   h[i] = alpha * g[i]^2 + (1 - alpha) * h[i]
    //
    // This approximates the diagonal of the Fisher information matrix
    // (or equivalently, the Gauss-Newton Hessian for least-squares).
    const float alpha = config_.hessian_ema_alpha;
    const float one_minus_alpha = 1.0f - alpha;

    for (std::size_t i = 0; i < count; ++i) {
        const float g = gradients[i];
        hessian_diag_[i] = alpha * (g * g) + one_minus_alpha * hessian_diag_[i];
    }

    return core::Status::kOk;
}

core::Status SecondOrderAccelerator::apply(
    float* gradients,
    std::size_t count) {

    if (gradients == nullptr) {
        return core::Status::kInvalidArgument;
    }
    if (!initialized_ || count != total_params_) {
        return core::Status::kInvalidArgument;
    }

    // Preconditioned gradient:
    //   precond_g[i] = g[i] / (sqrt(h[i]) + damping)
    //
    // This is equivalent to a diagonal Newton step where the Hessian
    // is approximated by the EMA of squared gradients.  Clamping
    // prevents catastrophically large steps.
    const float damping = config_.damping;
    const float max_step = config_.max_step_size;

    for (std::size_t i = 0; i < count; ++i) {
        const float h_sqrt = std::sqrt(hessian_diag_[i]);
        const float denominator = h_sqrt + damping;

        float preconditioned = gradients[i] / denominator;

        // Clamp to max step size.
        preconditioned = std::max(-max_step, std::min(max_step, preconditioned));

        gradients[i] = preconditioned;
    }

    return core::Status::kOk;
}

void SecondOrderAccelerator::reset() {
    if (initialized_) {
        std::fill(hessian_diag_.begin(), hessian_diag_.end(), 0.0f);
    }
    initialized_ = false;
    total_params_ = 0;
    hessian_diag_.clear();
}

}  // namespace trainer
}  // namespace aether
