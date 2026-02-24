// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/evidence/mc_uncertainty.h"

#include <cassert>
#include <cmath>
#include <cstdio>
#include <vector>

using namespace aether::evidence;

namespace {

void test_high_evidence_tight_ci() {
    std::vector<MCCellObservation> cells(20);
    for (auto& c : cells) {
        c.occupied = 0.95;
        c.unknown = 0.05;
        c.view_count = 50;
        c.area_weight = 1.0;
    }
    MCUncertaintyConfig cfg;
    cfg.num_iterations = 100;
    MCUncertaintyResult result;
    int rc = mc_uncertainty_estimate(cells.data(), cells.size(), cfg, &result);
    assert(rc == 0);
    assert(result.coverage_ci.mean > 0.7);
    assert(result.coverage_ci.p95 - result.coverage_ci.p5 < 0.3);
    (void)rc;
    std::printf("  PASS: high_evidence_tight_ci [%.3f, %.3f]\n",
                result.coverage_ci.p5, result.coverage_ci.p95);
}

void test_mixed_cells_wider_ci() {
    std::vector<MCCellObservation> cells(20);
    for (int i = 0; i < 20; ++i) {
        cells[i].occupied = (i % 2 == 0) ? 0.9 : 0.1;
        cells[i].unknown = 1.0 - cells[i].occupied;
        cells[i].view_count = 3;
        cells[i].area_weight = 1.0;
    }
    MCUncertaintyConfig cfg;
    cfg.num_iterations = 100;
    MCUncertaintyResult result;
    int rc = mc_uncertainty_estimate(cells.data(), cells.size(), cfg, &result);
    assert(rc == 0);
    // Wider CI expected with mixed + low views
    assert(result.coverage_ci.std_dev > 0.0);
    (void)rc;
    std::printf("  PASS: mixed_cells_wider_ci (std=%.4f)\n",
                result.coverage_ci.std_dev);
}

void test_deterministic_same_seed() {
    std::vector<MCCellObservation> cells(10);
    for (auto& c : cells) {
        c.occupied = 0.5; c.unknown = 0.5;
        c.view_count = 10; c.area_weight = 1.0;
    }
    MCUncertaintyConfig cfg;
    cfg.seed = 12345;
    cfg.num_iterations = 50;

    MCUncertaintyResult r1, r2;
    mc_uncertainty_estimate(cells.data(), cells.size(), cfg, &r1);
    mc_uncertainty_estimate(cells.data(), cells.size(), cfg, &r2);

    assert(std::abs(r1.coverage_ci.mean - r2.coverage_ci.mean) < 1e-12);
    std::printf("  PASS: deterministic_same_seed\n");
}

void test_realtime_fewer_iterations() {
    std::vector<MCCellObservation> cells(10);
    for (auto& c : cells) {
        c.occupied = 0.5; c.unknown = 0.5;
        c.view_count = 10; c.area_weight = 1.0;
    }
    MCUncertaintyConfig cfg;
    cfg.realtime_mode = true;
    cfg.realtime_iterations = 30;
    cfg.num_iterations = 200;

    MCUncertaintyResult result;
    mc_uncertainty_estimate(cells.data(), cells.size(), cfg, &result);
    assert(result.iterations_run <= 30);
    std::printf("  PASS: realtime_fewer_iterations (k=%zu)\n",
                result.iterations_run);
}

void test_empty_cells_error() {
    MCUncertaintyConfig cfg;
    MCUncertaintyResult result;
    assert(mc_uncertainty_estimate(nullptr, 0, cfg, &result) < 0);
    (void)cfg;
    (void)result;
    std::printf("  PASS: empty_cells_error\n");
}

void test_excluded_cells_skipped() {
    std::vector<MCCellObservation> cells(10);
    for (auto& c : cells) {
        c.occupied = 0.8; c.unknown = 0.2;
        c.view_count = 20; c.area_weight = 1.0;
        c.excluded = true;
    }
    MCUncertaintyConfig cfg;
    cfg.num_iterations = 50;
    MCUncertaintyResult result;
    int rc = mc_uncertainty_estimate(cells.data(), cells.size(), cfg, &result);
    assert(rc == 0);
    // All excluded → zero coverage
    assert(result.coverage_ci.mean < 0.01);
    (void)rc;
    std::printf("  PASS: excluded_cells_skipped\n");
}

}  // namespace

int main() {
    std::printf("mc_uncertainty_test\n");
    test_high_evidence_tight_ci();
    test_mixed_cells_wider_ci();
    test_deterministic_same_seed();
    test_realtime_fewer_iterations();
    test_empty_cells_error();
    test_excluded_cells_skipped();
    std::printf("All mc_uncertainty tests passed.\n");
    return 0;
}
