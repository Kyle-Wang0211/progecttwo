// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#ifndef AETHER_TSDF_POSE_STABILIZER_H
#define AETHER_TSDF_POSE_STABILIZER_H

#ifdef __cplusplus

#include "aether/core/status.h"

#include <array>
#include <cstdint>

namespace aether {
namespace tsdf {

/// IESKF noise parameters for Kalman filter tuning.
struct IESKFParams {
    float gyro_noise{1e-4f};            ///< Gyroscope noise (rad/s/√Hz)
    float accel_noise{1e-3f};           ///< Accelerometer noise (m/s²/√Hz)
    float gyro_bias_noise{1e-6f};       ///< Gyroscope bias random walk
    float accel_bias_noise{1e-5f};      ///< Accelerometer bias random walk
    float pos_obs_noise{5e-3f};         ///< Position observation noise (m)
    float rot_obs_noise{5e-3f};         ///< Rotation observation noise (rad)
    int max_iterations{3};              ///< IEKF iterations per update
    float convergence_threshold{1e-4f}; ///< IEKF convergence threshold
};

struct PoseStabilizerConfig {
    float translation_alpha{0.22f};
    float rotation_alpha{0.18f};
    float max_prediction_horizon_s{0.15f};
    float bias_alpha{0.03f};
    std::uint32_t init_frames{4u};
    bool fast_init{true};
    bool use_ieskf{false};
    IESKFParams ieskf_params;
};

class PoseStabilizer {
public:
    explicit PoseStabilizer(const PoseStabilizerConfig& config = PoseStabilizerConfig());

    void reset();

    core::Status update(
        const float raw_pose_16[16],
        const float gyro_xyz[3],
        const float accel_xyz[3],
        std::uint64_t timestamp_ns,
        float out_stabilized_pose_16[16],
        float* out_pose_quality);

    core::Status predict(
        std::uint64_t target_timestamp_ns,
        float out_predicted_pose_16[16]) const;

private:
    PoseStabilizerConfig config_;
    bool initialized_{false};
    std::uint64_t last_timestamp_ns_{0u};
    std::uint32_t frame_count_{0u};
    float pose_quality_{0.0f};

    std::array<float, 3> filtered_position_{{0.0f, 0.0f, 0.0f}};
    std::array<float, 4> filtered_rotation_{{1.0f, 0.0f, 0.0f, 0.0f}};  // wxyz
    std::array<float, 3> linear_velocity_{{0.0f, 0.0f, 0.0f}};
    std::array<float, 3> angular_velocity_{{0.0f, 0.0f, 0.0f}};
    std::array<float, 3> gyro_bias_{{0.0f, 0.0f, 0.0f}};
    std::array<float, 3> accel_bias_{{0.0f, 0.0f, 0.0f}};

    // ── IESKF state (15-dimensional error-state Kalman filter) ──
    // State: [δp(3), δv(3), δθ(3), δbg(3), δba(3)]
    // Error-state formulation: true = nominal ⊕ error
    static constexpr int kIeskfStateSize = 15;
    std::array<float, kIeskfStateSize * kIeskfStateSize> P_{};  ///< Covariance (row-major 15×15)

    void ieskf_predict(float dt_s, const float corrected_gyro[3], const float corrected_accel[3]);
    void ieskf_update(const float raw_position[3], const float raw_rotation_wxyz[4]);
};

}  // namespace tsdf
}  // namespace aether

#endif  // __cplusplus

#endif  // AETHER_TSDF_POSE_STABILIZER_H
