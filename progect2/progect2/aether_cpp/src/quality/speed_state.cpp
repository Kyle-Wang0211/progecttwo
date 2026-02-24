// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/speed_state.h"

#include <algorithm>
#include <cmath>
#include <limits>

namespace aether {
namespace quality {
namespace {

constexpr std::int64_t kDefaultSpeedWindowMs = 200;
constexpr double kDefaultMaxChangeRate = 0.30;
constexpr std::int64_t kDefaultNoProgressTriggerMs = 2000;
constexpr std::int64_t kDefaultNoProgressCooldownMs = 1000;
constexpr double kDefaultStoppedAnimationHz = 0.5;
constexpr std::int64_t kDefaultTrendWindowMs = 300;

bool finite(double value) {
    return std::isfinite(value);
}

bool valid_visual_state(int state) {
    return state >= static_cast<int>(VisualState::kBlack) &&
           state <= static_cast<int>(VisualState::kClear);
}

}  // namespace

int monotonic_visual_state_update(int current_state, int new_state) {
    if (!valid_visual_state(current_state) || !valid_visual_state(new_state)) {
        return static_cast<int>(VisualState::kBlack);
    }
    return std::max(current_state, new_state);
}

TransitionDecision can_transition(
    int from_state,
    int to_state,
    int fps_tier,
    bool has_critical_metrics,
    double brightness_confidence,
    double laplacian_confidence,
    bool has_stability,
    double stability,
    double confidence_threshold_full,
    double full_white_stability_max) {
    if (!valid_visual_state(from_state) || !valid_visual_state(to_state)) {
        return TransitionDecision{false, TransitionReason::kInvalidVisualState};
    }

    const bool gray_to_white =
        from_state == static_cast<int>(VisualState::kGray) &&
        to_state == static_cast<int>(VisualState::kWhite);

    if (gray_to_white) {
        if (fps_tier != static_cast<int>(FpsTier::kFull)) {
            return TransitionDecision{false, TransitionReason::kOnlyFullTierAllowsGrayToWhite};
        }
        if (!has_critical_metrics || !finite(brightness_confidence) || !finite(laplacian_confidence)) {
            return TransitionDecision{false, TransitionReason::kMissingCriticalMetrics};
        }
        if (!has_stability || !finite(stability)) {
            return TransitionDecision{false, TransitionReason::kMissingStability};
        }

        const double confidence_threshold =
            (finite(confidence_threshold_full) && confidence_threshold_full >= 0.0)
                ? confidence_threshold_full
                : 0.80;
        const double stability_threshold =
            (finite(full_white_stability_max) && full_white_stability_max >= 0.0)
                ? full_white_stability_max
                : 0.15;

        if (brightness_confidence < confidence_threshold ||
            laplacian_confidence < confidence_threshold) {
            return TransitionDecision{false, TransitionReason::kConfidenceThresholdNotMet};
        }
        if (stability > stability_threshold) {
            return TransitionDecision{false, TransitionReason::kStabilityThresholdExceeded};
        }
        return TransitionDecision{true, TransitionReason::kNone};
    }

    if (to_state > from_state) {
        return TransitionDecision{true, TransitionReason::kNone};
    }
    return TransitionDecision{false, TransitionReason::kCannotRetreatVisualState};
}

bool check_black_to_gray(
    bool has_brightness,
    double brightness_confidence,
    bool has_focus,
    double focus_confidence,
    double threshold) {
    const double t = (finite(threshold) && threshold >= 0.0) ? threshold : 0.7;
    const bool brightness_pass = has_brightness && finite(brightness_confidence) &&
        brightness_confidence >= t;
    const bool focus_pass = has_focus && finite(focus_confidence) && focus_confidence >= t;
    return brightness_pass || focus_pass;
}

double smooth_speed(
    double current_speed,
    double target_speed,
    std::int64_t time_delta_ms,
    double max_change_rate,
    std::int64_t window_ms) {
    if (!finite(current_speed) || !finite(target_speed)) {
        return 0.0;
    }
    const std::int64_t win = (window_ms > 0) ? window_ms : kDefaultSpeedWindowMs;
    const std::int64_t dt_ms = std::max<std::int64_t>(0, time_delta_ms);
    const double rate = (finite(max_change_rate) && max_change_rate >= 0.0)
        ? max_change_rate
        : kDefaultMaxChangeRate;
    const double max_change = rate * (static_cast<double>(dt_ms) / static_cast<double>(win));
    const double delta = target_speed - current_speed;
    const double clamped_delta = std::max(-max_change, std::min(max_change, delta));
    const double out = current_speed + clamped_delta;
    return finite(out) ? out : 0.0;
}

double progress_speed_from_coverage_increment(int white_coverage_increment) {
    if (white_coverage_increment <= 0) {
        return 0.0;
    }
    const double speed = static_cast<double>(white_coverage_increment) * 0.1;
    if (!finite(speed)) {
        return 0.0;
    }
    return std::max(0.0, std::min(100.0, speed));
}

SpeedTier speed_tier_from_progress(double progress_speed) {
    const double s = finite(progress_speed)
        ? std::max(0.0, std::min(100.0, progress_speed))
        : 0.0;
    if (s >= 100.0) return SpeedTier::kExcellent;
    if (s >= 70.0) return SpeedTier::kGood;
    if (s >= 40.0) return SpeedTier::kModerate;
    if (s >= 15.0) return SpeedTier::kPoor;
    return SpeedTier::kStopped;
}

double animation_speed_for_tier(SpeedTier tier) {
    switch (tier) {
        case SpeedTier::kExcellent: return 100.0;
        case SpeedTier::kGood: return 85.0;
        case SpeedTier::kModerate: return 60.0;
        case SpeedTier::kPoor: return 35.0;
        case SpeedTier::kStopped:
        default: return 5.0;
    }
}

NoProgressWarningStepResult step_no_progress_warning(
    NoProgressWarningState state,
    std::int64_t armed_time_ms,
    std::int64_t no_progress_duration_ms,
    std::int64_t now_ms,
    std::int64_t trigger_ms,
    std::int64_t cooldown_ms) {
    const std::int64_t trigger = (trigger_ms > 0) ? trigger_ms : kDefaultNoProgressTriggerMs;
    const std::int64_t cooldown = (cooldown_ms > 0) ? cooldown_ms : kDefaultNoProgressCooldownMs;
    const std::int64_t now = std::max<std::int64_t>(0, now_ms);
    const std::int64_t no_progress = std::max<std::int64_t>(0, no_progress_duration_ms);

    NoProgressWarningState next_state = state;
    std::int64_t next_armed_time = armed_time_ms;

    switch (state) {
        case NoProgressWarningState::kArmed:
            if (no_progress >= trigger) {
                next_state = NoProgressWarningState::kFired;
            }
            break;
        case NoProgressWarningState::kFired:
            next_state = NoProgressWarningState::kCooldown;
            next_armed_time = now;
            break;
        case NoProgressWarningState::kCooldown:
            if (next_armed_time >= 0 && (now - next_armed_time) >= cooldown) {
                next_state = NoProgressWarningState::kArmed;
                next_armed_time = -1;
            }
            break;
        default:
            next_state = NoProgressWarningState::kArmed;
            next_armed_time = -1;
            break;
    }

    return NoProgressWarningStepResult{
        next_state,
        next_armed_time,
        next_state == NoProgressWarningState::kFired
    };
}

double stopped_animation_alpha(std::int64_t timestamp_ms, double frequency_hz) {
    const double hz = (finite(frequency_hz) && frequency_hz > 0.0)
        ? frequency_hz
        : kDefaultStoppedAnimationHz;
    const std::int64_t period_ms = std::max<std::int64_t>(1, static_cast<std::int64_t>(1000.0 / hz));
    const std::int64_t ts = std::max<std::int64_t>(0, timestamp_ms);
    const double phase = static_cast<double>(ts % period_ms) / static_cast<double>(period_ms);
    const double alpha = 0.5 + 0.5 * std::sin(phase * 2.0 * 3.14159265358979323846);
    if (!finite(alpha)) {
        return 0.5;
    }
    return std::max(0.0, std::min(1.0, alpha));
}

bool trend_variance_in_window(
    const double* values,
    const std::int64_t* timestamps,
    std::size_t count,
    std::int64_t now_ms,
    std::int64_t window_ms,
    double* out_variance) {
    if (values == nullptr || timestamps == nullptr || out_variance == nullptr || count == 0u) {
        return false;
    }
    const std::int64_t window = (window_ms > 0) ? window_ms : kDefaultTrendWindowMs;
    const std::int64_t now = std::max<std::int64_t>(0, now_ms);
    const std::int64_t window_start = now - window;

    double sum = 0.0;
    std::size_t n = 0u;
    for (std::size_t i = 0u; i < count; ++i) {
        if (timestamps[i] < window_start) {
            continue;
        }
        const double v = values[i];
        if (!finite(v)) {
            return false;
        }
        sum += v;
        ++n;
    }
    if (n < 2u || !finite(sum)) {
        return false;
    }
    const double mean = sum / static_cast<double>(n);
    if (!finite(mean)) {
        return false;
    }

    double variance_acc = 0.0;
    for (std::size_t i = 0u; i < count; ++i) {
        if (timestamps[i] < window_start) {
            continue;
        }
        const double d = values[i] - mean;
        variance_acc += d * d;
    }
    if (!finite(variance_acc)) {
        return false;
    }
    *out_variance = variance_acc / static_cast<double>(n);
    return finite(*out_variance);
}

}  // namespace quality
}  // namespace aether

