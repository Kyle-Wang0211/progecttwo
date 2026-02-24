// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/environment_light_estimator.h"

#include <algorithm>
#include <cmath>
#include <cstring>

namespace aether {
namespace quality {

namespace {

constexpr float kFallbackDirectionDefault[3] = {0.0f, 1.0f, 0.0f};
constexpr float kOneOverFrame60 = 1.0f / 60.0f;
constexpr float kMinDt = 1.0f / 240.0f;
constexpr float kMaxDt = 0.5f;
constexpr float kSHY00 = 0.28209479177387814f;
constexpr float kSHY1 = 0.4886025119029199f;
constexpr float kSHY2_0 = 1.0925484305920792f;
constexpr float kSHY2_1 = 0.31539156525252005f;
constexpr float kSHY2_2 = 0.5462742152960396f;

float clampf(const float v, const float lo, const float hi) {
    return std::max(lo, std::min(hi, v));
}

bool finite3(const float v[3]) {
    return std::isfinite(v[0]) && std::isfinite(v[1]) && std::isfinite(v[2]);
}

void copy3(float dst[3], const float src[3]) {
    dst[0] = src[0];
    dst[1] = src[1];
    dst[2] = src[2];
}

void normalize3(float v[3], const float fallback[3]) {
    if (!finite3(v)) {
        copy3(v, fallback);
    }
    const float n2 = v[0] * v[0] + v[1] * v[1] + v[2] * v[2];
    if (!std::isfinite(n2) || n2 < 1e-12f) {
        copy3(v, fallback);
        return;
    }
    const float inv = 1.0f / std::sqrt(n2);
    v[0] *= inv;
    v[1] *= inv;
    v[2] *= inv;
}

float alpha_for_dt(float alpha_60fps, double dt_s) {
    const float a = clampf(alpha_60fps, 0.0f, 1.0f);
    if (a <= 0.0f) return 0.0f;
    if (a >= 1.0f) return 1.0f;
    const double dt = std::isfinite(dt_s) ? dt_s : static_cast<double>(kOneOverFrame60);
    const double scaled_steps = clampf(static_cast<float>(dt * 60.0), 0.0f, 8.0f);
    return static_cast<float>(1.0 - std::pow(1.0 - a, scaled_steps));
}

void build_sh_from_directional(
    const float direction[3],
    float intensity,
    float out_sh[27]) {
    const float d[3] = {direction[0], direction[1], direction[2]};
    const float i = std::max(0.0f, intensity);

    const float ambient = i * 0.45f;
    const float direct = i * 0.55f;

    float mono[9]{};
    mono[0] = ambient * kSHY00;
    mono[1] = direct * d[1] * kSHY1;
    mono[2] = direct * d[2] * kSHY1;
    mono[3] = direct * d[0] * kSHY1;
    mono[4] = direct * d[0] * d[1] * kSHY2_0 * 0.25f;
    mono[5] = direct * d[1] * d[2] * kSHY2_0 * 0.25f;
    mono[6] = direct * (3.0f * d[2] * d[2] - 1.0f) * kSHY2_1 * 0.25f;
    mono[7] = direct * d[0] * d[2] * kSHY2_0 * 0.25f;
    mono[8] = direct * (d[0] * d[0] - d[1] * d[1]) * kSHY2_2 * 0.25f;

    for (int i_coeff = 0; i_coeff < 9; ++i_coeff) {
        const int base = i_coeff * 3;
        out_sh[base + 0] = mono[i_coeff];
        out_sh[base + 1] = mono[i_coeff];
        out_sh[base + 2] = mono[i_coeff];
    }
}

bool finite_sh(const float sh[27]) {
    for (int i = 0; i < 27; ++i) {
        if (!std::isfinite(sh[i])) return false;
    }
    return true;
}

}  // namespace

EnvironmentLightEstimator::EnvironmentLightEstimator(const EnvironmentLightConfig& config)
    : config_(config) {
    if (!finite3(config_.fallback_direction)) {
        std::memcpy(config_.fallback_direction, kFallbackDirectionDefault, sizeof(kFallbackDirectionDefault));
    }
    normalize3(config_.fallback_direction, kFallbackDirectionDefault);

    if (!std::isfinite(config_.min_intensity) || !std::isfinite(config_.max_intensity) ||
        config_.min_intensity <= 0.0f || config_.max_intensity <= config_.min_intensity) {
        config_.min_intensity = 0.05f;
        config_.max_intensity = 4.0f;
    }
    if (!std::isfinite(config_.fallback_intensity)) {
        config_.fallback_intensity = 1.0f;
    }
    config_.fallback_intensity = clampf(config_.fallback_intensity, config_.min_intensity, config_.max_intensity);
    config_.rise_alpha = clampf(config_.rise_alpha, 0.001f, 1.0f);
    config_.fall_alpha = clampf(config_.fall_alpha, 0.001f, 1.0f);
    config_.direction_alpha = clampf(config_.direction_alpha, 0.001f, 1.0f);
    config_.sh_alpha = clampf(config_.sh_alpha, 0.001f, 1.0f);
    config_.missing_decay_per_s = std::isfinite(config_.missing_decay_per_s)
        ? std::max(0.0f, config_.missing_decay_per_s)
        : 2.5f;
    config_.max_missing_hold_s = std::isfinite(config_.max_missing_hold_s)
        ? std::max(0.0f, config_.max_missing_hold_s)
        : 0.6f;

    reset();
}

void EnvironmentLightEstimator::reset() {
    state_ = EnvironmentLightState{};
    state_.tier = 2;
    copy3(state_.direction, config_.fallback_direction);
    state_.intensity = config_.fallback_intensity;
    build_sh_from_directional(state_.direction, state_.intensity, state_.sh_coeffs_rgb);
    state_.missing_seconds = 0.0f;
    initialized_ = false;
    has_last_timestamp_ = false;
    last_timestamp_s_ = 0.0;
}

void EnvironmentLightEstimator::set_state_from_observation(
    const EnvironmentLightObservation& observation) {
    const float intensity = clampf(
        std::isfinite(observation.intensity) ? observation.intensity : config_.fallback_intensity,
        config_.min_intensity,
        config_.max_intensity);
    state_.intensity = intensity;

    float target_dir[3] = {
        config_.fallback_direction[0],
        config_.fallback_direction[1],
        config_.fallback_direction[2],
    };
    if (observation.has_direction != 0 && finite3(observation.direction)) {
        copy3(target_dir, observation.direction);
    }
    normalize3(target_dir, config_.fallback_direction);
    copy3(state_.direction, target_dir);

    if (observation.has_sh != 0 && finite_sh(observation.sh_coeffs_rgb)) {
        std::memcpy(state_.sh_coeffs_rgb, observation.sh_coeffs_rgb, sizeof(state_.sh_coeffs_rgb));
    } else {
        build_sh_from_directional(state_.direction, state_.intensity, state_.sh_coeffs_rgb);
    }

    state_.tier = std::max(0, std::min(2, observation.source_tier));
    state_.missing_seconds = 0.0f;
}

const EnvironmentLightState& EnvironmentLightEstimator::step(
    const EnvironmentLightObservation* observation_or_null,
    double timestamp_s) {
    double dt_s = static_cast<double>(kOneOverFrame60);
    if (std::isfinite(timestamp_s)) {
        if (has_last_timestamp_) {
            const double raw_dt = timestamp_s - last_timestamp_s_;
            if (std::isfinite(raw_dt) && raw_dt > 0.0) {
                dt_s = std::max(static_cast<double>(kMinDt), std::min(raw_dt, static_cast<double>(kMaxDt)));
            }
        }
        last_timestamp_s_ = timestamp_s;
        has_last_timestamp_ = true;
    }

    const bool has_observation =
        observation_or_null != nullptr && std::isfinite(observation_or_null->intensity);

    if (!initialized_) {
        initialized_ = true;
        if (has_observation) {
            set_state_from_observation(*observation_or_null);
        }
        return state_;
    }

    if (has_observation) {
        const EnvironmentLightObservation& observation = *observation_or_null;

        state_.missing_seconds = 0.0f;
        const float target_intensity = clampf(
            observation.intensity,
            config_.min_intensity,
            config_.max_intensity);
        const float intensity_alpha = alpha_for_dt(
            target_intensity >= state_.intensity ? config_.rise_alpha : config_.fall_alpha,
            dt_s);
        state_.intensity += (target_intensity - state_.intensity) * intensity_alpha;
        state_.intensity = clampf(state_.intensity, config_.min_intensity, config_.max_intensity);

        float target_dir[3] = {
            config_.fallback_direction[0],
            config_.fallback_direction[1],
            config_.fallback_direction[2],
        };
        if (observation.has_direction != 0 && finite3(observation.direction)) {
            copy3(target_dir, observation.direction);
        }
        normalize3(target_dir, config_.fallback_direction);
        const float direction_alpha = alpha_for_dt(config_.direction_alpha, dt_s);
        float blended_dir[3] = {
            state_.direction[0] + (target_dir[0] - state_.direction[0]) * direction_alpha,
            state_.direction[1] + (target_dir[1] - state_.direction[1]) * direction_alpha,
            state_.direction[2] + (target_dir[2] - state_.direction[2]) * direction_alpha,
        };
        normalize3(blended_dir, config_.fallback_direction);
        copy3(state_.direction, blended_dir);

        float target_sh[27]{};
        if (observation.has_sh != 0 && finite_sh(observation.sh_coeffs_rgb)) {
            std::memcpy(target_sh, observation.sh_coeffs_rgb, sizeof(target_sh));
        } else {
            build_sh_from_directional(state_.direction, state_.intensity, target_sh);
        }
        const float sh_alpha = alpha_for_dt(config_.sh_alpha, dt_s);
        for (int i = 0; i < 27; ++i) {
            state_.sh_coeffs_rgb[i] += (target_sh[i] - state_.sh_coeffs_rgb[i]) * sh_alpha;
            if (!std::isfinite(state_.sh_coeffs_rgb[i])) {
                state_.sh_coeffs_rgb[i] = target_sh[i];
            }
        }

        state_.tier = std::max(0, std::min(2, observation.source_tier));
    } else {
        state_.missing_seconds += static_cast<float>(dt_s);
        const float decay = std::exp(-config_.missing_decay_per_s * static_cast<float>(dt_s));

        state_.intensity =
            config_.fallback_intensity + (state_.intensity - config_.fallback_intensity) * decay;
        state_.intensity = clampf(state_.intensity, config_.min_intensity, config_.max_intensity);

        float fallback_sh[27]{};
        build_sh_from_directional(config_.fallback_direction, config_.fallback_intensity, fallback_sh);
        for (int i = 0; i < 27; ++i) {
            state_.sh_coeffs_rgb[i] = fallback_sh[i] + (state_.sh_coeffs_rgb[i] - fallback_sh[i]) * decay;
            if (!std::isfinite(state_.sh_coeffs_rgb[i])) {
                state_.sh_coeffs_rgb[i] = fallback_sh[i];
            }
        }

        const float direction_alpha = alpha_for_dt(config_.direction_alpha * 0.5f, dt_s);
        float blended_dir[3] = {
            state_.direction[0] +
                (config_.fallback_direction[0] - state_.direction[0]) * direction_alpha,
            state_.direction[1] +
                (config_.fallback_direction[1] - state_.direction[1]) * direction_alpha,
            state_.direction[2] +
                (config_.fallback_direction[2] - state_.direction[2]) * direction_alpha,
        };
        normalize3(blended_dir, config_.fallback_direction);
        copy3(state_.direction, blended_dir);

        if (state_.missing_seconds >= config_.max_missing_hold_s) {
            state_.tier = 2;
        }
    }

    // Keep ambient SH floor finite to avoid dark flicker in the shader fallback path.
    const float ambient_floor = std::max(0.02f, state_.intensity * 0.08f) * kSHY00;
    for (int channel = 0; channel < 3; ++channel) {
        const int idx = channel;
        if (!std::isfinite(state_.sh_coeffs_rgb[idx]) || state_.sh_coeffs_rgb[idx] < ambient_floor) {
            state_.sh_coeffs_rgb[idx] = ambient_floor;
        }
    }

    return state_;
}

}  // namespace quality
}  // namespace aether
