// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_QUALITY_SPEED_STATE_H
#define AETHER_QUALITY_SPEED_STATE_H

#include <cstddef>
#include <cstdint>

namespace aether {
namespace quality {

enum class VisualState : int {
    kBlack = 0,
    kGray = 1,
    kWhite = 2,
    kClear = 3,
};

enum class FpsTier : int {
    kFull = 0,
    kDegraded = 1,
    kEmergency = 2,
};

enum class SpeedTier : int {
    kExcellent = 0,
    kGood = 1,
    kModerate = 2,
    kPoor = 3,
    kStopped = 4,
};

enum class TransitionReason : int {
    kNone = 0,
    kOnlyFullTierAllowsGrayToWhite = 1,
    kMissingCriticalMetrics = 2,
    kMissingStability = 3,
    kConfidenceThresholdNotMet = 4,
    kStabilityThresholdExceeded = 5,
    kCannotRetreatVisualState = 6,
    kInvalidVisualState = 7,
};

struct TransitionDecision {
    bool allowed{false};
    TransitionReason reason{TransitionReason::kNone};
};

enum class NoProgressWarningState : int {
    kArmed = 0,
    kFired = 1,
    kCooldown = 2,
};

struct NoProgressWarningStepResult {
    NoProgressWarningState state{NoProgressWarningState::kArmed};
    std::int64_t armed_time_ms{-1};
    bool warning_active{false};
};

int monotonic_visual_state_update(int current_state, int new_state);

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
    double full_white_stability_max);

bool check_black_to_gray(
    bool has_brightness,
    double brightness_confidence,
    bool has_focus,
    double focus_confidence,
    double threshold);

double smooth_speed(
    double current_speed,
    double target_speed,
    std::int64_t time_delta_ms,
    double max_change_rate,
    std::int64_t window_ms);

double progress_speed_from_coverage_increment(int white_coverage_increment);
SpeedTier speed_tier_from_progress(double progress_speed);
double animation_speed_for_tier(SpeedTier tier);

NoProgressWarningStepResult step_no_progress_warning(
    NoProgressWarningState state,
    std::int64_t armed_time_ms,
    std::int64_t no_progress_duration_ms,
    std::int64_t now_ms,
    std::int64_t trigger_ms,
    std::int64_t cooldown_ms);

double stopped_animation_alpha(std::int64_t timestamp_ms, double frequency_hz);

bool trend_variance_in_window(
    const double* values,
    const std::int64_t* timestamps,
    std::size_t count,
    std::int64_t now_ms,
    std::int64_t window_ms,
    double* out_variance);

}  // namespace quality
}  // namespace aether

#endif  // AETHER_QUALITY_SPEED_STATE_H

