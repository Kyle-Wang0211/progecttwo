// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_TRAINING_C_API_H
#define AETHER_TRAINING_C_API_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

// ═══════════════════════════════════════════════════════════════════════
// Opaque handle
// ═══════════════════════════════════════════════════════════════════════

typedef struct aether_training_pipeline_s* aether_training_pipeline_t;

// ═══════════════════════════════════════════════════════════════════════
// Status codes (matching core::Status)
// ═══════════════════════════════════════════════════════════════════════

typedef int aether_status_t;
#define AETHER_OK                  0
#define AETHER_INVALID_ARGUMENT   -1
#define AETHER_OUT_OF_RANGE       -2
#define AETHER_RESOURCE_EXHAUSTED -3

// ═══════════════════════════════════════════════════════════════════════
// Pipeline phase
// ═══════════════════════════════════════════════════════════════════════

typedef enum {
    AETHER_PHASE_IDLE         = 0,
    AETHER_PHASE_CAPTURING    = 1,
    AETHER_PHASE_INITIALIZING = 2,
    AETHER_PHASE_TRAINING     = 3,
    AETHER_PHASE_COMPLETE     = 4,
    AETHER_PHASE_FAILED       = 5
} aether_pipeline_phase_t;

// ═══════════════════════════════════════════════════════════════════════
// TSDF voxel seed for bridge initialization
// ═══════════════════════════════════════════════════════════════════════

typedef struct {
    float position[3];
    float normal[3];
    float sdf_value;
    float confidence;
    float voxel_size;
    uint8_t rgb[3];
    uint8_t pad;
} aether_voxel_seed_t;

// ═══════════════════════════════════════════════════════════════════════
// Camera frame for training
// ═══════════════════════════════════════════════════════════════════════

typedef struct {
    uint64_t frame_id;
    float pose[16];          // 4x4 column-major view matrix
    const float* rgb_data;   // width x height x 3
    const float* depth_data; // width x height
    uint32_t width;
    uint32_t height;
    float information_gain;
    float blur_score;
} aether_camera_frame_t;

// ═══════════════════════════════════════════════════════════════════════
// Training step result
// ═══════════════════════════════════════════════════════════════════════

typedef struct {
    float loss;
    float psnr_estimate;
    uint32_t gaussians_active;
    uint32_t training_step;
    aether_pipeline_phase_t phase;
    int checkpoint_saved;  // bool
} aether_training_result_t;

// ═══════════════════════════════════════════════════════════════════════
// Quality metrics (6-dimensional)
// ═══════════════════════════════════════════════════════════════════════

typedef struct {
    float psnr_estimate;
    float ssim_estimate;
    float chamfer_estimate;
    float normal_consistency;
    float scale_accuracy;
    float coverage_f_score;
    float psnr_ci_lower;
    float psnr_ci_upper;
    int meets_world_model_standard;  // bool
} aether_quality_metrics_t;

// ═══════════════════════════════════════════════════════════════════════
// Pipeline configuration
// ═══════════════════════════════════════════════════════════════════════

typedef struct {
    uint32_t max_gaussians;
    uint32_t render_width;
    uint32_t render_height;
    float lr_position;
    float lr_opacity;
    float lr_scale;
    float lr_sh;
    float lr_rotation;
    uint32_t densify_interval;
    uint32_t gaussian_budget;
    float quality_floor_psnr;  // 28.0 dB
    const char* checkpoint_dir;
} aether_pipeline_config_t;

// ═══════════════════════════════════════════════════════════════════════
// Default configuration
// ═══════════════════════════════════════════════════════════════════════

aether_pipeline_config_t aether_training_default_config(void);

// ═══════════════════════════════════════════════════════════════════════
// Lifecycle
// ═══════════════════════════════════════════════════════════════════════

aether_training_pipeline_t aether_training_pipeline_create(
    const aether_pipeline_config_t* config);

void aether_training_pipeline_destroy(aether_training_pipeline_t pipeline);

// ═══════════════════════════════════════════════════════════════════════
// Initialize from TSDF seeds
// ═══════════════════════════════════════════════════════════════════════

aether_status_t aether_training_pipeline_initialize(
    aether_training_pipeline_t pipeline,
    const aether_voxel_seed_t* seeds,
    size_t seed_count);

// ═══════════════════════════════════════════════════════════════════════
// Training steps
// ═══════════════════════════════════════════════════════════════════════

aether_status_t aether_training_pipeline_micro_step(
    aether_training_pipeline_t pipeline,
    const aether_camera_frame_t* frame,
    aether_training_result_t* result);

aether_status_t aether_training_pipeline_full_step(
    aether_training_pipeline_t pipeline,
    const aether_camera_frame_t* frame,
    aether_training_result_t* result);

// ═══════════════════════════════════════════════════════════════════════
// Quality query
// ═══════════════════════════════════════════════════════════════════════

aether_status_t aether_training_pipeline_get_quality(
    aether_training_pipeline_t pipeline,
    aether_quality_metrics_t* metrics);

// ═══════════════════════════════════════════════════════════════════════
// Phase query
// ═══════════════════════════════════════════════════════════════════════

aether_pipeline_phase_t aether_training_pipeline_get_phase(
    aether_training_pipeline_t pipeline);

// ═══════════════════════════════════════════════════════════════════════
// Checkpoint
// ═══════════════════════════════════════════════════════════════════════

aether_status_t aether_training_pipeline_save_checkpoint(
    aether_training_pipeline_t pipeline,
    const char* path);

aether_status_t aether_training_pipeline_load_checkpoint(
    aether_training_pipeline_t pipeline,
    const char* path);

// ═══════════════════════════════════════════════════════════════════════
// Queries
// ═══════════════════════════════════════════════════════════════════════

uint32_t aether_training_pipeline_gaussian_count(
    aether_training_pipeline_t pipeline);

uint32_t aether_training_pipeline_training_step(
    aether_training_pipeline_t pipeline);

#ifdef __cplusplus
}
#endif

#endif // AETHER_TRAINING_C_API_H
