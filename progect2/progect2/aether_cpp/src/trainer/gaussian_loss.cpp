// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/trainer/gaussian_loss.h"

#include <algorithm>
#include <cmath>
#include <cstddef>

namespace {

// SSIM constants for dynamic range = 1.0 (normalized [0,1] pixel values).
static constexpr float kC1 = 0.01f * 0.01f;  // (0.01 * L)^2, L=1
static constexpr float kC2 = 0.03f * 0.03f;  // (0.03 * L)^2, L=1

// SSIM window radius. Full window = 2*radius+1 = 11x11.
static constexpr int kSSIMRadius = 5;

// Maximum scale before regularization penalty applies.
static constexpr float kMaxScaleThreshold = 5.0f;

// Minimum valid normal length for normal loss (avoids degenerate normals).
static constexpr float kMinNormalLength = 0.1f;

inline float clamp01(float v) {
    if (v < 0.0f) return 0.0f;
    if (v > 1.0f) return 1.0f;
    return v;
}

inline float safe_abs(float v) {
    return v < 0.0f ? -v : v;
}

}  // namespace

namespace aether {
namespace trainer {

GaussianLoss::GaussianLoss(const LossConfig& config)
    : config_(config) {}

core::Status GaussianLoss::compute(const LossInput& input,
                                   LossResult* result) {
    if (result == nullptr) {
        return core::Status::kInvalidArgument;
    }

    // Validate minimum required inputs
    if (input.width == 0 || input.height == 0) {
        return core::Status::kInvalidArgument;
    }
    if (input.rendered_rgb == nullptr || input.gt_rgb == nullptr) {
        return core::Status::kInvalidArgument;
    }
    if (input.total_steps == 0) {
        return core::Status::kInvalidArgument;
    }

    const std::size_t pixel_count =
        static_cast<std::size_t>(input.width) * input.height;

    // ── L_rgb = (1 - ssim_lambda) * L1 + ssim_lambda * (1 - SSIM) ────
    const float l1 = compute_l1_loss(input.rendered_rgb, input.gt_rgb,
                                     pixel_count);
    const float ssim = compute_ssim_loss(input.rendered_rgb, input.gt_rgb,
                                         input.width, input.height);
    const float loss_rgb =
        (1.0f - config_.ssim_lambda) * l1 +
        config_.ssim_lambda * (1.0f - ssim);

    // ── L_depth = noise-aware weighted depth residual ─────────────────
    float loss_depth = 0.0f;
    if (input.rendered_depth != nullptr && input.tsdf_depth != nullptr) {
        loss_depth = compute_depth_loss(input.rendered_depth,
                                        input.tsdf_depth,
                                        input.depth_confidence,
                                        pixel_count);
    }

    // ── L_normal = mean(1 - dot(n_rendered, n_tsdf)) ─────────────────
    float loss_normal = 0.0f;
    if (input.rendered_normal != nullptr && input.tsdf_normal != nullptr) {
        loss_normal = compute_normal_loss(input.rendered_normal,
                                          input.tsdf_normal,
                                          pixel_count);
    }

    // ── L_pbr = 0 (placeholder for future PBR inference) ─────────────
    const float loss_pbr = 0.0f;

    // ── L_reg = scale + opacity + scaffold binding ────────────────────
    float loss_reg = 0.0f;
    if (input.num_gaussians > 0) {
        loss_reg = compute_regularization_loss(input);
    }

    // ── Effective beta with exponential decay ─────────────────────────
    // beta(t) = weight_depth * exp(-decay_rate * step / total_steps)
    const float t_ratio =
        static_cast<float>(input.current_step) /
        static_cast<float>(input.total_steps);
    const float effective_beta =
        config_.weight_depth *
        std::exp(-config_.depth_weight_decay_rate * t_ratio);

    // ── Total loss ────────────────────────────────────────────────────
    const float total =
        config_.weight_rgb * loss_rgb +
        effective_beta * loss_depth +
        config_.weight_normal * loss_normal +
        config_.weight_pbr * loss_pbr +
        config_.weight_regularize * loss_reg;

    result->total_loss = total;
    result->loss_rgb = loss_rgb;
    result->loss_depth = loss_depth;
    result->loss_normal = loss_normal;
    result->loss_pbr = loss_pbr;
    result->loss_regularize = loss_reg;
    result->effective_beta = effective_beta;

    return core::Status::kOk;
}

// ═══════════════════════════════════════════════════════════════════════
// L1 loss: mean absolute error over all RGB channels
// ═══════════════════════════════════════════════════════════════════════

float GaussianLoss::compute_l1_loss(const float* rendered, const float* gt,
                                    std::size_t pixel_count) const {
    if (pixel_count == 0) {
        return 0.0f;
    }

    const std::size_t total_elements = pixel_count * 3;  // RGB channels
    double sum = 0.0;
    for (std::size_t i = 0; i < total_elements; ++i) {
        sum += static_cast<double>(safe_abs(rendered[i] - gt[i]));
    }
    return static_cast<float>(sum / static_cast<double>(total_elements));
}

// ═══════════════════════════════════════════════════════════════════════
// SSIM: Structural Similarity Index (per-channel, 11x11 window)
// ═══════════════════════════════════════════════════════════════════════
// Computes mean SSIM across the image using a sliding window.
// Returns SSIM in [0, 1], where 1 = perfect similarity.
// For the loss we use (1 - SSIM).

float GaussianLoss::compute_ssim_loss(const float* rendered, const float* gt,
                                      std::uint32_t width,
                                      std::uint32_t height) const {
    if (width == 0 || height == 0) {
        return 0.0f;
    }

    const int w = static_cast<int>(width);
    const int h = static_cast<int>(height);

    // Minimum image dimension must be larger than the window size
    if (w <= 2 * kSSIMRadius || h <= 2 * kSSIMRadius) {
        // Fall back to a simpler metric for tiny images
        return 0.0f;
    }

    double ssim_sum = 0.0;
    std::size_t window_count = 0;

    // Iterate over valid window centers (avoid boundary effects)
    for (int cy = kSSIMRadius; cy < h - kSSIMRadius; ++cy) {
        for (int cx = kSSIMRadius; cx < w - kSSIMRadius; ++cx) {
            // Compute per-channel SSIM for this window
            double channel_ssim = 0.0;

            for (int c = 0; c < 3; ++c) {
                double mu_r = 0.0;
                double mu_g = 0.0;
                double sigma_r2 = 0.0;
                double sigma_g2 = 0.0;
                double sigma_rg = 0.0;
                int count = 0;

                for (int dy = -kSSIMRadius; dy <= kSSIMRadius; ++dy) {
                    const int y = cy + dy;
                    for (int dx = -kSSIMRadius; dx <= kSSIMRadius; ++dx) {
                        const int x = cx + dx;
                        const std::size_t idx =
                            (static_cast<std::size_t>(y) * width + x) * 3 + c;
                        const double r = static_cast<double>(rendered[idx]);
                        const double g = static_cast<double>(gt[idx]);
                        mu_r += r;
                        mu_g += g;
                        ++count;
                    }
                }

                const double inv_count = 1.0 / static_cast<double>(count);
                mu_r *= inv_count;
                mu_g *= inv_count;

                // Second pass: variance and covariance
                for (int dy = -kSSIMRadius; dy <= kSSIMRadius; ++dy) {
                    const int y = cy + dy;
                    for (int dx = -kSSIMRadius; dx <= kSSIMRadius; ++dx) {
                        const int x = cx + dx;
                        const std::size_t idx =
                            (static_cast<std::size_t>(y) * width + x) * 3 + c;
                        const double dr =
                            static_cast<double>(rendered[idx]) - mu_r;
                        const double dg =
                            static_cast<double>(gt[idx]) - mu_g;
                        sigma_r2 += dr * dr;
                        sigma_g2 += dg * dg;
                        sigma_rg += dr * dg;
                    }
                }

                sigma_r2 *= inv_count;
                sigma_g2 *= inv_count;
                sigma_rg *= inv_count;

                const double c1 = static_cast<double>(kC1);
                const double c2 = static_cast<double>(kC2);
                const double numerator =
                    (2.0 * mu_r * mu_g + c1) * (2.0 * sigma_rg + c2);
                const double denominator =
                    (mu_r * mu_r + mu_g * mu_g + c1) *
                    (sigma_r2 + sigma_g2 + c2);

                if (denominator > 0.0) {
                    channel_ssim += numerator / denominator;
                }
            }

            ssim_sum += channel_ssim / 3.0;
            ++window_count;
        }
    }

    if (window_count == 0) {
        return 0.0f;
    }
    return static_cast<float>(ssim_sum / static_cast<double>(window_count));
}

// ═══════════════════════════════════════════════════════════════════════
// Depth loss: noise-aware weighted L1
// ═══════════════════════════════════════════════════════════════════════
// L_depth = sum(|d_rendered - d_tsdf| * confidence) / sum(confidence)

float GaussianLoss::compute_depth_loss(const float* rendered_depth,
                                       const float* tsdf_depth,
                                       const float* confidence,
                                       std::size_t pixel_count) const {
    if (pixel_count == 0) {
        return 0.0f;
    }

    double weighted_sum = 0.0;
    double weight_total = 0.0;

    for (std::size_t i = 0; i < pixel_count; ++i) {
        // Skip invalid depth values (zero or negative)
        if (rendered_depth[i] <= 0.0f || tsdf_depth[i] <= 0.0f) {
            continue;
        }

        const float w = (confidence != nullptr) ? clamp01(confidence[i]) : 1.0f;
        const double residual =
            static_cast<double>(safe_abs(rendered_depth[i] - tsdf_depth[i]));

        weighted_sum += residual * static_cast<double>(w);
        weight_total += static_cast<double>(w);
    }

    if (weight_total <= 0.0) {
        return 0.0f;
    }
    return static_cast<float>(weighted_sum / weight_total);
}

// ═══════════════════════════════════════════════════════════════════════
// Normal loss: mean cosine distance
// ═══════════════════════════════════════════════════════════════════════
// L_normal = mean(1 - dot(n_rendered, n_tsdf))  for valid normals

float GaussianLoss::compute_normal_loss(const float* rendered_normal,
                                        const float* tsdf_normal,
                                        std::size_t pixel_count) const {
    if (pixel_count == 0) {
        return 0.0f;
    }

    double loss_sum = 0.0;
    std::size_t valid_count = 0;

    for (std::size_t i = 0; i < pixel_count; ++i) {
        const std::size_t base = i * 3;
        const float rx = rendered_normal[base + 0];
        const float ry = rendered_normal[base + 1];
        const float rz = rendered_normal[base + 2];
        const float tx = tsdf_normal[base + 0];
        const float ty = tsdf_normal[base + 1];
        const float tz = tsdf_normal[base + 2];

        // Check that both normals have sufficient length (not degenerate)
        const float r_len_sq = rx * rx + ry * ry + rz * rz;
        const float t_len_sq = tx * tx + ty * ty + tz * tz;

        if (r_len_sq < kMinNormalLength * kMinNormalLength ||
            t_len_sq < kMinNormalLength * kMinNormalLength) {
            continue;
        }

        // Normalize and compute cosine similarity
        const float r_inv_len = 1.0f / std::sqrt(r_len_sq);
        const float t_inv_len = 1.0f / std::sqrt(t_len_sq);

        const float dot_val =
            (rx * r_inv_len) * (tx * t_inv_len) +
            (ry * r_inv_len) * (ty * t_inv_len) +
            (rz * r_inv_len) * (tz * t_inv_len);

        // Clamp dot product to [-1, 1] to avoid numerical issues
        const float clamped_dot = std::max(-1.0f, std::min(1.0f, dot_val));
        loss_sum += static_cast<double>(1.0f - clamped_dot);
        ++valid_count;
    }

    if (valid_count == 0) {
        return 0.0f;
    }
    return static_cast<float>(loss_sum / static_cast<double>(valid_count));
}

// ═══════════════════════════════════════════════════════════════════════
// Regularization loss: scale + opacity + scaffold binding
// ═══════════════════════════════════════════════════════════════════════
// L_reg = w_s * mean(max(0, |scale| - threshold)^2)
//       + w_o * mean(opacity * (1 - opacity))
//       + w_b * mean(|gaussian_pos - scaffold_pos|^2)  for bound gaussians

float GaussianLoss::compute_regularization_loss(const LossInput& input) const {
    const std::size_t n = static_cast<std::size_t>(input.num_gaussians);
    if (n == 0) {
        return 0.0f;
    }

    double scale_loss = 0.0;
    double opacity_loss = 0.0;
    double binding_loss = 0.0;
    std::size_t bound_count = 0;

    // Scale regularization: penalize scales exceeding threshold
    if (input.gaussian_scales != nullptr) {
        for (std::size_t i = 0; i < n; ++i) {
            for (std::size_t c = 0; c < 3; ++c) {
                const float s = safe_abs(input.gaussian_scales[i * 3 + c]);
                const float excess = s - kMaxScaleThreshold;
                if (excess > 0.0f) {
                    scale_loss += static_cast<double>(excess * excess);
                }
            }
        }
        scale_loss /= static_cast<double>(n * 3);
    }

    // Opacity regularization: encourage binary opacity (0 or 1)
    // Loss = opacity * (1 - opacity), maximized at 0.5
    if (input.gaussian_opacities != nullptr) {
        for (std::size_t i = 0; i < n; ++i) {
            const float o = clamp01(input.gaussian_opacities[i]);
            opacity_loss += static_cast<double>(o * (1.0f - o));
        }
        opacity_loss /= static_cast<double>(n);
    }

    // Scaffold binding: penalize distance between gaussian and its scaffold
    if (input.gaussian_positions != nullptr &&
        input.scaffold_positions != nullptr &&
        input.gaussian_scaffold_bindings != nullptr) {
        for (std::size_t i = 0; i < n; ++i) {
            // Only penalize bound gaussians (binding index != UINT32_MAX)
            if (input.gaussian_scaffold_bindings[i] == UINT32_MAX) {
                continue;
            }

            const std::size_t g_base = i * 3;
            const std::size_t s_idx =
                static_cast<std::size_t>(
                    input.gaussian_scaffold_bindings[i]) * 3;

            const float dx =
                input.gaussian_positions[g_base + 0] -
                input.scaffold_positions[s_idx + 0];
            const float dy =
                input.gaussian_positions[g_base + 1] -
                input.scaffold_positions[s_idx + 1];
            const float dz =
                input.gaussian_positions[g_base + 2] -
                input.scaffold_positions[s_idx + 2];

            binding_loss += static_cast<double>(dx * dx + dy * dy + dz * dz);
            ++bound_count;
        }
        if (bound_count > 0) {
            binding_loss /= static_cast<double>(bound_count);
        }
    }

    return config_.scale_reg_weight * static_cast<float>(scale_loss) +
           config_.opacity_reg_weight * static_cast<float>(opacity_loss) +
           config_.scaffold_binding_weight * static_cast<float>(binding_loss);
}

}  // namespace trainer
}  // namespace aether
