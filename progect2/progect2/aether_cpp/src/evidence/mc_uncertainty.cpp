// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/evidence/mc_uncertainty.h"

#include <algorithm>
#include <cmath>
#include <vector>

namespace aether {
namespace evidence {

namespace {

// Splitmix64 PRNG (deterministic, no library dependency)
std::uint64_t splitmix64(std::uint64_t& state) {
    state += 0x9e3779b97f4a7c15ULL;
    std::uint64_t z = state;
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}

// Uniform [0,1) from PRNG
double uniform01(std::uint64_t& state) {
    return static_cast<double>(splitmix64(state) >> 11) * 0x1.0p-53;
}

// Sample from Beta(alpha, beta) using Joehnk's algorithm
// For alpha, beta >= 1, use ratio of gammas approximation
double sample_beta(double alpha, double beta, std::uint64_t& state) {
    if (alpha <= 0.0 || beta <= 0.0) return 0.5;

    // Simplified: use Kumaraswamy approximation for speed
    // For Beta(a,b), approximate via inverse CDF of Kumaraswamy(a,b)
    // F^{-1}(u) = (1 - (1-u)^{1/b})^{1/a}
    const double u = std::max(1e-15, std::min(1.0 - 1e-15, uniform01(state)));
    const double v = 1.0 - std::pow(1.0 - u, 1.0 / beta);
    return std::pow(v, 1.0 / alpha);
}

// Compute confidence interval from sorted samples
MCConfidenceInterval compute_ci(std::vector<double>& samples) {
    MCConfidenceInterval ci;
    if (samples.empty()) return ci;

    std::sort(samples.begin(), samples.end());
    const std::size_t n = samples.size();

    ci.p5 = samples[std::min(static_cast<std::size_t>(n * 0.05), n - 1)];
    ci.median = samples[n / 2];
    ci.p95 = samples[std::min(static_cast<std::size_t>(n * 0.95), n - 1)];

    double sum = 0.0;
    for (double v : samples) sum += v;
    ci.mean = sum / static_cast<double>(n);

    double var_sum = 0.0;
    for (double v : samples) {
        const double d = v - ci.mean;
        var_sum += d * d;
    }
    ci.std_dev = std::sqrt(var_sum / static_cast<double>(n));

    return ci;
}

}  // namespace

int mc_uncertainty_estimate(
    const MCCellObservation* cells,
    std::size_t cell_count,
    const MCUncertaintyConfig& config,
    MCUncertaintyResult* out_result) {

    if (!cells || !out_result || cell_count == 0) return -1;

    *out_result = {};

    const std::size_t K = config.realtime_mode
        ? config.realtime_iterations
        : config.num_iterations;

    std::vector<double> cov_samples(K);
    std::vector<double> bel_samples(K);
    std::vector<double> pl_samples(K);
    std::vector<double> lyap_samples(K);
    std::vector<double> pac_samples(K);

    std::uint64_t rng = config.seed;
    double prev_std = 1e9;
    std::size_t actual_k = K;

    for (std::size_t k = 0; k < K; ++k) {
        double num = 0.0, denom = 0.0;
        double bel_num = 0.0, pl_num = 0.0;
        double lyap = 0.0, pac_sum = 0.0;

        for (std::size_t i = 0; i < cell_count; ++i) {
            const auto& cell = cells[i];
            if (cell.excluded || cell.area_weight <= 0.0) continue;

            // Beta distribution parameters from observation counts
            const double p = std::max(0.01, std::min(0.99, cell.occupied));
            const double n = std::max(1.0, static_cast<double>(cell.view_count));
            const double alpha = p * n + 1.0;
            const double beta_param = (1.0 - p) * n + 1.0;

            // Sample p_i ~ Beta(alpha, beta)
            const double p_i = sample_beta(alpha, beta_param, rng);
            const double w = cell.area_weight;

            num += p_i * w;
            denom += w;
            bel_num += p_i * w;
            pl_num += std::min(1.0, p_i + cell.unknown) * w;

            const double gap = 1.0 - p_i;
            lyap += gap * gap;

            // PAC: KL divergence bound
            const double ps = std::max(0.01, std::min(0.99, p_i));
            const double kl = ps * std::log(2.0 * ps) +
                              (1.0 - ps) * std::log(2.0 * (1.0 - ps));
            pac_sum += std::exp(-n * std::max(0.0, kl));
        }

        const double safe_denom = std::max(denom, 1e-12);
        cov_samples[k] = num / safe_denom;
        bel_samples[k] = bel_num / safe_denom;
        pl_samples[k] = pl_num / safe_denom;
        lyap_samples[k] = lyap;
        pac_samples[k] = pac_sum;

        // Early convergence check every 20 iterations
        if (k > 20 && k % 20 == 0) {
            double sum = 0.0;
            for (std::size_t j = 0; j <= k; ++j) sum += cov_samples[j];
            const double mean = sum / static_cast<double>(k + 1);
            double vsum = 0.0;
            for (std::size_t j = 0; j <= k; ++j) {
                const double d = cov_samples[j] - mean;
                vsum += d * d;
            }
            const double cur_std = std::sqrt(vsum / static_cast<double>(k + 1));
            if (std::abs(cur_std - prev_std) < config.convergence_threshold) {
                actual_k = k + 1;
                out_result->converged = true;
                break;
            }
            prev_std = cur_std;
        }
    }

    // Trim to actual iterations run
    cov_samples.resize(actual_k);
    bel_samples.resize(actual_k);
    pl_samples.resize(actual_k);
    lyap_samples.resize(actual_k);
    pac_samples.resize(actual_k);

    out_result->coverage_ci = compute_ci(cov_samples);
    out_result->belief_ci = compute_ci(bel_samples);
    out_result->plausibility_ci = compute_ci(pl_samples);
    out_result->lyapunov_rate_ci = compute_ci(lyap_samples);
    out_result->pac_bound_ci = compute_ci(pac_samples);
    out_result->iterations_run = actual_k;

    return 0;
}

}  // namespace evidence
}  // namespace aether
