// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/multiscale_image_quality.h"

#include <algorithm>
#include <cmath>
#include <vector>

namespace aether {
namespace quality {

namespace {

// 3x3 Gaussian blur + 2x downsample
void downsample_2x(const std::uint8_t* src, int sw, int sh, int sr,
                   std::uint8_t* dst, int dw, int dh) {
    for (int y = 0; y < dh; ++y) {
        const int sy = std::min(y * 2, sh - 1);
        const int sy1 = std::min(sy + 1, sh - 1);
        for (int x = 0; x < dw; ++x) {
            const int sx = std::min(x * 2, sw - 1);
            const int sx1 = std::min(sx + 1, sw - 1);
            // Simple 2x2 box filter
            const int sum = static_cast<int>(src[sy * sr + sx]) +
                            static_cast<int>(src[sy * sr + sx1]) +
                            static_cast<int>(src[sy1 * sr + sx]) +
                            static_cast<int>(src[sy1 * sr + sx1]);
            dst[y * dw + x] = static_cast<std::uint8_t>(sum / 4);
        }
    }
}

// Compute Laplacian energy for a level
double laplacian_energy(const std::uint8_t* buf, int w, int h, int stride_step) {
    double sum_sq = 0.0;
    int count = 0;
    for (int y = 1; y < h - 1; y += stride_step) {
        for (int x = 1; x < w - 1; x += stride_step) {
            const int center = static_cast<int>(buf[y * w + x]);
            const int lap = static_cast<int>(buf[(y - 1) * w + x]) +
                            static_cast<int>(buf[(y + 1) * w + x]) +
                            static_cast<int>(buf[y * w + (x - 1)]) +
                            static_cast<int>(buf[y * w + (x + 1)]) -
                            4 * center;
            sum_sq += static_cast<double>(lap) * static_cast<double>(lap);
            ++count;
        }
    }
    return count > 0 ? sum_sq / static_cast<double>(count) : 0.0;
}

// MAD-based noise estimation from Laplacian responses
double estimate_noise_mad(const std::uint8_t* buf, int w, int h,
                          double mad_scale) {
    std::vector<double> responses;
    responses.reserve(static_cast<std::size_t>((w - 2) * (h - 2)));

    for (int y = 1; y < h - 1; ++y) {
        for (int x = 1; x < w - 1; ++x) {
            const int center = static_cast<int>(buf[y * w + x]);
            const int lap = static_cast<int>(buf[(y - 1) * w + x]) +
                            static_cast<int>(buf[(y + 1) * w + x]) +
                            static_cast<int>(buf[y * w + (x - 1)]) +
                            static_cast<int>(buf[y * w + (x + 1)]) -
                            4 * center;
            responses.push_back(std::abs(static_cast<double>(lap)));
        }
    }

    if (responses.empty()) return 0.0;

    const std::size_t mid = responses.size() / 2;
    std::nth_element(responses.begin(),
                     responses.begin() + static_cast<long>(mid),
                     responses.end());
    const double median = responses[mid];

    return median / std::max(mad_scale, 1e-9);
}

}  // namespace

int multiscale_image_quality(
    const std::uint8_t* bytes,
    int width,
    int height,
    int row_bytes,
    const MultiscaleConfig& config,
    MultiscaleImageResult* out_result) {

    if (!bytes || !out_result || width < 3 || height < 3) return -1;

    *out_result = {};

    if (config.quality_level >= 2) {
        out_result->confidence = 0.0;
        return 0;
    }

    const int stride = (config.quality_level == 1) ? 2 : 1;

    // Build pyramid levels
    struct Level {
        std::vector<std::uint8_t> data;
        int w, h;
    };
    std::vector<Level> levels;

    // Level 0: copy from source (normalize row_bytes to w)
    {
        Level l0;
        l0.w = width;
        l0.h = height;
        l0.data.resize(static_cast<std::size_t>(width * height));
        for (int y = 0; y < height; ++y) {
            for (int x = 0; x < width; ++x) {
                l0.data[static_cast<std::size_t>(y * width + x)] =
                    bytes[y * row_bytes + x];
            }
        }
        levels.push_back(std::move(l0));
    }

    // Build downsampled levels
    for (int lev = 1; lev < config.max_levels; ++lev) {
        const auto& prev = levels.back();
        const int nw = prev.w / 2;
        const int nh = prev.h / 2;
        if (nw < config.min_dimension || nh < config.min_dimension) break;

        Level next;
        next.w = nw;
        next.h = nh;
        next.data.resize(static_cast<std::size_t>(nw * nh));
        downsample_2x(prev.data.data(), prev.w, prev.h, prev.w,
                       next.data.data(), nw, nh);
        levels.push_back(std::move(next));
    }

    out_result->levels_computed = static_cast<int>(levels.size());

    // Per-level Laplacian energy
    for (int i = 0; i < out_result->levels_computed && i < 4; ++i) {
        out_result->per_level_energy[i] =
            laplacian_energy(levels[static_cast<std::size_t>(i)].data.data(),
                            levels[static_cast<std::size_t>(i)].w,
                            levels[static_cast<std::size_t>(i)].h,
                            stride);
    }

    // Sharpness profile: fine/coarse energy ratio
    // High ratio = detail concentrated at fine scale (sharp).
    // If coarse energy is near zero but fine is large, clamp to a high value.
    const int last = out_result->levels_computed - 1;
    if (last > 0) {
        if (out_result->per_level_energy[last] > 1e-9) {
            out_result->sharpness_profile =
                out_result->per_level_energy[0] / out_result->per_level_energy[last];
        } else if (out_result->per_level_energy[0] > 1e-9) {
            out_result->sharpness_profile = 1000.0;  // All detail at fine scale
        }
    }

    // Noise estimation from finest level
    out_result->noise_estimate =
        estimate_noise_mad(levels[0].data.data(), levels[0].w, levels[0].h,
                          config.noise_mad_scale);

    // Composite quality score
    const double sharpness_norm =
        std::min(1.0, out_result->sharpness_profile / 50.0);
    const double energy_norm =
        std::min(1.0, out_result->per_level_energy[0] / 2000.0);
    const double noise_penalty =
        std::min(1.0, out_result->noise_estimate / 25.0);

    out_result->composite_quality = std::max(0.0, std::min(1.0,
        0.40 * sharpness_norm + 0.35 * energy_norm +
        0.25 * (1.0 - noise_penalty)));

    // Confidence based on pixel count processed
    const int total_pixels = levels[0].w * levels[0].h;
    const int sampled = total_pixels / (stride * stride);
    out_result->confidence = std::min(1.0,
        static_cast<double>(sampled) / 10000.0);

    return 0;
}

}  // namespace quality
}  // namespace aether
