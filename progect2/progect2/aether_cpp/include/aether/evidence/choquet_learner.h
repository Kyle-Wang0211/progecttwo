// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_EVIDENCE_CHOQUET_LEARNER_H
#define AETHER_EVIDENCE_CHOQUET_LEARNER_H

#ifdef __cplusplus

#include "aether/evidence/evidence_state_machine.h"

#include <cstddef>
#include <cstdint>
#include <vector>

namespace aether {
namespace evidence {

/// A single observation for Choquet measure learning.
struct ChoquetObservation {
    double super_dims[5]{};
    bool certified{false};
    double weight{1.0};
};

/// Configuration for the Choquet learner.
struct ChoquetLearnerConfig {
    double learning_rate{0.01};
    double regularization_lambda{0.1};
    std::size_t min_observations{20};
    std::size_t max_observations{1000};
    double monotonicity_epsilon{1e-6};
};

/// Statistics from the learner.
struct ChoquetLearnerStats {
    std::size_t observation_count{0};
    double log_likelihood{0.0};
    double mu_delta_norm{0.0};
    bool has_learned{false};
};

/// Online learner for Choquet integral fuzzy measure.
///
/// Adapts the 32-value fuzzy measure mu[] via gradient descent on
/// the log-likelihood of observed S5 certification outcomes.
/// Enforces monotonicity constraint: A subset B => mu[A] <= mu[B].
/// Regularizes toward handcrafted defaults to prevent divergence.
class ChoquetLearner {
public:
    explicit ChoquetLearner(ChoquetLearnerConfig config = {});

    void reset();

    /// Record an observation (super_dims + S5 outcome).
    void add_observation(const ChoquetObservation& obs);

    /// Run one gradient step. Returns updated measure.
    ChoquetFuzzyMeasure step();

    /// Current learned measure.
    const ChoquetFuzzyMeasure& current_measure() const;

    /// Learning statistics.
    ChoquetLearnerStats stats() const;

private:
    ChoquetLearnerConfig config_;
    ChoquetFuzzyMeasure current_mu_;
    ChoquetFuzzyMeasure default_mu_;
    std::vector<ChoquetObservation> observations_;
    std::size_t obs_cursor_{0};
    ChoquetLearnerStats stats_;

    /// Compute Choquet integral sensitivity dC/dmu for a specific coalition.
    double choquet_sensitivity(const double super_dims[5],
                               std::uint32_t coalition) const;

    /// Evaluate Choquet integral with current measure.
    double choquet_eval(const double super_dims[5]) const;

    /// Project mu onto the monotone cone.
    void project_monotone();

    /// Check if coalition a is a subset of coalition b.
    static bool is_subset(std::uint32_t a, std::uint32_t b);

    /// Count set bits.
    static int popcount(std::uint32_t x);
};

}  // namespace evidence
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_EVIDENCE_CHOQUET_LEARNER_H
