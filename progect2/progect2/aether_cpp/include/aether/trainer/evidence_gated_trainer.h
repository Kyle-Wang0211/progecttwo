// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_TRAINER_EVIDENCE_GATED_TRAINER_H
#define AETHER_TRAINER_EVIDENCE_GATED_TRAINER_H

#ifdef __cplusplus

#include "aether/core/status.h"
#include "aether/evidence/evidence_state_machine.h"

#include <cstdint>

namespace aether {
namespace trainer {

/// Configuration for Evidence-Gated Training (EGT).
///
/// Maps each evidence ColorState to a training strategy that controls
/// learning rate scaling, iteration multipliers, and densification
/// permissions.  The design principle: high-confidence regions (S5)
/// are frozen, while low-coverage regions (S0-S2) receive accelerated
/// training and prioritized densification.
struct EGTConfig {
    // ── S5 (kOriginal): Frozen — capture complete ──
    float s5_lr_scale{0.0f};
    bool  s5_allow_densify{false};

    // ── S4 (kWhite): Refinement only ──
    float s4_lr_scale{0.25f};
    bool  s4_allow_densify{false};

    // ── S3 (kLightGray): Normal training ──
    float s3_lr_scale{1.0f};
    bool  s3_allow_densify{true};

    // ── S0-S2 (kBlack, kDarkGray): Accelerated training ──
    float s012_lr_scale{2.0f};
    float s012_iter_multiplier{2.0f};
    bool  s012_prioritize_densify{true};

    // ── Uncertainty-based thresholds ──
    /// Gaussians with uncertainty above this threshold are kept for
    /// potential densification (not pruned).
    float uncertainty_densify_threshold{0.6f};

    /// Gaussians with uncertainty below this threshold are candidates
    /// for pruning (low information value).
    float uncertainty_prune_threshold{0.1f};
};

/// Decision output from the EGT strategy computation.
///
/// Encapsulates all training decisions for a given region based on
/// its evidence state and associated quality signals.
struct EGTDecision {
    bool  should_train{false};
    float lr_scale{0.0f};
    float iter_multiplier{1.0f};
    bool  allow_densify{false};
    bool  prioritize_densify{false};
    bool  should_prune_uncertain{false};
};

/// Compute the training strategy for a region given its evidence state
/// and associated quality signals.
///
/// @param state         Current evidence ColorState (S0-S5).
/// @param choquet_score Choquet integral aggregation score [0,1].
///                      Near 1.0 indicates convergence → reduce LR.
/// @param f8_uncertainty DS uncertainty width (Pl - Bel) [0,1].
///                      Controls prune/densify decisions.
/// @param psnr_local   Local PSNR estimate (unused reserve, for future
///                      per-region quality gating).
/// @param config       EGT configuration parameters.
/// @return             Training decision for this region.
EGTDecision compute_egt_strategy(
    evidence::ColorState state,
    float choquet_score,
    float f8_uncertainty,
    float psnr_local,
    const EGTConfig& config);

}  // namespace trainer
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_TRAINER_EVIDENCE_GATED_TRAINER_H
