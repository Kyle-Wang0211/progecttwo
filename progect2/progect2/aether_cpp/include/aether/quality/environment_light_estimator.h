// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_QUALITY_ENVIRONMENT_LIGHT_ESTIMATOR_H
#define AETHER_QUALITY_ENVIRONMENT_LIGHT_ESTIMATOR_H

#ifdef __cplusplus

namespace aether {
namespace quality {

/// Stateful environment-light fusion config.
///
/// This kernel fuses platform-provided light samples with temporal smoothing and
/// missing-sample recovery. It is platform-agnostic and deterministic.
struct EnvironmentLightConfig {
    float fallback_direction[3]{0.0f, 1.0f, 0.0f};
    float fallback_intensity{1.0f};
    float min_intensity{0.05f};
    float max_intensity{4.0f};
    float rise_alpha{0.35f};          ///< Alpha when target intensity is increasing.
    float fall_alpha{0.12f};          ///< Alpha when target intensity is decreasing.
    float direction_alpha{0.20f};     ///< Direction smoothing alpha.
    float sh_alpha{0.18f};            ///< SH coefficient smoothing alpha.
    float missing_decay_per_s{2.5f};  ///< Exponential decay toward fallback when samples are missing.
    float max_missing_hold_s{0.6f};   ///< After this, tier is forced to fallback.
};

/// One frame of platform light observation.
struct EnvironmentLightObservation {
    int source_tier{2};  ///< 0=ARKit, 1=Vision, 2=Fallback.
    int has_direction{0};
    int has_sh{0};
    float direction[3]{0.0f, 1.0f, 0.0f};
    float intensity{1.0f};
    float sh_coeffs_rgb[27]{};  ///< Flattened [coeff][rgb], coeff in [0..8].
};

/// Output state consumed by render layer.
struct EnvironmentLightState {
    int tier{2};  ///< 0=ARKit, 1=Vision, 2=Fallback.
    float direction[3]{0.0f, 1.0f, 0.0f};
    float intensity{1.0f};
    float sh_coeffs_rgb[27]{};  ///< Flattened [coeff][rgb], coeff in [0..8].
    float missing_seconds{0.0f};
};

/// Deterministic temporal light fusion kernel.
class EnvironmentLightEstimator {
public:
    explicit EnvironmentLightEstimator(const EnvironmentLightConfig& config = EnvironmentLightConfig());
    ~EnvironmentLightEstimator() = default;

    EnvironmentLightEstimator(const EnvironmentLightEstimator&) = delete;
    EnvironmentLightEstimator& operator=(const EnvironmentLightEstimator&) = delete;

    void reset();

    /// Advance estimator by one frame.
    ///
    /// @param observation_or_null nullptr means no valid platform light sample.
    /// @param timestamp_s monotonic timestamp in seconds.
    /// @return fused light state.
    const EnvironmentLightState& step(
        const EnvironmentLightObservation* observation_or_null,
        double timestamp_s);

    const EnvironmentLightState& state() const { return state_; }

private:
    EnvironmentLightConfig config_{};
    EnvironmentLightState state_{};
    bool initialized_{false};
    bool has_last_timestamp_{false};
    double last_timestamp_s_{0.0};

    void set_state_from_observation(const EnvironmentLightObservation& observation);
};

}  // namespace quality
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_QUALITY_ENVIRONMENT_LIGHT_ESTIMATOR_H
