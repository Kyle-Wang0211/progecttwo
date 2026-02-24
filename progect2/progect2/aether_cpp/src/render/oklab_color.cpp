// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/render/oklab_color.h"

#include <algorithm>
#include <cmath>

namespace aether {
namespace render {

namespace {

float gamma_encode(float x) {
    const float c = std::max(0.0f, std::min(1.0f, x));
    return c <= 0.0031308f ? c * 12.92f : 1.055f * std::pow(c, 1.0f / 2.4f) - 0.055f;
}

}  // namespace

SRGBColor oklab_to_srgb(float L, float a, float b) {
    // Oklab → LMS (cube root space)
    const float l_ = L + 0.3963377774f * a + 0.2158037573f * b;
    const float m_ = L - 0.1055613458f * a - 0.0638541728f * b;
    const float s_ = L - 0.0894841775f * a - 1.2914855480f * b;

    // Undo cube root
    const float l = l_ * l_ * l_;
    const float m = m_ * m_ * m_;
    const float s = s_ * s_ * s_;

    // LMS → linear sRGB
    const float r_lin =  4.0767416621f * l - 3.3077115913f * m + 0.2309699292f * s;
    const float g_lin = -1.2684380046f * l + 2.6097574011f * m - 0.3413193965f * s;
    const float b_lin = -0.0041960863f * l - 0.7034186147f * m + 1.7076147010f * s;

    return {gamma_encode(r_lin), gamma_encode(g_lin), gamma_encode(b_lin)};
}

SRGBColor oklab_color_from_display(float display) {
    const float t = std::max(0.0f, std::min(1.0f, display));

    // Weber-Fechner perceptual transform: log(1 + t*(e-1)) maps [0,1]→[0,1]
    constexpr float eM1 = 2.718281828f - 1.0f;  // e - 1
    const float transformed = std::log(1.0f + t * eM1);

    // Oklab interpolation: dark cool → bright warm
    const float L = 0.15f + 0.70f * transformed;   // Lightness [0.15, 0.85]
    const float a = -0.05f + 0.08f * transformed;   // Green-red axis
    const float b = -0.10f + 0.20f * transformed;   // Blue-yellow axis

    return oklab_to_srgb(L, a, b);
}

}  // namespace render
}  // namespace aether
