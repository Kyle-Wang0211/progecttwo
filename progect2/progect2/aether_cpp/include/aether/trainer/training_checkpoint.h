// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_TRAINER_TRAINING_CHECKPOINT_H
#define AETHER_TRAINER_TRAINING_CHECKPOINT_H

#ifdef __cplusplus

#include "aether/core/status.h"

#include <cstddef>
#include <cstdint>
#include <vector>

namespace aether {
namespace trainer {

/// Magic number identifying an Aether3D training checkpoint file.
/// ASCII: 'A','E','T','H' = 0x41455448.
constexpr std::uint32_t kCheckpointMagic = 0x41455448u;

/// Current checkpoint format version.
constexpr std::uint32_t kCheckpointVersion = 1u;

/// Number of metric slots in the checkpoint.
constexpr std::size_t kCheckpointMetricSlots = 8;

/// Binary header for the training checkpoint file format.
///
/// Layout (32 bytes):
///   [0..3]   magic          — 0x41455448
///   [4..7]   version        — format version
///   [8..11]  num_gaussians  — gaussian count at save time
///   [12..15] optimizer_step — training iteration
///   [16..19] best_psnr      — best PSNR achieved (float)
///   [20..23] reserved       — padding for alignment
///   [24..31] timestamp_ms   — wall-clock time (ms since epoch)
struct CheckpointHeader {
    std::uint32_t magic{kCheckpointMagic};
    std::uint32_t version{kCheckpointVersion};
    std::uint32_t num_gaussians{0};
    std::uint32_t optimizer_step{0};
    float best_psnr{0.0f};
    std::uint32_t reserved{0};
    std::uint64_t timestamp_ms{0};
};

/// Complete training state for save/resume and rollback.
struct TrainingCheckpoint {
    CheckpointHeader header{};

    /// Serialized gaussian parameter data.
    std::vector<std::uint8_t> gaussian_data;

    /// Serialized optimizer state (momentum, variance, etc.).
    std::vector<std::uint8_t> optimizer_state;

    /// Metric slots: [0]=psnr, [1]=ssim, [2]=chamfer, [3]=coverage,
    /// [4..7]=reserved for future use.
    float metrics[kCheckpointMetricSlots]{};
};

/// Checkpoint manager for save/load/rollback of training state.
///
/// Supports saving arbitrary checkpoints to file and tracking the
/// "best" checkpoint (highest PSNR) in a directory for automatic
/// rollback on quality regression.
///
/// File format: binary, little-endian assumed (ARM64 / x86-64).
///   header (32 bytes)
///   gaussian_data_size (8 bytes, uint64)
///   gaussian_data (variable)
///   optimizer_state_size (8 bytes, uint64)
///   optimizer_state (variable)
///   metrics (32 bytes = 8 * float)
///
/// Thread safety: NOT thread-safe.  Caller must synchronize.
class CheckpointManager {
public:
    /// Save a checkpoint to the specified file path.
    core::Status save(const char* path, const TrainingCheckpoint& checkpoint);

    /// Load a checkpoint from the specified file path.
    core::Status load(const char* path, TrainingCheckpoint* checkpoint);

    /// Save as the "best" checkpoint if psnr > previous best.
    /// Writes to "<dir>/best_checkpoint.aeth" if the checkpoint's
    /// best_psnr exceeds the internally tracked best.
    core::Status save_best(const char* dir, const TrainingCheckpoint& checkpoint);

    /// Load the best checkpoint from the directory.
    core::Status load_best(const char* dir, TrainingCheckpoint* checkpoint);

private:
    float best_psnr_{-1.0f};
};

}  // namespace trainer
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_TRAINER_TRAINING_CHECKPOINT_H
