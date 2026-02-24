// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_QUALITY_THERMAL_QUALITY_DECISION_H
#define AETHER_QUALITY_THERMAL_QUALITY_DECISION_H

#ifdef __cplusplus

#include <cstdint>

namespace aether {
namespace quality {

/// Configuration for thermal quality decision engine.
struct ThermalQualityConfig {
    float hysteresis_s{5.0f};
    float overshoot_ratio{1.2f};
    int window_frames{120};
    float proactive_threshold{0.70f};     ///< P50 GPU ratio to trigger proactive .fair
    float cooldown_threshold{0.60f};      ///< P95 GPU ratio below which to de-escalate
    float cooldown_multiplier{2.0f};      ///< Cool-down hysteresis = base × multiplier
    int tier_max_triangles[4]{20000, 12000, 6000, 3000};  ///< Per-tier triangle budgets
    int tier_target_fps[4]{60, 60, 30, 24};                ///< Per-tier target FPS
};

/// Output state from thermal quality decision.
struct ThermalQualityState {
    int current_tier;         ///< 0=nominal, 1=fair, 2=serious, 3=critical
    std::uint32_t pass_mask;  ///< Bitmask: bit0=wedge, bit1=border, bit2=metallic,
                              ///<          bit3=colorCorr, bit4=AO, bit5=postProcess
    int max_triangles;
    int target_fps;
    int enable_flip;          ///< 0 or 1
    int enable_ripple;
    int enable_metallic;
    int enable_haptics;
};

/// Stateful thermal quality decision engine.
///
/// Replaces Swift ThermalQualityAdapter: manages render tier based on
/// OS thermal state + GPU frame time percentile analysis.
/// Includes proactive escalation and cool-down de-escalation.
class ThermalQualityDecision {
public:
    explicit ThermalQualityDecision(const ThermalQualityConfig& config = ThermalQualityConfig());
    ~ThermalQualityDecision();

    ThermalQualityDecision(const ThermalQualityDecision&) = delete;
    ThermalQualityDecision& operator=(const ThermalQualityDecision&) = delete;

    void reset();

    /// React to OS-level thermal state change.
    /// @param os_level 0=nominal, 1=fair, 2=serious, 3=critical
    void update_os_thermal(int os_level, double timestamp_s);

    /// Feed GPU frame timing sample.
    void update_frame_timing(float gpu_duration_ms, double timestamp_s);

    /// Run proactive + cool-down evaluation in one call.
    void evaluate(double timestamp_s);

    /// Force tier override.
    void force_tier(int tier, double timestamp_s);

    /// Query current state including tier, pass mask, budgets.
    ThermalQualityState state() const;

    int current_tier() const { return current_tier_; }

private:
    ThermalQualityConfig config_;
    int current_tier_{0};
    double last_tier_change_s_{0.0};
    float* samples_;
    int sample_count_{0};
    int sample_capacity_;

    void try_set_tier(int new_tier, double timestamp_s);
    float percentile(float p) const;
    static std::uint32_t compute_pass_mask(int tier);
};

}  // namespace quality
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_QUALITY_THERMAL_QUALITY_DECISION_H
