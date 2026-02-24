// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_TRAINER_BUDGET_DENSIFIER_H
#define AETHER_TRAINER_BUDGET_DENSIFIER_H

#ifdef __cplusplus

#include "aether/core/status.h"

#include <cstddef>
#include <cstdint>
#include <vector>

namespace aether {
namespace trainer {

/// Configuration for budget-controlled densification and pruning.
///
/// Controls the gaussian budget, gradient-based densification thresholds,
/// and opacity/scale-based pruning criteria.  Evidence and uncertainty
/// overrides allow the system to preserve high-uncertainty gaussians
/// that may benefit from densification.
struct DensifyConfig {
    /// Maximum number of gaussians allowed.
    std::size_t gaussian_budget{30000};

    /// Fractional tolerance around the budget (0.02 = 2%).
    float budget_error_tolerance{0.02f};

    /// Minimum gradient norm to trigger clone or split.
    float grad_threshold{0.0002f};

    /// Gaussians with opacity below this are prune candidates.
    float opacity_prune_threshold{0.005f};

    /// Gaussians with scale below this are prune candidates.
    float scale_prune_threshold{0.01f};

    /// Densification is evaluated every this many iterations.
    std::uint32_t densify_interval{100};

    /// Uncertainty above this → keep for densification (don't prune).
    float uncertainty_densify_threshold{0.6f};

    /// Uncertainty below this → candidate for pruning.
    float uncertainty_prune_threshold{0.1f};

    /// Maximum allowed gaussian scale (prevents blow-up).
    float max_gaussian_scale{0.05f};

    /// Minimum allowed gaussian opacity after densification.
    float min_gaussian_opacity{0.01f};
};

/// Result of a densification/pruning evaluation pass.
struct DensifyResult {
    /// Indices of gaussians to clone (small + high gradient).
    std::vector<std::uint32_t> clone_indices;

    /// Indices of gaussians to split (large + high gradient).
    std::vector<std::uint32_t> split_indices;

    /// Indices of gaussians to prune (low opacity/scale).
    std::vector<std::uint32_t> prune_indices;

    /// Projected gaussian count after applying all operations.
    std::size_t final_count{0};
};

/// Budget-controlled densification and pruning engine.
///
/// Two-phase algorithm:
///   Phase 1 — Score each gaussian for clone/split/prune candidacy
///             based on gradient norms, opacity, scale, evidence state,
///             and uncertainty.
///   Phase 2 — Enforce the gaussian budget by adjusting prune/clone
///             lists to keep the total count within budget ± tolerance.
///
/// Thread safety: NOT thread-safe.  Caller must synchronize.
class BudgetDensifier {
public:
    explicit BudgetDensifier(const DensifyConfig& config);

    /// Evaluate densification and pruning decisions for all gaussians.
    ///
    /// @param grad_norms       Per-gaussian gradient norms (size = num_gaussians).
    /// @param opacities        Per-gaussian opacities (size = num_gaussians).
    /// @param scales           Per-gaussian max-axis scales (size = num_gaussians).
    /// @param evidence_states  Per-gaussian evidence state as uint8 (ColorState).
    /// @param uncertainties    Per-gaussian DS uncertainty width [0,1].
    /// @param num_gaussians    Number of gaussians.
    /// @param result           Output densification result.
    /// @return                 kOk on success, kInvalidArgument if null pointers.
    core::Status evaluate(
        const float* grad_norms,
        const float* opacities,
        const float* scales,
        const std::uint8_t* evidence_states,
        const float* uncertainties,
        std::size_t num_gaussians,
        DensifyResult* result);

private:
    /// Returns true if the gaussian should be cloned (small + high grad).
    bool should_clone(float grad_norm, float scale) const;

    /// Returns true if the gaussian should be split (large + high grad).
    bool should_split(float grad_norm, float scale) const;

    /// Returns true if the gaussian should be pruned (low opacity/scale).
    bool should_prune(float opacity, float scale) const;

    /// Adjust clone/split/prune lists to enforce budget constraints.
    void enforce_budget(
        const float* grad_norms,
        const float* opacities,
        std::size_t num_gaussians,
        DensifyResult* result) const;

    DensifyConfig config_;
};

}  // namespace trainer
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_TRAINER_BUDGET_DENSIFIER_H
