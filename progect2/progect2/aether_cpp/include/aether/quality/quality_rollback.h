// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_QUALITY_QUALITY_ROLLBACK_H
#define AETHER_QUALITY_QUALITY_ROLLBACK_H

#ifdef __cplusplus

#include <cstdint>
#include <vector>

namespace aether {
namespace quality {

/// Configuration for quality rollback detection (Risk R7 mitigation).
///
/// Monitors PSNR over a sliding window and triggers rollback when
/// a sustained quality drop is detected.
struct RollbackConfig {
    /// PSNR drop threshold (dB) relative to initial PSNR.
    /// Rollback triggers if current PSNR < initial - drop_threshold.
    float drop_threshold{2.0f};

    /// Number of consecutive frames the PSNR must remain below
    /// threshold before rollback is triggered.
    std::uint32_t consecutive_frames{10};

    /// If true, freeze all densification when rollback is triggered
    /// to prevent further quality degradation.
    bool freeze_densification{true};
};

/// Quality drop detection and automatic rollback monitor.
///
/// Tracks PSNR over time and triggers a rollback signal when a
/// sustained quality regression is detected.  This addresses Risk R7
/// (catastrophic quality drop during online optimization).
///
/// Usage:
///   1. Construct with RollbackConfig.
///   2. Call update(psnr) each frame.
///   3. If update() returns true, rollback to the last known-good
///      checkpoint and optionally freeze densification.
///
/// Once triggered, the monitor stays in the triggered state until
/// reset() is called (typically after a successful rollback).
///
/// Thread safety: NOT thread-safe.  Caller must synchronize.
class QualityRollbackMonitor {
public:
    explicit QualityRollbackMonitor(const RollbackConfig& config);

    /// Update with the latest PSNR estimate.
    /// @param psnr_estimate  Current PSNR (dB).
    /// @return true if rollback was triggered this frame.
    bool update(float psnr_estimate);

    /// Returns true if rollback has been triggered (and not yet reset).
    bool triggered() const;

    /// Reset the monitor to its initial state.
    void reset();

private:
    RollbackConfig config_;

    /// Circular buffer of recent PSNR values for sliding window analysis.
    std::vector<float> psnr_history_;
    std::size_t history_pos_{0};
    std::size_t history_count_{0};

    /// Number of consecutive frames below the drop threshold.
    std::uint32_t consecutive_drop_count_{0};

    /// Whether rollback has been triggered.
    bool triggered_{false};

    /// The initial (baseline) PSNR captured from the first update.
    float initial_psnr_{0.0f};

    /// Whether the initial PSNR has been captured.
    bool has_initial_{false};
};

}  // namespace quality
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_QUALITY_QUALITY_ROLLBACK_H
