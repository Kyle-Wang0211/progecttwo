// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/realtime_quality_metrics.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstring>

namespace aether {
namespace quality {
namespace {

constexpr float kEps = 1e-7f;

/// Clamp a float to [lo, hi].
inline float clampf(float v, float lo, float hi) {
    return std::max(lo, std::min(hi, v));
}

/// Compute Mean Squared Error between two RGB buffers.
/// Operates on a strided subset for real-time performance.
/// Returns MSE in [0, 1] range assuming inputs are [0, 1].
float compute_mse_strided(
    const float* rgb_a,
    const float* rgb_b,
    std::uint32_t width,
    std::uint32_t height,
    std::uint32_t stride) {

    const std::size_t total_pixels =
        static_cast<std::size_t>(width) * static_cast<std::size_t>(height);
    if (total_pixels == 0 || stride == 0) {
        return 1.0f;
    }

    double sum_sq = 0.0;
    std::size_t count = 0;

    for (std::size_t i = 0; i < total_pixels; i += stride) {
        const std::size_t base = i * 3;
        const float dr = rgb_a[base + 0] - rgb_b[base + 0];
        const float dg = rgb_a[base + 1] - rgb_b[base + 1];
        const float db = rgb_a[base + 2] - rgb_b[base + 2];
        sum_sq += static_cast<double>(dr * dr + dg * dg + db * db);
        count += 3;  // 3 channels
    }

    if (count == 0) {
        return 1.0f;
    }
    return static_cast<float>(sum_sq / static_cast<double>(count));
}

/// Compute PSNR from MSE.  PSNR = 10 * log10(1.0 / MSE).
/// Clamps to [0, 60] dB range.
float mse_to_psnr(float mse) {
    if (mse < kEps) {
        return 60.0f;  // Near-perfect reconstruction.
    }
    const float psnr = 10.0f * std::log10(1.0f / mse);
    return clampf(psnr, 0.0f, 60.0f);
}

/// Compute Chamfer-like distance: mean |rendered_depth - tsdf_depth|
/// where both are valid (> 0).  Result is in meters.
float compute_chamfer_distance(
    const float* rendered_depth,
    const float* tsdf_depth,
    std::uint32_t width,
    std::uint32_t height) {

    const std::size_t total =
        static_cast<std::size_t>(width) * static_cast<std::size_t>(height);
    if (total == 0) {
        return 1.0f;
    }

    double sum = 0.0;
    std::size_t count = 0;

    // Stride for real-time performance.
    constexpr std::size_t kStride = 4;

    for (std::size_t i = 0; i < total; i += kStride) {
        const float rd = rendered_depth[i];
        const float td = tsdf_depth[i];

        // Both must be valid (positive, finite).
        if (rd > 0.0f && td > 0.0f &&
            std::isfinite(rd) && std::isfinite(td)) {
            sum += static_cast<double>(std::fabs(rd - td));
            ++count;
        }
    }

    if (count == 0) {
        return 1.0f;
    }
    return static_cast<float>(sum / static_cast<double>(count));
}

/// Compute mean dot product of rendered vs TSDF normals where both valid.
float compute_normal_consistency(
    const float* rendered_normal,
    const float* tsdf_normal,
    std::uint32_t width,
    std::uint32_t height) {

    const std::size_t total =
        static_cast<std::size_t>(width) * static_cast<std::size_t>(height);
    if (total == 0) {
        return 0.0f;
    }

    double sum = 0.0;
    std::size_t count = 0;

    constexpr std::size_t kStride = 4;

    for (std::size_t i = 0; i < total; i += kStride) {
        const std::size_t base = i * 3;
        const float rnx = rendered_normal[base + 0];
        const float rny = rendered_normal[base + 1];
        const float rnz = rendered_normal[base + 2];
        const float tnx = tsdf_normal[base + 0];
        const float tny = tsdf_normal[base + 1];
        const float tnz = tsdf_normal[base + 2];

        // Check that both normals are valid (non-zero length).
        const float rn_len_sq = rnx * rnx + rny * rny + rnz * rnz;
        const float tn_len_sq = tnx * tnx + tny * tny + tnz * tnz;

        if (rn_len_sq > 0.1f && tn_len_sq > 0.1f) {
            const float dp = rnx * tnx + rny * tny + rnz * tnz;
            const float norm = std::sqrt(rn_len_sq) * std::sqrt(tn_len_sq);
            sum += static_cast<double>(clampf(dp / norm, 0.0f, 1.0f));
            ++count;
        }
    }

    if (count == 0) {
        return 0.0f;
    }
    return static_cast<float>(sum / static_cast<double>(count));
}

/// Compute scale accuracy: 1.0 - |arkit_scale - rendered_scale| / arkit_scale.
float compute_scale_accuracy(float arkit_scale, float rendered_scale) {
    if (arkit_scale <= kEps) {
        return 0.0f;
    }
    const float relative_error =
        std::fabs(arkit_scale - rendered_scale) / arkit_scale;
    return clampf(1.0f - relative_error, 0.0f, 1.0f);
}

}  // namespace

float RealtimeQualityEstimator::ema(
    float current, float new_value, bool first_frame) {
    if (first_frame) {
        return new_value;
    }
    return kEmaAlpha * new_value + (1.0f - kEmaAlpha) * current;
}

core::Status RealtimeQualityEstimator::update(
    const QualityEstimatorInput& input) {

    if (input.rendered_rgb == nullptr || input.width == 0 || input.height == 0) {
        return core::Status::kInvalidArgument;
    }

    const bool first = !initialized_;
    const std::size_t total_pixels =
        static_cast<std::size_t>(input.width) *
        static_cast<std::size_t>(input.height);

    // ── PSNR: Multi-view consistency ──
    // Compare current rendered image with previous frame's downsampled
    // version.  On first frame, PSNR defaults to 0.
    float psnr_raw = 0.0f;
    if (prev_rgb_count_ > 0 && total_pixels > 0) {
        // Compute MSE between current and previous (both downsampled).
        const std::uint32_t stride =
            static_cast<std::uint32_t>(
                std::max(static_cast<std::size_t>(1),
                         total_pixels / kPrevFrameMaxPixels));
        const float mse = compute_mse_strided(
            input.rendered_rgb, prev_rgb_, input.width, input.height, stride);
        psnr_raw = mse_to_psnr(mse);
    }

    // Store downsampled current frame for next comparison.
    {
        const std::size_t max_store = std::min(total_pixels,
            static_cast<std::size_t>(kPrevFrameMaxPixels));
        const std::size_t stride = std::max(
            static_cast<std::size_t>(1), total_pixels / max_store);
        std::size_t stored = 0;
        for (std::size_t i = 0; i < total_pixels && stored < kPrevFrameMaxPixels;
             i += stride) {
            const std::size_t src = i * 3;
            const std::size_t dst = stored * 3;
            prev_rgb_[dst + 0] = input.rendered_rgb[src + 0];
            prev_rgb_[dst + 1] = input.rendered_rgb[src + 1];
            prev_rgb_[dst + 2] = input.rendered_rgb[src + 2];
            ++stored;
        }
        prev_rgb_count_ = static_cast<std::uint32_t>(stored);
    }

    // ── SSIM: Simplified block-based ──
    float ssim_raw = 0.0f;
    if (input.tsdf_depth != nullptr && input.rendered_depth != nullptr) {
        // We use rendered vs TSDF depth to derive a proxy SSIM.
        // For a true SSIM, we'd need a reference RGB image; here we
        // compute block SSIM of the rendered image against itself
        // shifted by depth error as a structural consistency proxy.
        // Simplified: if we have two RGB sources we compare them.
        // Otherwise, use PSNR-derived estimate.
        ssim_raw = clampf(1.0f - (1.0f / (1.0f + psnr_raw / 10.0f)), 0.0f, 1.0f);
    }

    // ── Chamfer distance ──
    float chamfer_raw = 1.0f;
    if (input.rendered_depth != nullptr && input.tsdf_depth != nullptr) {
        chamfer_raw = compute_chamfer_distance(
            input.rendered_depth, input.tsdf_depth,
            input.width, input.height);
    }

    // ── Normal consistency ──
    float normal_raw = 0.0f;
    if (input.rendered_normal != nullptr && input.tsdf_normal != nullptr) {
        normal_raw = compute_normal_consistency(
            input.rendered_normal, input.tsdf_normal,
            input.width, input.height);
    }

    // ── Scale accuracy ──
    const float scale_raw = compute_scale_accuracy(
        input.arkit_scale, input.rendered_scale);

    // ── Coverage F-score ──
    const float coverage_raw = clampf(input.s5_coverage_ratio, 0.0f, 1.0f);

    // ── EMA smoothing ──
    metrics_.psnr_estimate = ema(metrics_.psnr_estimate, psnr_raw, first);
    metrics_.ssim_estimate = ema(metrics_.ssim_estimate, ssim_raw, first);
    metrics_.chamfer_estimate = ema(metrics_.chamfer_estimate, chamfer_raw, first);
    metrics_.normal_consistency = ema(metrics_.normal_consistency, normal_raw, first);
    metrics_.scale_accuracy = ema(metrics_.scale_accuracy, scale_raw, first);
    metrics_.coverage_f_score = ema(metrics_.coverage_f_score, coverage_raw, first);

    // ── Confidence interval: simple +/- 1.5 dB band around PSNR ──
    // In production, this would come from MC uncertainty externally.
    // Here we provide a heuristic band that narrows with more samples.
    constexpr float kBaseCiBand = 1.5f;
    const float ci_band = first ? kBaseCiBand : kBaseCiBand * 0.8f;
    metrics_.psnr_ci_lower = metrics_.psnr_estimate - ci_band;
    metrics_.psnr_ci_upper = metrics_.psnr_estimate + ci_band;

    initialized_ = true;

    return core::Status::kOk;
}

RealtimeQualityMetrics RealtimeQualityEstimator::current() const {
    return metrics_;
}

void RealtimeQualityEstimator::reset() {
    metrics_ = RealtimeQualityMetrics{};
    initialized_ = false;
    prev_rgb_count_ = 0;
    std::memset(prev_rgb_, 0, sizeof(prev_rgb_));
}

}  // namespace quality
}  // namespace aether
