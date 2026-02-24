// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/speed_state.h"

#include <cmath>
#include <cstdio>

using namespace aether::quality;

namespace {

int g_failed = 0;

void check(bool cond, const char* msg, int line) {
    if (!cond) {
        std::fprintf(stderr, "FAIL [line %d]: %s\n", line, msg);
        ++g_failed;
    }
}

#define CHECK(cond) check((cond), #cond, __LINE__)

bool near(double a, double b, double eps = 1e-9) {
    return std::fabs(a - b) <= eps;
}

void test_visual_state_monotonic() {
    CHECK(monotonic_visual_state_update(1, 0) == 1);
    CHECK(monotonic_visual_state_update(1, 2) == 2);
}

void test_transition_policy() {
    const TransitionDecision blocked = can_transition(
        1, 2, static_cast<int>(FpsTier::kDegraded),
        true, 0.95, 0.95, true, 0.10, 0.80, 0.15);
    CHECK(!blocked.allowed);
    CHECK(blocked.reason == TransitionReason::kOnlyFullTierAllowsGrayToWhite);

    const TransitionDecision allowed = can_transition(
        1, 2, static_cast<int>(FpsTier::kFull),
        true, 0.85, 0.85, true, 0.10, 0.80, 0.15);
    CHECK(allowed.allowed);
    CHECK(allowed.reason == TransitionReason::kNone);
}

void test_speed_feedback_primitives() {
    CHECK(near(progress_speed_from_coverage_increment(0), 0.0));
    CHECK(near(progress_speed_from_coverage_increment(1000), 100.0));
    CHECK(speed_tier_from_progress(100.0) == SpeedTier::kExcellent);
    CHECK(near(animation_speed_for_tier(SpeedTier::kStopped), 5.0));
    CHECK(near(smooth_speed(0.0, 1.0, 200, 0.30, 200), 0.30));
}

void test_no_progress_warning() {
    NoProgressWarningStepResult s = step_no_progress_warning(
        NoProgressWarningState::kArmed, -1, 2500, 1000, 2000, 1000);
    CHECK(s.state == NoProgressWarningState::kFired);
    CHECK(s.warning_active);

    s = step_no_progress_warning(s.state, s.armed_time_ms, 2500, 1001, 2000, 1000);
    CHECK(s.state == NoProgressWarningState::kCooldown);
    CHECK(!s.warning_active);

    s = step_no_progress_warning(s.state, s.armed_time_ms, 0, 2505, 2000, 1000);
    CHECK(s.state == NoProgressWarningState::kArmed);
}

void test_stopped_animation_and_trend() {
    const double alpha = stopped_animation_alpha(1000, 0.5);
    CHECK(alpha >= 0.0 && alpha <= 1.0);

    const double values[4] = {0.1, 0.2, 0.3, 0.4};
    const std::int64_t ts[4] = {1000, 1100, 1200, 1300};
    double variance = 0.0;
    CHECK(trend_variance_in_window(values, ts, 4u, 1300, 500, &variance));
    CHECK(variance >= 0.0);
}

}  // namespace

int main() {
    test_visual_state_monotonic();
    test_transition_policy();
    test_speed_feedback_primitives();
    test_no_progress_warning();
    test_stopped_animation_and_trend();

    if (g_failed == 0) {
        std::fprintf(stdout, "speed_state_test: all tests passed\n");
    }
    return g_failed;
}

