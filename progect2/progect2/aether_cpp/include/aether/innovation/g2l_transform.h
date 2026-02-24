// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_INNOVATION_G2L_TRANSFORM_H
#define AETHER_INNOVATION_G2L_TRANSFORM_H

#include <cstddef>

#include "aether/innovation/core_types.h"

namespace aether {
namespace innovation {

/// Global-to-Local coordinate transform.
///
/// For scenes spanning > 100 m the single-precision Float3 representation
/// loses sub-millimetre accuracy.  G2LTransform keeps a double-precision
/// origin and produces single-precision *local* offsets for all Gaussian
/// operations, then reconstructs full precision on export.
///
/// The origin is chosen so that local coordinates stay within ±50 m
/// (well within fp32 millimetre precision).  When a scan extends beyond
/// that range, `recenter_if_needed` shifts the origin and patches all
/// existing positions in a single pass.
struct G2LTransform {
    double origin[3]{0.0, 0.0, 0.0};

    // ── conversions ────────────────────────────────────────────

    /// Global (double) → local (float): local = float(global - origin).
    Float3 to_local(double gx, double gy, double gz) const;

    /// Local (float) → global (double): global = origin + double(local).
    void to_global(Float3 local, double* gx, double* gy, double* gz) const;

    // ── recentering ────────────────────────────────────────────

    /// If any component of any position exceeds \p threshold metres from
    /// the current origin, shift the origin to the centroid of the given
    /// positions and return true.  The caller is responsible for
    /// recomputing local coordinates after a recenter.
    ///
    /// Returns false (and does not modify origin) when no recenter is
    /// required.
    bool recenter_if_needed(const Float3* positions, std::size_t count,
                            float threshold = 50.0f);

    /// Compute the local-space centroid of \p count positions.
    static Float3 centroid(const Float3* positions, std::size_t count);
};

}  // namespace innovation
}  // namespace aether

#endif  // AETHER_INNOVATION_G2L_TRANSFORM_H
