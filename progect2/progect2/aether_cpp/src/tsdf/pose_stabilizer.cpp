// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include "aether/tsdf/pose_stabilizer.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>

namespace aether {
namespace tsdf {
namespace {

constexpr float kEps = 1e-6f;

struct Vec3 {
    float x{0.0f};
    float y{0.0f};
    float z{0.0f};
};

struct Quat {
    float w{1.0f};
    float x{0.0f};
    float y{0.0f};
    float z{0.0f};
};

bool finite(float v) {
    return std::isfinite(v);
}

bool finite_pose(const float* pose16) {
    if (pose16 == nullptr) {
        return false;
    }
    for (std::size_t i = 0u; i < 16u; ++i) {
        if (!finite(pose16[i])) {
            return false;
        }
    }
    return true;
}

Vec3 make_vec3(const float xyz[3]) {
    return Vec3{xyz[0], xyz[1], xyz[2]};
}

Vec3 make_vec3(const std::array<float, 3>& xyz) {
    return Vec3{xyz[0], xyz[1], xyz[2]};
}

std::array<float, 3> to_array(const Vec3& v) {
    return {v.x, v.y, v.z};
}

Vec3 add(const Vec3& a, const Vec3& b) {
    return Vec3{a.x + b.x, a.y + b.y, a.z + b.z};
}

Vec3 sub(const Vec3& a, const Vec3& b) {
    return Vec3{a.x - b.x, a.y - b.y, a.z - b.z};
}

Vec3 mul(const Vec3& v, float s) {
    return Vec3{v.x * s, v.y * s, v.z * s};
}

float dot(const Vec3& a, const Vec3& b) {
    return a.x * b.x + a.y * b.y + a.z * b.z;
}

float norm(const Vec3& v) {
    return std::sqrt(std::max(0.0f, dot(v, v)));
}

Vec3 normalized(const Vec3& v) {
    const float n = norm(v);
    if (n <= kEps) {
        return Vec3{1.0f, 0.0f, 0.0f};
    }
    return mul(v, 1.0f / n);
}

float clamp01(float v) {
    return std::max(0.0f, std::min(1.0f, v));
}

float clampf(float v, float lo, float hi) {
    return std::max(lo, std::min(hi, v));
}

Vec3 lerp(const Vec3& a, const Vec3& b, float t) {
    return add(mul(a, 1.0f - t), mul(b, t));
}

Quat quat_normalize(const Quat& q) {
    const float n = std::sqrt(std::max(kEps, q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z));
    const float inv = 1.0f / n;
    return Quat{q.w * inv, q.x * inv, q.y * inv, q.z * inv};
}

Quat quat_mul(const Quat& a, const Quat& b) {
    return Quat{
        a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z,
        a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
        a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
        a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w};
}

Quat quat_from_axis_angle(const Vec3& axis_in, float angle_rad) {
    const Vec3 axis = normalized(axis_in);
    const float half = 0.5f * angle_rad;
    const float s = std::sin(half);
    const float c = std::cos(half);
    return quat_normalize(Quat{c, axis.x * s, axis.y * s, axis.z * s});
}

Quat quat_slerp(Quat a, Quat b, float t) {
    a = quat_normalize(a);
    b = quat_normalize(b);
    float cos_theta = a.w * b.w + a.x * b.x + a.y * b.y + a.z * b.z;
    if (cos_theta < 0.0f) {
        cos_theta = -cos_theta;
        b.w = -b.w;
        b.x = -b.x;
        b.y = -b.y;
        b.z = -b.z;
    }

    if (cos_theta > 0.9995f) {
        const Quat blended{
            a.w + t * (b.w - a.w),
            a.x + t * (b.x - a.x),
            a.y + t * (b.y - a.y),
            a.z + t * (b.z - a.z)};
        return quat_normalize(blended);
    }

    const float theta = std::acos(clampf(cos_theta, -1.0f, 1.0f));
    const float sin_theta = std::sin(theta);
    if (sin_theta <= kEps) {
        return quat_normalize(a);
    }
    const float w0 = std::sin((1.0f - t) * theta) / sin_theta;
    const float w1 = std::sin(t * theta) / sin_theta;
    return quat_normalize(Quat{
        a.w * w0 + b.w * w1,
        a.x * w0 + b.x * w1,
        a.y * w0 + b.y * w1,
        a.z * w0 + b.z * w1});
}

float quat_angular_distance_rad(const Quat& a_in, const Quat& b_in) {
    const Quat a = quat_normalize(a_in);
    const Quat b = quat_normalize(b_in);
    float d = a.w * b.w + a.x * b.x + a.y * b.y + a.z * b.z;
    d = std::fabs(clampf(d, -1.0f, 1.0f));
    return 2.0f * std::acos(clampf(d, -1.0f, 1.0f));
}

Quat quat_from_pose_matrix(const float m[16]) {
    // Matrix is treated as column-major (translation in m[12..14]).
    // rXY means row X, col Y.
    const float r00 = m[0];
    const float r01 = m[4];
    const float r02 = m[8];
    const float r10 = m[1];
    const float r11 = m[5];
    const float r12 = m[9];
    const float r20 = m[2];
    const float r21 = m[6];
    const float r22 = m[10];

    const float trace = r00 + r11 + r22;
    Quat q{};
    if (trace > 0.0f) {
        const float s = std::sqrt(trace + 1.0f) * 2.0f;
        q.w = 0.25f * s;
        q.x = (r21 - r12) / s;
        q.y = (r02 - r20) / s;
        q.z = (r10 - r01) / s;
    } else if (r00 > r11 && r00 > r22) {
        const float s = std::sqrt(std::max(kEps, 1.0f + r00 - r11 - r22)) * 2.0f;
        q.w = (r21 - r12) / s;
        q.x = 0.25f * s;
        q.y = (r01 + r10) / s;
        q.z = (r02 + r20) / s;
    } else if (r11 > r22) {
        const float s = std::sqrt(std::max(kEps, 1.0f + r11 - r00 - r22)) * 2.0f;
        q.w = (r02 - r20) / s;
        q.x = (r01 + r10) / s;
        q.y = 0.25f * s;
        q.z = (r12 + r21) / s;
    } else {
        const float s = std::sqrt(std::max(kEps, 1.0f + r22 - r00 - r11)) * 2.0f;
        q.w = (r10 - r01) / s;
        q.x = (r02 + r20) / s;
        q.y = (r12 + r21) / s;
        q.z = 0.25f * s;
    }
    return quat_normalize(q);
}

void quat_to_pose_rotation(const Quat& q_in, float m[16]) {
    const Quat q = quat_normalize(q_in);

    const float xx = q.x * q.x;
    const float yy = q.y * q.y;
    const float zz = q.z * q.z;
    const float xy = q.x * q.y;
    const float xz = q.x * q.z;
    const float yz = q.y * q.z;
    const float wx = q.w * q.x;
    const float wy = q.w * q.y;
    const float wz = q.w * q.z;

    const float r00 = 1.0f - 2.0f * (yy + zz);
    const float r01 = 2.0f * (xy - wz);
    const float r02 = 2.0f * (xz + wy);

    const float r10 = 2.0f * (xy + wz);
    const float r11 = 1.0f - 2.0f * (xx + zz);
    const float r12 = 2.0f * (yz - wx);

    const float r20 = 2.0f * (xz - wy);
    const float r21 = 2.0f * (yz + wx);
    const float r22 = 1.0f - 2.0f * (xx + yy);

    // Column-major layout.
    m[0] = r00;
    m[1] = r10;
    m[2] = r20;
    m[3] = 0.0f;

    m[4] = r01;
    m[5] = r11;
    m[6] = r21;
    m[7] = 0.0f;

    m[8] = r02;
    m[9] = r12;
    m[10] = r22;
    m[11] = 0.0f;
}

Vec3 extract_position(const float pose16[16]) {
    return Vec3{pose16[12], pose16[13], pose16[14]};
}

void compose_pose_quat(const Quat& rotation, const Vec3& position, float out_pose16[16]) {
    quat_to_pose_rotation(rotation, out_pose16);
    out_pose16[12] = position.x;
    out_pose16[13] = position.y;
    out_pose16[14] = position.z;
    out_pose16[15] = 1.0f;
}

Quat integrate_gyro(const Quat& base, const Vec3& gyro_rad_s, float dt_s) {
    const float omega = norm(gyro_rad_s);
    if (omega <= kEps || dt_s <= kEps) {
        return quat_normalize(base);
    }
    const float angle = omega * dt_s;
    const Vec3 axis = mul(gyro_rad_s, 1.0f / omega);
    const Quat delta = quat_from_axis_angle(axis, angle);
    return quat_normalize(quat_mul(base, delta));
}

// ── 15×15 matrix utilities for IESKF covariance propagation ──
// Row-major layout, no Eigen dependency. State: [δp(3), δv(3), δθ(3), δbg(3), δba(3)]

constexpr int kN = 15;

inline float& mat15(float* M, int r, int c) { return M[r * kN + c]; }
inline float mat15c(const float* M, int r, int c) { return M[r * kN + c]; }

void mat15_zero(float* M) {
    for (int i = 0; i < kN * kN; ++i) M[i] = 0.0f;
}

void mat15_identity(float* M) {
    mat15_zero(M);
    for (int i = 0; i < kN; ++i) mat15(M, i, i) = 1.0f;
}

// C = A * B (all kN×kN)
void mat15_mul(float* C, const float* A, const float* B) {
    float tmp[kN * kN];
    for (int i = 0; i < kN; ++i) {
        for (int j = 0; j < kN; ++j) {
            float sum = 0.0f;
            for (int k = 0; k < kN; ++k) {
                sum += mat15c(A, i, k) * mat15c(B, k, j);
            }
            mat15(tmp, i, j) = sum;
        }
    }
    for (int i = 0; i < kN * kN; ++i) C[i] = tmp[i];
}

// C = A + B
void mat15_add(float* C, const float* A, const float* B) {
    for (int i = 0; i < kN * kN; ++i) C[i] = A[i] + B[i];
}

// B = Aᵀ
void mat15_transpose(float* B, const float* A) {
    float tmp[kN * kN];
    for (int i = 0; i < kN; ++i)
        for (int j = 0; j < kN; ++j)
            mat15(tmp, j, i) = mat15c(A, i, j);
    for (int i = 0; i < kN * kN; ++i) B[i] = tmp[i];
}

// 3×3 skew-symmetric matrix from vector
void skew3x3(float m[9], float x, float y, float z) {
    m[0] = 0.0f;  m[1] = -z;    m[2] = y;
    m[3] = z;      m[4] = 0.0f;  m[5] = -x;
    m[6] = -y;     m[7] = x;     m[8] = 0.0f;
}

// Invert a 6×6 matrix via Gauss-Jordan elimination.
bool invert6x6(float out[36], const float in_m[36]) {
    float aug[72];
    for (int i = 0; i < 6; ++i) {
        for (int j = 0; j < 6; ++j) {
            aug[i * 12 + j] = in_m[i * 6 + j];
            aug[i * 12 + 6 + j] = (i == j) ? 1.0f : 0.0f;
        }
    }
    for (int col = 0; col < 6; ++col) {
        int pivot = col;
        float maxVal = std::fabs(aug[col * 12 + col]);
        for (int row = col + 1; row < 6; ++row) {
            float v = std::fabs(aug[row * 12 + col]);
            if (v > maxVal) { maxVal = v; pivot = row; }
        }
        if (maxVal < 1e-12f) return false;
        if (pivot != col) {
            for (int j = 0; j < 12; ++j) std::swap(aug[col * 12 + j], aug[pivot * 12 + j]);
        }
        float diagInv = 1.0f / aug[col * 12 + col];
        for (int j = 0; j < 12; ++j) aug[col * 12 + j] *= diagInv;
        for (int row = 0; row < 6; ++row) {
            if (row == col) continue;
            float factor = aug[row * 12 + col];
            for (int j = 0; j < 12; ++j) aug[row * 12 + j] -= factor * aug[col * 12 + j];
        }
    }
    for (int i = 0; i < 6; ++i)
        for (int j = 0; j < 6; ++j)
            out[i * 6 + j] = aug[i * 12 + 6 + j];
    return true;
}

// Rotation matrix (3×3 row-major) from quaternion (wxyz)
void quat_to_rot3(float R[9], float qw, float qx, float qy, float qz) {
    float n = std::sqrt(std::max(kEps, qw*qw + qx*qx + qy*qy + qz*qz));
    float inv = 1.0f / n;
    qw *= inv; qx *= inv; qy *= inv; qz *= inv;
    R[0] = 1-2*(qy*qy+qz*qz); R[1] = 2*(qx*qy-qw*qz);   R[2] = 2*(qx*qz+qw*qy);
    R[3] = 2*(qx*qy+qw*qz);   R[4] = 1-2*(qx*qx+qz*qz); R[5] = 2*(qy*qz-qw*qx);
    R[6] = 2*(qx*qz-qw*qy);   R[7] = 2*(qy*qz+qw*qx);   R[8] = 1-2*(qx*qx+qy*qy);
}

}  // namespace

PoseStabilizer::PoseStabilizer(const PoseStabilizerConfig& config)
    : config_(config) {
    if (!std::isfinite(config_.translation_alpha)) {
        config_.translation_alpha = 0.22f;
    }
    if (!std::isfinite(config_.rotation_alpha)) {
        config_.rotation_alpha = 0.18f;
    }
    if (!std::isfinite(config_.max_prediction_horizon_s) || config_.max_prediction_horizon_s <= 0.0f) {
        config_.max_prediction_horizon_s = 0.15f;
    }
    if (!std::isfinite(config_.bias_alpha) || config_.bias_alpha <= 0.0f) {
        config_.bias_alpha = 0.03f;
    }
    if (config_.init_frames == 0u) {
        config_.init_frames = 4u;
    }
    reset();
}

void PoseStabilizer::reset() {
    initialized_ = false;
    last_timestamp_ns_ = 0u;
    frame_count_ = 0u;
    pose_quality_ = 0.0f;
    filtered_position_ = {{0.0f, 0.0f, 0.0f}};
    filtered_rotation_ = {{1.0f, 0.0f, 0.0f, 0.0f}};
    linear_velocity_ = {{0.0f, 0.0f, 0.0f}};
    angular_velocity_ = {{0.0f, 0.0f, 0.0f}};
    gyro_bias_ = {{0.0f, 0.0f, 0.0f}};
    accel_bias_ = {{0.0f, 0.0f, 0.0f}};

    // Initialize IESKF covariance with conservative diagonal values
    mat15_identity(P_.data());
    // Position uncertainty: 10cm
    for (int i = 0; i < 3; ++i) mat15(P_.data(), i, i) = 0.01f;
    // Velocity uncertainty: 0.1 m/s
    for (int i = 3; i < 6; ++i) mat15(P_.data(), i, i) = 0.01f;
    // Orientation uncertainty: ~5.7° (0.1 rad)
    for (int i = 6; i < 9; ++i) mat15(P_.data(), i, i) = 0.01f;
    // Gyro bias uncertainty
    for (int i = 9; i < 12; ++i) mat15(P_.data(), i, i) = 1e-6f;
    // Accel bias uncertainty
    for (int i = 12; i < 15; ++i) mat15(P_.data(), i, i) = 1e-4f;
}

core::Status PoseStabilizer::update(
    const float raw_pose_16[16],
    const float gyro_xyz[3],
    const float accel_xyz[3],
    std::uint64_t timestamp_ns,
    float out_stabilized_pose_16[16],
    float* out_pose_quality) {
    if (out_stabilized_pose_16 == nullptr || out_pose_quality == nullptr ||
        raw_pose_16 == nullptr || gyro_xyz == nullptr || accel_xyz == nullptr) {
        return core::Status::kInvalidArgument;
    }
    if (!finite_pose(raw_pose_16) ||
        !finite(gyro_xyz[0]) || !finite(gyro_xyz[1]) || !finite(gyro_xyz[2]) ||
        !finite(accel_xyz[0]) || !finite(accel_xyz[1]) || !finite(accel_xyz[2])) {
        return core::Status::kInvalidArgument;
    }

    const Vec3 raw_position = extract_position(raw_pose_16);
    const Quat raw_rotation = quat_from_pose_matrix(raw_pose_16);
    const Vec3 raw_gyro = make_vec3(gyro_xyz);
    const Vec3 raw_accel = make_vec3(accel_xyz);

    if (!initialized_) {
        filtered_position_ = to_array(raw_position);
        filtered_rotation_ = {{raw_rotation.w, raw_rotation.x, raw_rotation.y, raw_rotation.z}};
        linear_velocity_ = {{0.0f, 0.0f, 0.0f}};
        angular_velocity_ = {{raw_gyro.x, raw_gyro.y, raw_gyro.z}};
        last_timestamp_ns_ = timestamp_ns;
        frame_count_ = 1u;
        initialized_ = true;
        pose_quality_ = config_.fast_init ? 0.60f : 0.40f;
        compose_pose_quat(raw_rotation, raw_position, out_stabilized_pose_16);
        *out_pose_quality = pose_quality_;
        return core::Status::kOk;
    }

    float dt_s = 1.0f / 120.0f;
    if (timestamp_ns > last_timestamp_ns_) {
        const std::uint64_t dt_ns = timestamp_ns - last_timestamp_ns_;
        dt_s = static_cast<float>(static_cast<double>(dt_ns) * 1e-9);
    }
    dt_s = clampf(dt_s, 1.0f / 500.0f, config_.max_prediction_horizon_s);

    const float warmup_scale = config_.fast_init ? 1.0f : 0.5f;
    const float bias_alpha = clampf(config_.bias_alpha * warmup_scale, 0.001f, 0.25f);
    if (frame_count_ < config_.init_frames + 2u) {
        Vec3 gyro_bias = make_vec3(gyro_bias_);
        Vec3 accel_bias = make_vec3(accel_bias_);
        gyro_bias = lerp(gyro_bias, raw_gyro, bias_alpha);
        accel_bias = lerp(accel_bias, raw_accel, bias_alpha);
        gyro_bias_ = to_array(gyro_bias);
        accel_bias_ = to_array(accel_bias);
    }

    const Vec3 corrected_gyro = sub(raw_gyro, make_vec3(gyro_bias_));
    const Vec3 corrected_accel = sub(raw_accel, make_vec3(accel_bias_));

    const Vec3 prev_position = make_vec3(filtered_position_);
    const Quat prev_rotation{
        filtered_rotation_[0], filtered_rotation_[1], filtered_rotation_[2], filtered_rotation_[3]};

    const Quat predicted_rotation = integrate_gyro(prev_rotation, corrected_gyro, dt_s);
    const Vec3 predicted_position = add(
        prev_position,
        add(
            mul(make_vec3(linear_velocity_), dt_s),
            mul(corrected_accel, 0.5f * dt_s * dt_s * 0.1f)));

    const float gyro_mag = norm(corrected_gyro);
    const float innovation_translation_m = norm(sub(raw_position, predicted_position));
    const float innovation_rotation_rad = quat_angular_distance_rad(raw_rotation, predicted_rotation);

    // Innovation gating suppresses abrupt raw-pose outliers so overlays stay spatially stable.
    constexpr float kInnovationPosSoft = 0.08f;  // 8 cm
    constexpr float kInnovationPosHard = 0.25f;  // 25 cm
    constexpr float kInnovationRotSoft = 0.20f;  // ~11.5 deg
    constexpr float kInnovationRotHard = 0.70f;  // ~40 deg

    const float pos_trust = 1.0f - clamp01(
        (innovation_translation_m - kInnovationPosSoft) /
        std::max(kEps, kInnovationPosHard - kInnovationPosSoft));
    const float rot_trust = 1.0f - clamp01(
        (innovation_rotation_rad - kInnovationRotSoft) /
        std::max(kEps, kInnovationRotHard - kInnovationRotSoft));
    const float innovation_trust = clampf(std::min(pos_trust, rot_trust), 0.0f, 1.0f);

    Vec3 fused_position;
    Quat fused_rotation;

    if (config_.use_ieskf) {
        // ── IESKF: Iterated Error-State Kalman Filter ──
        // 15-state: [δp(3), δv(3), δθ(3), δbg(3), δba(3)]

        // Step 1: Prediction — propagate nominal state
        // Position: p = p + v*dt + 0.5*R*(a-ba)*dt²
        // Velocity: v = v + R*(a-ba)*dt + g*dt
        // Rotation: R = R * Exp(ω*dt) (already done by integrate_gyro)
        // Biases: constant (random walk added via Q)

        // Get rotation matrix for current orientation
        float R3[9];
        quat_to_rot3(R3, prev_rotation.w, prev_rotation.x, prev_rotation.y, prev_rotation.z);

        // Rotate accelerometer measurement to world frame: a_w = R * (a - ba)
        float accel_body[3] = {corrected_accel.x, corrected_accel.y, corrected_accel.z};
        float accel_world[3];
        for (int i = 0; i < 3; ++i) {
            accel_world[i] = R3[i*3+0]*accel_body[0] + R3[i*3+1]*accel_body[1] + R3[i*3+2]*accel_body[2];
        }

        // Step 2: Build F (state transition Jacobian, 15×15)
        // F = I + Fₓ*dt where Fₓ is the continuous-time Jacobian
        float F[kN * kN];
        mat15_identity(F);

        // dp/dv = I*dt  (rows 0-2, cols 3-5)
        for (int i = 0; i < 3; ++i) mat15(F, i, 3+i) = dt_s;

        // dv/dθ = -R*[a-ba]× * dt  (rows 3-5, cols 6-8)
        float skew_a[9];
        skew3x3(skew_a, accel_body[0], accel_body[1], accel_body[2]);
        for (int i = 0; i < 3; ++i) {
            for (int j = 0; j < 3; ++j) {
                float val = 0.0f;
                for (int k = 0; k < 3; ++k) {
                    val -= R3[i*3+k] * skew_a[k*3+j];
                }
                mat15(F, 3+i, 6+j) = val * dt_s;
            }
        }

        // dv/dba = -R*dt  (rows 3-5, cols 12-14)
        for (int i = 0; i < 3; ++i)
            for (int j = 0; j < 3; ++j)
                mat15(F, 3+i, 12+j) = -R3[i*3+j] * dt_s;

        // dθ/dbg = -I*dt  (rows 6-8, cols 9-11)
        for (int i = 0; i < 3; ++i) mat15(F, 6+i, 9+i) = -dt_s;

        // Step 3: Build Q (process noise covariance)
        float Q[kN * kN];
        mat15_zero(Q);
        const auto& ip = config_.ieskf_params;
        float dt2 = dt_s * dt_s;
        // Position noise from acceleration
        for (int i = 0; i < 3; ++i) mat15(Q, i, i) = ip.accel_noise * dt2 * dt2 * 0.25f;
        // Velocity noise
        for (int i = 3; i < 6; ++i) mat15(Q, i, i) = ip.accel_noise * dt2;
        // Orientation noise
        for (int i = 6; i < 9; ++i) mat15(Q, i, i) = ip.gyro_noise * dt2;
        // Gyro bias random walk
        for (int i = 9; i < 12; ++i) mat15(Q, i, i) = ip.gyro_bias_noise * dt_s;
        // Accel bias random walk
        for (int i = 12; i < 15; ++i) mat15(Q, i, i) = ip.accel_bias_noise * dt_s;

        // Step 4: Propagate covariance: P = F * P * Fᵀ + Q
        float FP[kN * kN];
        mat15_mul(FP, F, P_.data());
        float Ft[kN * kN];
        mat15_transpose(Ft, F);
        float FPFt[kN * kN];
        mat15_mul(FPFt, FP, Ft);
        mat15_add(P_.data(), FPFt, Q);

        // Step 5: Iterated update (IEKF)
        // Observation: z = [position(3), rotation_error(3)]  (6-dim)
        // h(x) = [p, 0]  for error-state at linearization point
        // Innovation: y = [p_obs - p_pred, 2*quat_error_xyz]

        // Compute innovation
        float innov_pos[3] = {
            raw_position.x - predicted_position.x,
            raw_position.y - predicted_position.y,
            raw_position.z - predicted_position.z
        };

        // Rotation error: δq = q_obs * q_pred⁻¹, error ≈ 2*δq.xyz
        Quat q_pred_inv = quat_normalize(Quat{predicted_rotation.w, -predicted_rotation.x,
                                                -predicted_rotation.y, -predicted_rotation.z});
        Quat dq = quat_normalize(quat_mul(raw_rotation, q_pred_inv));
        if (dq.w < 0.0f) { dq.w = -dq.w; dq.x = -dq.x; dq.y = -dq.y; dq.z = -dq.z; }
        float innov_rot[3] = {2.0f * dq.x, 2.0f * dq.y, 2.0f * dq.z};

        // Clamp innovation by trust to harden against relocalization spikes / tracking glitches.
        const float innovation_scale = std::max(0.05f, innovation_trust);
        innov_pos[0] *= innovation_scale;
        innov_pos[1] *= innovation_scale;
        innov_pos[2] *= innovation_scale;
        innov_rot[0] *= innovation_scale;
        innov_rot[1] *= innovation_scale;
        innov_rot[2] *= innovation_scale;

        // H matrix (6×15): observation Jacobian
        // H = [I₃ 0 0 0 0]  (position observes δp)
        //     [0  0 I₃ 0 0]  (rotation observes δθ)
        // R matrix (6×6): observation noise
        float R_obs[36];
        for (int i = 0; i < 36; ++i) R_obs[i] = 0.0f;
        for (int i = 0; i < 3; ++i) R_obs[i*6+i] = ip.pos_obs_noise * ip.pos_obs_noise;
        for (int i = 3; i < 6; ++i) R_obs[i*6+i] = ip.rot_obs_noise * ip.rot_obs_noise;

        // Iterated update: refine state estimate
        float dx[kN] = {};  // cumulative error-state correction

        for (int iter = 0; iter < ip.max_iterations; ++iter) {
            // S = H * P * Hᵀ + R  (6×6)
            // With our H structure: S_ij = P[row_i][col_j] + R_ij
            // where row mapping: obs 0-2 → state 0-2, obs 3-5 → state 6-8
            int h_rows[6] = {0, 1, 2, 6, 7, 8};
            float S[36];
            for (int i = 0; i < 6; ++i) {
                for (int j = 0; j < 6; ++j) {
                    S[i*6+j] = mat15c(P_.data(), h_rows[i], h_rows[j]) + R_obs[i*6+j];
                }
            }

            // S⁻¹
            float S_inv[36];
            if (!invert6x6(S_inv, S)) {
                // Singular — fall back to EMA
                break;
            }

            // K = P * Hᵀ * S⁻¹  (15×6)
            // PHt[i][j] = P[i][h_rows[j]]
            float K[kN * 6];
            for (int i = 0; i < kN; ++i) {
                for (int j = 0; j < 6; ++j) {
                    float sum = 0.0f;
                    for (int k = 0; k < 6; ++k) {
                        sum += mat15c(P_.data(), i, h_rows[k]) * S_inv[k*6+j];
                    }
                    K[i*6+j] = sum;
                }
            }

            // Innovation vector (adjusted for current dx estimate)
            float y[6] = {innov_pos[0] - dx[0], innov_pos[1] - dx[1], innov_pos[2] - dx[2],
                          innov_rot[0] - dx[6], innov_rot[1] - dx[7], innov_rot[2] - dx[8]};

            // dx = K * y
            float new_dx[kN];
            for (int i = 0; i < kN; ++i) {
                float sum = 0.0f;
                for (int j = 0; j < 6; ++j) {
                    sum += K[i*6+j] * y[j];
                }
                new_dx[i] = sum;
            }

            // Check convergence
            float diff_sq = 0.0f;
            for (int i = 0; i < kN; ++i) diff_sq += (new_dx[i] - dx[i]) * (new_dx[i] - dx[i]);
            for (int i = 0; i < kN; ++i) dx[i] = new_dx[i];

            if (diff_sq < ip.convergence_threshold * ip.convergence_threshold) break;
        }

        // Step 6: Apply error-state correction to nominal state
        fused_position = Vec3{
            predicted_position.x + dx[0],
            predicted_position.y + dx[1],
            predicted_position.z + dx[2]
        };

        // Velocity correction
        linear_velocity_ = {{
            linear_velocity_[0] + dx[3],
            linear_velocity_[1] + dx[4],
            linear_velocity_[2] + dx[5]
        }};

        // Rotation correction: q = Exp(δθ/2) ⊗ q_pred
        float half_angle = 0.5f * std::sqrt(dx[6]*dx[6] + dx[7]*dx[7] + dx[8]*dx[8]);
        Quat dq_corr;
        if (half_angle > kEps) {
            float sinc = std::sin(half_angle) / half_angle;
            dq_corr = Quat{std::cos(half_angle), 0.5f*dx[6]*sinc, 0.5f*dx[7]*sinc, 0.5f*dx[8]*sinc};
        } else {
            dq_corr = Quat{1.0f, 0.5f*dx[6], 0.5f*dx[7], 0.5f*dx[8]};
        }
        fused_rotation = quat_normalize(quat_mul(dq_corr, predicted_rotation));

        // Bias corrections
        gyro_bias_[0] += dx[9];
        gyro_bias_[1] += dx[10];
        gyro_bias_[2] += dx[11];
        accel_bias_[0] += dx[12];
        accel_bias_[1] += dx[13];
        accel_bias_[2] += dx[14];

        // Step 7: Update covariance: P = (I - K*H) * P
        int h_rows[6] = {0, 1, 2, 6, 7, 8};
        float KH[kN * kN];
        mat15_zero(KH);

        // Recompute K for final covariance update
        float S_final[36];
        for (int i = 0; i < 6; ++i)
            for (int j = 0; j < 6; ++j)
                S_final[i*6+j] = mat15c(P_.data(), h_rows[i], h_rows[j]) +
                    R_obs[i*6+j];
        float S_inv_final[36];
        if (invert6x6(S_inv_final, S_final)) {
            float K_final[kN * 6];
            for (int i = 0; i < kN; ++i) {
                for (int j = 0; j < 6; ++j) {
                    float sum = 0.0f;
                    for (int k = 0; k < 6; ++k)
                        sum += mat15c(P_.data(), i, h_rows[k]) * S_inv_final[k*6+j];
                    K_final[i*6+j] = sum;
                }
            }
            // KH[i][j] = sum_k K[i][k] * H[k][j]  where H[k][j] = delta(h_rows[k], j)
            for (int i = 0; i < kN; ++i)
                for (int k = 0; k < 6; ++k)
                    mat15(KH, i, h_rows[k]) += K_final[i*6+k];

            // P = (I - KH) * P
            float IminusKH[kN * kN];
            mat15_identity(IminusKH);
            for (int i = 0; i < kN * kN; ++i) IminusKH[i] -= KH[i];
            float newP[kN * kN];
            mat15_mul(newP, IminusKH, P_.data());
            for (int i = 0; i < kN * kN; ++i) P_.data()[i] = newP[i];
        }

    } else {
        // ── EMA fallback (original implementation) ──
        float translation_blend = config_.translation_alpha / (1.0f + 0.75f * gyro_mag);
        float rotation_blend = config_.rotation_alpha / (1.0f + 0.55f * gyro_mag);
        translation_blend *= (0.25f + 0.75f * innovation_trust);
        rotation_blend *= (0.20f + 0.80f * innovation_trust);
        translation_blend = clampf(translation_blend, 0.05f, 0.90f);
        rotation_blend = clampf(rotation_blend, 0.04f, 0.90f);

        fused_position = lerp(predicted_position, raw_position, translation_blend);
        fused_rotation = quat_slerp(predicted_rotation, raw_rotation, rotation_blend);
    }

    if (!config_.use_ieskf) {
        // EMA mode: compute velocity from position delta
        const Vec3 velocity = mul(sub(fused_position, prev_position), 1.0f / std::max(dt_s, 1e-4f));
        linear_velocity_ = to_array(velocity);
    }
    // IESKF mode: linear_velocity_ already updated with Kalman correction
    angular_velocity_ = to_array(corrected_gyro);
    filtered_position_ = to_array(fused_position);
    filtered_rotation_ = {{fused_rotation.w, fused_rotation.x, fused_rotation.y, fused_rotation.z}};
    last_timestamp_ns_ = timestamp_ns;
    frame_count_ += 1u;

    const float jitter = norm(sub(raw_position, fused_position));
    const float accel_mag = norm(corrected_accel);
    const float accel_penalty = clampf(std::fabs(accel_mag - 9.81f) / 9.81f, 0.0f, 1.0f);
    const float motion_penalty = clampf(gyro_mag / 2.5f, 0.0f, 1.0f);
    const float jitter_penalty = clampf(jitter / 0.02f, 0.0f, 1.0f);
    const float dt_penalty = (dt_s > (1.0f / 30.0f))
        ? clampf((dt_s - (1.0f / 30.0f)) / 0.05f, 0.0f, 1.0f)
        : 0.0f;

    const float innovation_penalty = 1.0f - innovation_trust;

    float quality = 1.0f
        - (0.35f * motion_penalty)
        - (0.25f * accel_penalty)
        - (0.25f * jitter_penalty)
        - (0.15f * dt_penalty)
        - (0.18f * innovation_penalty);
    quality = clamp01(quality);

    if (frame_count_ <= config_.init_frames) {
        const float progress = static_cast<float>(frame_count_) / static_cast<float>(config_.init_frames);
        quality *= (0.45f + 0.55f * clamp01(progress));
    }

    pose_quality_ = quality;
    compose_pose_quat(fused_rotation, fused_position, out_stabilized_pose_16);
    *out_pose_quality = pose_quality_;
    return core::Status::kOk;
}

core::Status PoseStabilizer::predict(
    std::uint64_t target_timestamp_ns,
    float out_predicted_pose_16[16]) const {
    if (out_predicted_pose_16 == nullptr || !initialized_) {
        return core::Status::kInvalidArgument;
    }

    float dt_s = 0.0f;
    if (target_timestamp_ns > last_timestamp_ns_) {
        const std::uint64_t dt_ns = target_timestamp_ns - last_timestamp_ns_;
        dt_s = static_cast<float>(static_cast<double>(dt_ns) * 1e-9);
    }
    dt_s = clampf(dt_s, 0.0f, config_.max_prediction_horizon_s);

    const Vec3 position = add(make_vec3(filtered_position_), mul(make_vec3(linear_velocity_), dt_s));
    const Quat rotation = integrate_gyro(
        Quat{filtered_rotation_[0], filtered_rotation_[1], filtered_rotation_[2], filtered_rotation_[3]},
        make_vec3(angular_velocity_),
        dt_s);
    compose_pose_quat(rotation, position, out_predicted_pose_16);
    return core::Status::kOk;
}

}  // namespace tsdf
}  // namespace aether
