// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_QUALITY_SCENE_FEATURE_DETECTOR_H
#define AETHER_QUALITY_SCENE_FEATURE_DETECTOR_H

#include <cstdint>

#include "aether/core/status.h"

namespace aether {
namespace quality {

/// Seven adaptive scene-feature signals (A–G).
///
/// Each feature can independently trigger a specialised training module.
/// Binary indoor/outdoor (ceiling vs sky) is derived from feature B.
enum class SceneFeature : uint8_t {
    kNoTexture       = 0u,  ///< A: textureless regions (white wall / ceiling)
    kSky             = 1u,  ///< B: open sky (unbounded depth, blue hue)
    kLargeDepth      = 2u,  ///< C: depth > 5 m
    kExposureDrift   = 3u,  ///< D: exposure value drifting across frames
    kReflection      = 4u,  ///< E: specular / mirror surface
    kTransparency    = 5u,  ///< F: transparent object
    kLargeScale      = 6u,  ///< G: scene extent > 100 m²
    kCount           = 7u
};

struct FeatureDetectionResult {
    bool    active[7]{};         ///< per-feature activation flag
    float   confidence[7]{};     ///< per-feature confidence [0,1]
    bool    is_indoor{true};     ///< true = has ceiling; false = sky visible
    uint8_t active_count{0};     ///< number of active features
};

struct FeatureDetectorConfig {
    // A – no texture
    float no_texture_variance_threshold{200.0f};
    float no_texture_area_ratio{0.15f};

    // B – sky
    float sky_brightness_threshold{0.7f};
    float sky_blue_ratio_threshold{0.3f};

    // C – large depth
    float large_depth_threshold{5.0f};
    float large_depth_area_ratio{0.20f};

    // D – exposure drift
    float exposure_drift_rate_threshold{0.05f};

    // E – reflection
    float reflection_cross_view_delta_e{8.0f};

    // F – transparency
    float transparency_confidence_threshold{0.3f};

    // G – large scale
    float large_scale_area_m2{100.0f};
};

/// Incremental scene-feature detector.
///
/// Call `update()` once per frame with image-level statistics derived
/// from existing quality modules (image_metrics, photometric_checker,
/// coverage_estimator).  Then call `detect()` to retrieve the current
/// feature mask.  An exponential moving average (α = 0.08) smooths
/// per-frame noise so that a single outlier frame does not flip a flag.
class SceneFeatureDetector {
public:
    explicit SceneFeatureDetector(FeatureDetectorConfig config = {});

    /// Feed one frame of statistics.  All inputs are expected to come
    /// from existing quality pipelines – no new image processing here.
    core::Status update(
        float texture_variance,       ///< from image_metrics (Laplacian var)
        float sky_area_ratio,         ///< fraction of frame classified as sky [0,1]
        float mean_depth,             ///< average depth in metres
        float depth_confidence,       ///< TSDF mean confidence [0,1]
        float exposure_value,         ///< EV from camera metadata
        float cross_view_delta_e,     ///< from multiview_photometric
        float scene_extent_m2);       ///< estimated scene area (m²)

    /// Return the current feature detection result.
    FeatureDetectionResult detect() const;

    /// Reset all internal EMA state.
    void reset();

private:
    FeatureDetectorConfig config_;

    // EMA-smoothed signals (one per feature).
    float ema_texture_var_{0.0f};
    float ema_sky_ratio_{0.0f};
    float ema_depth_{0.0f};
    float ema_depth_conf_{1.0f};
    float prev_exposure_{0.0f};
    float ema_exposure_drift_{0.0f};
    float ema_cross_view_de_{0.0f};
    float ema_extent_{0.0f};
    uint32_t frame_count_{0};

    static constexpr float kEmaAlpha = 0.08f;
};

}  // namespace quality
}  // namespace aether

#endif  // AETHER_QUALITY_SCENE_FEATURE_DETECTOR_H
