// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include <metal_stdlib>
using namespace metal;

// ═══════════════════════════════════════════════════════════════════════════
// GaussianUtility.metal — Shared utility functions for 3DGS shaders
// ═══════════════════════════════════════════════════════════════════════════
// Provides:
//   - Spherical Harmonics evaluation (L0, L1, L2)
//   - 3D Covariance computation from scale + quaternion
//   - 2D Covariance projection (EWA splatting)
//   - Bitonic sort for tiled backward pass
//   - Opacity / log-scale decoding
//   - NaN guard utilities
// ═══════════════════════════════════════════════════════════════════════════

// ---------------------------------------------------------------------------
// Spherical Harmonics Constants
// ---------------------------------------------------------------------------
// L0 (DC): 1 coefficient per channel = 3 total
// L1: 3 coefficients per channel = 9 total
// L2: 5 coefficients per channel = 15 total
// Total: 27 coefficients, stored as half[27] in PackedGaussian::sh_rest
// sh_dc provides the 3 DC coefficients separately.

constant float SH_C0   = 0.28209479177387814;    // 1 / (2*sqrt(pi))
constant float SH_C1   = 0.4886025119029199;     // sqrt(3) / (2*sqrt(pi))
constant float SH_C2_0 = 1.0925484305920792;     // sqrt(15) / (2*sqrt(pi))
constant float SH_C2_1 = 0.31539156525252005;    // sqrt(5) / (4*sqrt(pi))
constant float SH_C2_2 = 0.5462742152960396;     // sqrt(15) / (4*sqrt(pi))

// ---------------------------------------------------------------------------
// SH Evaluation: L0 (DC only)
// ---------------------------------------------------------------------------

inline float3 evaluate_sh_l0(half3 sh_dc) {
    return float3(sh_dc) * SH_C0;
}

// ---------------------------------------------------------------------------
// SH Evaluation: L0 + L1
// ---------------------------------------------------------------------------
// sh_rest layout for L1 (9 coefficients):
//   [c0_sh1, c0_sh2, c0_sh3,  c1_sh1, c1_sh2, c1_sh3,  c2_sh1, c2_sh2, c2_sh3]
// where c0=R, c1=G, c2=B and sh1/sh2/sh3 are the 3 L1 basis coefficients.

inline float3 evaluate_sh_l1(half3 sh_dc,
                              device const half* sh_rest,
                              float3 dir) {
    float3 result = float3(sh_dc) * SH_C0;

    // L1 basis: Y_{1,-1} = -y, Y_{1,0} = z, Y_{1,1} = -x
    for (int c = 0; c < 3; c++) {
        float sh1 = float(sh_rest[c * 3 + 0]);
        float sh2 = float(sh_rest[c * 3 + 1]);
        float sh3 = float(sh_rest[c * 3 + 2]);
        result[c] += SH_C1 * (-sh1 * dir.y + sh2 * dir.z - sh3 * dir.x);
    }

    return max(result, float3(0.0));
}

// Constant-address-space variant for uniform/constant buffers
inline float3 evaluate_sh_l1_const(half3 sh_dc,
                                    constant half* sh_rest,
                                    float3 dir) {
    float3 result = float3(sh_dc) * SH_C0;

    for (int c = 0; c < 3; c++) {
        float sh1 = float(sh_rest[c * 3 + 0]);
        float sh2 = float(sh_rest[c * 3 + 1]);
        float sh3 = float(sh_rest[c * 3 + 2]);
        result[c] += SH_C1 * (-sh1 * dir.y + sh2 * dir.z - sh3 * dir.x);
    }

    return max(result, float3(0.0));
}

// ---------------------------------------------------------------------------
// SH Evaluation: L0 + L1 + L2
// ---------------------------------------------------------------------------
// sh_rest layout for L1+L2 (24 coefficients):
//   [L1: 9 coefficients] [L2: 15 coefficients]
// L2 layout per channel (5 coefficients each):
//   offset 9 + c*5: [xy, yz, (2zz-xx-yy), xz, (xx-yy)]

inline float3 evaluate_sh_l2(half3 sh_dc,
                              device const half* sh_rest,
                              float3 dir) {
    // Start with L0+L1
    float3 result = float3(sh_dc) * SH_C0;

    // L1
    for (int c = 0; c < 3; c++) {
        float sh1 = float(sh_rest[c * 3 + 0]);
        float sh2 = float(sh_rest[c * 3 + 1]);
        float sh3 = float(sh_rest[c * 3 + 2]);
        result[c] += SH_C1 * (-sh1 * dir.y + sh2 * dir.z - sh3 * dir.x);
    }

    // L2 precomputed products
    float xx = dir.x * dir.x;
    float yy = dir.y * dir.y;
    float zz = dir.z * dir.z;
    float xy = dir.x * dir.y;
    float yz = dir.y * dir.z;
    float xz = dir.x * dir.z;

    for (int c = 0; c < 3; c++) {
        int offset = 9 + c * 5;
        result[c] += SH_C2_0 * float(sh_rest[offset + 0]) * xy;
        result[c] += SH_C2_0 * float(sh_rest[offset + 1]) * yz;
        result[c] += SH_C2_1 * float(sh_rest[offset + 2]) * (2.0 * zz - xx - yy);
        result[c] += SH_C2_0 * float(sh_rest[offset + 3]) * xz;
        result[c] += SH_C2_2 * float(sh_rest[offset + 4]) * (xx - yy);
    }

    return max(result, float3(0.0));
}

// Constant-address-space variant
inline float3 evaluate_sh_l2_const(half3 sh_dc,
                                    constant half* sh_rest,
                                    float3 dir) {
    float3 result = float3(sh_dc) * SH_C0;

    for (int c = 0; c < 3; c++) {
        float sh1 = float(sh_rest[c * 3 + 0]);
        float sh2 = float(sh_rest[c * 3 + 1]);
        float sh3 = float(sh_rest[c * 3 + 2]);
        result[c] += SH_C1 * (-sh1 * dir.y + sh2 * dir.z - sh3 * dir.x);
    }

    float xx = dir.x * dir.x;
    float yy = dir.y * dir.y;
    float zz = dir.z * dir.z;
    float xy = dir.x * dir.y;
    float yz = dir.y * dir.z;
    float xz = dir.x * dir.z;

    for (int c = 0; c < 3; c++) {
        int offset = 9 + c * 5;
        result[c] += SH_C2_0 * float(sh_rest[offset + 0]) * xy;
        result[c] += SH_C2_0 * float(sh_rest[offset + 1]) * yz;
        result[c] += SH_C2_1 * float(sh_rest[offset + 2]) * (2.0 * zz - xx - yy);
        result[c] += SH_C2_0 * float(sh_rest[offset + 3]) * xz;
        result[c] += SH_C2_2 * float(sh_rest[offset + 4]) * (xx - yy);
    }

    return max(result, float3(0.0));
}

// ---------------------------------------------------------------------------
// 3D Covariance from scale + quaternion
// ---------------------------------------------------------------------------
// Computes Sigma = R * S * S^T * R^T where:
//   R = rotation matrix from quaternion (r, x, y, z)
//   S = diagonal scale matrix

inline float3x3 compute_covariance_3d(float3 scale, float4 quat) {
    // Normalize quaternion
    float4 q = normalize(quat);
    float r = q.x, x = q.y, y = q.z, z = q.w;

    // Rotation matrix from quaternion
    float3x3 R = float3x3(
        float3(1.0 - 2.0*(y*y + z*z), 2.0*(x*y + r*z),     2.0*(x*z - r*y)),
        float3(2.0*(x*y - r*z),        1.0 - 2.0*(x*x + z*z), 2.0*(y*z + r*x)),
        float3(2.0*(x*z + r*y),        2.0*(y*z - r*x),     1.0 - 2.0*(x*x + y*y))
    );

    // S is diagonal, so R*S is just scaling each column of R
    float3x3 M = float3x3(
        R[0] * scale.x,
        R[1] * scale.y,
        R[2] * scale.z
    );

    // Sigma = M * M^T
    return M * transpose(M);
}

// ---------------------------------------------------------------------------
// 2D Covariance Projection (EWA Splatting)
// ---------------------------------------------------------------------------
// Projects 3D covariance into screen space using the Jacobian of the
// perspective projection.

inline float2x2 project_covariance_2d(float3x3 cov3D,
                                       float4x4 viewMatrix,
                                       float3 mean3D,
                                       float fx, float fy,
                                       float width, float height) {
    // Transform mean to view space
    float3 t = (viewMatrix * float4(mean3D, 1.0)).xyz;

    // Clamp to avoid numerical issues at screen edges
    float limx = 1.3 * fx / width;
    float limy = 1.3 * fy / height;
    t.x = clamp(t.x / t.z, -limx, limx) * t.z;
    t.y = clamp(t.y / t.z, -limy, limy) * t.z;

    // Avoid division by zero
    float tz_inv = 1.0 / max(t.z, 1e-6);
    float tz_inv2 = tz_inv * tz_inv;

    // Jacobian of the projection
    float3x3 J = float3x3(
        float3(fx * tz_inv,  0.0,             -fx * t.x * tz_inv2),
        float3(0.0,          fy * tz_inv,      -fy * t.y * tz_inv2),
        float3(0.0,          0.0,              0.0)
    );

    // Extract 3x3 rotation part of view matrix
    float3x3 W = float3x3(
        viewMatrix[0].xyz,
        viewMatrix[1].xyz,
        viewMatrix[2].xyz
    );

    // T = W * J
    float3x3 T = W * J;

    // Project: cov2D = T^T * cov3D * T  (taking 2x2 upper-left)
    float3x3 cov = transpose(T) * cov3D * T;

    // Add low-pass filter (anti-aliasing) to the diagonal
    return float2x2(
        cov[0][0] + 0.3, cov[0][1],
        cov[1][0],        cov[1][1] + 0.3
    );
}

// ---------------------------------------------------------------------------
// 2x2 Matrix Inverse
// ---------------------------------------------------------------------------

inline float2x2 inverse_2x2(float2x2 m) {
    float det = m[0][0] * m[1][1] - m[0][1] * m[1][0];
    float inv_det = 1.0 / max(abs(det), 1e-10);
    return float2x2(
         m[1][1] * inv_det, -m[0][1] * inv_det,
        -m[1][0] * inv_det,  m[0][0] * inv_det
    );
}

// ---------------------------------------------------------------------------
// 2x2 Determinant
// ---------------------------------------------------------------------------

inline float determinant_2x2(float2x2 m) {
    return m[0][0] * m[1][1] - m[0][1] * m[1][0];
}

// ---------------------------------------------------------------------------
// Bitonic Sort (for tiled backward pass)
// ---------------------------------------------------------------------------
// Sorts an array of float2 in threadgroup memory by the .y component (depth).
// Each thread handles multiple elements in a strided pattern.

inline void bitonic_sort(threadgroup float2* data,
                         uint count,
                         uint lid,
                         uint group_size) {
    for (uint k = 2; k <= count; k <<= 1) {
        for (uint j = k >> 1; j > 0; j >>= 1) {
            uint i = lid;
            while (i < count) {
                uint ixj = i ^ j;
                if (ixj > i && ixj < count) {
                    bool ascending = ((i & k) == 0);
                    if ((ascending && data[i].y > data[ixj].y) ||
                        (!ascending && data[i].y < data[ixj].y)) {
                        float2 tmp = data[i];
                        data[i] = data[ixj];
                        data[ixj] = tmp;
                    }
                }
                i += group_size;
            }
            threadgroup_barrier(mem_flags::mem_threadgroup);
        }
    }
}

// ---------------------------------------------------------------------------
// Opacity Decoding
// ---------------------------------------------------------------------------
// Decodes uint8 sigmoid-encoded opacity back to [0,1].
// Encoding: encode(o) = round(sigmoid_inverse(o) * 255 / 12 + 127.5)
// Decoding: decode(e) = sigmoid((e/255) * 12 - 6)

inline float decode_opacity(uint encoded) {
    float x = (float(encoded) / 255.0) * 12.0 - 6.0;
    return 1.0 / (1.0 + exp(-x));
}

// ---------------------------------------------------------------------------
// Log-Scale Decoding
// ---------------------------------------------------------------------------
// Decodes fp16 log-encoded scale: scale = exp(half_to_float(encoded))

inline float3 decode_log_scale(half3 log_s) {
    return exp(float3(log_s));
}

// ---------------------------------------------------------------------------
// NaN Guard
// ---------------------------------------------------------------------------
// Returns 0 if the value is NaN or Inf, otherwise returns the value.

inline float safe_float(float v) {
    return isnan(v) || isinf(v) ? 0.0 : v;
}

inline float3 safe_float3(float3 v) {
    return float3(safe_float(v.x), safe_float(v.y), safe_float(v.z));
}

// ---------------------------------------------------------------------------
// Gaussian Weight Computation
// ---------------------------------------------------------------------------
// Computes the 2D Gaussian weight: alpha * exp(-0.5 * offset^T * inv_cov * offset)

inline float compute_gaussian_alpha(float2 offset,
                                     float2x2 inv_cov2D,
                                     float opacity) {
    float power = -0.5 * (
        offset.x * (inv_cov2D[0][0] * offset.x + inv_cov2D[0][1] * offset.y) +
        offset.y * (inv_cov2D[1][0] * offset.x + inv_cov2D[1][1] * offset.y)
    );

    // Clamp to prevent overflow in exp
    power = max(power, -20.0);

    float alpha = opacity * exp(power);

    // Clamp alpha to valid range
    return clamp(alpha, 0.0, 0.999);
}

// ---------------------------------------------------------------------------
// Ellipse Bounding Box (3-sigma)
// ---------------------------------------------------------------------------
// Computes the axis-aligned bounding box of a 2D Gaussian ellipse at 3 sigma.

inline float4 compute_ellipse_bbox(float2 center,
                                    float2x2 cov2D,
                                    float sigma_multiplier) {
    // Eigenvalue-based bounding box
    float a = cov2D[0][0];
    float b = cov2D[0][1];
    float d = cov2D[1][1];

    float radius_x = sigma_multiplier * sqrt(max(a, 0.0001));
    float radius_y = sigma_multiplier * sqrt(max(d, 0.0001));

    // Account for off-diagonal covariance
    float off_diag_contrib = sigma_multiplier * sqrt(max(abs(b), 0.0));
    radius_x = max(radius_x, off_diag_contrib);
    radius_y = max(radius_y, off_diag_contrib);

    return float4(
        center.x - radius_x,
        center.y - radius_y,
        center.x + radius_x,
        center.y + radius_y
    );
}
