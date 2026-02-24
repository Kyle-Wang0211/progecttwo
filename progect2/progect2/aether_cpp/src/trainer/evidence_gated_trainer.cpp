// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/evidence_gated_trainer.h"

#include <algorithm>
#include <cmath>

namespace aether {
namespace trainer {
namespace {

/// Clamp a float to [lo, hi].
inline float clampf(float v, float lo, float hi) {
    return std::max(lo, std::min(hi, v));
}

/// Apply near-convergence LR reduction.
/// When choquet_score > 0.9, reduce lr_scale by 0.5x (linearly
/// interpolated from 1.0x at 0.9 to 0.5x at 1.0).
inline float apply_convergence_scaling(float lr_scale, float choquet_score) {
    if (choquet_score > 0.9f) {
        const float blend = clampf((choquet_score - 0.9f) / 0.1f, 0.0f, 1.0f);
        const float reduction = 1.0f - 0.5f * blend;  // 1.0 → 0.5
        return lr_scale * reduction;
    }
    return lr_scale;
}

/// Determine pruning decision based on uncertainty.
///
/// High uncertainty (> densify_threshold): keep the gaussian — it may
/// benefit from densification, so should_prune_uncertain = false.
/// Low uncertainty (< prune_threshold): the gaussian contributes little
/// new information, so should_prune_uncertain = true.
/// In between: conservative — do not prune.
inline bool compute_uncertainty_prune(
    float f8_uncertainty,
    float densify_threshold,
    float prune_threshold) {
    if (f8_uncertainty > densify_threshold) {
        // High uncertainty → keep for densification.
        return false;
    }
    if (f8_uncertainty < prune_threshold) {
        // Low uncertainty → candidate for pruning.
        return true;
    }
    // In between → conservative, do not prune.
    return false;
}

}  // namespace

EGTDecision compute_egt_strategy(
    evidence::ColorState state,
    float choquet_score,
    float f8_uncertainty,
    float /*psnr_local*/,
    const EGTConfig& config) {

    EGTDecision decision{};

    // Sanitize inputs.
    choquet_score = clampf(choquet_score, 0.0f, 1.0f);
    f8_uncertainty = clampf(f8_uncertainty, 0.0f, 1.0f);

    switch (state) {
        case evidence::ColorState::kOriginal: {
            // S5: Frozen — capture complete, no training allowed.
            decision.should_train       = false;
            decision.lr_scale           = config.s5_lr_scale;
            decision.iter_multiplier    = 1.0f;
            decision.allow_densify      = config.s5_allow_densify;
            decision.prioritize_densify = false;
            break;
        }

        case evidence::ColorState::kWhite: {
            // S4: Refinement only — low LR, no densification.
            decision.should_train       = true;
            decision.lr_scale           = config.s4_lr_scale;
            decision.iter_multiplier    = 1.0f;
            decision.allow_densify      = config.s4_allow_densify;
            decision.prioritize_densify = false;
            break;
        }

        case evidence::ColorState::kLightGray: {
            // S3: Normal training — standard LR, densification enabled.
            decision.should_train       = true;
            decision.lr_scale           = config.s3_lr_scale;
            decision.iter_multiplier    = 1.0f;
            decision.allow_densify      = config.s3_allow_densify;
            decision.prioritize_densify = false;
            break;
        }

        case evidence::ColorState::kDarkGray:
        case evidence::ColorState::kBlack: {
            // S0-S2: Accelerated training — high LR, 2x iterations,
            // prioritized densification.
            decision.should_train       = true;
            decision.lr_scale           = config.s012_lr_scale;
            decision.iter_multiplier    = config.s012_iter_multiplier;
            decision.allow_densify      = true;
            decision.prioritize_densify = config.s012_prioritize_densify;
            break;
        }

        case evidence::ColorState::kUnknown:
        default: {
            // Unknown state — treat as S0 (accelerated) to be safe.
            decision.should_train       = true;
            decision.lr_scale           = config.s012_lr_scale;
            decision.iter_multiplier    = config.s012_iter_multiplier;
            decision.allow_densify      = true;
            decision.prioritize_densify = config.s012_prioritize_densify;
            break;
        }
    }

    // ── Near-convergence LR reduction ──
    // If choquet_score > 0.9, the region is nearly converged.
    // Reduce LR by up to 0.5x to prevent overshooting.
    if (decision.should_train) {
        decision.lr_scale = apply_convergence_scaling(
            decision.lr_scale, choquet_score);
    }

    // ── Uncertainty-based pruning decision ──
    decision.should_prune_uncertain = compute_uncertainty_prune(
        f8_uncertainty,
        config.uncertainty_densify_threshold,
        config.uncertainty_prune_threshold);

    return decision;
}

}  // namespace trainer
}  // namespace aether
