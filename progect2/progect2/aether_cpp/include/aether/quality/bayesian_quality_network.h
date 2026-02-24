// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_QUALITY_BAYESIAN_QUALITY_NETWORK_H
#define AETHER_QUALITY_BAYESIAN_QUALITY_NETWORK_H

#ifdef __cplusplus

#include <cstdint>

namespace aether {
namespace quality {

/// Node indices in the quality DAG.
enum class BayesNodeId : std::uint8_t {
    kGeometry = 0,
    kCrossValidation = 1,
    kCapture = 2,
    kEvidence = 3,
    kTransport = 4,
    kSecurity = 5,
    kFusion = 6,
    kRisk = 7,
    kCount = 8
};

/// 5-bin discretized conditional probability table.
struct BayesCPT {
    static constexpr int kBins = 5;
    /// P(child_bin | parent_bin) for single-parent edges.
    /// Row = parent_bin, Column = child_bin.
    double table[kBins * kBins]{};

    /// For multi-parent fusion node: blend weight per parent.
    double weights[8]{};
};

/// Posterior distribution summary.
struct BayesPosterior {
    double mean{0.0};
    double variance{0.0};
    double credible_low{0.0};    // 5th percentile
    double credible_high{0.0};   // 95th percentile
};

/// Result of Bayesian inference.
struct BayesianQualityResult {
    double fusion_score{0.0};
    double fusion_variance{0.0};
    double risk_score{1.0};
    double risk_variance{0.0};
    BayesPosterior component_posteriors[6]{};
    BayesPosterior fusion_posterior{};
    BayesPosterior risk_posterior{};
};

/// Configuration for the Bayesian network.
struct BayesianNetworkConfig {
    double prior_alpha{1.0};        // Beta prior pseudo-count
    double prior_beta{1.0};
    int belief_prop_iterations{3};  // Forward-backward iterations
};

/// Bayesian quality network for 6-dimensional quality assessment.
///
/// Replaces independent weighted-sum fusion with DAG-based belief
/// propagation that models real dependencies between quality dimensions
/// (e.g., thermal affects motion affects blur).
///
/// Uses 5-bin discretization with conditional probability tables (CPTs)
/// and mean-field factored approximation for tractable inference.
class BayesianQualityNetwork {
public:
    explicit BayesianQualityNetwork(BayesianNetworkConfig config = {});

    /// Initialize CPTs from existing linear weights (backward compatible).
    void initialize_from_weights(const double weights[6]);

    /// Run inference: forward pass + optional backward refinement.
    BayesianQualityResult infer(const double component_scores[6]) const;

    void reset();

private:
    BayesianNetworkConfig config_;
    BayesCPT cpts_[8]{};

    static constexpr double kBinCenters[5] = {0.1, 0.3, 0.5, 0.7, 0.9};

    /// Forward pass: propagate observations through DAG.
    void forward_pass(const double observations[6],
                      double beliefs[8][5]) const;

    /// Backward pass: refine component beliefs from fusion evidence.
    void backward_pass(double beliefs[8][5]) const;

    /// Convert 5-bin belief distribution to continuous posterior.
    static BayesPosterior belief_to_posterior(const double belief[5]);

    /// Normalize a 5-element probability distribution.
    static void normalize5(double p[5]);
};

}  // namespace quality
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_QUALITY_BAYESIAN_QUALITY_NETWORK_H
