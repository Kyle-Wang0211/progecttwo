// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_RENDER_GAUSSIAN_BACKWARD_PASS_H
#define AETHER_RENDER_GAUSSIAN_BACKWARD_PASS_H

#ifdef __cplusplus

#include <cstddef>
#include <cstdint>

#include "aether/core/status.h"
#include "aether/render/gaussian_shader_types.h"
#include "aether/render/gpu_command.h"
#include "aether/render/gpu_device.h"
#include "aether/render/gpu_resource.h"

namespace aether {
namespace render {

// ═══════════════════════════════════════════════════════════════════════
// BackwardMode: Strategy for computing gradients on GPU
// ═══════════════════════════════════════════════════════════════════════
// kTiledCompute: Compute shader with tiled dispatch (preferred on Metal).
//   Each threadgroup processes one tile, iterating over gaussians that
//   overlap the tile. Writes per-gaussian gradient atomics.
// kFragmentShader: Fragment-based backward using raster order groups.
//   Uses a fullscreen quad to drive per-pixel gradient accumulation.
//   Requires raster order group support (Metal 2+, Vulkan equivalent).

enum class BackwardMode : std::uint8_t {
    kTiledCompute = 0,
    kFragmentShader = 1,
};

// ═══════════════════════════════════════════════════════════════════════
// BackwardPassConfig
// ═══════════════════════════════════════════════════════════════════════

struct BackwardPassConfig {
    BackwardMode mode{BackwardMode::kTiledCompute};
    std::uint32_t tile_size{64};
    std::uint32_t max_gaussians_per_tile{200};
    bool use_fp16{false};  // half-precision gradient accumulation
};

// ═══════════════════════════════════════════════════════════════════════
// GaussianBackwardPass: GPU backward propagation for 3DGS training
// ═══════════════════════════════════════════════════════════════════════
// Dispatches the backward computation that produces per-gaussian
// gradients from the rendered vs ground truth image difference.
// Supports two execution strategies selectable via BackwardMode.

class GaussianBackwardPass {
public:
    GaussianBackwardPass() = default;

    /// Initialize pipelines and resources for the chosen backward mode.
    core::Status init(GPUDevice* device, const BackwardPassConfig& config);

    /// Dispatch the backward computation.
    /// Reads rendered_color and gt_color textures, reads gaussian_buffer,
    /// and writes accumulated gradients to gradient_buffer.
    /// \p gradient_buffer must be pre-allocated with sufficient size
    ///    (gaussian_count * kGradientsPerGaussian * sizeof(float)).
    core::Status dispatch(GPUCommandBuffer* cmd,
                          const GaussianTrainingUniforms& uniforms,
                          GPUBufferHandle gaussian_buffer,
                          std::uint32_t gaussian_count,
                          GPUTextureHandle rendered_color,
                          GPUTextureHandle gt_color,
                          GPUBufferHandle gradient_buffer);

    /// Release all GPU resources. Safe to call multiple times.
    void destroy();

private:
    GPUDevice* device_{nullptr};
    BackwardPassConfig config_{};
    bool initialized_{false};

    // Tiled compute path
    GPUShaderHandle tiled_compute_shader_{};
    GPUComputePipelineHandle tiled_compute_pipeline_{};

    // Fragment shader path
    GPUShaderHandle backward_vertex_shader_{};
    GPUShaderHandle backward_fragment_shader_{};
    GPURenderPipelineHandle fragment_pipeline_{};

    // Shared
    GPUBufferHandle tile_constants_buffer_{};

    // Non-copyable
    GaussianBackwardPass(const GaussianBackwardPass&) = delete;
    GaussianBackwardPass& operator=(const GaussianBackwardPass&) = delete;
};

}  // namespace render
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_RENDER_GAUSSIAN_BACKWARD_PASS_H
