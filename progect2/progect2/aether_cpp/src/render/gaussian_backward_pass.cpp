// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/render/gaussian_backward_pass.h"

namespace {

/// Buffer binding indices for the backward compute shader.
static constexpr std::uint32_t kBindingGaussians      = 0;
static constexpr std::uint32_t kBindingGradients       = 1;
static constexpr std::uint32_t kBindingTileConstants   = 2;
static constexpr std::uint32_t kBindingUniforms        = 3;

/// Texture binding indices.
static constexpr std::uint32_t kTextureRenderedColor   = 0;
static constexpr std::uint32_t kTextureGTColor         = 1;

/// Fullscreen quad: 6 vertices for the fragment shader path.
static constexpr std::uint32_t kFullscreenQuadVertices = 6;

}  // namespace

namespace aether {
namespace render {

core::Status GaussianBackwardPass::init(GPUDevice* device,
                                        const BackwardPassConfig& config) {
    if (device == nullptr) {
        return core::Status::kInvalidArgument;
    }
    if (config.tile_size == 0 || config.max_gaussians_per_tile == 0) {
        return core::Status::kInvalidArgument;
    }

    device_ = device;
    config_ = config;

    // ── Create tile constants buffer ──────────────────────────────
    {
        GPUBufferDesc tile_buf_desc{};
        tile_buf_desc.size_bytes = sizeof(TileConstants);
        tile_buf_desc.storage = GPUStorageMode::kShared;
        tile_buf_desc.usage_mask = static_cast<std::uint8_t>(
            GPUBufferUsage::kUniform);
        tile_buf_desc.label = "backward_tile_constants";
        tile_constants_buffer_ = device_->create_buffer(tile_buf_desc);
        if (!tile_constants_buffer_.valid()) {
            return core::Status::kResourceExhausted;
        }
    }

    // ── Load shaders and create pipelines based on mode ───────────
    if (config_.mode == BackwardMode::kTiledCompute) {
        tiled_compute_shader_ = device_->load_shader(
            "gaussian_backward_tiled", GPUShaderStage::kCompute);
        if (!tiled_compute_shader_.valid()) {
            return core::Status::kResourceExhausted;
        }

        tiled_compute_pipeline_ = device_->create_compute_pipeline(
            tiled_compute_shader_);
        if (!tiled_compute_pipeline_.valid()) {
            return core::Status::kResourceExhausted;
        }
    } else {
        // Fragment shader path
        backward_vertex_shader_ = device_->load_shader(
            "gaussian_backward_vertex", GPUShaderStage::kVertex);
        backward_fragment_shader_ = device_->load_shader(
            "gaussian_backward_fragment", GPUShaderStage::kFragment);

        if (!backward_vertex_shader_.valid() ||
            !backward_fragment_shader_.valid()) {
            return core::Status::kResourceExhausted;
        }

        // The fragment backward pass renders to a dummy target; actual
        // output goes to the gradient buffer via raster order groups.
        GPURenderTargetDesc backward_target{};
        backward_target.color_format = GPUTextureFormat::kRGBA16Float;
        backward_target.depth_format = GPUTextureFormat::kDepth32Float;
        backward_target.width = 1;   // Dummy; actual size set at dispatch
        backward_target.height = 1;
        backward_target.color_load = GPULoadAction::kDontCare;
        backward_target.color_store = GPUStoreAction::kDontCare;
        backward_target.depth_load = GPULoadAction::kDontCare;
        backward_target.depth_store = GPUStoreAction::kDontCare;

        fragment_pipeline_ = device_->create_render_pipeline(
            backward_vertex_shader_, backward_fragment_shader_,
            backward_target);
        if (!fragment_pipeline_.valid()) {
            return core::Status::kResourceExhausted;
        }
    }

    initialized_ = true;
    return core::Status::kOk;
}

core::Status GaussianBackwardPass::dispatch(
    GPUCommandBuffer* cmd,
    const GaussianTrainingUniforms& uniforms,
    GPUBufferHandle gaussian_buffer,
    std::uint32_t gaussian_count,
    GPUTextureHandle rendered_color,
    GPUTextureHandle gt_color,
    GPUBufferHandle gradient_buffer) {
    if (!initialized_) {
        return core::Status::kInvalidArgument;
    }
    if (cmd == nullptr) {
        return core::Status::kInvalidArgument;
    }
    if (!gaussian_buffer.valid() || !gradient_buffer.valid()) {
        return core::Status::kInvalidArgument;
    }
    if (!rendered_color.valid() || !gt_color.valid()) {
        return core::Status::kInvalidArgument;
    }
    if (gaussian_count == 0) {
        return core::Status::kOk;  // Nothing to do
    }

    // Derive image dimensions from the training uniforms
    const std::uint32_t image_width =
        static_cast<std::uint32_t>(uniforms.gt_image_size[0]);
    const std::uint32_t image_height =
        static_cast<std::uint32_t>(uniforms.gt_image_size[1]);

    if (image_width == 0 || image_height == 0) {
        return core::Status::kInvalidArgument;
    }

    // ── Upload tile constants ─────────────────────────────────────
    TileConstants tile_consts{};
    tile_consts.image_width = image_width;
    tile_consts.image_height = image_height;
    tile_consts.tile_size = config_.tile_size;
    tile_consts.max_gaussians_per_tile = config_.max_gaussians_per_tile;
    tile_consts.total_gaussians = gaussian_count;
    tile_consts.pad[0] = 0;
    tile_consts.pad[1] = 0;
    tile_consts.pad[2] = 0;

    device_->update_buffer(tile_constants_buffer_, &tile_consts, 0,
                           sizeof(TileConstants));

    // ── Dispatch based on mode ────────────────────────────────────
    if (config_.mode == BackwardMode::kTiledCompute) {
        // Tiled compute dispatch
        GPUComputeEncoder* encoder = cmd->make_compute_encoder();
        if (encoder == nullptr) {
            return core::Status::kResourceExhausted;
        }

        encoder->set_pipeline(tiled_compute_pipeline_);

        // Bind buffers
        encoder->set_buffer(gaussian_buffer, 0, kBindingGaussians);
        encoder->set_buffer(gradient_buffer, 0, kBindingGradients);
        encoder->set_buffer(tile_constants_buffer_, 0, kBindingTileConstants);
        encoder->set_bytes(&uniforms, sizeof(GaussianTrainingUniforms),
                          kBindingUniforms);

        // Bind textures
        encoder->set_texture(rendered_color, kTextureRenderedColor);
        encoder->set_texture(gt_color, kTextureGTColor);

        // Dispatch threadgroups:
        //   groups = ceil(image_width / tile_size) x ceil(image_height / tile_size)
        //   threads per group = tile_size x tile_size x 1
        const std::uint32_t groups_x =
            (image_width + config_.tile_size - 1) / config_.tile_size;
        const std::uint32_t groups_y =
            (image_height + config_.tile_size - 1) / config_.tile_size;

        encoder->dispatch(groups_x, groups_y, 1,
                         config_.tile_size, config_.tile_size, 1);

        encoder->end_encoding();

    } else {
        // Fragment shader path with raster order groups
        GPURenderTargetDesc backward_desc{};
        backward_desc.color_format = GPUTextureFormat::kRGBA16Float;
        backward_desc.depth_format = GPUTextureFormat::kDepth32Float;
        backward_desc.width = image_width;
        backward_desc.height = image_height;
        backward_desc.color_load = GPULoadAction::kDontCare;
        backward_desc.color_store = GPUStoreAction::kDontCare;
        backward_desc.depth_load = GPULoadAction::kDontCare;
        backward_desc.depth_store = GPUStoreAction::kDontCare;

        GPURenderEncoder* encoder = cmd->make_render_encoder(backward_desc);
        if (encoder == nullptr) {
            return core::Status::kResourceExhausted;
        }

        encoder->set_pipeline(fragment_pipeline_);

        // Set viewport to match image dimensions
        GPUViewport viewport{};
        viewport.origin_x = 0.0f;
        viewport.origin_y = 0.0f;
        viewport.width = static_cast<float>(image_width);
        viewport.height = static_cast<float>(image_height);
        viewport.near_depth = 0.0f;
        viewport.far_depth = 1.0f;
        encoder->set_viewport(viewport);

        // Bind buffers to fragment stage (raster order group writes)
        encoder->set_fragment_buffer(gaussian_buffer, 0, kBindingGaussians);
        encoder->set_fragment_buffer(gradient_buffer, 0, kBindingGradients);
        encoder->set_fragment_buffer(tile_constants_buffer_, 0,
                                     kBindingTileConstants);
        encoder->set_fragment_bytes(&uniforms,
                                    sizeof(GaussianTrainingUniforms),
                                    kBindingUniforms);

        // Bind textures to fragment stage
        encoder->set_fragment_texture(rendered_color, kTextureRenderedColor);
        encoder->set_fragment_texture(gt_color, kTextureGTColor);

        // Disable culling for fullscreen quad
        encoder->set_cull_mode(GPUCullMode::kNone);

        // Draw fullscreen quad (6 vertices = 2 triangles)
        encoder->draw(GPUPrimitiveType::kTriangle, 0,
                     kFullscreenQuadVertices);

        encoder->end_encoding();
    }

    return core::Status::kOk;
}

void GaussianBackwardPass::destroy() {
    if (device_ == nullptr) {
        return;
    }

    // Tiled compute path
    if (tiled_compute_pipeline_.valid()) {
        device_->destroy_compute_pipeline(tiled_compute_pipeline_);
        tiled_compute_pipeline_ = GPUComputePipelineHandle{};
    }
    if (tiled_compute_shader_.valid()) {
        device_->destroy_shader(tiled_compute_shader_);
        tiled_compute_shader_ = GPUShaderHandle{};
    }

    // Fragment shader path
    if (fragment_pipeline_.valid()) {
        device_->destroy_render_pipeline(fragment_pipeline_);
        fragment_pipeline_ = GPURenderPipelineHandle{};
    }
    if (backward_vertex_shader_.valid()) {
        device_->destroy_shader(backward_vertex_shader_);
        backward_vertex_shader_ = GPUShaderHandle{};
    }
    if (backward_fragment_shader_.valid()) {
        device_->destroy_shader(backward_fragment_shader_);
        backward_fragment_shader_ = GPUShaderHandle{};
    }

    // Shared resources
    if (tile_constants_buffer_.valid()) {
        device_->destroy_buffer(tile_constants_buffer_);
        tile_constants_buffer_ = GPUBufferHandle{};
    }

    initialized_ = false;
    device_ = nullptr;
}

}  // namespace render
}  // namespace aether
