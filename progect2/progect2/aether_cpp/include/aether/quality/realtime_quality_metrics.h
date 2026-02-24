// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_QUALITY_REALTIME_QUALITY_METRICS_H
#define AETHER_QUALITY_REALTIME_QUALITY_METRICS_H

#ifdef __cplusplus

#include "aether/core/status.h"

#include <cstdint>

namespace aether {
namespace quality {

/// Six-dimensional real-time quality metrics for the 3DGS optimizer.
///
/// Provides a comprehensive quality snapshot covering photometric,
/// geometric, and coverage dimensions.  All values are EMA-smoothed
/// for temporal stability.
struct RealtimeQualityMetrics {
    /// Peak Signal-to-Noise Ratio estimate (dB).
    float psnr_estimate{0.0f};

    /// Structural Similarity Index (simplified block-based) [0,1].
    float ssim_estimate{0.0f};

    /// Chamfer distance estimate (cm) — lower is better.
    float chamfer_estimate{1.0f};

    /// Normal consistency — mean dot product of rendered vs TSDF normals [0,1].
    float normal_consistency{0.0f};

    /// Scale accuracy — agreement between ARKit and rendered scale [0,1].
    float scale_accuracy{0.0f};

    /// Coverage F-score — S5 coverage ratio [0,1].
    float coverage_f_score{0.0f};

    /// PSNR confidence interval bounds (from external MC uncertainty).
    float psnr_ci_lower{0.0f};
    float psnr_ci_upper{0.0f};

    /// Returns true if ALL six quality dimensions meet the world-model
    /// standard thresholds:
    ///   PSNR >= 28 dB
    ///   Chamfer <= 0.02 m (2 cm)
    ///   Normal consistency >= 0.85
    ///   Scale accuracy >= 0.98
    ///   Coverage F-score >= 0.75
    ///   CI lower bound >= 28 dB
    bool meets_world_model_standard() const {
        return psnr_estimate >= 28.0f &&
               chamfer_estimate <= 0.02f &&
               normal_consistency >= 0.85f &&
               scale_accuracy >= 0.98f &&
               coverage_f_score >= 0.75f &&
               psnr_ci_lower >= 28.0f;
    }
};

/// Input data for a single quality estimation update.
///
/// Pointers to rendered and TSDF reference data are externally owned
/// and must remain valid for the duration of the update() call.
struct QualityEstimatorInput {
    /// Rendered RGB image (width * height * 3 floats, [0,1] range).
    const float* rendered_rgb{nullptr};

    /// Rendered depth image (width * height floats, meters).
    const float* rendered_depth{nullptr};

    /// Rendered surface normals (width * height * 3 floats, unit vectors).
    const float* rendered_normal{nullptr};

    /// TSDF reference depth image (width * height floats, meters).
    const float* tsdf_depth{nullptr};

    /// TSDF reference surface normals (width * height * 3 floats).
    const float* tsdf_normal{nullptr};

    /// ARKit-reported metric scale.
    float arkit_scale{1.0f};

    /// Scale estimated from the rendered 3DGS model.
    float rendered_scale{1.0f};

    /// Image dimensions.
    std::uint32_t width{0};
    std::uint32_t height{0};

    /// S5 coverage ratio from the evidence state machine [0,1].
    float s5_coverage_ratio{0.0f};
};

/// Real-time six-dimensional quality estimator with EMA smoothing.
///
/// Computes photometric (PSNR, SSIM), geometric (Chamfer, normal
/// consistency), scale, and coverage metrics from rendered vs
/// reference imagery.  All outputs are EMA-smoothed with alpha=0.1
/// for temporal stability during live optimization.
///
/// Thread safety: NOT thread-safe.  Caller must synchronize.
class RealtimeQualityEstimator {
public:
    /// Update all quality metrics from the given input.
    /// @param input  Rendered + TSDF reference data for this frame.
    /// @return kOk on success, kInvalidArgument if critical pointers are null.
    core::Status update(const QualityEstimatorInput& input);

    /// Return current EMA-smoothed metrics.
    RealtimeQualityMetrics current() const;

    /// Reset all metrics to initial state.
    void reset();

private:
    RealtimeQualityMetrics metrics_{};
    bool initialized_{false};

    /// EMA smoothing factor.
    static constexpr float kEmaAlpha = 0.1f;

    /// Previous frame rendered RGB for multi-view consistency PSNR.
    /// We store a downsampled version to limit memory usage.
    static constexpr std::uint32_t kPrevFrameMaxPixels = 4096;
    float prev_rgb_[kPrevFrameMaxPixels * 3]{};
    std::uint32_t prev_rgb_count_{0};

    /// Smooth a metric value with EMA.
    static float ema(float current, float new_value, bool first_frame);
};

}  // namespace quality
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_QUALITY_REALTIME_QUALITY_METRICS_H
