// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/training_pipeline.h"

#include <cstdio>
#include <memory>
#include <vector>

static int g_failed = 0;

static void check(bool cond, const char* msg, int line) {
    if (!cond) {
        std::fprintf(stderr, "FAIL [line %d]: %s\n", line, msg);
        ++g_failed;
    }
}
#define CHECK(cond) check((cond), #cond, __LINE__)

static std::vector<aether::trainer::TSDFVoxelSeed> make_mock_voxels(int count) {
    std::vector<aether::trainer::TSDFVoxelSeed> voxels(static_cast<std::size_t>(count));
    for (int i = 0; i < count; ++i) {
        auto& v = voxels[static_cast<std::size_t>(i)];
        v.position.x = static_cast<float>(i % 10) * 0.1f;
        v.position.y = static_cast<float>((i / 10) % 10) * 0.1f;
        v.position.z = static_cast<float>(i / 100) * 0.1f;
        v.normal.x = 0.0f; v.normal.y = 1.0f; v.normal.z = 0.0f;
        v.sdf_value = 0.001f;
        v.confidence = 0.8f;
        v.voxel_size = 0.01f;
        v.rgb[0] = 128; v.rgb[1] = 128; v.rgb[2] = 128;
    }
    return voxels;
}

static void test_create_pipeline() {
    auto gpu = std::make_unique<aether::render::NullGPUDevice>();
    aether::trainer::PipelineConfig cfg{};
    cfg.render_width = 128;
    cfg.render_height = 128;
    cfg.max_gaussians = 10000;

    aether::trainer::TrainingPipeline pipeline(std::move(gpu), cfg);
    CHECK(pipeline.phase() == aether::trainer::PipelinePhase::kIdle);
}

static void test_initialize_with_seeds() {
    auto gpu = std::make_unique<aether::render::NullGPUDevice>();
    aether::trainer::PipelineConfig cfg{};
    cfg.render_width = 64;
    cfg.render_height = 64;
    cfg.max_gaussians = 5000;

    aether::trainer::TrainingPipeline pipeline(std::move(gpu), cfg);

    auto voxels = make_mock_voxels(50);
    auto status = pipeline.initialize(voxels.data(), voxels.size());
    CHECK(aether::core::is_ok(status));
    CHECK(pipeline.gaussian_count() > 0);
}

static void test_full_step_produces_result() {
    auto gpu = std::make_unique<aether::render::NullGPUDevice>();
    aether::trainer::PipelineConfig cfg{};
    cfg.render_width = 64;
    cfg.render_height = 64;
    cfg.max_gaussians = 5000;

    aether::trainer::TrainingPipeline pipeline(std::move(gpu), cfg);

    auto voxels = make_mock_voxels(50);
    pipeline.initialize(voxels.data(), voxels.size());

    // We need persistent matrices for the frame since it stores pointers.
    float view[16] = {1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1};
    float proj[16] = {1,0,0,0, 0,1,0,0, 0,0,-1,0, 0,0,-1,0};
    aether::trainer::CameraFrame frame{};
    frame.view_matrix = view;
    frame.proj_matrix = proj;
    frame.image_rgba = nullptr;
    frame.depth_map = nullptr;
    frame.width = 64;
    frame.height = 64;
    frame.frame_id = 1;
    frame.timestamp_s = 0.033;
    frame.camera_position[0] = 0.0f;
    frame.camera_position[1] = 0.0f;
    frame.camera_position[2] = 0.0f;
    frame.focal_x = 32.0f;
    frame.focal_y = 32.0f;

    aether::trainer::TrainingStepResult result{};
    auto status = pipeline.full_step(frame, &result);
    CHECK(aether::core::is_ok(status));
    CHECK(result.loss >= 0.0f);
}

static void test_training_step_counter() {
    auto gpu = std::make_unique<aether::render::NullGPUDevice>();
    aether::trainer::PipelineConfig cfg{};
    cfg.render_width = 32;
    cfg.render_height = 32;
    cfg.max_gaussians = 2000;

    aether::trainer::TrainingPipeline pipeline(std::move(gpu), cfg);
    CHECK(pipeline.training_step() == 0);

    auto voxels = make_mock_voxels(30);
    pipeline.initialize(voxels.data(), voxels.size());

    float view[16] = {1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1};
    float proj[16] = {1,0,0,0, 0,1,0,0, 0,0,-1,0, 0,0,-1,0};
    aether::trainer::CameraFrame frame{};
    frame.view_matrix = view;
    frame.proj_matrix = proj;
    frame.width = 32;
    frame.height = 32;
    frame.focal_x = 16.0f;
    frame.focal_y = 16.0f;

    aether::trainer::TrainingStepResult result{};
    pipeline.full_step(frame, &result);
    CHECK(pipeline.training_step() >= 1);
}

static void test_quality_metrics_snapshot() {
    auto gpu = std::make_unique<aether::render::NullGPUDevice>();
    aether::trainer::PipelineConfig cfg{};
    cfg.render_width = 32;
    cfg.render_height = 32;
    cfg.max_gaussians = 2000;

    aether::trainer::TrainingPipeline pipeline(std::move(gpu), cfg);

    auto voxels = make_mock_voxels(25);
    pipeline.initialize(voxels.data(), voxels.size());

    auto metrics = pipeline.current_quality();
    CHECK(metrics.psnr >= 0.0f);
    CHECK(metrics.loss >= 0.0f);
}

static void test_gaussian_count_positive_after_init() {
    auto gpu = std::make_unique<aether::render::NullGPUDevice>();
    aether::trainer::PipelineConfig cfg{};
    cfg.render_width = 64;
    cfg.render_height = 64;
    cfg.max_gaussians = 10000;

    aether::trainer::TrainingPipeline pipeline(std::move(gpu), cfg);

    auto voxels = make_mock_voxels(50);
    pipeline.initialize(voxels.data(), voxels.size());

    CHECK(pipeline.gaussian_count() > 0);
    CHECK(pipeline.gaussian_count() <= cfg.max_gaussians);
}

int main() {
    test_create_pipeline();
    test_initialize_with_seeds();
    test_full_step_produces_result();
    test_training_step_counter();
    test_quality_metrics_snapshot();
    test_gaussian_count_positive_after_init();

    if (g_failed == 0) {
        std::fprintf(stdout, "training_pipeline_test: all tests passed\n");
    } else {
        std::fprintf(stderr, "training_pipeline_test: %d test(s) failed\n", g_failed);
    }
    return g_failed;
}
