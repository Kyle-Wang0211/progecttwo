// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/frame_selector.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>

namespace aether {
namespace trainer {
namespace {

constexpr float kEps = 1e-7f;

/// Extract the forward direction (negative Z column) from a 4x4
/// column-major pose matrix.
struct Vec3 {
    float x{0.0f};
    float y{0.0f};
    float z{0.0f};
};

Vec3 extract_forward(const float pose[16]) {
    // Column 2 (indices 8,9,10) is the Z axis.
    // Camera looks along -Z in OpenGL convention.
    return Vec3{-pose[8], -pose[9], -pose[10]};
}

float vec3_dot(const Vec3& a, const Vec3& b) {
    return a.x * b.x + a.y * b.y + a.z * b.z;
}

float vec3_length(const Vec3& v) {
    return std::sqrt(std::max(0.0f, vec3_dot(v, v)));
}

/// Compute the angle (radians) between two direction vectors.
float angle_between(const Vec3& a, const Vec3& b) {
    const float la = vec3_length(a);
    const float lb = vec3_length(b);
    if (la < kEps || lb < kEps) {
        return 0.0f;
    }
    float cos_theta = vec3_dot(a, b) / (la * lb);
    cos_theta = std::max(-1.0f, std::min(1.0f, cos_theta));
    return std::acos(cos_theta);
}

/// Score penalty for very similar frame to reduce redundancy.
/// Returns a value in [0, 1] where 1 = maximally diverse.
float diversity_from_angle(float angle_rad) {
    // Normalize: 0 rad → 0, pi/4 (45 deg) → ~1.0, clamped to [0,1].
    constexpr float kMaxAngle = 0.7854f;  // pi/4
    return std::min(1.0f, angle_rad / kMaxAngle);
}

}  // namespace

FrameSelector::FrameSelector(const FrameSelectorConfig& config)
    : config_(config) {
    if (config_.max_cache_frames == 0) {
        config_.max_cache_frames = 64;
    }
    cache_.reserve(config_.max_cache_frames);
    scores_.reserve(config_.max_cache_frames);
}

float FrameSelector::compute_score(const CameraFrame& frame) const {
    return frame.information_gain +
           config_.diversity_weight * frame.motion_diversity;
}

bool FrameSelector::add_frame(const CameraFrame& frame) {
    // Reject frames that are too blurry.
    if (frame.blur_score < config_.blur_threshold) {
        return false;
    }

    // Reject frames with insufficient information gain.
    if (frame.information_gain < config_.min_information_gain) {
        return false;
    }

    // Compute motion diversity relative to the most recently added frame.
    CameraFrame enriched = frame;
    if (!cache_.empty()) {
        const CameraFrame& prev = cache_.back();
        const Vec3 curr_fwd = extract_forward(enriched.pose);
        const Vec3 prev_fwd = extract_forward(prev.pose);
        const float angle = angle_between(curr_fwd, prev_fwd);
        enriched.motion_diversity = diversity_from_angle(angle);
    } else {
        // First frame — maximum diversity by convention.
        enriched.motion_diversity = 1.0f;
    }

    const float score = compute_score(enriched);

    // If cache is full, evict the lowest-scored frame.
    if (cache_.size() >= config_.max_cache_frames) {
        // Find the index of the minimum score.
        std::size_t min_idx = 0;
        float min_score = scores_[0];
        for (std::size_t i = 1; i < scores_.size(); ++i) {
            if (scores_[i] < min_score) {
                min_score = scores_[i];
                min_idx = i;
            }
        }

        // Only evict if new frame is better than the worst cached frame.
        if (score <= min_score) {
            return false;
        }

        // Evict by overwriting the lowest-scored slot.
        cache_[min_idx] = enriched;
        scores_[min_idx] = score;
    } else {
        cache_.push_back(enriched);
        scores_.push_back(score);
    }

    return true;
}

const CameraFrame* FrameSelector::select_next() {
    if (cache_.empty()) {
        return nullptr;
    }

    // Find frame with highest composite score.
    std::size_t best_idx = 0;
    float best_score = scores_[0];
    for (std::size_t i = 1; i < scores_.size(); ++i) {
        if (scores_[i] > best_score) {
            best_score = scores_[i];
            best_idx = i;
        }
    }

    // Reduce selected frame's score to prevent immediate reuse.
    // Multiply by a decay factor so it sinks in priority but
    // can still be reselected after other frames are consumed.
    scores_[best_idx] *= 0.1f;

    return &cache_[best_idx];
}

std::size_t FrameSelector::cache_size() const {
    return cache_.size();
}

void FrameSelector::clear() {
    cache_.clear();
    scores_.clear();
}

}  // namespace trainer
}  // namespace aether
