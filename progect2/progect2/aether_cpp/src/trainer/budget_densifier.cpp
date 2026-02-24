// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/budget_densifier.h"
#include "aether/evidence/evidence_state_machine.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <numeric>
#include <utility>
#include <vector>

namespace aether {
namespace trainer {
namespace {

/// Scale threshold separating small (clone) from large (split) gaussians.
/// Gaussians with max-axis scale below this are considered "small".
constexpr float kSmallScaleThreshold = 0.02f;

/// Pair of (index, score) for sorting candidates.
struct IndexScore {
    std::uint32_t index{0};
    float score{0.0f};
};

/// Compare by score descending (highest first).
inline bool compare_score_desc(const IndexScore& a, const IndexScore& b) {
    return a.score > b.score;
}

/// Compare by score ascending (lowest first).
inline bool compare_score_asc(const IndexScore& a, const IndexScore& b) {
    return a.score < b.score;
}

}  // namespace

BudgetDensifier::BudgetDensifier(const DensifyConfig& config)
    : config_(config) {
    // Sanitize configuration.
    if (config_.gaussian_budget == 0) {
        config_.gaussian_budget = 30000;
    }
    if (config_.budget_error_tolerance < 0.0f) {
        config_.budget_error_tolerance = 0.02f;
    }
    if (config_.grad_threshold < 0.0f) {
        config_.grad_threshold = 0.0002f;
    }
}

bool BudgetDensifier::should_clone(float grad_norm, float scale) const {
    return grad_norm > config_.grad_threshold && scale < kSmallScaleThreshold;
}

bool BudgetDensifier::should_split(float grad_norm, float scale) const {
    return grad_norm > config_.grad_threshold && scale >= kSmallScaleThreshold;
}

bool BudgetDensifier::should_prune(float opacity, float scale) const {
    return opacity < config_.opacity_prune_threshold ||
           scale < config_.scale_prune_threshold;
}

core::Status BudgetDensifier::evaluate(
    const float* grad_norms,
    const float* opacities,
    const float* scales,
    const std::uint8_t* evidence_states,
    const float* uncertainties,
    std::size_t num_gaussians,
    DensifyResult* result) {

    if (grad_norms == nullptr || opacities == nullptr || scales == nullptr ||
        evidence_states == nullptr || uncertainties == nullptr ||
        result == nullptr) {
        return core::Status::kInvalidArgument;
    }

    if (num_gaussians == 0) {
        result->clone_indices.clear();
        result->split_indices.clear();
        result->prune_indices.clear();
        result->final_count = 0;
        return core::Status::kOk;
    }

    // ── Phase 1: Score each gaussian for clone/split/prune ──

    // Temporary scored candidates for sorting.
    std::vector<IndexScore> clone_candidates;
    std::vector<IndexScore> split_candidates;
    std::vector<IndexScore> prune_candidates;

    clone_candidates.reserve(num_gaussians / 8);
    split_candidates.reserve(num_gaussians / 8);
    prune_candidates.reserve(num_gaussians / 8);

    for (std::size_t i = 0; i < num_gaussians; ++i) {
        const float grad = grad_norms[i];
        const float opacity = opacities[i];
        const float scale = scales[i];
        const auto evidence = static_cast<evidence::ColorState>(evidence_states[i]);
        const float uncertainty = uncertainties[i];

        // Evidence override: S5 (kOriginal) gaussians are never densified.
        const bool is_frozen = (evidence == evidence::ColorState::kOriginal);

        // ── Prune evaluation ──
        if (should_prune(opacity, scale)) {
            // Uncertainty override: high-uncertainty gaussians are kept
            // because they might benefit from densification.
            if (uncertainty > config_.uncertainty_densify_threshold) {
                // Keep — high uncertainty, potential for improvement.
            } else {
                prune_candidates.push_back(
                    IndexScore{static_cast<std::uint32_t>(i), opacity});
            }
        }

        // ── Clone/Split evaluation (skip frozen S5 gaussians) ──
        if (!is_frozen) {
            if (should_clone(grad, scale)) {
                clone_candidates.push_back(
                    IndexScore{static_cast<std::uint32_t>(i), grad});
            } else if (should_split(grad, scale)) {
                split_candidates.push_back(
                    IndexScore{static_cast<std::uint32_t>(i), grad});
            }
        }
    }

    // Sort clone/split by gradient (highest first) for budget prioritization.
    std::sort(clone_candidates.begin(), clone_candidates.end(), compare_score_desc);
    std::sort(split_candidates.begin(), split_candidates.end(), compare_score_desc);

    // Sort prune by opacity (lowest first) — most expendable first.
    std::sort(prune_candidates.begin(), prune_candidates.end(), compare_score_asc);

    // Populate result with initial candidates.
    result->clone_indices.clear();
    result->split_indices.clear();
    result->prune_indices.clear();

    result->clone_indices.reserve(clone_candidates.size());
    for (const auto& c : clone_candidates) {
        result->clone_indices.push_back(c.index);
    }

    result->split_indices.reserve(split_candidates.size());
    for (const auto& c : split_candidates) {
        result->split_indices.push_back(c.index);
    }

    result->prune_indices.reserve(prune_candidates.size());
    for (const auto& c : prune_candidates) {
        result->prune_indices.push_back(c.index);
    }

    // ── Phase 2: Budget enforcement ──
    enforce_budget(grad_norms, opacities, num_gaussians, result);

    return core::Status::kOk;
}

void BudgetDensifier::enforce_budget(
    const float* grad_norms,
    const float* opacities,
    std::size_t num_gaussians,
    DensifyResult* result) const {

    const std::size_t budget = config_.gaussian_budget;
    const float tolerance = config_.budget_error_tolerance;
    const std::size_t budget_upper =
        budget + static_cast<std::size_t>(
            static_cast<float>(budget) * tolerance);
    const std::size_t budget_lower =
        (tolerance >= 1.0f) ? 0 :
        budget - std::min(budget,
            static_cast<std::size_t>(
                static_cast<float>(budget) * tolerance));

    // Projected count: current + clones + splits (each split adds 1 net)
    //                  - prunes.
    // Clone: duplicates one gaussian → +1.
    // Split: replaces one gaussian with two → +1 net.
    const std::size_t num_clones = result->clone_indices.size();
    const std::size_t num_splits = result->split_indices.size();
    const std::size_t num_prunes = result->prune_indices.size();

    // Use signed arithmetic to handle underflow.
    auto projected = static_cast<std::int64_t>(num_gaussians)
                   + static_cast<std::int64_t>(num_clones)
                   + static_cast<std::int64_t>(num_splits)
                   - static_cast<std::int64_t>(num_prunes);

    // ── Over budget: prune more (lowest opacity first) ──
    if (projected > static_cast<std::int64_t>(budget_upper)) {
        const std::int64_t excess = projected - static_cast<std::int64_t>(budget);

        // First: trim densification lists (remove lowest-gradient clones/splits).
        std::size_t trimmed = 0;

        // Trim clones from the back (lowest gradient, since sorted desc).
        while (!result->clone_indices.empty() &&
               static_cast<std::int64_t>(trimmed) < excess) {
            result->clone_indices.pop_back();
            ++trimmed;
        }

        // Trim splits from the back.
        while (!result->split_indices.empty() &&
               static_cast<std::int64_t>(trimmed) < excess) {
            result->split_indices.pop_back();
            ++trimmed;
        }

        // If still over budget, add more prune candidates.
        if (static_cast<std::int64_t>(trimmed) < excess) {
            // Build a list of all non-pruned, non-densified gaussians
            // sorted by opacity ascending for additional pruning.
            std::vector<IndexScore> additional_prune;
            additional_prune.reserve(
                static_cast<std::size_t>(excess - static_cast<std::int64_t>(trimmed)));

            // Create a set of already-pruned indices for fast lookup.
            std::vector<bool> is_marked(num_gaussians, false);
            for (std::uint32_t idx : result->prune_indices) {
                if (idx < num_gaussians) {
                    is_marked[idx] = true;
                }
            }
            for (std::uint32_t idx : result->clone_indices) {
                if (idx < num_gaussians) {
                    is_marked[idx] = true;
                }
            }
            for (std::uint32_t idx : result->split_indices) {
                if (idx < num_gaussians) {
                    is_marked[idx] = true;
                }
            }

            for (std::size_t i = 0; i < num_gaussians; ++i) {
                if (!is_marked[i]) {
                    additional_prune.push_back(
                        IndexScore{static_cast<std::uint32_t>(i), opacities[i]});
                }
            }
            std::sort(additional_prune.begin(), additional_prune.end(),
                      compare_score_asc);

            const std::size_t need = static_cast<std::size_t>(
                excess - static_cast<std::int64_t>(trimmed));
            const std::size_t add_count = std::min(need, additional_prune.size());
            for (std::size_t i = 0; i < add_count; ++i) {
                result->prune_indices.push_back(additional_prune[i].index);
            }
        }
    }

    // ── Under budget: allow more clones (highest gradient first) ──
    if (projected < static_cast<std::int64_t>(budget_lower) &&
        num_gaussians > 0) {
        const std::int64_t deficit = static_cast<std::int64_t>(budget) - projected;

        // Build candidates not already in clone/split/prune lists.
        std::vector<bool> is_marked(num_gaussians, false);
        for (std::uint32_t idx : result->clone_indices) {
            if (idx < num_gaussians) {
                is_marked[idx] = true;
            }
        }
        for (std::uint32_t idx : result->split_indices) {
            if (idx < num_gaussians) {
                is_marked[idx] = true;
            }
        }
        for (std::uint32_t idx : result->prune_indices) {
            if (idx < num_gaussians) {
                is_marked[idx] = true;
            }
        }

        std::vector<IndexScore> extra_clones;
        extra_clones.reserve(static_cast<std::size_t>(deficit));

        for (std::size_t i = 0; i < num_gaussians; ++i) {
            if (!is_marked[i] && grad_norms[i] > 0.0f) {
                extra_clones.push_back(
                    IndexScore{static_cast<std::uint32_t>(i), grad_norms[i]});
            }
        }
        std::sort(extra_clones.begin(), extra_clones.end(), compare_score_desc);

        const std::size_t add_count = std::min(
            static_cast<std::size_t>(deficit), extra_clones.size());
        for (std::size_t i = 0; i < add_count; ++i) {
            result->clone_indices.push_back(extra_clones[i].index);
        }
    }

    // Compute final projected count.
    const auto final_projected =
        static_cast<std::int64_t>(num_gaussians)
        + static_cast<std::int64_t>(result->clone_indices.size())
        + static_cast<std::int64_t>(result->split_indices.size())
        - static_cast<std::int64_t>(result->prune_indices.size());

    result->final_count = static_cast<std::size_t>(std::max(
        static_cast<std::int64_t>(0), final_projected));
}

}  // namespace trainer
}  // namespace aether
