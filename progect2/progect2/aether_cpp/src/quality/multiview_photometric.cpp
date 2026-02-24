// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/multiview_photometric.h"

#include <algorithm>
#include <cmath>

namespace aether {
namespace quality {

// ────────────────────────────────────────────────────────────────────
// Construction / reset
// ────────────────────────────────────────────────────────────────────

MultiViewPhotometricValidator::MultiViewPhotometricValidator(
    MultiViewPhotometricConfig config)
    : config_(config) {}

void MultiViewPhotometricValidator::reset() {
    patches_.clear();
}

// ────────────────────────────────────────────────────────────────────
// Observation ingestion
// ────────────────────────────────────────────────────────────────────

void MultiViewPhotometricValidator::add_observation(
    std::uint64_t patch_id,
    const ViewRadianceSample& sample) {

    std::size_t idx = find_patch_index(patch_id);
    if (idx == patches_.size()) {
        PatchObservationRecord rec;
        rec.patch_id = patch_id;
        patches_.push_back(rec);
        idx = patches_.size() - 1;
    }

    auto& record = patches_[idx];

    // FIFO eviction
    if (record.samples.size() >= config_.max_views_per_patch) {
        record.samples.erase(record.samples.begin());
    }

    record.samples.push_back(sample);
}

// ────────────────────────────────────────────────────────────────────
// Per-patch evaluation
// ────────────────────────────────────────────────────────────────────

CrossViewResult MultiViewPhotometricValidator::evaluate_patch(
    std::uint64_t patch_id) const {

    CrossViewResult result;
    const std::size_t idx = find_patch_index(patch_id);
    if (idx == patches_.size()) return result;

    const auto& record = patches_[idx];
    if (record.samples.size() < config_.min_views_per_patch) {
        result.is_consistent = true;
        result.confidence = 0.0;
        return result;
    }

    double sum_delta_e = 0.0;
    double max_delta_e = 0.0;
    double sum_lamb_err = 0.0;
    std::size_t valid_pairs = 0;

    const std::size_t n = record.samples.size();
    for (std::size_t i = 0; i < n; ++i) {
        const auto& si = record.samples[i];
        const double cos_i = dot3(si.normal, si.view_dir);
        if (cos_i < config_.grazing_angle_cos_min) continue;

        for (std::size_t j = i + 1; j < n; ++j) {
            const auto& sj = record.samples[j];
            const double cos_j = dot3(sj.normal, sj.view_dir);
            if (cos_j < config_.grazing_angle_cos_min) continue;

            // Cross-view CIEDE2000
            const double de = PhotometricChecker::ciede2000(si.lab, sj.lab);

            // Lambertian ratio error
            const double le = lambertian_ratio_error(si, sj);

            sum_delta_e += de;
            if (de > max_delta_e) max_delta_e = de;
            sum_lamb_err += le;
            ++valid_pairs;
        }
    }

    if (valid_pairs == 0) {
        result.is_consistent = true;
        result.confidence = 0.0;
        return result;
    }

    result.pair_count = valid_pairs;
    result.mean_cross_view_delta_e = sum_delta_e / static_cast<double>(valid_pairs);
    result.max_cross_view_delta_e = max_delta_e;

    const double mean_lamb_err = sum_lamb_err / static_cast<double>(valid_pairs);
    result.lambertian_consistency =
        std::max(0.0, 1.0 - mean_lamb_err / config_.lambertian_tolerance);

    result.is_consistent =
        result.mean_cross_view_delta_e <= config_.max_cross_view_delta_e;

    // Confidence grows with pair count, saturating at ~10 pairs
    result.confidence = std::min(1.0,
        static_cast<double>(valid_pairs) / 10.0);

    return result;
}

// ────────────────────────────────────────────────────────────────────
// Aggregate evaluation across all patches
// ────────────────────────────────────────────────────────────────────

CrossViewResult MultiViewPhotometricValidator::evaluate_all() const {
    CrossViewResult agg;
    if (patches_.empty()) return agg;

    double weighted_de = 0.0;
    double max_de = 0.0;
    double weighted_lamb = 0.0;
    double total_conf = 0.0;
    std::size_t total_pairs = 0;
    std::size_t evaluated = 0;

    for (const auto& rec : patches_) {
        CrossViewResult pr = evaluate_patch(rec.patch_id);
        if (pr.pair_count == 0) continue;

        const double w = pr.confidence;
        weighted_de += pr.mean_cross_view_delta_e * w;
        if (pr.max_cross_view_delta_e > max_de) {
            max_de = pr.max_cross_view_delta_e;
        }
        weighted_lamb += pr.lambertian_consistency * w;
        total_conf += w;
        total_pairs += pr.pair_count;
        ++evaluated;
    }

    if (total_conf < 1e-12 || evaluated == 0) return agg;

    agg.mean_cross_view_delta_e = weighted_de / total_conf;
    agg.max_cross_view_delta_e = max_de;
    agg.lambertian_consistency = weighted_lamb / total_conf;
    agg.pair_count = total_pairs;
    agg.is_consistent =
        agg.mean_cross_view_delta_e <= config_.max_cross_view_delta_e;
    agg.confidence = std::min(1.0,
        static_cast<double>(evaluated) / std::max(patches_.size(), std::size_t{1}));

    return agg;
}

std::size_t MultiViewPhotometricValidator::patch_count() const {
    return patches_.size();
}

// ────────────────────────────────────────────────────────────────────
// Internal helpers
// ────────────────────────────────────────────────────────────────────

double MultiViewPhotometricValidator::lambertian_ratio_error(
    const ViewRadianceSample& a,
    const ViewRadianceSample& b) {

    const double cos_a = std::max(dot3(a.normal, a.view_dir), 1e-6);
    const double cos_b = std::max(dot3(b.normal, b.view_dir), 1e-6);

    // Under Lambertian assumption:
    //   L_a * cos(theta_a) ≈ L_b * cos(theta_b)   (same irradiance)
    // So L_a / L_b should equal cos_b / cos_a
    const double lum_a = std::max(a.luminance, 1e-9);
    const double lum_b = std::max(b.luminance, 1e-9);

    const double actual_ratio = lum_a / lum_b;
    const double expected_ratio = cos_b / cos_a;

    if (expected_ratio < 1e-9) return 1.0;

    return std::abs(actual_ratio - expected_ratio) / expected_ratio;
}

double MultiViewPhotometricValidator::dot3(
    const double a[3], const double b[3]) {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

std::size_t MultiViewPhotometricValidator::find_patch_index(
    std::uint64_t patch_id) const {
    for (std::size_t i = 0; i < patches_.size(); ++i) {
        if (patches_[i].patch_id == patch_id) return i;
    }
    return patches_.size();
}

}  // namespace quality
}  // namespace aether
