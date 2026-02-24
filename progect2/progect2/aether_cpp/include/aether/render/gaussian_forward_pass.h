// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_RENDER_GAUSSIAN_FORWARD_PASS_H
#define AETHER_RENDER_GAUSSIAN_FORWARD_PASS_H

#ifdef __cplusplus

#include <cstddef>
#include <cstdint>

#include "aether/core/status.h"
#include "aether/innovation/packed_gaussian.h"
#include "aether/render/gaussian_shader_types.h"
#include "aether/render/gpu_command.h"
#include "aether/render/gpu_device.h"
#include "aether/render/gpu_resource.h"

namespace aether {
namespace render {

// ═══════════════════════════════════════════════════════════════════════
// ForwardPassConfig: Configuration for the gaussian forward renderer
// ═══════════════════════════════════════════════════════════════════════

struct ForwardPassConfig {
    std::uint32_t max_gaussians{50000};
    std::uint32_t render_width{1920};
    std::uint32_t render_height{1080};
    bool use_raster_order_groups{true};
};

// ═══════════════════════════════════════════════════════════════════════
// GaussianForwardPass: Sort-free forward rendering for 3D Gaussian
//                      Splatting with scaffold-anchored surfel pass
// ═══════════════════════════════════════════════════════════════════════
// Two-pass rendering:
//   1. Surfel pass: Opaque scaffold geometry (depth pre-pass)
//   2. Gaussian pass: Alpha-blended gaussian splats
//
// The surfel pass writes to the depth buffer, which the gaussian pass
// reads to correctly composite against the scaffold geometry.

class GaussianForwardPass {
public:
    GaussianForwardPass();

    /// Initialize GPU resources, pipelines, and render targets.
    /// Must be called before any dispatch method.
    core::Status init(GPUDevice* device, const ForwardPassConfig& config);

    /// Upload packed gaussian data to the GPU buffer.
    /// \p count must not exceed config.max_gaussians.
    core::Status upload_gaussians(const innovation::PackedGaussian* gaussians,
                                  std::size_t count);

    /// Dispatch the opaque scaffold surfel pass.
    /// Clears color/depth and renders scaffold triangles.
    core::Status dispatch_surfel_pass(GPUCommandBuffer* cmd,
                                      const GaussianUniforms& uniforms,
                                      GPUBufferHandle scaffold_vertices,
                                      std::uint32_t scaffold_vertex_count);

    /// Dispatch the alpha-blended gaussian splatting pass.
    /// Loads (preserves) the depth buffer from the surfel pass.
    core::Status dispatch_gaussian_pass(GPUCommandBuffer* cmd,
                                        const GaussianUniforms& uniforms);

    /// Convenience: dispatch surfel pass followed by gaussian pass.
    core::Status dispatch(GPUCommandBuffer* cmd,
                          const GaussianUniforms& uniforms,
                          GPUBufferHandle scaffold_vertices,
                          std::uint32_t scaffold_vertex_count);

    /// Current color render target (RGBA16Float).
    GPUTextureHandle color_target() const;

    /// Current depth render target (Depth32Float).
    GPUTextureHandle depth_target() const;

    /// Release all GPU resources. Safe to call multiple times.
    void destroy();

private:
    GPUDevice* device_{nullptr};
    ForwardPassConfig config_{};
    bool initialized_{false};

    // Render targets
    GPUTextureHandle color_texture_{};
    GPUTextureHandle depth_texture_{};

    // Shaders
    GPUShaderHandle surfel_vertex_shader_{};
    GPUShaderHandle surfel_fragment_shader_{};
    GPUShaderHandle gaussian_vertex_shader_{};
    GPUShaderHandle gaussian_fragment_shader_{};

    // Pipelines
    GPURenderPipelineHandle surfel_pipeline_{};
    GPURenderPipelineHandle gaussian_pipeline_{};

    // Buffers
    GPUBufferHandle gaussian_buffer_{};
    GPUBufferHandle uniform_buffer_{};

    // State
    std::uint32_t uploaded_gaussian_count_{0};

    // Non-copyable
    GaussianForwardPass(const GaussianForwardPass&) = delete;
    GaussianForwardPass& operator=(const GaussianForwardPass&) = delete;
};

}  // namespace render
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_RENDER_GAUSSIAN_FORWARD_PASS_H
