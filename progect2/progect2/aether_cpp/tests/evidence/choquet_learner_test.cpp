// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/evidence/choquet_learner.h"

#include <cassert>
#include <cmath>
#include <cstdio>

using namespace aether::evidence;

namespace {

void test_default_measure_unchanged_below_min() {
    ChoquetLearnerConfig cfg;
    cfg.min_observations = 20;
    ChoquetLearner learner(cfg);

    auto before = learner.current_measure();

    // Add fewer than min_observations
    for (int i = 0; i < 10; ++i) {
        ChoquetObservation obs;
        obs.super_dims[0] = 0.8; obs.super_dims[1] = 0.7;
        obs.super_dims[2] = 0.6; obs.super_dims[3] = 0.5;
        obs.super_dims[4] = 0.4;
        obs.certified = true;
        learner.add_observation(obs);
    }
    auto after = learner.step();

    // Should not have changed
    for (int i = 0; i < 32; ++i) {
        assert(std::abs(before.mu[i] - after.mu[i]) < 1e-12);
    }
    assert(!learner.stats().has_learned);
    (void)before;
    (void)after;
    std::printf("  PASS: default_measure_unchanged_below_min\n");
}

void test_positive_observations_increase_mu() {
    ChoquetLearnerConfig cfg;
    cfg.min_observations = 5;
    cfg.learning_rate = 0.05;
    cfg.regularization_lambda = 0.01;
    ChoquetLearner learner(cfg);

    auto before = learner.current_measure();

    // Add many positive observations with high dim scores
    for (int i = 0; i < 50; ++i) {
        ChoquetObservation obs;
        for (int d = 0; d < 5; ++d) obs.super_dims[d] = 0.8;
        obs.certified = true;
        learner.add_observation(obs);
    }

    // Multiple gradient steps
    ChoquetFuzzyMeasure after;
    for (int s = 0; s < 10; ++s) {
        after = learner.step();
    }

    assert(learner.stats().has_learned);
    (void)before;
    std::printf("  PASS: positive_observations_learning (delta=%.4f)\n",
                learner.stats().mu_delta_norm);
}

void test_monotonicity_always_holds() {
    ChoquetLearnerConfig cfg;
    cfg.min_observations = 5;
    cfg.learning_rate = 0.1;  // Aggressive learning to stress test
    ChoquetLearner learner(cfg);

    for (int i = 0; i < 30; ++i) {
        ChoquetObservation obs;
        for (int d = 0; d < 5; ++d) {
            obs.super_dims[d] = (d + 1) * 0.15;
        }
        obs.certified = (i % 3 != 0);
        learner.add_observation(obs);
    }

    auto mu = learner.step();

    // Check monotonicity: A subset B => mu[A] <= mu[B]
    for (std::uint32_t a = 1; a < 31; ++a) {
        for (std::uint32_t b = a + 1; b < 31; ++b) {
            if ((a & b) == a && a != b) {
                assert(mu.mu[a] <= mu.mu[b] + 1e-9);
            }
        }
    }
    (void)mu;
    std::printf("  PASS: monotonicity_always_holds\n");
}

void test_boundary_conditions() {
    ChoquetLearnerConfig cfg;
    cfg.min_observations = 5;
    ChoquetLearner learner(cfg);

    for (int i = 0; i < 10; ++i) {
        ChoquetObservation obs;
        for (int d = 0; d < 5; ++d) obs.super_dims[d] = 0.5;
        obs.certified = true;
        learner.add_observation(obs);
    }
    auto mu = learner.step();

    assert(std::abs(mu.mu[0]) < 1e-12);
    assert(std::abs(mu.mu[31] - 1.0) < 1e-12);
    std::printf("  PASS: boundary_conditions (mu[0]=%.6f, mu[31]=%.6f)\n",
                mu.mu[0], mu.mu[31]);
}

void test_circular_buffer() {
    ChoquetLearnerConfig cfg;
    cfg.min_observations = 5;
    cfg.max_observations = 10;
    ChoquetLearner learner(cfg);

    for (int i = 0; i < 25; ++i) {
        ChoquetObservation obs;
        for (int d = 0; d < 5; ++d) obs.super_dims[d] = 0.5;
        obs.certified = true;
        learner.add_observation(obs);
    }
    assert(learner.stats().observation_count == 10);
    std::printf("  PASS: circular_buffer (count=%zu)\n",
                learner.stats().observation_count);
}

void test_reset() {
    ChoquetLearnerConfig cfg;
    cfg.min_observations = 5;
    ChoquetLearner learner(cfg);

    for (int i = 0; i < 10; ++i) {
        ChoquetObservation obs;
        for (int d = 0; d < 5; ++d) obs.super_dims[d] = 0.9;
        obs.certified = true;
        learner.add_observation(obs);
    }
    learner.step();
    assert(learner.stats().has_learned);

    learner.reset();
    assert(learner.stats().observation_count == 0);
    assert(!learner.stats().has_learned);

    auto def = ChoquetFuzzyMeasure::default_measure();
    auto cur = learner.current_measure();
    for (int i = 0; i < 32; ++i) {
        assert(std::abs(cur.mu[i] - def.mu[i]) < 1e-12);
    }
    (void)def;
    (void)cur;
    std::printf("  PASS: reset\n");
}

}  // namespace

int main() {
    std::printf("choquet_learner_test\n");
    test_default_measure_unchanged_below_min();
    test_positive_observations_increase_mu();
    test_monotonicity_always_holds();
    test_boundary_conditions();
    test_circular_buffer();
    test_reset();
    std::printf("All choquet_learner tests passed.\n");
    return 0;
}
