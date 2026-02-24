// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_TRAINER_FRAME_SELECTOR_H
#define AETHER_TRAINER_FRAME_SELECTOR_H

#ifdef __cplusplus

#include <cstddef>
#include <cstdint>
#include <vector>

namespace aether {
namespace trainer {

/// Camera frame data for training frame selection.
///
/// Contains the RGB/depth imagery, camera pose, and pre-computed
/// quality signals used for information-gain-based selection.
struct CameraFrame {
    std::uint64_t frame_id{0};

    /// 4x4 column-major camera-to-world pose matrix.
    float pose[16]{};

    /// Pointer to RGB image data (width * height * 3 floats).
    /// Owned externally — the selector does not manage this memory.
    float* rgb_data{nullptr};

    /// Pointer to depth image data (width * height floats).
    /// Owned externally.
    float* depth_data{nullptr};

    std::uint32_t width{0};
    std::uint32_t height{0};

    /// Pre-computed information gain score [0, inf).
    float information_gain{0.0f};

    /// Blur metric — higher is sharper.  Frames below
    /// blur_threshold are rejected.
    float blur_score{0.0f};

    /// Motion diversity score — angular difference from previous
    /// frame's viewing direction.  Higher = more diverse viewpoint.
    float motion_diversity{0.0f};
};

/// Configuration for the frame selector.
struct FrameSelectorConfig {
    /// Maximum number of frames to cache (circular buffer).
    std::size_t max_cache_frames{64};

    /// Minimum information gain to accept a frame.
    float min_information_gain{0.01f};

    /// Minimum blur score — frames below this are rejected (too blurry).
    float blur_threshold{100.0f};

    /// Weight of motion diversity in the composite score.
    float diversity_weight{0.3f};
};

/// Training frame selector with information gain scoring.
///
/// Maintains a circular buffer of camera frames ranked by a composite
/// score combining information gain and motion diversity.  Blurry
/// frames are rejected outright.  When the cache is full, the
/// lowest-scored frame is evicted.
///
/// Thread safety: NOT thread-safe.  Caller must synchronize.
class FrameSelector {
public:
    explicit FrameSelector(const FrameSelectorConfig& config);

    /// Add a frame to the cache.
    /// @return true if the frame was accepted, false if rejected (e.g., too blurry).
    bool add_frame(const CameraFrame& frame);

    /// Select the next best training frame (highest composite score).
    /// Reduces the selected frame's score to prevent immediate reuse.
    /// @return Pointer to the selected frame, or nullptr if cache is empty.
    const CameraFrame* select_next();

    /// Current number of frames in the cache.
    std::size_t cache_size() const;

    /// Clear all cached frames.
    void clear();

private:
    /// Compute composite score for a frame.
    float compute_score(const CameraFrame& frame) const;

    FrameSelectorConfig config_;
    std::vector<CameraFrame> cache_;
    std::vector<float> scores_;
};

}  // namespace trainer
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_TRAINER_FRAME_SELECTOR_H
