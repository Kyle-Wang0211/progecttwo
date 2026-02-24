// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/quality/motion_speed.h"

#include <algorithm>
#include <cmath>

namespace aether {
namespace quality {

double camera_translation_speed(
    const double current_position[3],
    const double previous_position[3],
    double current_timestamp_s,
    double previous_timestamp_s,
    double min_dt_s) {
    if (current_position == nullptr || previous_position == nullptr) {
        return 0.0;
    }
    if (!std::isfinite(current_position[0]) || !std::isfinite(current_position[1]) ||
        !std::isfinite(current_position[2]) || !std::isfinite(previous_position[0]) ||
        !std::isfinite(previous_position[1]) || !std::isfinite(previous_position[2]) ||
        !std::isfinite(current_timestamp_s) || !std::isfinite(previous_timestamp_s)) {
        return 0.0;
    }

    const double dt_floor = (std::isfinite(min_dt_s) && min_dt_s > 0.0)
        ? min_dt_s
        : (1.0 / 240.0);
    const double raw_dt = current_timestamp_s - previous_timestamp_s;
    const double dt = std::max(raw_dt, dt_floor);

    const double dx = current_position[0] - previous_position[0];
    const double dy = current_position[1] - previous_position[1];
    const double dz = current_position[2] - previous_position[2];
    const double dist = std::sqrt(dx * dx + dy * dy + dz * dz);
    if (!std::isfinite(dist)) {
        return 0.0;
    }

    const double speed = dist / dt;
    if (!std::isfinite(speed) || speed < 0.0) {
        return 0.0;
    }
    return std::min(speed, 100.0);
}

}  // namespace quality
}  // namespace aether
