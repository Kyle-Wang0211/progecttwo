// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_INNOVATION_PACKED_GAUSSIAN_H
#define AETHER_INNOVATION_PACKED_GAUSSIAN_H

#include <cstddef>
#include <cstdint>

#include "aether/core/status.h"
#include "aether/innovation/core_types.h"

namespace aether {
namespace innovation {

/// 96-byte GPU-compact Gaussian representation.
/// Layout optimised for cache-line alignment (96 = lcm(32,48)) and
/// Metal / Vulkan buffer binding.  During training the extra 7 bytes
/// freed by log-scale fp16 and uint8 opacity are re-used for caching
/// the position Hessian diagonal, eliminating a separate allocation.
struct PackedGaussian {
    float    position[3];          //  12B  world position (f32, precision required)
    uint16_t log_scale[3];         //   6B  log-encoded scale (fp16, exp on GPU)
    uint16_t rotation[4];          //   8B  quaternion (fp16)
    uint8_t  opacity_u8;           //   1B  sigmoid-encoded opacity [0..255]
    uint8_t  evidence_state;       //   1B  S0-S5 (ColorState mapping)
    uint16_t sh_dc[3];             //   6B  SH band-0 DC (fp16)
    uint16_t sh_rest[24];          //  48B  SH L1-L2 (fp16)
    uint8_t  flags;                //   1B  bit0=dynamic bit1=frozen bit2-3=lod
    uint8_t  padding0;             //   1B
    uint32_t gaussian_id;          //   4B  GaussianId
    uint16_t hessian_pos_diag[3];  //   6B  cached position Hessian diagonal (fp16)
    uint16_t padding1;             //   2B  align to 96
};

static_assert(sizeof(PackedGaussian) == 96,
              "PackedGaussian must be exactly 96 bytes");

// ─── fp16 ↔ fp32 (software, no hardware dependency) ────────────

uint16_t float_to_half(float f);
float    half_to_float(uint16_t h);

// ─── opacity encoding ──────────────────────────────────────────

/// Encode opacity ∈ [0,1] to uint8 via sigmoid inverse then quantise.
uint8_t  encode_opacity_u8(float opacity);
/// Decode uint8 back to opacity ∈ (0, 1).
float    decode_opacity_u8(uint8_t encoded);

// ─── log-scale encoding ────────────────────────────────────────

/// Encode positive scale to fp16 of log(scale).  Returns 0 for
/// non-positive inputs (clamped to 1e-7).
uint16_t encode_log_scale(float scale);
/// Decode fp16 log-scale back to positive float.
float    decode_log_scale(uint16_t encoded);

// ─── conversion ────────────────────────────────────────────────

/// Pack a single GaussianPrimitive into the compact 96-byte form.
core::Status pack_gaussian(const GaussianPrimitive& src, PackedGaussian* dst);

/// Unpack a single PackedGaussian back to the rich host form.
core::Status unpack_gaussian(const PackedGaussian& src, GaussianPrimitive* dst);

/// Batch pack.  \p dst must point to at least \p count elements.
core::Status pack_buffer(const GaussianPrimitive* src, std::size_t count,
                         PackedGaussian* dst);

/// Batch unpack.
core::Status unpack_buffer(const PackedGaussian* src, std::size_t count,
                           GaussianPrimitive* dst);

}  // namespace innovation
}  // namespace aether

#endif  // AETHER_INNOVATION_PACKED_GAUSSIAN_H
