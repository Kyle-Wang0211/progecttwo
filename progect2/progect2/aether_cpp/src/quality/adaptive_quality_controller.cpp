// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/adaptive_quality_controller.h"

#include <cmath>

namespace {

inline float clamp_f(float v, float lo, float hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

inline std::uint32_t clamp_u32(std::uint32_t v, std::uint32_t lo, std::uint32_t hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

/// Compute target frame time in milliseconds from FPS.
inline float target_frame_time_ms(float fps) {
    return (fps > 0.0f) ? (1000.0f / fps) : 16.67f;
}

/// Map thermal headroom to a thermal tier.
inline aether::quality::ThermalTier classify_thermal(float headroom) {
    if (headroom > 0.5f)  return aether::quality::ThermalTier::kNominal;
    if (headroom > 0.3f)  return aether::quality::ThermalTier::kFair;
    if (headroom > 0.15f) return aether::quality::ThermalTier::kSerious;
    return aether::quality::ThermalTier::kCritical;
}

/// Get the effective FPS cap for a given thermal tier.
inline float fps_for_tier(aether::quality::ThermalTier tier) {
    switch (tier) {
        case aether::quality::ThermalTier::kNominal:  return 60.0f;
        case aether::quality::ThermalTier::kFair:     return 50.0f;
        case aether::quality::ThermalTier::kSerious:  return 30.0f;
        case aether::quality::ThermalTier::kCritical: return 24.0f;
    }
    return 60.0f;
}

}  // namespace

namespace aether {
namespace quality {

// ---------------------------------------------------------------------------
// Constructor
// ---------------------------------------------------------------------------

AdaptiveQualityController::AdaptiveQualityController(const AdaptiveConfig& config)
    : config_(config),
      thermal_tier_(ThermalTier::kNominal),
      integral_(0.0f),
      prev_error_(0.0f),
      pid_output_(0.0f) {}

// ---------------------------------------------------------------------------
// Reset
// ---------------------------------------------------------------------------

void AdaptiveQualityController::reset() {
    thermal_tier_ = ThermalTier::kNominal;
    integral_ = 0.0f;
    prev_error_ = 0.0f;
    pid_output_ = 0.0f;
}

// ---------------------------------------------------------------------------
// Main update
// ---------------------------------------------------------------------------

core::Status AdaptiveQualityController::update(
    const AdaptiveControllerInput& input,
    AdaptiveControllerOutput* output) {
    if (output == nullptr) {
        return core::Status::kInvalidArgument;
    }

    // Start with maximum quality defaults
    output->gaussian_budget = config_.gaussian_budget_max;
    output->resolution_scale = config_.resolution_scale_max;
    output->sh_order = 3;
    output->training_iters_per_frame = 4;
    output->use_fp16_backward = config_.allow_fp16_backward;
    output->densify_rate = 1.0f;
    output->depth_loss_weight = 0.1f;
    output->tile_size = config_.tile_size;
    output->backward_path = config_.backward_mode;
    output->thermal_tier = ThermalTier::kNominal;

    // Hard constraint 1: Thermal
    apply_thermal_constraints(input, output);

    // Hard constraint 2: Memory
    apply_memory_constraints(input, output);

    // Hard constraint 3: PSNR quality floor
    // If PSNR is below 20 dB, boost gaussian budget and training iterations
    if (input.psnr_estimate > 0.0f && input.psnr_estimate < 20.0f) {
        // Try to increase quality by boosting training iterations
        if (output->training_iters_per_frame < 6) {
            output->training_iters_per_frame = 6;
        }
        // Increase densification rate to improve coverage
        output->densify_rate = clamp_f(output->densify_rate * 1.5f, 0.0f, 2.0f);
        output->depth_loss_weight = 0.2f;
    }

    // Hard constraint 4: FPS — PID-based adjustment
    apply_pid_adjustment(input, output);

    // Final clamps to respect config bounds
    output->gaussian_budget = clamp_u32(
        output->gaussian_budget,
        config_.gaussian_budget_min,
        config_.gaussian_budget_max);
    output->resolution_scale = clamp_f(
        output->resolution_scale,
        config_.resolution_scale_min,
        config_.resolution_scale_max);

    return core::Status::kOk;
}

// ---------------------------------------------------------------------------
// Thermal constraints (4-tier degradation)
// ---------------------------------------------------------------------------

void AdaptiveQualityController::apply_thermal_constraints(
    const AdaptiveControllerInput& input,
    AdaptiveControllerOutput* output) {
    thermal_tier_ = classify_thermal(input.thermal_headroom);
    output->thermal_tier = thermal_tier_;

    switch (thermal_tier_) {
        case ThermalTier::kNominal:
            // Full quality, no restrictions
            break;

        case ThermalTier::kFair:
            // Mild degradation: drop to 50 fps effective, disable film grain
            output->training_iters_per_frame = 3;
            output->densify_rate = 0.8f;
            break;

        case ThermalTier::kSerious:
            // Significant degradation: drop to 30 fps, reduce resolution
            output->resolution_scale = clamp_f(
                output->resolution_scale, 0.0f, 0.75f);
            output->training_iters_per_frame = 2;
            output->sh_order = 2;
            output->densify_rate = 0.5f;
            // Disable Challenger paths by switching to fragment shader path
            output->backward_path = BackwardPassMode::kFragmentShader;
            // Reduce gaussian budget to 70% of max
            output->gaussian_budget = static_cast<std::uint32_t>(
                static_cast<float>(config_.gaussian_budget_max) * 0.7f);
            break;

        case ThermalTier::kCritical:
            // Minimal features: 24 fps, lowest resolution, smallest budget
            output->resolution_scale = config_.resolution_scale_min;
            output->training_iters_per_frame = 1;
            output->sh_order = 1;
            output->densify_rate = 0.0f;
            output->use_fp16_backward = true;
            output->backward_path = BackwardPassMode::kFragmentShader;
            // Reduce gaussian budget to 40% of max
            output->gaussian_budget = static_cast<std::uint32_t>(
                static_cast<float>(config_.gaussian_budget_max) * 0.4f);
            // Clamp to minimum
            if (output->gaussian_budget < config_.gaussian_budget_min) {
                output->gaussian_budget = config_.gaussian_budget_min;
            }
            break;
    }
}

// ---------------------------------------------------------------------------
// Memory constraints
// ---------------------------------------------------------------------------

void AdaptiveQualityController::apply_memory_constraints(
    const AdaptiveControllerInput& input,
    AdaptiveControllerOutput* output) {
    // Rough memory estimate: each Gaussian uses ~256 bytes (params + SH + state)
    constexpr float kBytesPerGaussian = 256.0f;
    constexpr float kMBToBytes = 1024.0f * 1024.0f;
    constexpr float kMemoryBudgetFraction = 0.6f;  // use at most 60% for Gaussians

    if (input.available_memory_mb <= 0.0f) {
        return;
    }

    const float budget_bytes = input.available_memory_mb * kMBToBytes * kMemoryBudgetFraction;
    const std::uint32_t max_by_memory = static_cast<std::uint32_t>(
        budget_bytes / kBytesPerGaussian);

    if (max_by_memory < output->gaussian_budget) {
        output->gaussian_budget = max_by_memory;
    }

    // If memory is very tight (< 100 MB), reduce resolution too
    if (input.available_memory_mb < 100.0f) {
        output->resolution_scale = clamp_f(
            output->resolution_scale, 0.0f, 0.75f);
    }
    if (input.available_memory_mb < 50.0f) {
        output->resolution_scale = clamp_f(
            output->resolution_scale, 0.0f, 0.5f);
        output->use_fp16_backward = true;
    }
}

// ---------------------------------------------------------------------------
// PID-based FPS adjustment
// ---------------------------------------------------------------------------

void AdaptiveQualityController::apply_pid_adjustment(
    const AdaptiveControllerInput& input,
    AdaptiveControllerOutput* output) {
    // The effective target FPS is the minimum of the user's target
    // and the thermal tier's cap.
    const float tier_fps = fps_for_tier(thermal_tier_);
    const float effective_target_fps = (input.target_fps < tier_fps)
        ? input.target_fps : tier_fps;
    const float target_ms = target_frame_time_ms(effective_target_fps);

    // PID error: positive means we have headroom, negative means too slow
    const float error = target_ms - input.frame_time_ms;

    // PID update
    integral_ += error;
    integral_ = clamp_f(integral_, -kIntegralMax, kIntegralMax);

    const float derivative = error - prev_error_;
    prev_error_ = error;

    pid_output_ = kKp * error + kKi * integral_ + kKd * derivative;

    // If frame time is overshooting (pid_output_ < 0), we need to reduce quality
    if (pid_output_ < -1.0f) {
        // Scale factor: how much we need to reduce (0-1, lower = more reduction)
        const float pressure = clamp_f(1.0f + pid_output_ / 20.0f, 0.3f, 1.0f);

        // Reduce gaussian budget proportionally
        output->gaussian_budget = static_cast<std::uint32_t>(
            static_cast<float>(output->gaussian_budget) * pressure);

        // Reduce resolution scale
        output->resolution_scale *= pressure;

        // If severe, reduce training iterations
        if (pressure < 0.6f && output->training_iters_per_frame > 1) {
            output->training_iters_per_frame =
                static_cast<std::uint8_t>(output->training_iters_per_frame - 1);
        }

        // If very severe, drop SH order
        if (pressure < 0.4f && output->sh_order > 1) {
            output->sh_order = static_cast<std::uint8_t>(output->sh_order - 1);
        }
    }
}

// ---------------------------------------------------------------------------
// Factory function
// ---------------------------------------------------------------------------

AdaptiveConfig create_config_for_device(const render::GPUCaps& caps,
                                        render::GraphicsBackend backend) {
    AdaptiveConfig config{};

    // Tile size based on compute workgroup size
    if (caps.max_compute_workgroup_size > 512) {
        config.tile_size = 64;
    } else if (caps.max_compute_workgroup_size > 256) {
        config.tile_size = 32;
    } else {
        config.tile_size = 16;
    }

    // Backward pass mode: use compute on capable devices
    if (caps.supports_compute) {
        config.backward_mode = BackwardPassMode::kTiledCompute;
    } else {
        config.backward_mode = BackwardPassMode::kFragmentShader;
    }

    // FP16 backward: only on devices that support half precision
    config.allow_fp16_backward = caps.supports_half_precision;

    // Gaussian budget based on GPU memory
    // max_buffer_size gives a rough proxy for GPU capability
    if (caps.max_buffer_size >= 256u * 1024u * 1024u) {
        // High-end device (256+ MB max buffer)
        config.gaussian_budget_max = 100000;
        config.gaussian_budget_min = 10000;
        config.resolution_scale_max = 1.0f;
    } else if (caps.max_buffer_size >= 128u * 1024u * 1024u) {
        // Mid-range
        config.gaussian_budget_max = 50000;
        config.gaussian_budget_min = 5000;
        config.resolution_scale_max = 1.0f;
    } else {
        // Low-end
        config.gaussian_budget_max = 25000;
        config.gaussian_budget_min = 3000;
        config.resolution_scale_max = 0.75f;
    }

    // Metal-specific optimizations
    if (backend == render::GraphicsBackend::kMetal) {
        // Apple Silicon has fast shared memory
        if (caps.supports_simd_group && caps.simd_width >= 32) {
            config.tile_size = 64;
        }
    }

    // Vulkan-specific adjustments
    if (backend == render::GraphicsBackend::kVulkan) {
        // Conservative tile size for broader compatibility
        if (config.tile_size > 32) {
            config.tile_size = 32;
        }
    }

    // OpenGL ES fallback
    if (backend == render::GraphicsBackend::kOpenGLES) {
        config.backward_mode = BackwardPassMode::kFragmentShader;
        config.allow_fp16_backward = false;
        config.gaussian_budget_max = 20000;
        config.tile_size = 16;
    }

    config.resolution_scale_min = 0.5f;

    return config;
}

}  // namespace quality
}  // namespace aether
