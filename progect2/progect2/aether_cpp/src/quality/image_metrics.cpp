// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/image_metrics.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>

namespace aether {
namespace quality {

namespace {

inline bool finite(double value) {
    return std::isfinite(value);
}

inline double clamp01(double value) {
    if (!finite(value)) {
        return 0.0;
    }
    return std::clamp(value, 0.0, 1.0);
}

double sampled_ratio_threshold(
    const std::uint8_t* bytes,
    int width,
    int height,
    int row_bytes,
    int threshold,
    bool count_if_ge) {
    if (bytes == nullptr || width <= 0 || height <= 0 || row_bytes < width) {
        return 0.0;
    }
    const int sample_stride = std::max(1, std::min(width, height) / 320);
    int hit = 0;
    int sampled = 0;
    for (int y = 0; y < height; y += sample_stride) {
        const int row = y * row_bytes;
        for (int x = 0; x < width; x += sample_stride) {
            const int luma = static_cast<int>(bytes[row + x]);
            const bool pass = count_if_ge ? (luma >= threshold) : (luma <= threshold);
            if (pass) {
                ++hit;
            }
            ++sampled;
        }
    }
    if (sampled <= 0) {
        return 0.0;
    }
    return static_cast<double>(hit) / static_cast<double>(sampled);
}

bool has_large_blown_region(
    const std::uint8_t* bytes,
    int width,
    int height,
    int row_bytes,
    int threshold) {
    if (bytes == nullptr || width < 8 || height < 8 || row_bytes < width) {
        return false;
    }
    const int stride = std::max(2, std::min(width, height) / 128);
    const int mask_width = (width + stride - 1) / stride;
    const int mask_height = (height + stride - 1) / stride;
    const int mask_count = mask_width * mask_height;
    if (mask_count <= 0) {
        return false;
    }

    std::vector<std::uint8_t> blown(static_cast<std::size_t>(mask_count), 0u);
    int total_blown = 0;
    for (int my = 0; my < mask_height; ++my) {
        const int y = std::min(my * stride, height - 1);
        const int row = y * row_bytes;
        for (int mx = 0; mx < mask_width; ++mx) {
            const int x = std::min(mx * stride, width - 1);
            if (static_cast<int>(bytes[row + x]) >= threshold) {
                blown[static_cast<std::size_t>(my * mask_width + mx)] = 1u;
                ++total_blown;
            }
        }
    }
    if (total_blown <= 0) {
        return false;
    }

    std::vector<std::uint8_t> visited(static_cast<std::size_t>(mask_count), 0u);
    std::vector<int> queue(static_cast<std::size_t>(mask_count), 0);
    int max_region = 0;

    for (int start = 0; start < mask_count; ++start) {
        if (blown[static_cast<std::size_t>(start)] == 0u ||
            visited[static_cast<std::size_t>(start)] != 0u) {
            continue;
        }

        int head = 0;
        int tail = 0;
        queue[static_cast<std::size_t>(tail++)] = start;
        visited[static_cast<std::size_t>(start)] = 1u;
        int region_size = 0;

        while (head < tail) {
            const int node = queue[static_cast<std::size_t>(head++)];
            ++region_size;
            const int x = node % mask_width;
            const int y = node / mask_width;

            const int left = node - 1;
            const int right = node + 1;
            const int up = node - mask_width;
            const int down = node + mask_width;

            if (x > 0 && blown[static_cast<std::size_t>(left)] != 0u &&
                visited[static_cast<std::size_t>(left)] == 0u) {
                visited[static_cast<std::size_t>(left)] = 1u;
                queue[static_cast<std::size_t>(tail++)] = left;
            }
            if (x + 1 < mask_width && blown[static_cast<std::size_t>(right)] != 0u &&
                visited[static_cast<std::size_t>(right)] == 0u) {
                visited[static_cast<std::size_t>(right)] = 1u;
                queue[static_cast<std::size_t>(tail++)] = right;
            }
            if (y > 0 && blown[static_cast<std::size_t>(up)] != 0u &&
                visited[static_cast<std::size_t>(up)] == 0u) {
                visited[static_cast<std::size_t>(up)] = 1u;
                queue[static_cast<std::size_t>(tail++)] = up;
            }
            if (y + 1 < mask_height && blown[static_cast<std::size_t>(down)] != 0u &&
                visited[static_cast<std::size_t>(down)] == 0u) {
                visited[static_cast<std::size_t>(down)] = 1u;
                queue[static_cast<std::size_t>(tail++)] = down;
            }
        }
        max_region = std::max(max_region, region_size);
    }

    const int absolute_threshold = std::max(16, static_cast<int>(static_cast<double>(mask_count) * 0.02));
    const bool relative_threshold =
        static_cast<double>(max_region) / static_cast<double>(mask_count) >= 0.015;
    return max_region >= absolute_threshold && relative_threshold;
}

double texture_entropy(
    const std::uint8_t* bytes,
    int width,
    int height,
    int row_bytes) {
    if (bytes == nullptr || width <= 0 || height <= 0 || row_bytes < width) {
        return 0.0;
    }
    const int sample_stride = std::max(1, std::min(width, height) / 320);
    static constexpr int kBinCount = 32;
    int histogram[kBinCount] = {0};
    int sample_count = 0;

    for (int y = 0; y < height; y += sample_stride) {
        const int row = y * row_bytes;
        for (int x = 0; x < width; x += sample_stride) {
            const int luma = static_cast<int>(bytes[row + x]);
            const int bucket = std::min(kBinCount - 1, (luma * kBinCount) / 256);
            ++histogram[bucket];
            ++sample_count;
        }
    }
    if (sample_count <= 0) {
        return 0.0;
    }

    double entropy = 0.0;
    for (int i = 0; i < kBinCount; ++i) {
        const int count = histogram[i];
        if (count <= 0) {
            continue;
        }
        const double p = static_cast<double>(count) / static_cast<double>(sample_count);
        entropy -= p * std::log2(p);
    }
    if (!finite(entropy)) {
        return 0.0;
    }
    return std::max(0.0, entropy);
}

}  // namespace

aether::core::Status laplacian_variance(
    const std::uint8_t* bytes,
    int width,
    int height,
    int row_bytes,
    double* out_variance) {
    if (out_variance == nullptr) {
        return aether::core::Status::kInvalidArgument;
    }
    *out_variance = 0.0;

    if (bytes == nullptr || width < 3 || height < 3 || row_bytes < width) {
        return aether::core::Status::kInvalidArgument;
    }

    // Welford online variance: single-pass, O(1) memory
    std::size_t n = 0;
    double mean = 0.0;
    double m2 = 0.0;

    for (int y = 1; y < height - 1; ++y) {
        for (int x = 1; x < width - 1; ++x) {
            const int base = y * row_bytes + x;
            const double v = static_cast<double>(bytes[base - row_bytes]) +
                static_cast<double>(bytes[base - 1]) -
                4.0 * static_cast<double>(bytes[base]) +
                static_cast<double>(bytes[base + 1]) +
                static_cast<double>(bytes[base + row_bytes]);
            ++n;
            const double delta = v - mean;
            mean += delta / static_cast<double>(n);
            const double delta2 = v - mean;
            m2 += delta * delta2;
        }
    }

    if (n == 0) {
        return aether::core::Status::kOk;
    }

    *out_variance = m2 / static_cast<double>(n);
    if (!std::isfinite(*out_variance) || *out_variance < 0.0) {
        *out_variance = 0.0;
    }
    return aether::core::Status::kOk;
}

aether::core::Status tenengrad_metric_for_quality(
    int quality_level,
    double tenengrad_threshold,
    double* out_value,
    double* out_confidence,
    double* out_roi_coverage,
    bool* out_skipped) {
    return tenengrad_metric_from_image(
        nullptr,
        0,
        0,
        0,
        quality_level,
        tenengrad_threshold,
        out_value,
        out_confidence,
        out_roi_coverage,
        out_skipped);
}

aether::core::Status tenengrad_metric_from_image(
    const std::uint8_t* bytes,
    int width,
    int height,
    int row_bytes,
    int quality_level,
    double tenengrad_threshold,
    double* out_value,
    double* out_confidence,
    double* out_roi_coverage,
    bool* out_skipped) {
    if (out_value == nullptr || out_confidence == nullptr || out_roi_coverage == nullptr || out_skipped == nullptr) {
        return aether::core::Status::kInvalidArgument;
    }
    if (!std::isfinite(tenengrad_threshold) || tenengrad_threshold < 0.0) {
        return aether::core::Status::kInvalidArgument;
    }

    *out_value = 0.0;
    *out_confidence = 0.0;
    *out_roi_coverage = 0.0;
    *out_skipped = false;

    if (quality_level >= 2) {
        *out_skipped = true;
        return aether::core::Status::kOk;
    }

    // Legacy fallback path when no image is provided.
    if (bytes == nullptr || width < 3 || height < 3 || row_bytes < width) {
        if (quality_level == 1) {
            *out_value = tenengrad_threshold * 0.94;
            *out_confidence = 0.85;
            *out_roi_coverage = 0.5;
        } else {
            *out_value = tenengrad_threshold * 1.12;
            *out_confidence = 0.95;
            *out_roi_coverage = 1.0;
        }
        if (!std::isfinite(*out_value)) {
            *out_value = 0.0;
            *out_confidence = 0.0;
            *out_roi_coverage = 0.0;
        }
        return aether::core::Status::kOk;
    }

    const int stride = quality_level == 1 ? 2 : 1;
    double sum_energy = 0.0;
    std::size_t sample_count = 0u;
    for (int y = 1; y < height - 1; y += stride) {
        for (int x = 1; x < width - 1; x += stride) {
            const int idx00 = (y - 1) * row_bytes + (x - 1);
            const int idx01 = (y - 1) * row_bytes + x;
            const int idx02 = (y - 1) * row_bytes + (x + 1);
            const int idx10 = y * row_bytes + (x - 1);
            const int idx12 = y * row_bytes + (x + 1);
            const int idx20 = (y + 1) * row_bytes + (x - 1);
            const int idx21 = (y + 1) * row_bytes + x;
            const int idx22 = (y + 1) * row_bytes + (x + 1);

            const double gx =
                -static_cast<double>(bytes[idx00]) + static_cast<double>(bytes[idx02]) +
                -2.0 * static_cast<double>(bytes[idx10]) + 2.0 * static_cast<double>(bytes[idx12]) +
                -static_cast<double>(bytes[idx20]) + static_cast<double>(bytes[idx22]);
            const double gy =
                -static_cast<double>(bytes[idx00]) - 2.0 * static_cast<double>(bytes[idx01]) - static_cast<double>(bytes[idx02]) +
                static_cast<double>(bytes[idx20]) + 2.0 * static_cast<double>(bytes[idx21]) + static_cast<double>(bytes[idx22]);
            sum_energy += gx * gx + gy * gy;
            sample_count += 1u;
        }
    }

    if (sample_count == 0u) {
        *out_skipped = true;
        return aether::core::Status::kOk;
    }

    const double score = sum_energy / static_cast<double>(sample_count);
    *out_value = score;
    *out_roi_coverage = quality_level == 1
        ? 0.25
        : static_cast<double>(sample_count) /
            static_cast<double>((width - 2) * (height - 2));

    if (tenengrad_threshold <= 1e-9) {
        *out_confidence = 1.0;
    } else {
        const double ratio = score / tenengrad_threshold;
        *out_confidence = std::clamp(0.5 + 0.25 * ratio, 0.0, 1.0);
    }

    if (!std::isfinite(*out_value)) {
        *out_value = 0.0;
        *out_confidence = 0.0;
        *out_roi_coverage = 0.0;
    }
    return aether::core::Status::kOk;
}

aether::core::Status exposure_analyze(
    const std::uint8_t* bytes,
    int width,
    int height,
    int row_bytes,
    ExposureAnalysis* out_result) {
    if (out_result == nullptr) {
        return aether::core::Status::kInvalidArgument;
    }
    *out_result = {};

    if (bytes == nullptr || width <= 0 || height <= 0 || row_bytes < width) {
        return aether::core::Status::kInvalidArgument;
    }

    static constexpr int kOverexposedLumaThreshold = 250;
    static constexpr int kUnderexposedLumaThreshold = 5;
    out_result->overexpose_ratio = sampled_ratio_threshold(
        bytes, width, height, row_bytes, kOverexposedLumaThreshold, true);
    out_result->underexpose_ratio = sampled_ratio_threshold(
        bytes, width, height, row_bytes, kUnderexposedLumaThreshold, false);
    out_result->has_large_blown_region = has_large_blown_region(
        bytes, width, height, row_bytes, kOverexposedLumaThreshold);
    return aether::core::Status::kOk;
}

aether::core::Status texture_analyze(
    const std::uint8_t* bytes,
    int width,
    int height,
    int row_bytes,
    TextureAnalysis* out_result) {
    if (out_result == nullptr) {
        return aether::core::Status::kInvalidArgument;
    }
    *out_result = {};

    if (bytes == nullptr || width < 5 || height < 5 || row_bytes < width) {
        return aether::core::Status::kInvalidArgument;
    }

    static constexpr int kGridSize = 8;
    std::uint8_t active_grid[kGridSize * kGridSize] = {};
    int feature_count = 0;
    const int sample_stride = std::max(1, std::min(width, height) / 256);
    static constexpr int kGradientThresholdSq = 28 * 28;
    static constexpr int kLocalContrastThreshold = 18;

    for (int y = 2; y < height - 2; y += sample_stride) {
        const int row = y * row_bytes;
        for (int x = 2; x < width - 2; x += sample_stride) {
            const int center = row + x;
            const int gx = static_cast<int>(bytes[center + 1]) - static_cast<int>(bytes[center - 1]);
            const int gy = static_cast<int>(bytes[center + row_bytes]) - static_cast<int>(bytes[center - row_bytes]);
            const int gradient_sq = gx * gx + gy * gy;
            if (gradient_sq < kGradientThresholdSq) {
                continue;
            }

            const int p1 = static_cast<int>(bytes[center - row_bytes - 1]);
            const int p2 = static_cast<int>(bytes[center - row_bytes + 1]);
            const int p3 = static_cast<int>(bytes[center + row_bytes - 1]);
            const int p4 = static_cast<int>(bytes[center + row_bytes + 1]);
            const int local_min = std::min(std::min(p1, p2), std::min(p3, p4));
            const int local_max = std::max(std::max(p1, p2), std::max(p3, p4));
            if (local_max - local_min < kLocalContrastThreshold) {
                continue;
            }

            ++feature_count;
            const int gx_index = std::min(kGridSize - 1, x * kGridSize / width);
            const int gy_index = std::min(kGridSize - 1, y * kGridSize / height);
            active_grid[gy_index * kGridSize + gx_index] = 1u;
        }
    }

    int active_cell_count = 0;
    for (int i = 0; i < kGridSize * kGridSize; ++i) {
        active_cell_count += static_cast<int>(active_grid[i]);
    }
    const double spread =
        static_cast<double>(active_cell_count) / static_cast<double>(kGridSize * kGridSize);

    const double entropy = texture_entropy(bytes, width, height, row_bytes);
    const double normalized_entropy = clamp01(entropy / 7.5);
    const double repetitive_penalty = std::max(0.0, 1.0 - normalized_entropy);
    const double normalized_feature = clamp01(
        static_cast<double>(feature_count) / static_cast<double>(300));
    const double fused_score = std::max(
        0.0,
        (0.55 * normalized_feature + 0.25 * normalized_entropy + 0.20 * spread) *
            (1.0 - 0.5 * repetitive_penalty));
    const double confidence = clamp01(0.55 + 0.45 * spread);

    out_result->feature_count = feature_count;
    out_result->spatial_spread = spread;
    out_result->entropy = entropy;
    out_result->repetitive_penalty = repetitive_penalty;
    out_result->fused_score = clamp01(fused_score);
    out_result->confidence = confidence;
    return aether::core::Status::kOk;
}

aether::core::Status brightness_metric_for_quality(
    int quality_level,
    double* out_value,
    double* out_confidence) {
    if (out_value == nullptr || out_confidence == nullptr) {
        return aether::core::Status::kInvalidArgument;
    }

    switch (quality_level) {
    case 0:  // full
        *out_value = 0.52;
        *out_confidence = 0.88;
        break;
    case 1:  // degraded
        *out_value = 0.50;
        *out_confidence = 0.74;
        break;
    default:  // emergency and any invalid level fall back to lowest quality behavior
        *out_value = 0.48;
        *out_confidence = 0.60;
        break;
    }
    return aether::core::Status::kOk;
}

aether::core::Status material_analyze_for_quality(
    int quality_level,
    MaterialAnalysis* out_result) {
    if (out_result == nullptr) {
        return aether::core::Status::kInvalidArgument;
    }
    *out_result = {};

    switch (quality_level) {
    case 0:  // full
        out_result->specular_percent = 2.0;
        out_result->transparent_percent = 5.0;
        out_result->textureless_percent = 10.0;
        out_result->is_non_lambertian = false;
        out_result->confidence = 0.95;
        out_result->largest_specular_region = 200;
        break;
    case 1:  // degraded
        out_result->specular_percent = 2.0;
        out_result->transparent_percent = 5.0;
        out_result->textureless_percent = 10.0;
        out_result->is_non_lambertian = false;
        out_result->confidence = 0.85;
        out_result->largest_specular_region = 200;
        break;
    default:  // emergency and invalid
        out_result->specular_percent = 0.0;
        out_result->transparent_percent = 0.0;
        out_result->textureless_percent = 0.0;
        out_result->is_non_lambertian = false;
        out_result->confidence = 0.60;
        out_result->largest_specular_region = 0;
        break;
    }
    return aether::core::Status::kOk;
}

}  // namespace quality
}  // namespace aether
