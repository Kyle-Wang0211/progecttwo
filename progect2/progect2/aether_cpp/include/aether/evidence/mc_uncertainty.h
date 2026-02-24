// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_EVIDENCE_MC_UNCERTAINTY_H
#define AETHER_EVIDENCE_MC_UNCERTAINTY_H

#ifdef __cplusplus

#include <cstddef>
#include <cstdint>

namespace aether {
namespace evidence {

struct MCConfidenceInterval {
    double median{0.0};
    double p5{0.0};
    double p95{0.0};
    double mean{0.0};
    double std_dev{0.0};
};

struct MCUncertaintyResult {
    MCConfidenceInterval coverage_ci{};
    MCConfidenceInterval belief_ci{};
    MCConfidenceInterval plausibility_ci{};
    MCConfidenceInterval lyapunov_rate_ci{};
    MCConfidenceInterval pac_bound_ci{};
    std::size_t iterations_run{0};
    bool converged{false};
};

struct MCUncertaintyConfig {
    std::size_t num_iterations{200};
    std::size_t realtime_iterations{50};
    std::uint64_t seed{42};
    double convergence_threshold{0.001};
    bool realtime_mode{false};
};

/// Cell observation data for MC resampling.
struct MCCellObservation {
    double occupied{0.0};       // DS belief mass for occupied
    double unknown{1.0};        // DS unknown mass
    int view_count{0};
    double area_weight{1.0};
    bool excluded{false};
};

/// Run Monte Carlo uncertainty estimation over coverage cells.
/// Returns 0 on success, negative on error.
int mc_uncertainty_estimate(
    const MCCellObservation* cells,
    std::size_t cell_count,
    const MCUncertaintyConfig& config,
    MCUncertaintyResult* out_result);

}  // namespace evidence
}  // namespace aether

#endif  // __cplusplus
#endif  // AETHER_EVIDENCE_MC_UNCERTAINTY_H
