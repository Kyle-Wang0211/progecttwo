// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_RENDER_SPZ_COMPRESSOR_H
#define AETHER_RENDER_SPZ_COMPRESSOR_H

#ifdef __cplusplus

#include <cstdint>
#include <cstddef>
#include <vector>

namespace aether {
namespace render {

/// SPZ file header (binary layout).
struct SpzHeader {
    static constexpr std::uint32_t kMagic = 0x53505A31;  // "SPZ1"
    static constexpr std::uint16_t kVersion = 1;

    std::uint32_t num_splats;
    std::uint8_t sh_degree;
    std::uint8_t flags;
    float bbox_min[3];
    float bbox_max[3];
};

/// Decompressed Gaussian splat data.
struct GaussianSplatBuffer {
    std::vector<float> positions;   ///< [x,y,z,...] length = num_splats * 3
    std::vector<float> scales;      ///< [sx,sy,sz,...] length = num_splats * 3
    std::vector<float> rotations;   ///< [w,x,y,z,...] length = num_splats * 4
    std::vector<float> opacities;   ///< length = num_splats
    std::vector<float> sh_coeffs;   ///< length = num_splats * sh_per_splat * 3
    std::vector<float> colors;      ///< [r,g,b,...] length = num_splats * 3
    int num_splats{0};
    int sh_degree{0};
};

/// SPZ (Sorted-Position-Zip) compressor for Gaussian splat data.
///
/// Compression pipeline:
/// 1. Morton code sorting (Z-order curve) for spatial locality
/// 2. Fixed-point quantization (16-bit position, 8-bit attributes)
/// 3. Delta encoding on sorted positions
/// 4. zlib compression on the byte stream
class SpzCompressor {
public:
    /// Compress Gaussian splat data to SPZ format.
    /// @return Compressed blob. Empty on failure.
    static std::vector<std::uint8_t> compress(
        const float* positions,
        const float* scales,
        const float* rotations,
        const float* opacities,
        const float* sh_coeffs,
        int num_splats,
        int sh_degree);

    /// Decompress SPZ blob back to Gaussian splat data.
    /// @return true on success.
    static bool decompress(
        const std::uint8_t* data,
        std::size_t size,
        GaussianSplatBuffer* out);

private:
    static std::uint64_t morton_code(std::uint32_t x, std::uint32_t y, std::uint32_t z);
    static int sh_coeff_count(int degree);
    static std::vector<std::uint8_t> zlib_compress(const std::uint8_t* data, std::size_t size);
    static std::vector<std::uint8_t> zlib_decompress(const std::uint8_t* data, std::size_t size);
};

}  // namespace render
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_RENDER_SPZ_COMPRESSOR_H
