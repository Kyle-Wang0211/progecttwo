// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_TRAINER_TRAINING_PIPELINE_H
#define AETHER_TRAINER_TRAINING_PIPELINE_H

#ifdef __cplusplus

#include "aether/core/status.h"
#include "aether/core/numeric_guard.h"
#include "aether/render/gpu_device.h"
#include "aether/render/gpu_resource.h"
#include "aether/render/gpu_command.h"
#include "aether/render/gaussian_shader_types.h"
#include "aether/render/gaussian_forward_pass.h"
#include "aether/innovation/packed_gaussian.h"
#include "aether/innovation/core_types.h"
#include "aether/trainer/tsdf_gaussian_bridge.h"
#include "aether/trainer/apollo_mini_optimizer.h"
#include "aether/trainer/evidence_gated_trainer.h"
#include "aether/trainer/gaussian_gradient_buffer.h"
#include "aether/evidence/evidence_state_machine.h"
#include "aether/quality/adaptive_quality_controller.h"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace aether {
namespace trainer {

// ---------------------------------------------------------------------------
// Forward declarations for components not yet included
// ---------------------------------------------------------------------------

struct BudgetDensifyConfig;
struct FrameSelectorConfig;
struct SecondOrderConfig;
struct LossConfig;
struct CheckpointConfig;
struct ScenePartitionConfig;

// ---------------------------------------------------------------------------
// Pipeline phase enumeration
// ---------------------------------------------------------------------------

enum class PipelinePhase : std::uint8_t {
    kIdle         = 0,
    kCapturing    = 1,
    kInitializing = 2,
    kTraining     = 3,
    kComplete     = 4,
    kFailed       = 5,
};

inline const char* pipeline_phase_name(PipelinePhase phase) {
    switch (phase) {
        case PipelinePhase::kIdle:         return "idle";
        case PipelinePhase::kCapturing:    return "capturing";
        case PipelinePhase::kInitializing: return "initializing";
        case PipelinePhase::kTraining:     return "training";
        case PipelinePhase::kComplete:     return "complete";
        case PipelinePhase::kFailed:       return "failed";
    }
    return "unknown";
}

// ---------------------------------------------------------------------------
// TrainingStepResult: output of a single training step
// ---------------------------------------------------------------------------

struct TrainingStepResult {
    float    loss{0.0f};
    float    psnr_estimate{0.0f};
    uint32_t gaussians_active{0};
    uint32_t training_step{0};
    PipelinePhase phase{PipelinePhase::kIdle};
    bool     checkpoint_saved{false};
};

// ---------------------------------------------------------------------------
// CameraFrame: input frame for training
// ---------------------------------------------------------------------------

struct CameraFrame {
    const float*    view_matrix;       // 4x4 column-major
    const float*    proj_matrix;       // 4x4 column-major
    const uint8_t*  image_rgba;        // RGBA8 pixel data
    const float*    depth_map;         // optional depth (nullptr if absent)
    uint32_t        width;
    uint32_t        height;
    uint64_t        frame_id;
    double          timestamp_s;
    float           camera_position[3];
    float           focal_x;
    float           focal_y;
};

// ---------------------------------------------------------------------------
// PipelineConfig: aggregate configuration for all sub-components
// ---------------------------------------------------------------------------

struct PipelineConfig {
    // Sub-component configs
    BridgeConfig                       bridge{};
    ApolloMiniConfig                   optimizer{};
    render::ForwardPassConfig          forward_pass{};
    EGTConfig                          egt{};
    quality::AdaptiveConfig            adaptive{};

    // Rendering resolution
    uint32_t render_width{1920};
    uint32_t render_height{1080};

    // Gaussian budget
    uint32_t max_gaussians{50000};

    // Training scheduling
    uint32_t densify_interval{100};
    uint32_t checkpoint_interval{500};
    uint32_t micro_step_subset_size{8};
    float    transmittance_threshold{0.001f};

    // Loss weights
    float loss_weight_rgb{1.0f};
    float loss_weight_depth{0.1f};
    float loss_weight_normal{0.05f};
    float loss_weight_reg{0.01f};

    // Second-order preconditioning
    float second_order_damping{0.1f};
    bool  enable_second_order{true};

    // Quality rollback
    float rollback_psnr_drop_threshold{2.0f};
    uint32_t rollback_window_steps{50};

    // Checkpoint directory (empty = no auto-save)
    std::string checkpoint_dir{};
};

// ---------------------------------------------------------------------------
// RealtimeQualityMetrics: lightweight quality snapshot
// ---------------------------------------------------------------------------

struct RealtimeQualityMetrics {
    float psnr{0.0f};
    float ssim_estimate{0.0f};
    float loss{0.0f};
    float coverage_ratio{0.0f};
    uint32_t gaussians_active{0};
    uint32_t training_step{0};
    quality::ThermalTier thermal_tier{quality::ThermalTier::kNominal};
};

// ---------------------------------------------------------------------------
// TrainingPipeline: main orchestrator
// ---------------------------------------------------------------------------
// Wires TSDFGaussianBridge, ApolloMiniOptimizer, GaussianForwardPass,
// EvidenceGatedTrainer, AdaptiveQualityController, gradient buffers, and
// densification into a coherent training loop.
//
// Thread safety: NOT thread-safe. Caller must synchronize.

class TrainingPipeline {
public:
    TrainingPipeline(std::unique_ptr<render::GPUDevice> gpu,
                     const PipelineConfig& config);
    ~TrainingPipeline();

    /// Initialize from TSDF voxel seeds.
    /// Extracts gaussian seeds, uploads to GPU, initializes optimizer state.
    core::Status initialize(const TSDFVoxelSeed* seeds, std::size_t seed_count);

    /// Capture-phase micro-step (~1ms budget).
    /// Performs mini forward/backward on a small gaussian subset.
    core::Status micro_step(const CameraFrame& frame, TrainingStepResult* result);

    /// Full training step (~20ms budget).
    /// Forward render, loss, backward, optimizer step, densification check.
    core::Status full_step(const CameraFrame& frame, TrainingStepResult* result);

    /// Current quality metrics snapshot.
    RealtimeQualityMetrics current_quality() const;

    /// Save full pipeline state to a checkpoint file.
    core::Status save_checkpoint(const char* path);

    /// Load pipeline state from a checkpoint file.
    core::Status load_checkpoint(const char* path);

    /// Current pipeline phase.
    PipelinePhase phase() const { return phase_; }

    /// Current training step counter.
    uint32_t training_step() const { return step_; }

    /// Number of active gaussians.
    uint32_t gaussian_count() const {
        return static_cast<uint32_t>(gaussians_.size());
    }

private:
    // ── Initialization helpers ──
    core::Status convert_seeds_to_packed(
        const std::vector<GaussianSeed>& seeds);
    core::Status upload_gaussians_to_gpu();
    void fill_uniforms(const CameraFrame& frame, render::GaussianUniforms* u) const;
    void fill_training_uniforms(render::GaussianTrainingUniforms* u) const;

    // ── Training helpers ──
    core::Status compute_loss(const CameraFrame& frame, float* out_loss);
    core::Status apply_egt_scaling(float* gradients, std::size_t count);
    core::Status apply_second_order(float* gradients, std::size_t count);
    core::Status evaluate_densification();
    core::Status check_quality_rollback();
    core::Status check_nan_quarantine();

    // ── GPU ownership ──
    std::unique_ptr<render::GPUDevice> gpu_;

    // ── Configuration ──
    PipelineConfig config_;

    // ── Sub-components ──
    TSDFGaussianBridge                  bridge_;
    ApolloMiniOptimizer                 optimizer_;
    render::GaussianForwardPass         forward_pass_;
    quality::AdaptiveQualityController  adaptive_controller_;
    GaussianGradientBuffer              gradient_buffer_;

    // ── Gaussian storage ──
    std::vector<innovation::PackedGaussian> gaussians_;
    std::vector<float>                      parameters_;      // unpacked flat params
    std::vector<float>                      best_parameters_; // for quality rollback

    // ── Evidence state per-gaussian ──
    std::vector<evidence::ColorState>       evidence_states_;

    // ── Frame buffer for frame selection ──
    static constexpr std::size_t kMaxStoredFrames = 64;
    struct StoredFrame {
        std::vector<uint8_t> image;
        std::vector<float>   depth;
        float view_matrix[16];
        float proj_matrix[16];
        float camera_position[3];
        float focal_x;
        float focal_y;
        uint32_t width;
        uint32_t height;
        uint64_t frame_id;
        double timestamp_s;
    };
    std::vector<StoredFrame> stored_frames_;
    uint64_t frame_insert_cursor_{0};

    // ── Quality tracking ──
    RealtimeQualityMetrics quality_metrics_{};
    float best_psnr_{0.0f};
    uint32_t best_psnr_step_{0};

    // ── Pipeline state ──
    PipelinePhase phase_{PipelinePhase::kIdle};
    uint32_t step_{0};
    uint32_t next_gaussian_id_{0};

    // Non-copyable
    TrainingPipeline(const TrainingPipeline&) = delete;
    TrainingPipeline& operator=(const TrainingPipeline&) = delete;
};

}  // namespace trainer
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_TRAINER_TRAINING_PIPELINE_H
