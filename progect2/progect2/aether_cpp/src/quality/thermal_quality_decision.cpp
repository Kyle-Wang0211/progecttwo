// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/thermal_quality_decision.h"

#include <algorithm>
#include <cstring>
#include <vector>

namespace aether {
namespace quality {

ThermalQualityDecision::ThermalQualityDecision(const ThermalQualityConfig& config)
    : config_(config)
    , sample_capacity_(config.window_frames > 0 ? config.window_frames : 120) {
    samples_ = new float[static_cast<std::size_t>(sample_capacity_)];
}

void ThermalQualityDecision::reset() {
    current_tier_ = 0;
    last_tier_change_s_ = 0.0;
    sample_count_ = 0;
}

void ThermalQualityDecision::try_set_tier(int new_tier, double timestamp_s) {
    new_tier = std::max(0, std::min(3, new_tier));
    if (new_tier == current_tier_) return;
    current_tier_ = new_tier;
    last_tier_change_s_ = timestamp_s;
}

float ThermalQualityDecision::percentile(float p) const {
    if (sample_count_ == 0) return 0.0f;

    // Copy to temp buffer for sorting (avoid modifying ring)
    std::vector<float> sorted(samples_, samples_ + sample_count_);
    std::sort(sorted.begin(), sorted.end());

    const int idx = static_cast<int>(static_cast<float>(sorted.size()) * p);
    return sorted[std::min(idx, static_cast<int>(sorted.size()) - 1)];
}

std::uint32_t ThermalQualityDecision::compute_pass_mask(int tier) {
    // bit0: wedge fill (always)
    // bit1: border stroke (always)
    // bit2: metallic lighting (nominal/fair)
    // bit3: color correction (nominal/fair)
    // bit4: ambient occlusion (nominal only)
    // bit5: post-processing (nominal only)
    std::uint32_t mask = 0x03;  // Pass 1-2 always on
    if (tier <= 1) mask |= 0x0C;  // Pass 3-4 at nominal/fair
    if (tier == 0) mask |= 0x30;  // Pass 5-6 at nominal only
    return mask;
}

void ThermalQualityDecision::update_os_thermal(int os_level, double timestamp_s) {
    // Map OS thermal level (0-3) to tier with hysteresis
    const int target = std::max(0, std::min(3, os_level));
    if (target != current_tier_ &&
        (timestamp_s - last_tier_change_s_) > static_cast<double>(config_.hysteresis_s)) {
        try_set_tier(target, timestamp_s);
    }
}

void ThermalQualityDecision::update_frame_timing(float gpu_duration_ms, double timestamp_s) {
    // Ring buffer append
    if (sample_count_ < sample_capacity_) {
        samples_[sample_count_++] = gpu_duration_ms;
    } else {
        // Shift left by 1 (O(n) but n is small, typically 120)
        std::memmove(samples_, samples_ + 1,
                     static_cast<std::size_t>(sample_capacity_ - 1) * sizeof(float));
        samples_[sample_capacity_ - 1] = gpu_duration_ms;
    }

    // P95 overshoot escalation
    const int fps = config_.tier_target_fps[current_tier_];
    const float target_ms = 1000.0f / static_cast<float>(fps > 0 ? fps : 60);
    const float threshold = target_ms * config_.overshoot_ratio;
    const float p95 = percentile(0.95f);

    if (p95 > threshold) {
        const int next = std::min(current_tier_ + 1, 3);
        if ((timestamp_s - last_tier_change_s_) > static_cast<double>(config_.hysteresis_s)) {
            try_set_tier(next, timestamp_s);
        }
    }
}

void ThermalQualityDecision::evaluate(double timestamp_s) {
    // ── Proactive escalation ──
    // At nominal, if P50 GPU time > proactive_threshold × budget → enter fair
    if (current_tier_ == 0 && sample_count_ >= config_.window_frames) {
        const int fps = config_.tier_target_fps[0];
        const float target_ms = 1000.0f / static_cast<float>(fps > 0 ? fps : 60);
        const float p50 = percentile(0.50f);

        if (p50 > target_ms * config_.proactive_threshold) {
            if ((timestamp_s - last_tier_change_s_) > static_cast<double>(config_.hysteresis_s)) {
                try_set_tier(1, timestamp_s);
            }
        }
    }

    // ── Cool-down de-escalation ──
    // If tier > 0 and P95 < cooldown_threshold × budget for sustained period → step down
    if (current_tier_ > 0 && sample_count_ >= config_.window_frames) {
        const int fps = config_.tier_target_fps[current_tier_];
        const float target_ms = 1000.0f / static_cast<float>(fps > 0 ? fps : 60);
        const float p95 = percentile(0.95f);

        if (p95 < target_ms * config_.cooldown_threshold) {
            const double cooldown_hysteresis =
                static_cast<double>(config_.hysteresis_s) *
                static_cast<double>(config_.cooldown_multiplier);
            if ((timestamp_s - last_tier_change_s_) > cooldown_hysteresis) {
                try_set_tier(current_tier_ - 1, timestamp_s);
            }
        }
    }
}

void ThermalQualityDecision::force_tier(int tier, double timestamp_s) {
    current_tier_ = std::max(0, std::min(3, tier));
    last_tier_change_s_ = timestamp_s;
}

ThermalQualityState ThermalQualityDecision::state() const {
    ThermalQualityState s{};
    s.current_tier = current_tier_;
    s.pass_mask = compute_pass_mask(current_tier_);
    s.max_triangles = config_.tier_max_triangles[current_tier_];
    s.target_fps = config_.tier_target_fps[current_tier_];
    s.enable_flip = (current_tier_ <= 1) ? 1 : 0;
    s.enable_ripple = (current_tier_ <= 1) ? 1 : 0;
    s.enable_metallic = (current_tier_ <= 1) ? 1 : 0;
    s.enable_haptics = (current_tier_ <= 2) ? 1 : 0;
    return s;
}

// Destructor
ThermalQualityDecision::~ThermalQualityDecision() {
    delete[] samples_;
}

}  // namespace quality
}  // namespace aether
