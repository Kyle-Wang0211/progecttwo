// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/render/spz_compressor.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <numeric>
#include <zlib.h>

namespace aether {
namespace render {

// ── Morton code (Z-order curve) ──

static std::uint64_t expand_bits(std::uint32_t v) {
    std::uint64_t n = static_cast<std::uint64_t>(v & 0x3FF);  // 10-bit input
    n = (n | (n << 16)) & 0x030000FF;
    n = (n | (n <<  8)) & 0x0300F00F;
    n = (n | (n <<  4)) & 0x030C30C3;
    n = (n | (n <<  2)) & 0x09249249;
    return n;
}

std::uint64_t SpzCompressor::morton_code(std::uint32_t x, std::uint32_t y, std::uint32_t z) {
    return expand_bits(x) | (expand_bits(y) << 1) | (expand_bits(z) << 2);
}

int SpzCompressor::sh_coeff_count(int degree) {
    switch (degree) {
        case 0: return 1;
        case 1: return 4;
        case 2: return 9;
        case 3: return 16;
        default: return 1;
    }
}

// ── zlib compression/decompression ──

std::vector<std::uint8_t> SpzCompressor::zlib_compress(const std::uint8_t* data, std::size_t size) {
    if (size == 0) return {};

    uLong bound = compressBound(static_cast<uLong>(size));
    std::vector<std::uint8_t> out(static_cast<std::size_t>(bound));
    uLong out_len = bound;

    int rc = compress2(out.data(), &out_len, data, static_cast<uLong>(size), Z_DEFAULT_COMPRESSION);
    if (rc != Z_OK) return {};

    out.resize(static_cast<std::size_t>(out_len));
    return out;
}

std::vector<std::uint8_t> SpzCompressor::zlib_decompress(const std::uint8_t* data, std::size_t size) {
    if (size == 0) return {};

    // Try progressively larger buffers
    for (int multiplier : {4, 8, 16, 32, 64}) {
        std::size_t out_capacity = size * static_cast<std::size_t>(multiplier);
        std::vector<std::uint8_t> out(out_capacity);
        uLong out_len = static_cast<uLong>(out_capacity);

        int rc = uncompress(out.data(), &out_len, data, static_cast<uLong>(size));
        if (rc == Z_OK) {
            out.resize(static_cast<std::size_t>(out_len));
            return out;
        }
        if (rc != Z_BUF_ERROR) break;  // Only retry on buffer-too-small
    }
    return {};
}

// ── Compress ──

std::vector<std::uint8_t> SpzCompressor::compress(
    const float* positions,
    const float* scales,
    const float* rotations,
    const float* opacities,
    const float* sh_coeffs,
    int num_splats,
    int sh_degree) {

    if (num_splats <= 0 || positions == nullptr) return {};

    // Step 1: Compute bounding box
    float min_p[3] = { positions[0], positions[1], positions[2] };
    float max_p[3] = { positions[0], positions[1], positions[2] };
    for (int i = 1; i < num_splats; ++i) {
        for (int c = 0; c < 3; ++c) {
            const float v = positions[i * 3 + c];
            min_p[c] = std::min(min_p[c], v);
            max_p[c] = std::max(max_p[c], v);
        }
    }

    // Step 2: Morton sort
    float range[3];
    for (int c = 0; c < 3; ++c)
        range[c] = std::max(max_p[c] - min_p[c], 1e-6f);

    std::vector<int> indices(static_cast<std::size_t>(num_splats));
    std::iota(indices.begin(), indices.end(), 0);

    std::vector<std::uint64_t> morton_codes(static_cast<std::size_t>(num_splats));
    for (int i = 0; i < num_splats; ++i) {
        auto nx = static_cast<std::uint32_t>(((positions[i * 3]     - min_p[0]) / range[0]) * 1023.0f);
        auto ny = static_cast<std::uint32_t>(((positions[i * 3 + 1] - min_p[1]) / range[1]) * 1023.0f);
        auto nz = static_cast<std::uint32_t>(((positions[i * 3 + 2] - min_p[2]) / range[2]) * 1023.0f);
        morton_codes[static_cast<std::size_t>(i)] = morton_code(nx, ny, nz);
    }

    std::sort(indices.begin(), indices.end(), [&](int a, int b) {
        return morton_codes[static_cast<std::size_t>(a)] < morton_codes[static_cast<std::size_t>(b)];
    });

    // Step 3: Build raw byte stream
    std::vector<std::uint8_t> raw;
    raw.reserve(static_cast<std::size_t>(num_splats) * 16);  // rough estimate

    // Header
    auto append_u32 = [&](std::uint32_t v) {
        raw.insert(raw.end(), reinterpret_cast<const std::uint8_t*>(&v),
                   reinterpret_cast<const std::uint8_t*>(&v) + 4);
    };
    auto append_u16 = [&](std::uint16_t v) {
        raw.insert(raw.end(), reinterpret_cast<const std::uint8_t*>(&v),
                   reinterpret_cast<const std::uint8_t*>(&v) + 2);
    };
    auto append_u8 = [&](std::uint8_t v) { raw.push_back(v); };
    auto append_f32 = [&](float v) {
        raw.insert(raw.end(), reinterpret_cast<const std::uint8_t*>(&v),
                   reinterpret_cast<const std::uint8_t*>(&v) + 4);
    };
    auto append_i16 = [&](std::int16_t v) {
        raw.insert(raw.end(), reinterpret_cast<const std::uint8_t*>(&v),
                   reinterpret_cast<const std::uint8_t*>(&v) + 2);
    };

    append_u32(SpzHeader::kMagic);
    append_u16(SpzHeader::kVersion);
    append_u32(static_cast<std::uint32_t>(num_splats));
    append_u8(static_cast<std::uint8_t>(sh_degree));
    append_u8(0);  // flags
    for (int c = 0; c < 3; ++c) append_f32(min_p[c]);
    for (int c = 0; c < 3; ++c) append_f32(max_p[c]);

    // Positions: 16-bit delta-encoded
    constexpr float scale16 = 65535.0f;
    std::int16_t prev_q[3] = {0, 0, 0};

    for (int idx : indices) {
        for (int c = 0; c < 3; ++c) {
            float norm = (positions[idx * 3 + c] - min_p[c]) / range[c];
            auto q = static_cast<std::int16_t>(
                std::max(-32768, std::min(32767, static_cast<int>(norm * scale16) - 32768)));
            auto delta = static_cast<std::int16_t>(q - prev_q[c]);
            append_i16(delta);
            prev_q[c] = q;
        }
    }

    // Scales: 8-bit log
    if (scales != nullptr) {
        for (int idx : indices) {
            for (int c = 0; c < 3; ++c) {
                float s = scales[idx * 3 + c];
                float log_s = std::log(std::max(s, 1e-8f));
                float norm = (log_s + 10.0f) / 12.0f;
                append_u8(static_cast<std::uint8_t>(
                    std::max(0, std::min(255, static_cast<int>(norm * 255.0f)))));
            }
        }
    }

    // Rotations: 8-bit per component
    if (rotations != nullptr) {
        for (int idx : indices) {
            for (int c = 0; c < 4; ++c) {
                float r = rotations[idx * 4 + c];
                append_u8(static_cast<std::uint8_t>(
                    std::max(0, std::min(255, static_cast<int>((r * 0.5f + 0.5f) * 255.0f)))));
            }
        }
    }

    // Opacities: 8-bit
    if (opacities != nullptr) {
        for (int idx : indices) {
            float o = opacities[idx];
            append_u8(static_cast<std::uint8_t>(
                std::max(0, std::min(255, static_cast<int>(o * 255.0f)))));
        }
    }

    // SH coefficients: 8-bit
    if (sh_coeffs != nullptr && sh_degree > 0) {
        const int sh_per_splat = sh_coeff_count(sh_degree) * 3;
        for (int idx : indices) {
            for (int c = 0; c < sh_per_splat; ++c) {
                float v = sh_coeffs[idx * sh_per_splat + c];
                float norm = (v + 2.0f) / 4.0f;
                append_u8(static_cast<std::uint8_t>(
                    std::max(0, std::min(255, static_cast<int>(norm * 255.0f)))));
            }
        }
    }

    // Step 4: zlib compress
    return zlib_compress(raw.data(), raw.size());
}

// ── Decompress ──

bool SpzCompressor::decompress(
    const std::uint8_t* data,
    std::size_t size,
    GaussianSplatBuffer* out) {

    if (data == nullptr || size == 0 || out == nullptr) return false;

    auto decompressed = zlib_decompress(data, size);
    if (decompressed.size() < 34) return false;  // Minimum header

    std::size_t offset = 0;
    auto read_u32 = [&]() -> std::uint32_t {
        std::uint32_t v; std::memcpy(&v, &decompressed[offset], 4); offset += 4; return v;
    };
    auto read_u16 = [&]() -> std::uint16_t {
        std::uint16_t v; std::memcpy(&v, &decompressed[offset], 2); offset += 2; return v;
    };
    auto read_u8 = [&]() -> std::uint8_t { return decompressed[offset++]; };
    auto read_f32 = [&]() -> float {
        float v; std::memcpy(&v, &decompressed[offset], 4); offset += 4; return v;
    };
    auto read_i16 = [&]() -> std::int16_t {
        std::int16_t v; std::memcpy(&v, &decompressed[offset], 2); offset += 2; return v;
    };

    // Header
    if (read_u32() != SpzHeader::kMagic) return false;
    if (read_u16() != SpzHeader::kVersion) return false;

    const auto num_splats = static_cast<int>(read_u32());
    const int sh_deg = static_cast<int>(read_u8());
    read_u8();  // flags

    float min_p[3], max_p[3];
    for (int c = 0; c < 3; ++c) min_p[c] = read_f32();
    for (int c = 0; c < 3; ++c) max_p[c] = read_f32();

    const auto n = static_cast<std::size_t>(num_splats);

    float range[3];
    for (int c = 0; c < 3; ++c)
        range[c] = std::max(max_p[c] - min_p[c], 1e-6f);
    constexpr float scale16 = 65535.0f;

    // Positions
    out->positions.resize(n * 3);
    std::int16_t prev_q[3] = {0, 0, 0};
    for (int i = 0; i < num_splats; ++i) {
        for (int c = 0; c < 3; ++c) {
            std::int16_t delta = read_i16();
            prev_q[c] = static_cast<std::int16_t>(prev_q[c] + delta);
            out->positions[static_cast<std::size_t>(i) * 3 + static_cast<std::size_t>(c)] =
                (static_cast<float>(static_cast<int>(prev_q[c]) + 32768) / scale16) * range[c] + min_p[c];
        }
    }

    // Scales
    out->scales.resize(n * 3);
    for (int i = 0; i < num_splats; ++i) {
        for (int c = 0; c < 3; ++c) {
            float norm = static_cast<float>(read_u8()) / 255.0f;
            float log_s = norm * 12.0f - 10.0f;
            out->scales[static_cast<std::size_t>(i) * 3 + static_cast<std::size_t>(c)] = std::exp(log_s);
        }
    }

    // Rotations
    out->rotations.resize(n * 4);
    for (int i = 0; i < num_splats; ++i) {
        for (int c = 0; c < 4; ++c) {
            out->rotations[static_cast<std::size_t>(i) * 4 + static_cast<std::size_t>(c)] =
                (static_cast<float>(read_u8()) / 255.0f - 0.5f) * 2.0f;
        }
    }

    // Opacities
    out->opacities.resize(n);
    for (int i = 0; i < num_splats; ++i) {
        out->opacities[static_cast<std::size_t>(i)] = static_cast<float>(read_u8()) / 255.0f;
    }

    // SH
    const int sh_per_splat = sh_coeff_count(sh_deg) * 3;
    if (sh_per_splat > 0 && offset + n * static_cast<std::size_t>(sh_per_splat) <= decompressed.size()) {
        out->sh_coeffs.resize(n * static_cast<std::size_t>(sh_per_splat));
        for (int i = 0; i < num_splats; ++i) {
            for (int c = 0; c < sh_per_splat; ++c) {
                out->sh_coeffs[static_cast<std::size_t>(i) * static_cast<std::size_t>(sh_per_splat) +
                               static_cast<std::size_t>(c)] =
                    static_cast<float>(read_u8()) / 255.0f * 4.0f - 2.0f;
            }
        }
    }

    // Colors from SH band 0
    out->colors.resize(n * 3, 1.0f);
    if (!out->sh_coeffs.empty() && sh_per_splat >= 3) {
        constexpr float c0 = 0.28209479f;  // Y_0^0 = 1 / (2*sqrt(pi))
        for (int i = 0; i < num_splats; ++i) {
            for (int c = 0; c < 3; ++c) {
                float val = out->sh_coeffs[static_cast<std::size_t>(i) * static_cast<std::size_t>(sh_per_splat) +
                                           static_cast<std::size_t>(c)] * c0 + 0.5f;
                out->colors[static_cast<std::size_t>(i) * 3 + static_cast<std::size_t>(c)] =
                    std::max(0.0f, std::min(1.0f, val));
            }
        }
    }

    out->num_splats = num_splats;
    out->sh_degree = sh_deg;
    return true;
}

}  // namespace render
}  // namespace aether
