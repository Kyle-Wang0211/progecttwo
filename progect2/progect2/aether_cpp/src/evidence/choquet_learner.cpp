// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/evidence/choquet_learner.h"

#include <algorithm>
#include <cmath>

namespace aether {
namespace evidence {

// ────────────────────────────────────────────────────────────────────
// Construction / reset
// ────────────────────────────────────────────────────────────────────

ChoquetLearner::ChoquetLearner(ChoquetLearnerConfig config)
    : config_(config)
    , current_mu_(ChoquetFuzzyMeasure::default_measure())
    , default_mu_(ChoquetFuzzyMeasure::default_measure()) {}

void ChoquetLearner::reset() {
    current_mu_ = default_mu_;
    observations_.clear();
    obs_cursor_ = 0;
    stats_ = {};
}

// ────────────────────────────────────────────────────────────────────
// Observation ingestion
// ────────────────────────────────────────────────────────────────────

void ChoquetLearner::add_observation(const ChoquetObservation& obs) {
    if (observations_.size() < config_.max_observations) {
        observations_.push_back(obs);
    } else {
        observations_[obs_cursor_ % config_.max_observations] = obs;
    }
    ++obs_cursor_;
    stats_.observation_count = std::min(obs_cursor_, config_.max_observations);
}

// ────────────────────────────────────────────────────────────────────
// Gradient step
// ────────────────────────────────────────────────────────────────────

ChoquetFuzzyMeasure ChoquetLearner::step() {
    if (stats_.observation_count < config_.min_observations) {
        stats_.has_learned = false;
        return current_mu_;
    }

    const std::size_t n = std::min(observations_.size(), config_.max_observations);
    double total_ll = 0.0;

    // Gradient descent on each mutable coalition (1..30)
    for (std::uint32_t c = 1; c < 31; ++c) {
        double grad = 0.0;

        for (std::size_t i = 0; i < n; ++i) {
            const auto& obs = observations_[i];
            const double C = choquet_eval(obs.super_dims);
            // Logistic mapping: sigma(10 * (C - 0.5))
            const double logit = 10.0 * (C - 0.5);
            const double sigma = 1.0 / (1.0 + std::exp(-logit));

            // dL/dmu[c] = (y - sigma) * dC/dmu[c]
            const double y = obs.certified ? 1.0 : 0.0;
            const double dC = choquet_sensitivity(obs.super_dims, c);
            grad += (y - sigma) * dC * obs.weight;

            if (c == 1) {
                // Accumulate log-likelihood (once per observation)
                const double eps = 1e-12;
                total_ll += y * std::log(sigma + eps) +
                            (1.0 - y) * std::log(1.0 - sigma + eps);
            }
        }

        grad = -grad / static_cast<double>(n);

        // Regularization toward defaults
        const double reg = config_.regularization_lambda *
                           (current_mu_.mu[c] - default_mu_.mu[c]);

        current_mu_.mu[c] -= config_.learning_rate * (grad + reg);
    }

    // Enforce boundary conditions
    current_mu_.mu[0] = 0.0;
    current_mu_.mu[31] = 1.0;

    // Clamp to [0, 1]
    for (int c = 1; c < 31; ++c) {
        current_mu_.mu[c] = std::max(0.0, std::min(1.0, current_mu_.mu[c]));
    }

    // Project onto monotone cone
    project_monotone();

    // Update stats
    stats_.log_likelihood = total_ll / static_cast<double>(n);
    double delta_norm = 0.0;
    for (int c = 0; c < 32; ++c) {
        const double d = current_mu_.mu[c] - default_mu_.mu[c];
        delta_norm += d * d;
    }
    stats_.mu_delta_norm = std::sqrt(delta_norm);
    stats_.has_learned = true;

    return current_mu_;
}

// ────────────────────────────────────────────────────────────────────
// Choquet evaluation and sensitivity
// ────────────────────────────────────────────────────────────────────

double ChoquetLearner::choquet_eval(const double super_dims[5]) const {
    // Sort dimensions ascending, track original indices
    struct IndexedVal { double val; int idx; };
    IndexedVal sorted[5];
    for (int i = 0; i < 5; ++i) {
        sorted[i].val = super_dims[i];
        sorted[i].idx = i;
    }
    std::sort(sorted, sorted + 5,
              [](const IndexedVal& a, const IndexedVal& b) {
                  return a.val < b.val;
              });

    double result = 0.0;
    for (int k = 0; k < 5; ++k) {
        // Tail coalition: {sorted[k].idx, sorted[k+1].idx, ..., sorted[4].idx}
        std::uint32_t tail = 0;
        for (int j = k; j < 5; ++j) {
            tail |= (1u << static_cast<unsigned>(sorted[j].idx));
        }
        const double delta = sorted[k].val - (k > 0 ? sorted[k - 1].val : 0.0);
        result += delta * current_mu_.mu[tail];
    }
    return result;
}

double ChoquetLearner::choquet_sensitivity(
    const double super_dims[5], std::uint32_t coalition) const {

    // dC/dmu[A] = x_{pi(k)} - x_{pi(k-1)} where A is the tail at step k
    struct IndexedVal { double val; int idx; };
    IndexedVal sorted[5];
    for (int i = 0; i < 5; ++i) {
        sorted[i].val = super_dims[i];
        sorted[i].idx = i;
    }
    std::sort(sorted, sorted + 5,
              [](const IndexedVal& a, const IndexedVal& b) {
                  return a.val < b.val;
              });

    for (int k = 0; k < 5; ++k) {
        std::uint32_t tail = 0;
        for (int j = k; j < 5; ++j) {
            tail |= (1u << static_cast<unsigned>(sorted[j].idx));
        }
        if (tail == coalition) {
            return sorted[k].val - (k > 0 ? sorted[k - 1].val : 0.0);
        }
    }
    return 0.0;
}

// ────────────────────────────────────────────────────────────────────
// Monotonicity projection
// ────────────────────────────────────────────────────────────────────

void ChoquetLearner::project_monotone() {
    // Iterate until convergence (typically 2-3 passes)
    for (int pass = 0; pass < 10; ++pass) {
        bool changed = false;
        for (std::uint32_t a = 1; a < 31; ++a) {
            for (std::uint32_t b = a + 1; b < 31; ++b) {
                if (!is_subset(a, b)) continue;
                if (current_mu_.mu[a] > current_mu_.mu[b] +
                    config_.monotonicity_epsilon) {
                    const double avg =
                        (current_mu_.mu[a] + current_mu_.mu[b]) * 0.5;
                    current_mu_.mu[a] = avg;
                    current_mu_.mu[b] = avg;
                    changed = true;
                }
            }
        }
        // Also enforce mu[singleton] <= mu[full]
        for (std::uint32_t c = 1; c < 31; ++c) {
            if (current_mu_.mu[c] > current_mu_.mu[31]) {
                current_mu_.mu[c] = current_mu_.mu[31];
            }
            if (current_mu_.mu[c] < current_mu_.mu[0]) {
                current_mu_.mu[c] = current_mu_.mu[0];
            }
        }
        if (!changed) break;
    }
}

const ChoquetFuzzyMeasure& ChoquetLearner::current_measure() const {
    return current_mu_;
}

ChoquetLearnerStats ChoquetLearner::stats() const {
    return stats_;
}

bool ChoquetLearner::is_subset(std::uint32_t a, std::uint32_t b) {
    return (a & b) == a && a != b;
}

int ChoquetLearner::popcount(std::uint32_t x) {
    int count = 0;
    while (x) { count += x & 1; x >>= 1; }
    return count;
}

}  // namespace evidence
}  // namespace aether
