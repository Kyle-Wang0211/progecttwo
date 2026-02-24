// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/bayesian_quality_network.h"

#include <algorithm>
#include <cmath>

namespace aether {
namespace quality {

constexpr double BayesianQualityNetwork::kBinCenters[5];

// ────────────────────────────────────────────────────────────────────
// Construction / reset
// ────────────────────────────────────────────────────────────────────

BayesianQualityNetwork::BayesianQualityNetwork(BayesianNetworkConfig config)
    : config_(config) {
    // Default initialization: identity CPTs (observation passes through)
    const double default_weights[6] = {0.28, 0.22, 0.20, 0.15, 0.10, 0.05};
    initialize_from_weights(default_weights);
}

void BayesianQualityNetwork::reset() {
    const double default_weights[6] = {0.28, 0.22, 0.20, 0.15, 0.10, 0.05};
    initialize_from_weights(default_weights);
}

// ────────────────────────────────────────────────────────────────────
// CPT initialization from linear weights
// ────────────────────────────────────────────────────────────────────

void BayesianQualityNetwork::initialize_from_weights(const double weights[6]) {
    // For observation nodes (0-5): CPT is peaked Gaussian around identity
    // Larger weight → tighter CPT (more influence on fusion)
    for (int node = 0; node < 6; ++node) {
        const double sigma = 1.5 - weights[node] * 2.0;  // [0.94, 1.44]
        const double sigma_sq = sigma * sigma;

        for (int parent = 0; parent < 5; ++parent) {
            double row_sum = 0.0;
            for (int child = 0; child < 5; ++child) {
                const double d = static_cast<double>(parent - child);
                const double val = std::exp(-0.5 * d * d / sigma_sq);
                cpts_[node].table[parent * 5 + child] = val;
                row_sum += val;
            }
            // Normalize row
            if (row_sum > 1e-15) {
                for (int child = 0; child < 5; ++child) {
                    cpts_[node].table[parent * 5 + child] /= row_sum;
                }
            }
        }
        cpts_[node].weights[node] = weights[node];
    }

    // Fusion node: weighted combination of parents
    double wsum = 0.0;
    for (int i = 0; i < 6; ++i) {
        cpts_[6].weights[i] = weights[i];
        wsum += weights[i];
    }
    if (wsum > 1e-15) {
        for (int i = 0; i < 6; ++i) {
            cpts_[6].weights[i] /= wsum;
        }
    }

    // Risk node: CPT maps high fusion to low risk
    for (int fb = 0; fb < 5; ++fb) {
        double row_sum = 0.0;
        for (int rb = 0; rb < 5; ++rb) {
            // Risk is roughly 1-fusion, so inverse peaked
            const int inv_fb = 4 - fb;
            const double d = static_cast<double>(inv_fb - rb);
            const double val = std::exp(-0.5 * d * d / 1.5);
            cpts_[7].table[fb * 5 + rb] = val;
            row_sum += val;
        }
        if (row_sum > 1e-15) {
            for (int rb = 0; rb < 5; ++rb) {
                cpts_[7].table[fb * 5 + rb] /= row_sum;
            }
        }
    }
}

// ────────────────────────────────────────────────────────────────────
// Inference
// ────────────────────────────────────────────────────────────────────

BayesianQualityResult BayesianQualityNetwork::infer(
    const double component_scores[6]) const {

    double beliefs[8][5] = {};

    // Initialize observation beliefs as delta distributions
    for (int node = 0; node < 6; ++node) {
        const double s = std::max(0.0, std::min(1.0, component_scores[node]));
        // Soft discretization: spread probability to adjacent bins
        const double bin_f = s * 4.0;  // [0, 4]
        const int bin_low = std::max(0, std::min(3, static_cast<int>(bin_f)));
        const int bin_high = bin_low + 1;
        const double frac = bin_f - static_cast<double>(bin_low);

        for (int b = 0; b < 5; ++b) beliefs[node][b] = 0.0;
        beliefs[node][bin_low] = 1.0 - frac;
        if (bin_high < 5) beliefs[node][bin_high] = frac;
    }

    // Forward + backward passes
    forward_pass(component_scores, beliefs);
    for (int iter = 0; iter < config_.belief_prop_iterations; ++iter) {
        backward_pass(beliefs);
    }

    // Extract results
    BayesianQualityResult result;
    for (int i = 0; i < 6; ++i) {
        result.component_posteriors[i] = belief_to_posterior(beliefs[i]);
    }
    result.fusion_posterior = belief_to_posterior(beliefs[6]);
    result.risk_posterior = belief_to_posterior(beliefs[7]);

    result.fusion_score = result.fusion_posterior.mean;
    result.fusion_variance = result.fusion_posterior.variance;
    result.risk_score = result.risk_posterior.mean;
    result.risk_variance = result.risk_posterior.variance;

    return result;
}

// ────────────────────────────────────────────────────────────────────
// Forward pass
// ────────────────────────────────────────────────────────────────────

void BayesianQualityNetwork::forward_pass(
    const double /*observations*/[6],
    double beliefs[8][5]) const {

    // Fusion node: mean-field factored approximation
    // P(fusion_bin) = sum over parent beliefs, weighted by CPT weights
    for (int fb = 0; fb < 5; ++fb) {
        double weighted = 0.0;
        for (int parent = 0; parent < 6; ++parent) {
            weighted += beliefs[parent][fb] * cpts_[6].weights[parent];
        }
        beliefs[6][fb] = weighted;
    }
    normalize5(beliefs[6]);

    // Risk node: standard CPT propagation from fusion
    for (int rb = 0; rb < 5; ++rb) {
        double sum = 0.0;
        for (int fb = 0; fb < 5; ++fb) {
            sum += cpts_[7].table[fb * 5 + rb] * beliefs[6][fb];
        }
        beliefs[7][rb] = sum;
    }
    normalize5(beliefs[7]);
}

// ────────────────────────────────────────────────────────────────────
// Backward pass (loopy belief propagation refinement)
// ────────────────────────────────────────────────────────────────────

void BayesianQualityNetwork::backward_pass(
    double beliefs[8][5]) const {

    // Back-propagate fusion beliefs to component nodes
    // P_new(component_bin) ∝ P_old(component_bin) * sum_fb P(fusion_fb|component_bin) * P(fusion_fb)
    for (int parent = 0; parent < 6; ++parent) {
        double new_belief[5] = {};
        for (int pb = 0; pb < 5; ++pb) {
            double msg = 0.0;
            for (int fb = 0; fb < 5; ++fb) {
                // P(fusion_fb | parent=pb): use weight-scaled correlation
                const double d = static_cast<double>(pb - fb);
                const double corr = std::exp(-0.5 * d * d / 2.0);
                msg += corr * beliefs[6][fb];
            }
            new_belief[pb] = beliefs[parent][pb] * msg;
        }
        normalize5(new_belief);
        // Damped update (0.3 new, 0.7 old) for stability
        for (int b = 0; b < 5; ++b) {
            beliefs[parent][b] = 0.7 * beliefs[parent][b] + 0.3 * new_belief[b];
        }
        normalize5(beliefs[parent]);
    }

    // Re-run forward after backward update
    for (int fb = 0; fb < 5; ++fb) {
        double weighted = 0.0;
        for (int parent = 0; parent < 6; ++parent) {
            weighted += beliefs[parent][fb] * cpts_[6].weights[parent];
        }
        beliefs[6][fb] = weighted;
    }
    normalize5(beliefs[6]);

    for (int rb = 0; rb < 5; ++rb) {
        double sum = 0.0;
        for (int fb = 0; fb < 5; ++fb) {
            sum += cpts_[7].table[fb * 5 + rb] * beliefs[6][fb];
        }
        beliefs[7][rb] = sum;
    }
    normalize5(beliefs[7]);
}

// ────────────────────────────────────────────────────────────────────
// Utilities
// ────────────────────────────────────────────────────────────────────

BayesPosterior BayesianQualityNetwork::belief_to_posterior(
    const double belief[5]) {
    BayesPosterior p;
    // Mean
    for (int i = 0; i < 5; ++i) {
        p.mean += belief[i] * kBinCenters[i];
    }
    // Variance
    for (int i = 0; i < 5; ++i) {
        const double d = kBinCenters[i] - p.mean;
        p.variance += belief[i] * d * d;
    }
    // Credible interval via cumulative distribution
    double cdf = 0.0;
    bool found_low = false;
    for (int i = 0; i < 5; ++i) {
        cdf += belief[i];
        if (!found_low && cdf >= 0.05) {
            p.credible_low = kBinCenters[i];
            found_low = true;
        }
        if (cdf >= 0.95) {
            p.credible_high = kBinCenters[i];
            break;
        }
    }
    if (!found_low) p.credible_low = kBinCenters[0];
    if (p.credible_high < p.credible_low) p.credible_high = kBinCenters[4];
    return p;
}

void BayesianQualityNetwork::normalize5(double p[5]) {
    double sum = 0.0;
    for (int i = 0; i < 5; ++i) sum += p[i];
    if (sum > 1e-15) {
        for (int i = 0; i < 5; ++i) p[i] /= sum;
    } else {
        // Uniform if degenerate
        for (int i = 0; i < 5; ++i) p[i] = 0.2;
    }
}

}  // namespace quality
}  // namespace aether
