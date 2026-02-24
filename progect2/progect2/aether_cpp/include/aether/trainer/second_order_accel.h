// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_TRAINER_SECOND_ORDER_ACCEL_H
#define AETHER_TRAINER_SECOND_ORDER_ACCEL_H

#ifdef __cplusplus

#include "aether/core/status.h"

#include <cstddef>
#include <vector>

namespace aether {
namespace trainer {

/// Configuration for the 3DGS-squared second-order accelerator.
///
/// Uses a cached EMA-smoothed diagonal Hessian approximation to
/// precondition gradients, yielding ~10x convergence acceleration
/// (approximate Newton's method / natural gradient).
struct SecondOrderConfig {
    /// EMA smoothing factor for Hessian diagonal update.
    /// Lower = more smoothing, higher = more responsive.
    float hessian_ema_alpha{0.1f};

    /// Damping term added to the Hessian diagonal to prevent
    /// division by zero and improve numerical stability.
    float damping{1e-4f};

    /// Maximum allowed step size per parameter after preconditioning.
    /// Prevents catastrophic parameter jumps.
    float max_step_size{0.1f};
};

/// Second-order acceleration via cached diagonal Hessian estimate.
///
/// Implements a diagonal approximation of the Fisher information
/// matrix / Hessian using EMA-smoothed squared gradients.  This
/// is mathematically equivalent to an adaptive learning rate
/// (similar to Adam's second moment) but framed as a second-order
/// preconditioner for the 3DGS optimizer.
///
/// Usage:
///   1. init(num_gaussians, params_per_gaussian)
///   2. For each iteration:
///      a. update_hessian(gradients, count)  — accumulate curvature info
///      b. apply(gradients, count)           — precondition gradients in-place
///
/// Thread safety: NOT thread-safe.  Caller must synchronize.
class SecondOrderAccelerator {
public:
    explicit SecondOrderAccelerator(const SecondOrderConfig& config);

    /// Initialize internal buffers.
    /// @param num_gaussians      Number of gaussians.
    /// @param params_per_gaussian Number of parameters per gaussian.
    /// @return kOk on success.
    core::Status init(std::size_t num_gaussians, std::size_t params_per_gaussian);

    /// Precondition gradients in-place using the cached Hessian diagonal.
    /// @param gradients  Gradient array (count elements), modified in-place.
    /// @param count      Total number of parameters (must match init size).
    /// @return kOk on success, kInvalidArgument if null or size mismatch.
    core::Status apply(float* gradients, std::size_t count);

    /// Update the diagonal Hessian estimate using EMA of squared gradients.
    /// @param gradients  Current gradient array (count elements).
    /// @param count      Total number of parameters.
    /// @return kOk on success.
    core::Status update_hessian(const float* gradients, std::size_t count);

    /// Reset internal state (Hessian diagonal) to zero.
    void reset();

private:
    SecondOrderConfig config_;
    std::vector<float> hessian_diag_;
    std::size_t total_params_{0};
    bool initialized_{false};
};

}  // namespace trainer
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_TRAINER_SECOND_ORDER_ACCEL_H
