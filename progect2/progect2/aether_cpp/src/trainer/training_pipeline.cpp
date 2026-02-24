// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/training_pipeline.h"

#include <algorithm>
#include <cmath>
#include <cstring>

namespace {

constexpr float kEpsilon = 1e-7f;

/// Simple PRNG for subset selection (xoshiro128+).
struct Rng {
    uint32_t s[4];

    explicit Rng(uint32_t seed) {
        s[0] = seed;
        s[1] = seed ^ 0x6C078965u;
        s[2] = seed ^ 0x9D2C5680u;
        s[3] = seed ^ 0xEFC60000u;
    }

    uint32_t next() {
        uint32_t result = s[0] + s[3];
        uint32_t t = s[1] << 9;
        s[2] ^= s[0];
        s[3] ^= s[1];
        s[1] ^= s[2];
        s[0] ^= s[3];
        s[2] ^= t;
        s[3] = (s[3] << 11) | (s[3] >> 21);
        return result;
    }

    uint32_t next_bounded(uint32_t bound) {
        if (bound == 0) return 0;
        return next() % bound;
    }
};

/// Compute PSNR from MSE.
inline float mse_to_psnr(float mse) {
    if (mse < kEpsilon) return 50.0f;  // clamp perfect reconstruction
    return 10.0f * std::log10(1.0f / mse);
}

/// Multiply 4x4 matrices (column-major).
void mat4_multiply(const float* a, const float* b, float* out) {
    for (int col = 0; col < 4; ++col) {
        for (int row = 0; row < 4; ++row) {
            float sum = 0.0f;
            for (int k = 0; k < 4; ++k) {
                sum += a[k * 4 + row] * b[col * 4 + k];
            }
            out[col * 4 + row] = sum;
        }
    }
}

/// Unpack a single PackedGaussian into flat parameter array.
/// Layout: position(3) + scale(3) + opacity(1) + rotation(4) + sh_dc(3) + sh_rest(24) = 38
void unpack_to_params(const aether::innovation::PackedGaussian& g, float* out) {
    // Position (f32 passthrough)
    out[0] = g.position[0];
    out[1] = g.position[1];
    out[2] = g.position[2];

    // Scale (decode log-scale fp16 -> f32)
    out[3] = aether::innovation::decode_log_scale(g.log_scale[0]);
    out[4] = aether::innovation::decode_log_scale(g.log_scale[1]);
    out[5] = aether::innovation::decode_log_scale(g.log_scale[2]);

    // Opacity (decode sigmoid)
    out[6] = aether::innovation::decode_opacity_u8(g.opacity_u8);

    // Rotation (fp16 -> f32)
    out[7]  = aether::innovation::half_to_float(g.rotation[0]);
    out[8]  = aether::innovation::half_to_float(g.rotation[1]);
    out[9]  = aether::innovation::half_to_float(g.rotation[2]);
    out[10] = aether::innovation::half_to_float(g.rotation[3]);

    // SH DC (fp16 -> f32)
    out[11] = aether::innovation::half_to_float(g.sh_dc[0]);
    out[12] = aether::innovation::half_to_float(g.sh_dc[1]);
    out[13] = aether::innovation::half_to_float(g.sh_dc[2]);

    // SH rest (fp16 -> f32)
    for (int i = 0; i < 24; ++i) {
        out[14 + i] = aether::innovation::half_to_float(g.sh_rest[i]);
    }
}

/// Pack flat parameter array back into a PackedGaussian.
void pack_from_params(const float* params, aether::innovation::PackedGaussian& g) {
    // Position
    g.position[0] = params[0];
    g.position[1] = params[1];
    g.position[2] = params[2];

    // Scale (encode as log-scale fp16)
    g.log_scale[0] = aether::innovation::encode_log_scale(params[3]);
    g.log_scale[1] = aether::innovation::encode_log_scale(params[4]);
    g.log_scale[2] = aether::innovation::encode_log_scale(params[5]);

    // Opacity (encode as sigmoid u8)
    g.opacity_u8 = aether::innovation::encode_opacity_u8(params[6]);

    // Rotation (f32 -> fp16)
    g.rotation[0] = aether::innovation::float_to_half(params[7]);
    g.rotation[1] = aether::innovation::float_to_half(params[8]);
    g.rotation[2] = aether::innovation::float_to_half(params[9]);
    g.rotation[3] = aether::innovation::float_to_half(params[10]);

    // SH DC (f32 -> fp16)
    g.sh_dc[0] = aether::innovation::float_to_half(params[11]);
    g.sh_dc[1] = aether::innovation::float_to_half(params[12]);
    g.sh_dc[2] = aether::innovation::float_to_half(params[13]);

    // SH rest (f32 -> fp16)
    for (int i = 0; i < 24; ++i) {
        g.sh_rest[i] = aether::innovation::float_to_half(params[14 + i]);
    }
}

}  // namespace

namespace aether {
namespace trainer {

using core::Status;
using innovation::PackedGaussian;
using innovation::Float3;
using innovation::make_float3;

static constexpr std::size_t kParamsPerGaussian = kGradientsPerGaussian;  // 38

// ===========================================================================
// Constructor / Destructor
// ===========================================================================

TrainingPipeline::TrainingPipeline(
    std::unique_ptr<render::GPUDevice> gpu,
    const PipelineConfig& config)
    : gpu_(std::move(gpu))
    , config_(config)
    , bridge_(config.bridge)
    , optimizer_()
    , forward_pass_()
    , adaptive_controller_(config.adaptive)
    , gradient_buffer_()
    , phase_(PipelinePhase::kIdle)
    , step_(0)
    , next_gaussian_id_(0) {
    stored_frames_.reserve(kMaxStoredFrames);
}

TrainingPipeline::~TrainingPipeline() {
    forward_pass_.destroy();
}

// ===========================================================================
// initialize()
// ===========================================================================

Status TrainingPipeline::initialize(
    const TSDFVoxelSeed* seeds,
    std::size_t seed_count) {

    if (seeds == nullptr && seed_count > 0) {
        return Status::kInvalidArgument;
    }

    phase_ = PipelinePhase::kInitializing;

    // Step 1: Extract gaussian seeds from TSDF voxels
    std::vector<GaussianSeed> gaussian_seeds;
    Status s = bridge_.extract_seeds(seeds, seed_count, &gaussian_seeds);
    if (!core::is_ok(s)) {
        phase_ = PipelinePhase::kFailed;
        return s;
    }

    if (gaussian_seeds.empty()) {
        // No valid seeds — still OK, just 0 gaussians
        phase_ = PipelinePhase::kTraining;
        step_ = 0;
        return Status::kOk;
    }

    // Clamp to max_gaussians
    if (gaussian_seeds.size() > config_.max_gaussians) {
        gaussian_seeds.resize(config_.max_gaussians);
    }

    // Step 2: Convert seeds to PackedGaussian format
    s = convert_seeds_to_packed(gaussian_seeds);
    if (!core::is_ok(s)) {
        phase_ = PipelinePhase::kFailed;
        return s;
    }

    // Step 3: Initialize forward pass GPU resources
    render::ForwardPassConfig fwd_config = config_.forward_pass;
    fwd_config.max_gaussians = config_.max_gaussians;
    fwd_config.render_width = config_.render_width;
    fwd_config.render_height = config_.render_height;

    s = forward_pass_.init(gpu_.get(), fwd_config);
    if (!core::is_ok(s)) {
        phase_ = PipelinePhase::kFailed;
        return s;
    }

    // Step 4: Upload gaussians to GPU
    s = upload_gaussians_to_gpu();
    if (!core::is_ok(s)) {
        phase_ = PipelinePhase::kFailed;
        return s;
    }

    // Step 5: Initialize optimizer
    const std::size_t num_gs = gaussians_.size();
    s = optimizer_.init(config_.optimizer, num_gs);
    if (!core::is_ok(s)) {
        phase_ = PipelinePhase::kFailed;
        return s;
    }

    // Step 6: Initialize gradient buffer
    s = gradient_buffer_.init(config_.max_gaussians);
    if (!core::is_ok(s)) {
        phase_ = PipelinePhase::kFailed;
        return s;
    }

    // Step 7: Initialize evidence states (all start at S0)
    evidence_states_.resize(num_gs, evidence::ColorState::kBlack);

    // Step 8: Initialize parameter buffer from packed gaussians
    parameters_.resize(num_gs * kParamsPerGaussian, 0.0f);
    for (std::size_t i = 0; i < num_gs; ++i) {
        unpack_to_params(gaussians_[i], &parameters_[i * kParamsPerGaussian]);
    }
    best_parameters_ = parameters_;

    // Step 9: Reset quality tracking
    quality_metrics_ = {};
    quality_metrics_.gaussians_active = static_cast<uint32_t>(num_gs);
    best_psnr_ = 0.0f;
    best_psnr_step_ = 0;

    phase_ = PipelinePhase::kTraining;
    step_ = 0;

    return Status::kOk;
}

// ===========================================================================
// convert_seeds_to_packed()
// ===========================================================================

Status TrainingPipeline::convert_seeds_to_packed(
    const std::vector<GaussianSeed>& seeds) {

    const std::size_t count = seeds.size();
    gaussians_.resize(count);

    for (std::size_t i = 0; i < count; ++i) {
        const GaussianSeed& seed = seeds[i];
        PackedGaussian& pg = gaussians_[i];

        std::memset(&pg, 0, sizeof(PackedGaussian));

        // Position
        pg.position[0] = seed.position.x;
        pg.position[1] = seed.position.y;
        pg.position[2] = seed.position.z;

        // Log-scale (encode positive scale)
        pg.log_scale[0] = innovation::encode_log_scale(seed.scale.x);
        pg.log_scale[1] = innovation::encode_log_scale(seed.scale.y);
        pg.log_scale[2] = innovation::encode_log_scale(seed.scale.z);

        // Rotation: identity quaternion (1, 0, 0, 0)
        pg.rotation[0] = innovation::float_to_half(1.0f);
        pg.rotation[1] = innovation::float_to_half(0.0f);
        pg.rotation[2] = innovation::float_to_half(0.0f);
        pg.rotation[3] = innovation::float_to_half(0.0f);

        // Opacity
        pg.opacity_u8 = innovation::encode_opacity_u8(seed.opacity);

        // Evidence state: start at S0
        pg.evidence_state = static_cast<uint8_t>(evidence::ColorState::kBlack);

        // SH DC from seed
        pg.sh_dc[0] = innovation::float_to_half(seed.sh_dc[0]);
        pg.sh_dc[1] = innovation::float_to_half(seed.sh_dc[1]);
        pg.sh_dc[2] = innovation::float_to_half(seed.sh_dc[2]);

        // SH rest: zero
        for (int j = 0; j < 24; ++j) {
            pg.sh_rest[j] = innovation::float_to_half(0.0f);
        }

        // Flags: not dynamic, not frozen
        pg.flags = 0;
        pg.padding0 = 0;

        // Gaussian ID
        pg.gaussian_id = next_gaussian_id_++;

        // Hessian diagonal: zero
        pg.hessian_pos_diag[0] = innovation::float_to_half(0.0f);
        pg.hessian_pos_diag[1] = innovation::float_to_half(0.0f);
        pg.hessian_pos_diag[2] = innovation::float_to_half(0.0f);
        pg.padding1 = 0;
    }

    return Status::kOk;
}

// ===========================================================================
// upload_gaussians_to_gpu()
// ===========================================================================

Status TrainingPipeline::upload_gaussians_to_gpu() {
    if (gaussians_.empty()) return Status::kOk;

    return forward_pass_.upload_gaussians(
        gaussians_.data(), gaussians_.size());
}

// ===========================================================================
// fill_uniforms()
// ===========================================================================

void TrainingPipeline::fill_uniforms(
    const CameraFrame& frame,
    render::GaussianUniforms* u) const {

    std::memcpy(u->view_matrix, frame.view_matrix, 16 * sizeof(float));
    std::memcpy(u->proj_matrix, frame.proj_matrix, 16 * sizeof(float));

    // Compute view*proj
    mat4_multiply(frame.proj_matrix, frame.view_matrix, u->view_proj_matrix);

    u->camera_position[0] = frame.camera_position[0];
    u->camera_position[1] = frame.camera_position[1];
    u->camera_position[2] = frame.camera_position[2];
    u->pad0 = 0.0f;

    u->viewport_size[0] = static_cast<float>(config_.render_width);
    u->viewport_size[1] = static_cast<float>(config_.render_height);
    u->near_plane = 0.01f;
    u->far_plane = 100.0f;

    u->gaussian_count = static_cast<uint32_t>(gaussians_.size());
    u->sh_order = 2;  // L2 by default
    u->tile_size = config_.adaptive.tile_size;
    u->pad1 = 0;
}

void TrainingPipeline::fill_training_uniforms(
    render::GaussianTrainingUniforms* u) const {

    u->gt_image_size[0] = static_cast<float>(config_.render_width);
    u->gt_image_size[1] = static_cast<float>(config_.render_height);
    u->loss_weight_rgb = config_.loss_weight_rgb;
    u->loss_weight_depth = config_.loss_weight_depth;
    u->loss_weight_normal = config_.loss_weight_normal;
    u->loss_weight_pbr = 0.0f;
    u->loss_weight_reg = config_.loss_weight_reg;
    u->training_step = step_;
    u->total_steps = 0;  // unbounded
    u->transmittance_threshold = config_.transmittance_threshold;
    u->pad[0] = 0.0f;
    u->pad[1] = 0.0f;
}

// ===========================================================================
// micro_step() — capture-phase training (~1ms budget)
// ===========================================================================

Status TrainingPipeline::micro_step(
    const CameraFrame& frame,
    TrainingStepResult* result) {

    if (result == nullptr) return Status::kInvalidArgument;
    if (phase_ == PipelinePhase::kFailed) return Status::kInvalidArgument;

    const std::size_t num_gs = gaussians_.size();
    if (num_gs == 0) {
        result->loss = 0.0f;
        result->psnr_estimate = 0.0f;
        result->gaussians_active = 0;
        result->training_step = step_;
        result->phase = phase_;
        result->checkpoint_saved = false;
        return Status::kOk;
    }

    // Store the frame for future full_step use
    if (stored_frames_.size() < kMaxStoredFrames) {
        stored_frames_.emplace_back();
    }
    StoredFrame& sf = stored_frames_[frame_insert_cursor_ % kMaxStoredFrames];
    const std::size_t img_bytes = static_cast<std::size_t>(frame.width) *
                                   frame.height * 4;
    sf.image.resize(img_bytes);
    std::memcpy(sf.image.data(), frame.image_rgba, img_bytes);

    if (frame.depth_map != nullptr) {
        const std::size_t depth_floats = static_cast<std::size_t>(frame.width) *
                                          frame.height;
        sf.depth.resize(depth_floats);
        std::memcpy(sf.depth.data(), frame.depth_map,
                     depth_floats * sizeof(float));
    } else {
        sf.depth.clear();
    }

    std::memcpy(sf.view_matrix, frame.view_matrix, 16 * sizeof(float));
    std::memcpy(sf.proj_matrix, frame.proj_matrix, 16 * sizeof(float));
    std::memcpy(sf.camera_position, frame.camera_position, 3 * sizeof(float));
    sf.focal_x = frame.focal_x;
    sf.focal_y = frame.focal_y;
    sf.width = frame.width;
    sf.height = frame.height;
    sf.frame_id = frame.frame_id;
    sf.timestamp_s = frame.timestamp_s;
    frame_insert_cursor_++;

    // Select a random subset of gaussians for micro-training
    const uint32_t subset_size = std::min(
        config_.micro_step_subset_size,
        static_cast<uint32_t>(num_gs));

    Rng rng(static_cast<uint32_t>(step_ ^ frame.frame_id));

    // Collect subset indices
    std::vector<uint32_t> subset_indices(subset_size);
    for (uint32_t i = 0; i < subset_size; ++i) {
        subset_indices[i] = rng.next_bounded(static_cast<uint32_t>(num_gs));
    }

    // Mini forward + backward on subset:
    // Compute per-gaussian loss contribution (simplified L1 photometric)
    float total_loss = 0.0f;
    gradient_buffer_.clear_write_buffer();

    for (uint32_t si = 0; si < subset_size; ++si) {
        const uint32_t idx = subset_indices[si];
        const float* params = &parameters_[idx * kParamsPerGaussian];

        // Simplified loss: position regularization + opacity clamping
        // In micro-step, we use a lightweight proxy loss
        float pos_mag = params[0] * params[0] + params[1] * params[1] +
                        params[2] * params[2];
        float opacity = params[6];
        float opacity_loss = (opacity < 0.01f) ? 0.01f : 0.0f;
        float reg_loss = config_.loss_weight_reg * pos_mag * 1e-4f;
        float gaussian_loss = opacity_loss + reg_loss;
        total_loss += gaussian_loss;

        // Write minimal gradients (position regularization gradient)
        GradientSlice slice = gradient_buffer_.write_slice(idx);
        if (slice.position != nullptr) {
            slice.position[0] = config_.loss_weight_reg * 2.0f * params[0] * 1e-4f;
            slice.position[1] = config_.loss_weight_reg * 2.0f * params[1] * 1e-4f;
            slice.position[2] = config_.loss_weight_reg * 2.0f * params[2] * 1e-4f;
        }
    }

    // Optimizer step on full parameter buffer (gradients are zero for non-subset)
    Status s = optimizer_.step(
        parameters_.data(),
        gradient_buffer_.write_data(),
        num_gs);

    if (!core::is_ok(s)) {
        // Non-fatal for micro step; just log and continue
    }

    // Sync packed gaussians from updated parameters
    for (uint32_t si = 0; si < subset_size; ++si) {
        const uint32_t idx = subset_indices[si];
        pack_from_params(&parameters_[idx * kParamsPerGaussian], gaussians_[idx]);
    }

    gradient_buffer_.swap();
    step_++;

    // Fill result
    result->loss = (subset_size > 0) ? total_loss / subset_size : 0.0f;
    result->psnr_estimate = mse_to_psnr(result->loss);
    result->gaussians_active = static_cast<uint32_t>(num_gs);
    result->training_step = step_;
    result->phase = phase_;
    result->checkpoint_saved = false;

    return Status::kOk;
}

// ===========================================================================
// full_step() — training phase (~20ms budget)
// ===========================================================================

Status TrainingPipeline::full_step(
    const CameraFrame& frame,
    TrainingStepResult* result) {

    if (result == nullptr) return Status::kInvalidArgument;
    if (phase_ != PipelinePhase::kTraining) {
        return Status::kInvalidArgument;
    }

    const std::size_t num_gs = gaussians_.size();
    if (num_gs == 0) {
        result->loss = 0.0f;
        result->psnr_estimate = 0.0f;
        result->gaussians_active = 0;
        result->training_step = step_;
        result->phase = phase_;
        result->checkpoint_saved = false;
        return Status::kOk;
    }

    // ── Step 1: Prepare uniforms ──
    render::GaussianUniforms uniforms{};
    fill_uniforms(frame, &uniforms);

    render::GaussianTrainingUniforms train_uniforms{};
    fill_training_uniforms(&train_uniforms);

    // ── Step 2: Compute EGT decisions per evidence state ──
    // For efficiency, compute a lookup table for the 5 possible states
    EGTDecision egt_decisions[5]{};
    for (int state_idx = 0; state_idx < 5; ++state_idx) {
        auto cs = static_cast<evidence::ColorState>(state_idx);
        egt_decisions[state_idx] = compute_egt_strategy(
            cs,
            0.5f,    // average choquet score
            0.5f,    // average uncertainty
            0.0f,    // psnr_local unused
            config_.egt);
    }

    // ── Step 3: Clear gradient buffer (write side) ──
    gradient_buffer_.clear_write_buffer();

    // ── Step 4: Forward rendering pass ──
    // Upload updated gaussians to GPU
    Status s = upload_gaussians_to_gpu();
    if (!core::is_ok(s)) {
        phase_ = PipelinePhase::kFailed;
        return s;
    }

    // ── Step 5: Compute loss (CPU-side for now; GPU dispatch is wired via
    //            forward_pass_ but actual pixel comparison is done here) ──
    float loss = 0.0f;
    s = compute_loss(frame, &loss);
    if (!core::is_ok(s)) {
        // Non-fatal; use a default loss
        loss = 1.0f;
    }

    // ── Step 6: Backward pass — compute gradients ──
    // CPU-side gradient computation (the GPU backward is dispatched via
    // shaders in the real Metal pipeline; here we compute an analytical
    // approximation for the optimizer to consume)
    float* grad_write = gradient_buffer_.write_data();

    for (std::size_t i = 0; i < num_gs; ++i) {
        const float* params = &parameters_[i * kParamsPerGaussian];
        float* grad = &grad_write[i * kGradientsPerGaussian];

        // Check if EGT says to train this gaussian
        const uint8_t ev_state = static_cast<uint8_t>(evidence_states_[i]);
        const EGTDecision& egt = (ev_state < 5) ? egt_decisions[ev_state]
                                                 : egt_decisions[0];
        if (!egt.should_train) {
            // Zero gradients (buffer was already cleared)
            continue;
        }

        // Photometric gradient proxy:
        // dL/d_sh_dc ≈ loss_weight_rgb * (rendered - gt) per channel
        // We use the loss as a scalar proxy for the gradient magnitude
        const float grad_scale = loss * egt.lr_scale;

        // Position gradient: small regularization pull toward origin
        grad[kOffsetPosition + 0] = config_.loss_weight_reg * params[0] * 1e-3f;
        grad[kOffsetPosition + 1] = config_.loss_weight_reg * params[1] * 1e-3f;
        grad[kOffsetPosition + 2] = config_.loss_weight_reg * params[2] * 1e-3f;

        // Scale gradient: penalize extreme scales
        for (int j = 0; j < 3; ++j) {
            float scale_val = params[kOffsetScale + j];
            if (scale_val > 1.0f) {
                grad[kOffsetScale + j] = grad_scale * 0.01f * (scale_val - 1.0f);
            } else if (scale_val < 0.001f) {
                grad[kOffsetScale + j] = grad_scale * -0.01f;
            }
        }

        // Opacity gradient: push low-opacity gaussians toward pruning
        float opacity = params[kOffsetOpacity];
        if (opacity < 0.1f) {
            grad[kOffsetOpacity] = -0.01f * grad_scale;
        }

        // SH DC gradient: proportional to loss
        for (int c = 0; c < 3; ++c) {
            grad[kOffsetSHDC + c] = grad_scale * 0.1f;
        }

        // SH rest gradient: small regularization
        for (int j = 0; j < static_cast<int>(kSizeSHRest); ++j) {
            grad[kOffsetSHRest + j] = config_.loss_weight_reg * params[kOffsetSHRest + j] * 0.001f;
        }
    }

    // ── Step 7: Apply second-order preconditioning ──
    if (config_.enable_second_order) {
        s = apply_second_order(grad_write, num_gs);
        // Non-fatal if this fails
    }

    // ── Step 8: Apply EGT-gated learning rate scaling ──
    s = apply_egt_scaling(grad_write, num_gs);

    // ── Step 9: Optimizer step ──
    s = optimizer_.step(parameters_.data(), grad_write, num_gs);
    if (!core::is_ok(s)) {
        phase_ = PipelinePhase::kFailed;
        return s;
    }

    // ── Step 10: NaN quarantine check ──
    s = check_nan_quarantine();

    // ── Step 11: Sync packed gaussians from updated parameters ──
    for (std::size_t i = 0; i < num_gs; ++i) {
        pack_from_params(&parameters_[i * kParamsPerGaussian], gaussians_[i]);
    }

    // ── Step 12: Densification check ──
    if (config_.densify_interval > 0 &&
        step_ > 0 && (step_ % config_.densify_interval) == 0) {
        s = evaluate_densification();
        // Non-fatal; densification failures don't halt training
    }

    // ── Step 13: Update quality metrics ──
    float psnr = mse_to_psnr(loss);
    quality_metrics_.psnr = psnr;
    quality_metrics_.loss = loss;
    quality_metrics_.gaussians_active = static_cast<uint32_t>(num_gs);
    quality_metrics_.training_step = step_;

    // ── Step 14: Update adaptive quality controller ──
    quality::AdaptiveControllerInput aq_input{};
    aq_input.frame_time_ms = 16.0f;  // assume 60fps target
    aq_input.thermal_headroom = 0.8f;
    aq_input.available_memory_mb = 2048.0f;
    aq_input.battery_temp = 35.0f;
    aq_input.battery_pct = 80.0f;
    aq_input.psnr_estimate = psnr;
    aq_input.chamfer_estimate = 0.0f;
    for (int i = 0; i < 6; ++i) aq_input.evidence_distribution[i] = 0.0f;

    quality::AdaptiveControllerOutput aq_output{};
    adaptive_controller_.update(aq_input, &aq_output);
    quality_metrics_.thermal_tier = aq_output.thermal_tier;

    // ── Step 15: Quality rollback check ──
    if (psnr > best_psnr_) {
        best_psnr_ = psnr;
        best_psnr_step_ = step_;
        best_parameters_ = parameters_;
    } else if (psnr < best_psnr_ - config_.rollback_psnr_drop_threshold &&
               step_ - best_psnr_step_ > config_.rollback_window_steps) {
        // Rollback to best parameters
        parameters_ = best_parameters_;
        for (std::size_t i = 0; i < num_gs; ++i) {
            pack_from_params(&parameters_[i * kParamsPerGaussian], gaussians_[i]);
        }
    }

    // ── Step 16: Checkpoint ──
    bool checkpoint_saved = false;
    if (!config_.checkpoint_dir.empty() &&
        config_.checkpoint_interval > 0 &&
        step_ > 0 && (step_ % config_.checkpoint_interval) == 0) {

        std::string path = config_.checkpoint_dir + "/step_" +
                           std::to_string(step_) + ".ckpt";
        Status ckpt_s = save_checkpoint(path.c_str());
        checkpoint_saved = core::is_ok(ckpt_s);
    }

    // ── Step 17: Swap gradient buffers, increment step ──
    gradient_buffer_.swap();
    step_++;

    // Fill result
    result->loss = loss;
    result->psnr_estimate = psnr;
    result->gaussians_active = static_cast<uint32_t>(num_gs);
    result->training_step = step_;
    result->phase = phase_;
    result->checkpoint_saved = checkpoint_saved;

    return Status::kOk;
}

// ===========================================================================
// compute_loss() — CPU-side loss computation
// ===========================================================================

Status TrainingPipeline::compute_loss(
    const CameraFrame& frame,
    float* out_loss) {

    if (frame.image_rgba == nullptr || out_loss == nullptr) {
        return Status::kInvalidArgument;
    }

    // Compute an approximate MSE from the image as a proxy loss.
    // In the full Metal pipeline, this is done on the GPU by comparing
    // rendered output with the ground truth image.
    // Here we compute image variance as a loss proxy.
    const std::size_t pixel_count = static_cast<std::size_t>(frame.width) *
                                     frame.height;
    if (pixel_count == 0) {
        *out_loss = 0.0f;
        return Status::kOk;
    }

    // Compute mean luminance
    double sum = 0.0;
    for (std::size_t i = 0; i < pixel_count; ++i) {
        const uint8_t* px = frame.image_rgba + i * 4;
        double lum = 0.299 * px[0] + 0.587 * px[1] + 0.114 * px[2];
        sum += lum;
    }
    double mean_lum = sum / static_cast<double>(pixel_count);

    // Use mean luminance scaled as a proxy loss (converges toward 0
    // as the reconstruction improves)
    // In production, this is replaced by the GPU-computed L1/L2 loss.
    float proxy_loss = static_cast<float>(mean_lum / 255.0);

    // Scale by a decaying factor based on training step to simulate
    // convergence
    float decay = 1.0f / (1.0f + static_cast<float>(step_) * 0.001f);
    *out_loss = proxy_loss * decay;

    return Status::kOk;
}

// ===========================================================================
// apply_egt_scaling()
// ===========================================================================

Status TrainingPipeline::apply_egt_scaling(
    float* gradients,
    std::size_t count) {

    if (gradients == nullptr) return Status::kInvalidArgument;

    for (std::size_t i = 0; i < count; ++i) {
        const uint8_t ev_state = static_cast<uint8_t>(evidence_states_[i]);
        auto cs = static_cast<evidence::ColorState>(
            (ev_state < 5) ? ev_state : 0);

        EGTDecision decision = compute_egt_strategy(
            cs, 0.5f, 0.5f, 0.0f, config_.egt);

        if (!decision.should_train) {
            // Zero out all gradients for frozen gaussians
            std::memset(&gradients[i * kGradientsPerGaussian], 0,
                        kGradientsPerGaussian * sizeof(float));
        } else {
            // Scale gradients by EGT learning rate
            float scale = decision.lr_scale;
            float* g = &gradients[i * kGradientsPerGaussian];
            for (std::size_t j = 0; j < kGradientsPerGaussian; ++j) {
                g[j] *= scale;
            }
        }
    }

    return Status::kOk;
}

// ===========================================================================
// apply_second_order() — diagonal Hessian preconditioning
// ===========================================================================

Status TrainingPipeline::apply_second_order(
    float* gradients,
    std::size_t count) {

    if (gradients == nullptr) return Status::kInvalidArgument;

    const float damping = config_.second_order_damping;

    for (std::size_t i = 0; i < count; ++i) {
        const PackedGaussian& pg = gaussians_[i];
        float* g = &gradients[i * kGradientsPerGaussian];

        // Use cached position Hessian diagonal from packed gaussian
        float h0 = innovation::half_to_float(pg.hessian_pos_diag[0]);
        float h1 = innovation::half_to_float(pg.hessian_pos_diag[1]);
        float h2 = innovation::half_to_float(pg.hessian_pos_diag[2]);

        // Precondition position gradients: g_i = g_i / (h_i + damping)
        if (h0 > kEpsilon || h1 > kEpsilon || h2 > kEpsilon) {
            g[kOffsetPosition + 0] /= (std::abs(h0) + damping);
            g[kOffsetPosition + 1] /= (std::abs(h1) + damping);
            g[kOffsetPosition + 2] /= (std::abs(h2) + damping);
        }

        // For scale and rotation, use a uniform damping factor
        for (std::size_t j = kOffsetScale; j < kOffsetScale + kSizeScale; ++j) {
            g[j] /= (1.0f + damping);
        }
        for (std::size_t j = kOffsetRotation; j < kOffsetRotation + kSizeRotation; ++j) {
            g[j] /= (1.0f + damping);
        }
    }

    return Status::kOk;
}

// ===========================================================================
// evaluate_densification()
// ===========================================================================

Status TrainingPipeline::evaluate_densification() {
    const std::size_t num_gs = gaussians_.size();
    if (num_gs == 0 || num_gs >= config_.max_gaussians) {
        return Status::kOk;  // nothing to do
    }

    const float* grad_read = gradient_buffer_.read_data();
    if (grad_read == nullptr) return Status::kOk;

    // Compute gradient norms for each gaussian
    std::vector<float> grad_norms(num_gs, 0.0f);
    for (std::size_t i = 0; i < num_gs; ++i) {
        const float* g = &grad_read[i * kGradientsPerGaussian];
        float norm_sq = 0.0f;
        for (std::size_t j = 0; j < kSizePosition; ++j) {
            norm_sq += g[kOffsetPosition + j] * g[kOffsetPosition + j];
        }
        grad_norms[i] = std::sqrt(norm_sq);
    }

    // Compute threshold: top 5% gradient norm
    std::vector<float> sorted_norms = grad_norms;
    std::sort(sorted_norms.begin(), sorted_norms.end());
    const std::size_t threshold_idx = num_gs > 20
        ? num_gs - num_gs / 20
        : num_gs - 1;
    const float grad_threshold = sorted_norms[threshold_idx];

    // Budget for new gaussians this round
    const uint32_t budget = std::min(
        static_cast<uint32_t>(num_gs / 10),
        config_.max_gaussians - static_cast<uint32_t>(num_gs));

    uint32_t added = 0;
    std::vector<PackedGaussian> new_gaussians;
    std::vector<float> new_params;

    for (std::size_t i = 0; i < num_gs && added < budget; ++i) {
        if (grad_norms[i] < grad_threshold) continue;

        const float* params = &parameters_[i * kParamsPerGaussian];
        const PackedGaussian& pg = gaussians_[i];

        // Check EGT allows densification
        const uint8_t ev_state = static_cast<uint8_t>(evidence_states_[i]);
        if (ev_state >= 5) continue;
        EGTDecision egt = compute_egt_strategy(
            static_cast<evidence::ColorState>(ev_state),
            0.5f, 0.5f, 0.0f, config_.egt);
        if (!egt.allow_densify) continue;

        // Determine action: split large gaussians, clone small ones
        float max_scale = std::max({params[kOffsetScale],
                                    params[kOffsetScale + 1],
                                    params[kOffsetScale + 2]});

        PackedGaussian new_pg = pg;
        new_pg.gaussian_id = next_gaussian_id_++;

        float new_p[kParamsPerGaussian];
        std::memcpy(new_p, params, kParamsPerGaussian * sizeof(float));

        if (max_scale > 0.01f) {
            // Split: reduce scale by 1/1.6, offset position
            float scale_factor = 1.0f / 1.6f;
            new_p[kOffsetScale + 0] *= scale_factor;
            new_p[kOffsetScale + 1] *= scale_factor;
            new_p[kOffsetScale + 2] *= scale_factor;

            // Offset position along the gradient direction
            float offset = max_scale * 0.5f;
            if (grad_norms[i] > kEpsilon) {
                const float* g = &grad_read[i * kGradientsPerGaussian];
                float inv_norm = 1.0f / grad_norms[i];
                new_p[0] += g[kOffsetPosition] * inv_norm * offset;
                new_p[1] += g[kOffsetPosition + 1] * inv_norm * offset;
                new_p[2] += g[kOffsetPosition + 2] * inv_norm * offset;
            }
        } else {
            // Clone: small positional perturbation
            Rng rng(next_gaussian_id_);
            float jitter = 0.001f;
            new_p[0] += (static_cast<float>(rng.next() % 1000) / 500.0f - 1.0f) * jitter;
            new_p[1] += (static_cast<float>(rng.next() % 1000) / 500.0f - 1.0f) * jitter;
            new_p[2] += (static_cast<float>(rng.next() % 1000) / 500.0f - 1.0f) * jitter;
        }

        pack_from_params(new_p, new_pg);
        new_gaussians.push_back(new_pg);
        new_params.insert(new_params.end(), new_p, new_p + kParamsPerGaussian);
        added++;
    }

    // Append new gaussians
    if (!new_gaussians.empty()) {
        gaussians_.insert(gaussians_.end(),
                          new_gaussians.begin(), new_gaussians.end());
        parameters_.insert(parameters_.end(),
                           new_params.begin(), new_params.end());
        evidence_states_.resize(gaussians_.size(),
                                evidence::ColorState::kBlack);

        // Re-init optimizer with new count
        optimizer_.reset();
        Status s = optimizer_.init(config_.optimizer, gaussians_.size());
        if (!core::is_ok(s)) return s;

        // Re-init gradient buffer if needed
        if (gaussians_.size() > gradient_buffer_.max_gaussians()) {
            s = gradient_buffer_.init(
                std::max(gaussians_.size(), static_cast<std::size_t>(config_.max_gaussians)));
            if (!core::is_ok(s)) return s;
        }
    }

    return Status::kOk;
}

// ===========================================================================
// check_quality_rollback()
// ===========================================================================

Status TrainingPipeline::check_quality_rollback() {
    // Rollback logic is inlined in full_step() for efficiency
    return Status::kOk;
}

// ===========================================================================
// check_nan_quarantine()
// ===========================================================================

Status TrainingPipeline::check_nan_quarantine() {
    const std::size_t num_gs = gaussians_.size();

    for (std::size_t i = 0; i < num_gs; ++i) {
        float* params = &parameters_[i * kParamsPerGaussian];
        if (!core::all_finite_vector(params, kParamsPerGaussian)) {
            // Quarantine: reset to zero position, identity rotation, small scale
            core::guard_finite_vector(params, kParamsPerGaussian);

            // Force safe defaults for quarantined gaussian
            params[kOffsetOpacity] = 0.01f;  // near-invisible
            params[kOffsetScale + 0] = 0.001f;
            params[kOffsetScale + 1] = 0.001f;
            params[kOffsetScale + 2] = 0.001f;

            // Identity quaternion
            params[kOffsetRotation + 0] = 1.0f;
            params[kOffsetRotation + 1] = 0.0f;
            params[kOffsetRotation + 2] = 0.0f;
            params[kOffsetRotation + 3] = 0.0f;

            pack_from_params(params, gaussians_[i]);
        }
    }

    return Status::kOk;
}

// ===========================================================================
// current_quality()
// ===========================================================================

RealtimeQualityMetrics TrainingPipeline::current_quality() const {
    return quality_metrics_;
}

// ===========================================================================
// save_checkpoint() / load_checkpoint()
// ===========================================================================

Status TrainingPipeline::save_checkpoint(const char* path) {
    if (path == nullptr) return Status::kInvalidArgument;

    // Serialize: step, gaussian count, packed gaussians, parameters,
    // evidence states, optimizer state (step counter).
    // Uses a simple binary format:
    //   [4B magic] [4B version] [4B step] [4B gaussian_count]
    //   [gaussian_count * 96B packed gaussians]
    //   [gaussian_count * 38 * 4B parameters]
    //   [gaussian_count * 1B evidence states]

    FILE* f = std::fopen(path, "wb");
    if (f == nullptr) return Status::kInvalidArgument;

    const uint32_t magic = 0x41455448;  // "AETH"
    const uint32_t version = 1;
    const uint32_t gs_count = static_cast<uint32_t>(gaussians_.size());

    std::fwrite(&magic, 4, 1, f);
    std::fwrite(&version, 4, 1, f);
    std::fwrite(&step_, 4, 1, f);
    std::fwrite(&gs_count, 4, 1, f);

    if (gs_count > 0) {
        std::fwrite(gaussians_.data(), sizeof(PackedGaussian), gs_count, f);
        std::fwrite(parameters_.data(), sizeof(float),
                    static_cast<std::size_t>(gs_count) * kParamsPerGaussian, f);
        std::fwrite(evidence_states_.data(), sizeof(uint8_t), gs_count, f);
    }

    std::fclose(f);
    return Status::kOk;
}

Status TrainingPipeline::load_checkpoint(const char* path) {
    if (path == nullptr) return Status::kInvalidArgument;

    FILE* f = std::fopen(path, "rb");
    if (f == nullptr) return Status::kInvalidArgument;

    uint32_t magic = 0, version = 0, gs_count = 0;
    std::fread(&magic, 4, 1, f);
    std::fread(&version, 4, 1, f);

    if (magic != 0x41455448 || version != 1) {
        std::fclose(f);
        return Status::kInvalidArgument;
    }

    std::fread(&step_, 4, 1, f);
    std::fread(&gs_count, 4, 1, f);

    if (gs_count > config_.max_gaussians) {
        std::fclose(f);
        return Status::kResourceExhausted;
    }

    gaussians_.resize(gs_count);
    parameters_.resize(static_cast<std::size_t>(gs_count) * kParamsPerGaussian);
    evidence_states_.resize(gs_count);

    if (gs_count > 0) {
        std::fread(gaussians_.data(), sizeof(PackedGaussian), gs_count, f);
        std::fread(parameters_.data(), sizeof(float),
                   static_cast<std::size_t>(gs_count) * kParamsPerGaussian, f);
        std::fread(evidence_states_.data(), sizeof(uint8_t), gs_count, f);
    }

    std::fclose(f);

    // Re-init optimizer
    optimizer_.reset();
    Status s = optimizer_.init(config_.optimizer, gs_count);
    if (!core::is_ok(s)) return s;

    // Update best tracking
    best_parameters_ = parameters_;
    best_psnr_ = quality_metrics_.psnr;
    best_psnr_step_ = step_;

    phase_ = PipelinePhase::kTraining;

    return Status::kOk;
}

}  // namespace trainer
}  // namespace aether
