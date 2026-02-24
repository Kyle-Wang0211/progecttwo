// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/innovation/packed_gaussian.h"

#include <cmath>
#include <cstring>

namespace aether {
namespace innovation {

// ─── fp16 ↔ fp32 (software, no hardware dependency) ────────────

uint16_t float_to_half(float f) {
    uint32_t bits;
    std::memcpy(&bits, &f, sizeof(bits));

    uint32_t sign = (bits >> 31) & 0x1u;
    uint32_t exponent = (bits >> 23) & 0xFFu;
    uint32_t mantissa = bits & 0x7FFFFFu;

    uint16_t h_sign = static_cast<uint16_t>(sign << 15);

    if (exponent == 0xFF) {
        // Inf / NaN
        uint16_t h_mantissa = (mantissa != 0) ? 0x0200u : 0x0000u;
        return static_cast<uint16_t>(h_sign | 0x7C00u | h_mantissa);
    }

    int unbiased = static_cast<int>(exponent) - 127;
    if (unbiased > 15) {
        // Overflow -> infinity
        return static_cast<uint16_t>(h_sign | 0x7C00u);
    }
    if (unbiased < -24) {
        // Underflow -> zero
        return h_sign;
    }
    if (unbiased < -14) {
        // Denormalized
        mantissa |= 0x800000u;
        int shift = -1 - unbiased;
        uint16_t h_mantissa = static_cast<uint16_t>(mantissa >> (13 + shift));
        return static_cast<uint16_t>(h_sign | h_mantissa);
    }

    uint16_t h_exp = static_cast<uint16_t>((unbiased + 15) << 10);
    uint16_t h_mantissa = static_cast<uint16_t>(mantissa >> 13);
    return static_cast<uint16_t>(h_sign | h_exp | h_mantissa);
}

float half_to_float(uint16_t h) {
    uint32_t sign = static_cast<uint32_t>((h >> 15) & 0x1u) << 31;
    uint32_t exponent = (h >> 10) & 0x1Fu;
    uint32_t mantissa = h & 0x3FFu;

    uint32_t bits;
    if (exponent == 0) {
        if (mantissa == 0) {
            bits = sign;
        } else {
            // Denormalized: normalize
            exponent = 1;
            while ((mantissa & 0x400u) == 0) {
                mantissa <<= 1;
                exponent--;
            }
            mantissa &= 0x3FFu;
            bits = sign | ((exponent + 127 - 15) << 23) | (mantissa << 13);
        }
    } else if (exponent == 31) {
        bits = sign | 0x7F800000u | (mantissa << 13);
    } else {
        bits = sign | ((exponent + 127 - 15) << 23) | (mantissa << 13);
    }

    float result;
    std::memcpy(&result, &bits, sizeof(result));
    return result;
}

// ─── opacity encoding ──────────────────────────────────────────

uint8_t encode_opacity_u8(float opacity) {
    // Clamp to (0, 1).
    if (opacity <= 0.0f) opacity = 1e-6f;
    if (opacity >= 1.0f) opacity = 1.0f - 1e-6f;
    // Inverse sigmoid: logit(p) = log(p / (1 - p))
    float logit = std::log(opacity / (1.0f - opacity));
    // Map logit from [-inf, +inf] to [0, 255] via scaled sigmoid.
    // Use a fixed scale factor of 5.0.
    float scaled = 1.0f / (1.0f + std::exp(-logit / 5.0f));
    int v = static_cast<int>(scaled * 255.0f + 0.5f);
    if (v < 0) v = 0;
    if (v > 255) v = 255;
    return static_cast<uint8_t>(v);
}

float decode_opacity_u8(uint8_t encoded) {
    float scaled = static_cast<float>(encoded) / 255.0f;
    if (scaled <= 0.0f) scaled = 1e-6f;
    if (scaled >= 1.0f) scaled = 1.0f - 1e-6f;
    float logit = 5.0f * std::log(scaled / (1.0f - scaled));
    return 1.0f / (1.0f + std::exp(-logit));
}

// ─── log-scale encoding ────────────────────────────────────────

uint16_t encode_log_scale(float scale) {
    if (scale <= 0.0f) scale = 1e-7f;
    return float_to_half(std::log(scale));
}

float decode_log_scale(uint16_t encoded) {
    return std::exp(half_to_float(encoded));
}

// ─── conversion ────────────────────────────────────────────────

core::Status pack_gaussian(const GaussianPrimitive& src, PackedGaussian* dst) {
    if (!dst) return core::Status::kInvalidArgument;

    dst->position[0] = src.position.x;
    dst->position[1] = src.position.y;
    dst->position[2] = src.position.z;

    dst->log_scale[0] = encode_log_scale(src.scale.x);
    dst->log_scale[1] = encode_log_scale(src.scale.y);
    dst->log_scale[2] = encode_log_scale(src.scale.z);

    // Rotation: identity quaternion encoded as fp16.
    dst->rotation[0] = float_to_half(1.0f);
    dst->rotation[1] = float_to_half(0.0f);
    dst->rotation[2] = float_to_half(0.0f);
    dst->rotation[3] = float_to_half(0.0f);

    dst->opacity_u8 = encode_opacity_u8(src.opacity);
    dst->evidence_state = 0;

    // SH DC from first 3 coefficients.
    for (int i = 0; i < 3 && i < 16; ++i) {
        dst->sh_dc[i] = float_to_half(src.sh_coeffs[static_cast<std::size_t>(i)]);
    }

    // SH rest.
    for (int i = 0; i < 24; ++i) {
        int src_idx = i + 3;
        dst->sh_rest[i] = (src_idx < 16) ? float_to_half(src.sh_coeffs[static_cast<std::size_t>(src_idx)]) : 0;
    }

    dst->flags = src.flags;
    dst->padding0 = 0;
    dst->gaussian_id = src.id;
    dst->hessian_pos_diag[0] = 0;
    dst->hessian_pos_diag[1] = 0;
    dst->hessian_pos_diag[2] = 0;
    dst->padding1 = 0;

    return core::Status::kOk;
}

core::Status unpack_gaussian(const PackedGaussian& src, GaussianPrimitive* dst) {
    if (!dst) return core::Status::kInvalidArgument;

    dst->position.x = src.position[0];
    dst->position.y = src.position[1];
    dst->position.z = src.position[2];

    dst->scale.x = decode_log_scale(src.log_scale[0]);
    dst->scale.y = decode_log_scale(src.log_scale[1]);
    dst->scale.z = decode_log_scale(src.log_scale[2]);

    dst->opacity = decode_opacity_u8(src.opacity_u8);
    dst->id = src.gaussian_id;
    dst->flags = src.flags;

    for (int i = 0; i < 3; ++i) {
        dst->sh_coeffs[static_cast<std::size_t>(i)] = half_to_float(src.sh_dc[i]);
    }
    for (int i = 0; i < 13; ++i) {
        dst->sh_coeffs[static_cast<std::size_t>(i + 3)] = half_to_float(src.sh_rest[i]);
    }

    return core::Status::kOk;
}

core::Status pack_buffer(const GaussianPrimitive* src, std::size_t count,
                         PackedGaussian* dst) {
    if (!src || !dst) return core::Status::kInvalidArgument;
    for (std::size_t i = 0; i < count; ++i) {
        auto s = pack_gaussian(src[i], &dst[i]);
        if (!core::is_ok(s)) return s;
    }
    return core::Status::kOk;
}

core::Status unpack_buffer(const PackedGaussian* src, std::size_t count,
                           GaussianPrimitive* dst) {
    if (!src || !dst) return core::Status::kInvalidArgument;
    for (std::size_t i = 0; i < count; ++i) {
        auto s = unpack_gaussian(src[i], &dst[i]);
        if (!core::is_ok(s)) return s;
    }
    return core::Status::kOk;
}

}  // namespace innovation
}  // namespace aether
