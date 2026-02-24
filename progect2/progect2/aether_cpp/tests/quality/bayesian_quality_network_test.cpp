// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/bayesian_quality_network.h"

#include <cassert>
#include <cmath>
#include <cstdio>

using namespace aether::quality;

namespace {

void test_all_high_scores() {
    BayesianQualityNetwork net;
    double scores[6] = {0.8, 0.8, 0.8, 0.8, 0.8, 0.8};
    auto r = net.infer(scores);
    assert(r.fusion_score > 0.5);
    assert(r.fusion_variance >= 0.0);
    assert(r.risk_score < 0.8);
    std::printf("  PASS: all_high_scores (fusion=%.3f, risk=%.3f)\n",
                r.fusion_score, r.risk_score);
}

void test_all_low_scores() {
    BayesianQualityNetwork net;
    double scores[6] = {0.1, 0.1, 0.1, 0.1, 0.1, 0.1};
    auto r = net.infer(scores);
    assert(r.fusion_score < 0.5);
    assert(r.risk_score > 0.3);
    std::printf("  PASS: all_low_scores (fusion=%.3f, risk=%.3f)\n",
                r.fusion_score, r.risk_score);
}

void test_variance_positive_for_mixed() {
    BayesianQualityNetwork net;
    double scores[6] = {0.9, 0.1, 0.8, 0.2, 0.7, 0.3};
    auto r = net.infer(scores);
    assert(r.fusion_variance > 0.0);
    assert(r.risk_variance > 0.0);
    std::printf("  PASS: variance_positive_for_mixed (var=%.4f)\n",
                r.fusion_variance);
}

void test_credible_interval_brackets_mean() {
    BayesianQualityNetwork net;
    double scores[6] = {0.6, 0.7, 0.5, 0.8, 0.4, 0.9};
    auto r = net.infer(scores);
    assert(r.fusion_posterior.credible_low <= r.fusion_posterior.mean);
    assert(r.fusion_posterior.credible_high >= r.fusion_posterior.mean);
    std::printf("  PASS: credible_interval_brackets_mean [%.2f, %.2f, %.2f]\n",
                r.fusion_posterior.credible_low,
                r.fusion_posterior.mean,
                r.fusion_posterior.credible_high);
}

void test_custom_weights() {
    BayesianQualityNetwork net;
    double custom[6] = {0.5, 0.1, 0.1, 0.1, 0.1, 0.1};
    net.initialize_from_weights(custom);
    // With high geometry weight, high geo should boost fusion more
    double scores_geo_high[6] = {0.9, 0.5, 0.5, 0.5, 0.5, 0.5};
    double scores_geo_low[6] = {0.1, 0.5, 0.5, 0.5, 0.5, 0.5};
    auto r_high = net.infer(scores_geo_high);
    auto r_low = net.infer(scores_geo_low);
    assert(r_high.fusion_score > r_low.fusion_score);
    std::printf("  PASS: custom_weights (high=%.3f > low=%.3f)\n",
                r_high.fusion_score, r_low.fusion_score);
}

void test_nan_safety() {
    BayesianQualityNetwork net;
    double scores[6] = {0.5, 0.5, 0.5, 0.5, 0.5, 0.5};
    auto r = net.infer(scores);
    assert(std::isfinite(r.fusion_score));
    assert(std::isfinite(r.risk_score));
    assert(std::isfinite(r.fusion_variance));
    (void)r;
    std::printf("  PASS: nan_safety\n");
}

void test_reset() {
    BayesianQualityNetwork net;
    double custom[6] = {0.5, 0.1, 0.1, 0.1, 0.1, 0.1};
    net.initialize_from_weights(custom);
    net.reset();
    double scores[6] = {0.8, 0.8, 0.8, 0.8, 0.8, 0.8};
    auto r = net.infer(scores);
    assert(std::isfinite(r.fusion_score));
    (void)r;
    std::printf("  PASS: reset\n");
}

}  // namespace

int main() {
    std::printf("bayesian_quality_network_test\n");
    test_all_high_scores();
    test_all_low_scores();
    test_variance_positive_for_mixed();
    test_credible_interval_brackets_mean();
    test_custom_weights();
    test_nan_safety();
    test_reset();
    std::printf("All bayesian_quality_network tests passed.\n");
    return 0;
}
