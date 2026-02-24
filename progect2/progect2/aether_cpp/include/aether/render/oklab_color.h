// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_RENDER_OKLAB_COLOR_H
#define AETHER_RENDER_OKLAB_COLOR_H

#ifdef __cplusplus

namespace aether {
namespace render {

struct SRGBColor {
    float r, g, b;
};

/// Oklab perceptual color from display value [0, 1].
///
/// Pipeline: Weber-Fechner perceptual transform → Oklab interpolation
///           (dark cool → bright warm) → LMS → linear sRGB → gamma sRGB.
///
/// Reference: Ottosson "A perceptual color space for image processing" (2020)
SRGBColor oklab_color_from_display(float display);

/// Raw Oklab → gamma sRGB conversion.
SRGBColor oklab_to_srgb(float L, float a, float b);

}  // namespace render
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_RENDER_OKLAB_COLOR_H
