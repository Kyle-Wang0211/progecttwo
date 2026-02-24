// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/render/gaussian_forward_pass.h"

#include <cstring>

namespace {

/// Number of vertices per gaussian splat quad (two triangles).
static constexpr std::uint32_t kVerticesPerQuad = 6;

/// Buffer binding indices matching the shader declarations.
static constexpr std::uint32_t kBindingGaussians = 0;
static constexpr std::uint32_t kBindingUniforms  = 1;
static constexpr std::uint32_t kBindingScaffold  = 0;

}  // namespace

namespace aether {
namespace render {

GaussianForwardPass::GaussianForwardPass() = default;

core::Status GaussianForwardPass::init(GPUDevice* device,
                                       const ForwardPassConfig& config) {
    if (device == nullptr) {
        return core::Status::kInvalidArgument;
    }
    if (config.max_gaussians == 0 || config.render_width == 0 ||
        config.render_height == 0) {
        return core::Status::kInvalidArgument;
    }

    device_ = device;
    config_ = config;

    // ── Create render targets ──────────────────────────────────────
    {
        GPUTextureDesc color_desc{};
        color_desc.width = config_.render_width;
        color_desc.height = config_.render_height;
        color_desc.format = GPUTextureFormat::kRGBA16Float;
        color_desc.usage_mask = static_cast<std::uint8_t>(
            static_cast<std::uint8_t>(GPUTextureUsage::kRenderTarget) |
            static_cast<std::uint8_t>(GPUTextureUsage::kShaderRead));
        color_desc.storage = GPUStorageMode::kPrivate;
        color_desc.label = "gaussian_color_rt";
        color_texture_ = device_->create_texture(color_desc);
        if (!color_texture_.valid()) {
            return core::Status::kResourceExhausted;
        }
    }
    {
        GPUTextureDesc depth_desc{};
        depth_desc.width = config_.render_width;
        depth_desc.height = config_.render_height;
        depth_desc.format = GPUTextureFormat::kDepth32Float;
        depth_desc.usage_mask = static_cast<std::uint8_t>(
            static_cast<std::uint8_t>(GPUTextureUsage::kRenderTarget) |
            static_cast<std::uint8_t>(GPUTextureUsage::kShaderRead));
        depth_desc.storage = GPUStorageMode::kPrivate;
        depth_desc.label = "gaussian_depth_rt";
        depth_texture_ = device_->create_texture(depth_desc);
        if (!depth_texture_.valid()) {
            return core::Status::kResourceExhausted;
        }
    }

    // ── Load shaders ───────────────────────────────────────────────
    surfel_vertex_shader_ = device_->load_shader(
        "gaussian_surfel_vertex", GPUShaderStage::kVertex);
    surfel_fragment_shader_ = device_->load_shader(
        "gaussian_surfel_fragment", GPUShaderStage::kFragment);
    gaussian_vertex_shader_ = device_->load_shader(
        "gaussian_splat_vertex", GPUShaderStage::kVertex);
    gaussian_fragment_shader_ = device_->load_shader(
        "gaussian_splat_fragment", GPUShaderStage::kFragment);

    if (!surfel_vertex_shader_.valid() || !surfel_fragment_shader_.valid() ||
        !gaussian_vertex_shader_.valid() || !gaussian_fragment_shader_.valid()) {
        return core::Status::kResourceExhausted;
    }

    // ── Create render pipelines ────────────────────────────────────
    {
        GPURenderTargetDesc surfel_target{};
        surfel_target.color_format = GPUTextureFormat::kRGBA16Float;
        surfel_target.depth_format = GPUTextureFormat::kDepth32Float;
        surfel_target.width = config_.render_width;
        surfel_target.height = config_.render_height;
        surfel_target.color_load = GPULoadAction::kClear;
        surfel_target.color_store = GPUStoreAction::kStore;
        surfel_target.depth_load = GPULoadAction::kClear;
        surfel_target.depth_store = GPUStoreAction::kStore;
        surfel_target.clear_color[0] = 0.0f;
        surfel_target.clear_color[1] = 0.0f;
        surfel_target.clear_color[2] = 0.0f;
        surfel_target.clear_color[3] = 0.0f;
        surfel_target.clear_depth = 1.0f;

        surfel_pipeline_ = device_->create_render_pipeline(
            surfel_vertex_shader_, surfel_fragment_shader_, surfel_target);
        if (!surfel_pipeline_.valid()) {
            return core::Status::kResourceExhausted;
        }
    }
    {
        GPURenderTargetDesc gaussian_target{};
        gaussian_target.color_format = GPUTextureFormat::kRGBA16Float;
        gaussian_target.depth_format = GPUTextureFormat::kDepth32Float;
        gaussian_target.width = config_.render_width;
        gaussian_target.height = config_.render_height;
        // Load existing color+depth from surfel pass (preserve)
        gaussian_target.color_load = GPULoadAction::kLoad;
        gaussian_target.color_store = GPUStoreAction::kStore;
        gaussian_target.depth_load = GPULoadAction::kLoad;
        gaussian_target.depth_store = GPUStoreAction::kStore;

        gaussian_pipeline_ = device_->create_render_pipeline(
            gaussian_vertex_shader_, gaussian_fragment_shader_,
            gaussian_target);
        if (!gaussian_pipeline_.valid()) {
            return core::Status::kResourceExhausted;
        }
    }

    // ── Create GPU buffers ─────────────────────────────────────────
    {
        GPUBufferDesc gauss_buf_desc{};
        gauss_buf_desc.size_bytes =
            static_cast<std::size_t>(config_.max_gaussians) *
            sizeof(innovation::PackedGaussian);
        gauss_buf_desc.storage = GPUStorageMode::kShared;
        gauss_buf_desc.usage_mask = static_cast<std::uint8_t>(
            GPUBufferUsage::kVertex);
        gauss_buf_desc.label = "gaussian_buffer";
        gaussian_buffer_ = device_->create_buffer(gauss_buf_desc);
        if (!gaussian_buffer_.valid()) {
            return core::Status::kResourceExhausted;
        }
    }
    {
        GPUBufferDesc uni_buf_desc{};
        uni_buf_desc.size_bytes = sizeof(GaussianUniforms);
        uni_buf_desc.storage = GPUStorageMode::kShared;
        uni_buf_desc.usage_mask = static_cast<std::uint8_t>(
            GPUBufferUsage::kUniform);
        uni_buf_desc.label = "gaussian_uniforms";
        uniform_buffer_ = device_->create_buffer(uni_buf_desc);
        if (!uniform_buffer_.valid()) {
            return core::Status::kResourceExhausted;
        }
    }

    initialized_ = true;
    return core::Status::kOk;
}

core::Status GaussianForwardPass::upload_gaussians(
    const innovation::PackedGaussian* gaussians,
    std::size_t count) {
    if (!initialized_) {
        return core::Status::kInvalidArgument;
    }
    if (count > 0 && gaussians == nullptr) {
        return core::Status::kInvalidArgument;
    }
    if (count > config_.max_gaussians) {
        return core::Status::kOutOfRange;
    }

    if (count > 0) {
        const std::size_t byte_count = count * sizeof(innovation::PackedGaussian);
        device_->update_buffer(gaussian_buffer_, gaussians, 0, byte_count);
    }
    uploaded_gaussian_count_ = static_cast<std::uint32_t>(count);
    return core::Status::kOk;
}

core::Status GaussianForwardPass::dispatch_surfel_pass(
    GPUCommandBuffer* cmd,
    const GaussianUniforms& uniforms,
    GPUBufferHandle scaffold_vertices,
    std::uint32_t scaffold_vertex_count) {
    if (!initialized_) {
        return core::Status::kInvalidArgument;
    }
    if (cmd == nullptr) {
        return core::Status::kInvalidArgument;
    }
    if (scaffold_vertex_count > 0 && !scaffold_vertices.valid()) {
        return core::Status::kInvalidArgument;
    }

    // Upload uniforms
    device_->update_buffer(uniform_buffer_, &uniforms, 0,
                           sizeof(GaussianUniforms));

    // Create surfel render pass descriptor (clear color + depth)
    GPURenderTargetDesc surfel_desc{};
    surfel_desc.color_format = GPUTextureFormat::kRGBA16Float;
    surfel_desc.depth_format = GPUTextureFormat::kDepth32Float;
    surfel_desc.width = config_.render_width;
    surfel_desc.height = config_.render_height;
    surfel_desc.color_load = GPULoadAction::kClear;
    surfel_desc.color_store = GPUStoreAction::kStore;
    surfel_desc.depth_load = GPULoadAction::kClear;
    surfel_desc.depth_store = GPUStoreAction::kStore;
    surfel_desc.clear_color[0] = 0.0f;
    surfel_desc.clear_color[1] = 0.0f;
    surfel_desc.clear_color[2] = 0.0f;
    surfel_desc.clear_color[3] = 0.0f;
    surfel_desc.clear_depth = 1.0f;

    GPURenderEncoder* encoder = cmd->make_render_encoder(surfel_desc);
    if (encoder == nullptr) {
        return core::Status::kResourceExhausted;
    }

    encoder->set_pipeline(surfel_pipeline_);

    // Set viewport
    GPUViewport viewport{};
    viewport.origin_x = 0.0f;
    viewport.origin_y = 0.0f;
    viewport.width = static_cast<float>(config_.render_width);
    viewport.height = static_cast<float>(config_.render_height);
    viewport.near_depth = 0.0f;
    viewport.far_depth = 1.0f;
    encoder->set_viewport(viewport);

    // Bind scaffold vertex buffer and uniforms
    encoder->set_vertex_buffer(scaffold_vertices, 0, kBindingScaffold);
    encoder->set_vertex_buffer(uniform_buffer_, 0, kBindingUniforms);

    // Set back-face culling for opaque scaffold geometry
    encoder->set_cull_mode(GPUCullMode::kBack);
    encoder->set_winding(GPUWindingOrder::kCounterClockwise);

    // Draw scaffold triangles
    if (scaffold_vertex_count > 0) {
        encoder->draw(GPUPrimitiveType::kTriangle, 0, scaffold_vertex_count);
    }

    encoder->end_encoding();
    return core::Status::kOk;
}

core::Status GaussianForwardPass::dispatch_gaussian_pass(
    GPUCommandBuffer* cmd,
    const GaussianUniforms& uniforms) {
    if (!initialized_) {
        return core::Status::kInvalidArgument;
    }
    if (cmd == nullptr) {
        return core::Status::kInvalidArgument;
    }

    // Upload uniforms (may have changed since surfel pass)
    device_->update_buffer(uniform_buffer_, &uniforms, 0,
                           sizeof(GaussianUniforms));

    // Create gaussian render pass descriptor (load existing color+depth)
    GPURenderTargetDesc gaussian_desc{};
    gaussian_desc.color_format = GPUTextureFormat::kRGBA16Float;
    gaussian_desc.depth_format = GPUTextureFormat::kDepth32Float;
    gaussian_desc.width = config_.render_width;
    gaussian_desc.height = config_.render_height;
    gaussian_desc.color_load = GPULoadAction::kLoad;
    gaussian_desc.color_store = GPUStoreAction::kStore;
    gaussian_desc.depth_load = GPULoadAction::kLoad;
    gaussian_desc.depth_store = GPUStoreAction::kStore;

    GPURenderEncoder* encoder = cmd->make_render_encoder(gaussian_desc);
    if (encoder == nullptr) {
        return core::Status::kResourceExhausted;
    }

    encoder->set_pipeline(gaussian_pipeline_);

    // Set viewport
    GPUViewport viewport{};
    viewport.origin_x = 0.0f;
    viewport.origin_y = 0.0f;
    viewport.width = static_cast<float>(config_.render_width);
    viewport.height = static_cast<float>(config_.render_height);
    viewport.near_depth = 0.0f;
    viewport.far_depth = 1.0f;
    encoder->set_viewport(viewport);

    // Bind gaussian buffer and uniforms
    encoder->set_vertex_buffer(gaussian_buffer_, 0, kBindingGaussians);
    encoder->set_vertex_buffer(uniform_buffer_, 0, kBindingUniforms);

    // Disable back-face culling for billboard quads
    encoder->set_cull_mode(GPUCullMode::kNone);

    // Draw instanced: 6 vertices per quad (two triangles),
    // one instance per gaussian.
    if (uploaded_gaussian_count_ > 0) {
        encoder->draw_instanced(GPUPrimitiveType::kTriangle,
                                kVerticesPerQuad,
                                uploaded_gaussian_count_);
    }

    encoder->end_encoding();
    return core::Status::kOk;
}

core::Status GaussianForwardPass::dispatch(
    GPUCommandBuffer* cmd,
    const GaussianUniforms& uniforms,
    GPUBufferHandle scaffold_vertices,
    std::uint32_t scaffold_vertex_count) {
    core::Status status = dispatch_surfel_pass(
        cmd, uniforms, scaffold_vertices, scaffold_vertex_count);
    if (status != core::Status::kOk) {
        return status;
    }
    return dispatch_gaussian_pass(cmd, uniforms);
}

GPUTextureHandle GaussianForwardPass::color_target() const {
    return color_texture_;
}

GPUTextureHandle GaussianForwardPass::depth_target() const {
    return depth_texture_;
}

void GaussianForwardPass::destroy() {
    if (device_ == nullptr) {
        return;
    }

    // Destroy pipelines
    if (surfel_pipeline_.valid()) {
        device_->destroy_render_pipeline(surfel_pipeline_);
        surfel_pipeline_ = GPURenderPipelineHandle{};
    }
    if (gaussian_pipeline_.valid()) {
        device_->destroy_render_pipeline(gaussian_pipeline_);
        gaussian_pipeline_ = GPURenderPipelineHandle{};
    }

    // Destroy shaders
    if (surfel_vertex_shader_.valid()) {
        device_->destroy_shader(surfel_vertex_shader_);
        surfel_vertex_shader_ = GPUShaderHandle{};
    }
    if (surfel_fragment_shader_.valid()) {
        device_->destroy_shader(surfel_fragment_shader_);
        surfel_fragment_shader_ = GPUShaderHandle{};
    }
    if (gaussian_vertex_shader_.valid()) {
        device_->destroy_shader(gaussian_vertex_shader_);
        gaussian_vertex_shader_ = GPUShaderHandle{};
    }
    if (gaussian_fragment_shader_.valid()) {
        device_->destroy_shader(gaussian_fragment_shader_);
        gaussian_fragment_shader_ = GPUShaderHandle{};
    }

    // Destroy buffers
    if (gaussian_buffer_.valid()) {
        device_->destroy_buffer(gaussian_buffer_);
        gaussian_buffer_ = GPUBufferHandle{};
    }
    if (uniform_buffer_.valid()) {
        device_->destroy_buffer(uniform_buffer_);
        uniform_buffer_ = GPUBufferHandle{};
    }

    // Destroy textures
    if (color_texture_.valid()) {
        device_->destroy_texture(color_texture_);
        color_texture_ = GPUTextureHandle{};
    }
    if (depth_texture_.valid()) {
        device_->destroy_texture(depth_texture_);
        depth_texture_ = GPUTextureHandle{};
    }

    initialized_ = false;
    uploaded_gaussian_count_ = 0;
    device_ = nullptr;
}

}  // namespace render
}  // namespace aether
