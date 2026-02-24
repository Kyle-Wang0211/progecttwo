// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_QUALITY_MULTIVIEW_PHOTOMETRIC_H
#define AETHER_QUALITY_MULTIVIEW_PHOTOMETRIC_H

#ifdef __cplusplus

#include "aether/quality/photometric_checker.h"

#include <cstddef>
#include <cstdint>
#include <vector>

namespace aether {
namespace quality {

/// A single radiance observation from one camera viewpoint.
struct ViewRadianceSample {
    double rgb[3]{};           // Linear RGB radiance
    double view_dir[3]{};      // Unit vector from surface toward camera
    double normal[3]{};        // Surface normal at observed point
    double luminance{0.0};     // Rec.709 luminance
    LabColor lab{};            // Pre-computed CIELAB for CIEDE2000
    std::int64_t timestamp_ms{0};
};

/// Per-patch record storing multiple view observations.
struct PatchObservationRecord {
    std::uint64_t patch_id{0};
    std::vector<ViewRadianceSample> samples;
};

/// Result of cross-view photometric validation.
struct CrossViewResult {
    double mean_cross_view_delta_e{0.0};   // Mean pairwise CIEDE2000
    double max_cross_view_delta_e{0.0};    // Worst-case pair
    double lambertian_consistency{1.0};    // [0,1] Lambertian model agreement
    std::size_t pair_count{0};             // Number of valid pairs evaluated
    bool is_consistent{true};              // Below threshold?
    double confidence{0.0};               // Based on sample count
};

/// Configuration for multi-view photometric validation.
struct MultiViewPhotometricConfig {
    double max_cross_view_delta_e{8.0};    // CIEDE2000 threshold for consistency
    double lambertian_tolerance{0.3};       // Relative luminance ratio tolerance
    std::size_t min_views_per_patch{2};     // Min views to evaluate
    std::size_t max_views_per_patch{16};    // FIFO cap per patch
    double grazing_angle_cos_min{0.15};     // Reject near-grazing observations
};

/// Multi-view photometric cross-validator.
///
/// Validates that the same surface patch observed from different camera
/// viewpoints has consistent radiance/color, assuming Lambertian reflectance.
/// Inconsistency indicates specular artifacts, lighting changes, or
/// measurement error — penalizing S5 certification confidence.
class MultiViewPhotometricValidator {
public:
    explicit MultiViewPhotometricValidator(
        MultiViewPhotometricConfig config = {});

    void reset();

    /// Add a new observation for a patch.
    void add_observation(std::uint64_t patch_id,
                         const ViewRadianceSample& sample);

    /// Evaluate cross-view consistency for a specific patch.
    CrossViewResult evaluate_patch(std::uint64_t patch_id) const;

    /// Evaluate aggregate cross-view consistency across all patches.
    CrossViewResult evaluate_all() const;

    /// Number of patches currently tracked.
    std::size_t patch_count() const;

private:
    MultiViewPhotometricConfig config_;
    std::vector<PatchObservationRecord> patches_;

    /// Compute Lambertian-expected luminance ratio between two views.
    /// Returns |actual_ratio - expected_ratio| / expected_ratio.
    static double lambertian_ratio_error(
        const ViewRadianceSample& a,
        const ViewRadianceSample& b);

    /// Dot product of 3-vectors.
    static double dot3(const double a[3], const double b[3]);

    /// Find patch index, or patches_.size() if not found.
    std::size_t find_patch_index(std::uint64_t patch_id) const;
};

}  // namespace quality
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_QUALITY_MULTIVIEW_PHOTOMETRIC_H
