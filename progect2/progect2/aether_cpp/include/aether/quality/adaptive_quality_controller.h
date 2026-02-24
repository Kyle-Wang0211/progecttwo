// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_QUALITY_ADAPTIVE_QUALITY_CONTROLLER_H
#define AETHER_QUALITY_ADAPTIVE_QUALITY_CONTROLLER_H

#ifdef __cplusplus

#include "aether/core/status.h"
#include "aether/render/gpu_resource.h"
#include "aether/render/runtime_backend.h"

#include <cstddef>
#include <cstdint>

namespace aether {
namespace quality {

// ---------------------------------------------------------------------------
// Thermal tier classification
// ---------------------------------------------------------------------------

enum class ThermalTier : std::uint8_t {
    kNominal  = 0,   // headroom > 0.5  -> 60 fps
    kFair     = 1,   // headroom > 0.3  -> 50 fps
    kSerious  = 2,   // headroom > 0.15 -> 30 fps
    kCritical = 3,   // headroom <= 0.15 -> 24 fps
};

// ---------------------------------------------------------------------------
// Backward pass execution mode
// ---------------------------------------------------------------------------

enum class BackwardPassMode : std::uint8_t {
    kFragmentShader = 0,
    kTiledCompute   = 1,
};

// ---------------------------------------------------------------------------
// AdaptiveConfig: initial quality parameters
// ---------------------------------------------------------------------------

struct AdaptiveConfig {
    std::uint32_t gaussian_budget_min{5000};
    std::uint32_t gaussian_budget_max{50000};
    float resolution_scale_min{0.5f};
    float resolution_scale_max{1.0f};
    BackwardPassMode backward_mode{BackwardPassMode::kTiledCompute};
    std::uint32_t tile_size{64};
    bool allow_fp16_backward{true};
};

// ---------------------------------------------------------------------------
// AdaptiveControllerInput: per-frame sensor readings
// ---------------------------------------------------------------------------

struct AdaptiveControllerInput {
    float frame_time_ms;
    float thermal_headroom;       // [0, 1] — higher is better
    float available_memory_mb;
    float battery_temp;           // Celsius
    float battery_pct;            // [0, 100]
    float psnr_estimate;          // dB
    float chamfer_estimate;       // lower is better
    float evidence_distribution[6];
    float target_fps{60.0f};
};

// ---------------------------------------------------------------------------
// AdaptiveControllerOutput: decisions for the current frame
// ---------------------------------------------------------------------------

struct AdaptiveControllerOutput {
    std::uint32_t gaussian_budget;
    float resolution_scale;
    std::uint8_t sh_order;
    std::uint8_t training_iters_per_frame;
    bool use_fp16_backward;
    float densify_rate;
    float depth_loss_weight;
    std::uint32_t tile_size;
    BackwardPassMode backward_path;
    ThermalTier thermal_tier;
};

// ---------------------------------------------------------------------------
// AdaptiveQualityController
// ---------------------------------------------------------------------------
// PID-based adaptive controller with hard thermal/memory/quality constraints.
// The controller operates in four tiers based on thermal headroom, progressively
// reducing quality to maintain thermal safety and frame time targets.

class AdaptiveQualityController {
public:
    explicit AdaptiveQualityController(const AdaptiveConfig& config);

    /// Update the controller with new sensor readings, producing output decisions.
    core::Status update(const AdaptiveControllerInput& input,
                        AdaptiveControllerOutput* output);

    /// Current thermal tier based on last update.
    ThermalTier current_thermal_tier() const { return thermal_tier_; }

    /// Reset all internal PID state.
    void reset();

private:
    void apply_thermal_constraints(const AdaptiveControllerInput& input,
                                   AdaptiveControllerOutput* output);
    void apply_memory_constraints(const AdaptiveControllerInput& input,
                                  AdaptiveControllerOutput* output);
    void apply_pid_adjustment(const AdaptiveControllerInput& input,
                              AdaptiveControllerOutput* output);

    AdaptiveConfig config_{};
    ThermalTier thermal_tier_{ThermalTier::kNominal};

    // PID state
    float integral_{0.0f};
    float prev_error_{0.0f};
    float pid_output_{0.0f};

    // PID gains
    static constexpr float kKp = 0.5f;
    static constexpr float kKi = 0.01f;
    static constexpr float kKd = 0.1f;

    // Integral windup limit
    static constexpr float kIntegralMax = 50.0f;
};

// ---------------------------------------------------------------------------
// Factory: create config from GPU capabilities
// ---------------------------------------------------------------------------

AdaptiveConfig create_config_for_device(const render::GPUCaps& caps,
                                        render::GraphicsBackend backend);

}  // namespace quality
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_QUALITY_ADAPTIVE_QUALITY_CONTROLLER_H
