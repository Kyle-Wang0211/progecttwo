// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/quality_rollback.h"

#include <algorithm>
#include <cmath>

namespace aether {
namespace quality {

QualityRollbackMonitor::QualityRollbackMonitor(const RollbackConfig& config)
    : config_(config) {
    // Sanitize configuration.
    if (config_.drop_threshold <= 0.0f) {
        config_.drop_threshold = 2.0f;
    }
    if (config_.consecutive_frames == 0) {
        config_.consecutive_frames = 10;
    }

    // Allocate sliding window buffer.
    // Size = 2x consecutive_frames to provide sufficient history context.
    const std::size_t window_size =
        static_cast<std::size_t>(config_.consecutive_frames) * 2;
    psnr_history_.resize(std::max(window_size, static_cast<std::size_t>(16)), 0.0f);
}

bool QualityRollbackMonitor::update(float psnr_estimate) {
    // Once triggered, stay triggered until reset().
    if (triggered_) {
        return false;  // Already triggered — no new trigger event.
    }

    // Reject non-finite values.
    if (!std::isfinite(psnr_estimate)) {
        return false;
    }

    // Capture initial PSNR on the first valid update.
    // Use a sliding maximum of the first few frames to establish
    // a robust baseline.
    if (!has_initial_) {
        initial_psnr_ = psnr_estimate;
        has_initial_ = true;
    } else if (history_count_ < static_cast<std::size_t>(config_.consecutive_frames)) {
        // During warm-up, track the maximum PSNR as the baseline.
        initial_psnr_ = std::max(initial_psnr_, psnr_estimate);
    }

    // Store in circular buffer.
    psnr_history_[history_pos_] = psnr_estimate;
    history_pos_ = (history_pos_ + 1) % psnr_history_.size();
    if (history_count_ < psnr_history_.size()) {
        ++history_count_;
    }

    // Check for sustained quality drop.
    const float threshold = initial_psnr_ - config_.drop_threshold;

    if (psnr_estimate < threshold) {
        ++consecutive_drop_count_;
    } else {
        consecutive_drop_count_ = 0;
    }

    // Trigger rollback if the drop persists for consecutive_frames.
    if (consecutive_drop_count_ >= config_.consecutive_frames) {
        triggered_ = true;
        return true;
    }

    return false;
}

bool QualityRollbackMonitor::triggered() const {
    return triggered_;
}

void QualityRollbackMonitor::reset() {
    consecutive_drop_count_ = 0;
    triggered_ = false;
    has_initial_ = false;
    initial_psnr_ = 0.0f;
    history_pos_ = 0;
    history_count_ = 0;
    std::fill(psnr_history_.begin(), psnr_history_.end(), 0.0f);
}

}  // namespace quality
}  // namespace aether
