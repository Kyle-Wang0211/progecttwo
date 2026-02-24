// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_QUALITY_MOTION_SPEED_H
#define AETHER_QUALITY_MOTION_SPEED_H

#ifdef __cplusplus

namespace aether {
namespace quality {

/// Compute camera translation speed (m/s) from two positions and timestamps.
///
/// - Returns 0 on invalid inputs or non-finite values.
/// - Uses min_dt_s floor (defaults to 1/240 when invalid) to avoid spikes.
double camera_translation_speed(
    const double current_position[3],
    const double previous_position[3],
    double current_timestamp_s,
    double previous_timestamp_s,
    double min_dt_s);

}  // namespace quality
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_QUALITY_MOTION_SPEED_H
