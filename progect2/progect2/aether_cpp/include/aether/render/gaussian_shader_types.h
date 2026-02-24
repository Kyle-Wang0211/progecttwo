// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_RENDER_GAUSSIAN_SHADER_TYPES_H
#define AETHER_RENDER_GAUSSIAN_SHADER_TYPES_H

#ifdef __cplusplus

#include <cstddef>
#include <cstdint>
#include <type_traits>

namespace aether {
namespace render {

// ═══════════════════════════════════════════════════════════════════════
// GaussianUniforms: Shared uniform buffer for gaussian rendering passes
// ═══════════════════════════════════════════════════════════════════════
// Must remain ABI-compatible with Metal/Vulkan shader declarations.
// All matrices are stored in column-major order.

struct GaussianUniforms {
    float view_matrix[16];       // 4x4 view matrix (column-major)
    float proj_matrix[16];       // 4x4 projection matrix (column-major)
    float view_proj_matrix[16];  // combined view*proj
    float camera_position[3];    // world-space camera position
    float pad0;
    float viewport_size[2];      // width, height in pixels
    float near_plane;
    float far_plane;
    std::uint32_t gaussian_count;
    std::uint32_t sh_order;      // 0=L0 (DC only), 1=L1, 2=L2
    std::uint32_t tile_size;     // for tiled compute (16, 32, or 64)
    std::uint32_t pad1;
};

static_assert(sizeof(GaussianUniforms) == 240,
              "GaussianUniforms must be 240 bytes (3x64 + 16 + 16 + 16)");
static_assert(std::is_standard_layout<GaussianUniforms>::value,
              "GaussianUniforms must be standard layout for GPU binding");
static_assert(alignof(GaussianUniforms) <= 16,
              "GaussianUniforms alignment must be <= 16 for GPU compatibility");

// ═══════════════════════════════════════════════════════════════════════
// GaussianTrainingUniforms: Constants for backward/training passes
// ═══════════════════════════════════════════════════════════════════════

struct GaussianTrainingUniforms {
    float gt_image_size[2];            // ground truth image dimensions
    float loss_weight_rgb;             // alpha
    float loss_weight_depth;           // beta
    float loss_weight_normal;          // gamma
    float loss_weight_pbr;             // delta
    float loss_weight_reg;             // epsilon
    std::uint32_t training_step;
    std::uint32_t total_steps;
    float transmittance_threshold;     // early termination (typically 0.001)
    float pad[2];
};

static_assert(sizeof(GaussianTrainingUniforms) == 48,
              "GaussianTrainingUniforms must be 48 bytes");
static_assert(std::is_standard_layout<GaussianTrainingUniforms>::value,
              "GaussianTrainingUniforms must be standard layout for GPU binding");

// ═══════════════════════════════════════════════════════════════════════
// TileConstants: Tile-based backward pass dispatch parameters
// ═══════════════════════════════════════════════════════════════════════
// max_gaussians_per_tile budget:
//   64x64 tiles -> 200 gaussians
//   32x32 tiles -> 50 gaussians
//   16x16 tiles -> 15 gaussians

struct TileConstants {
    std::uint32_t image_width;
    std::uint32_t image_height;
    std::uint32_t tile_size;
    std::uint32_t max_gaussians_per_tile;
    std::uint32_t total_gaussians;
    std::uint32_t pad[3];
};

static_assert(sizeof(TileConstants) == 32,
              "TileConstants must be 32 bytes (8 x uint32)");
static_assert(std::is_standard_layout<TileConstants>::value,
              "TileConstants must be standard layout for GPU binding");

}  // namespace render
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_RENDER_GAUSSIAN_SHADER_TYPES_H
