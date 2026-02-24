// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include <metal_stdlib>
using namespace metal;

// ═══════════════════════════════════════════════════════════════════════════
// GaussianInit.metal — TSDF-to-Gaussian GPU Initialization Kernel
// ═══════════════════════════════════════════════════════════════════════════
// Converts a buffer of TSDFVoxelSeed structs into PackedGaussian format
// on the GPU, avoiding the CPU round-trip for large voxel grids.
//
// Per-voxel operations:
//   1. Compute SDF gradient from 6 neighbors -> surface normal
//   2. Compute tangent basis via Gram-Schmidt orthogonalization
//   3. Set anisotropic scale: tangent = voxel_size, normal = voxel_size/4
//   4. Encode to PackedGaussian (log-scale fp16, sigmoid opacity, etc.)
//   5. Set SH DC from RGB color
// ═══════════════════════════════════════════════════════════════════════════

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

constant float INV_SQRT_4PI = 0.28209479177387814;  // 1 / (2 * sqrt(pi))
constant float EPSILON_INIT = 1e-7;

// ---------------------------------------------------------------------------
// Input Struct: TSDFVoxelSeed
// ---------------------------------------------------------------------------
// Must match the C++ TSDFVoxelSeed layout.

struct TSDFVoxelSeed {
    float3   position;        // 12B world position
    float3   normal;          // 12B surface normal (may be zero)
    float    sdf_value;       //  4B signed distance
    float    confidence;      //  4B voxel confidence [0,1+]
    float    voxel_size;      //  4B grid spacing
    uchar    rgb[3];          //  3B sRGB color
    uchar    pad;             //  1B alignment
};

// ---------------------------------------------------------------------------
// Output Struct: PackedGaussianGPU (96 bytes)
// ---------------------------------------------------------------------------

struct PackedGaussianGPU {
    float    position[3];       //  12B
    half     log_scale[3];      //   6B
    half     rotation[4];       //   8B
    uchar    opacity_u8;        //   1B
    uchar    evidence_state;    //   1B
    half     sh_dc[3];          //   6B
    half     sh_rest[24];       //  48B
    uchar    flags;             //   1B
    uchar    padding0;          //   1B
    uint     gaussian_id;       //   4B
    half     hessian_diag[3];   //   6B
    ushort   padding1;          //   2B
};

// ---------------------------------------------------------------------------
// Config Constants (passed via constant buffer)
// ---------------------------------------------------------------------------

struct InitConfig {
    float truncation_threshold;     // |SDF| must be below this
    float min_confidence;           // confidence threshold
    float opacity_scale;            // confidence-to-opacity multiplier
    float opacity_min;              // clamp lower bound
    float opacity_max;              // clamp upper bound
    float normal_scale_ratio;       // normal direction scale = voxel_size * ratio
    uint  base_gaussian_id;         // starting gaussian ID
    uint  seed_count;               // total number of input seeds
};

// ---------------------------------------------------------------------------
// SDF Gradient Struct (for optional 6-neighbor input)
// ---------------------------------------------------------------------------

struct SDFNeighborhood {
    float sdf_plus_x;
    float sdf_minus_x;
    float sdf_plus_y;
    float sdf_minus_y;
    float sdf_plus_z;
    float sdf_minus_z;
};

// ---------------------------------------------------------------------------
// Utility: Sigmoid Inverse (Logit)
// ---------------------------------------------------------------------------

inline float sigmoid_inverse(float x) {
    x = clamp(x, EPSILON_INIT, 1.0 - EPSILON_INIT);
    return log(x / (1.0 - x));
}

// ---------------------------------------------------------------------------
// Utility: Encode opacity to uint8 via sigmoid
// ---------------------------------------------------------------------------
// Encoding: sigmoid_inv(opacity) mapped to [0, 255]
// Range: sigmoid_inv maps (0,1) to (-inf, +inf)
// We use the convention: encoded = round((sigmoid_inv(o) + 6) / 12 * 255)

inline uchar encode_opacity(float opacity) {
    float logit = sigmoid_inverse(clamp(opacity, 0.01, 0.99));
    float encoded = (logit + 6.0) / 12.0 * 255.0;
    return uchar(clamp(encoded, 0.0, 255.0));
}

// ---------------------------------------------------------------------------
// Utility: Encode scale as fp16 of log(scale)
// ---------------------------------------------------------------------------

inline half encode_log_scale(float scale) {
    scale = max(scale, EPSILON_INIT);
    return half(log(scale));
}

// ---------------------------------------------------------------------------
// Utility: Gram-Schmidt orthogonalization for tangent basis
// ---------------------------------------------------------------------------

inline void compute_tangent_basis(float3 normal,
                                   thread float3& tangent1,
                                   thread float3& tangent2) {
    // Choose a reference vector not parallel to normal
    float3 up = float3(0.0, 1.0, 0.0);
    if (abs(dot(normal, up)) > 0.99) {
        up = float3(1.0, 0.0, 0.0);
    }

    // tangent1 = normalize(up - dot(up, normal) * normal)
    float d = dot(up, normal);
    tangent1 = up - d * normal;
    float t_len = length(tangent1);
    if (t_len > EPSILON_INIT) {
        tangent1 /= t_len;
    } else {
        tangent1 = float3(1.0, 0.0, 0.0);
    }

    // tangent2 = cross(normal, tangent1)
    tangent2 = cross(normal, tangent1);
    float t2_len = length(tangent2);
    if (t2_len > EPSILON_INIT) {
        tangent2 /= t2_len;
    }
}

// ---------------------------------------------------------------------------
// Utility: Quaternion from rotation matrix
// ---------------------------------------------------------------------------
// Converts a rotation matrix (tangent1, tangent2, normal) basis to quaternion.

inline float4 quaternion_from_basis(float3 t1, float3 t2, float3 n) {
    // Rotation matrix: columns are t1, t2, n
    // R = [t1.x t2.x n.x]
    //     [t1.y t2.y n.y]
    //     [t1.z t2.z n.z]

    float trace = t1.x + t2.y + n.z;
    float4 q;

    if (trace > 0.0) {
        float s = 0.5 / sqrt(trace + 1.0);
        q.x = 0.25 / s;                  // w
        q.y = (t2.z - n.y) * s;          // x
        q.z = (n.x - t1.z) * s;          // y
        q.w = (t1.y - t2.x) * s;         // z
    } else if (t1.x > t2.y && t1.x > n.z) {
        float s = 2.0 * sqrt(1.0 + t1.x - t2.y - n.z);
        q.x = (t2.z - n.y) / s;
        q.y = 0.25 * s;
        q.z = (t2.x + t1.y) / s;
        q.w = (n.x + t1.z) / s;
    } else if (t2.y > n.z) {
        float s = 2.0 * sqrt(1.0 + t2.y - t1.x - n.z);
        q.x = (n.x - t1.z) / s;
        q.y = (t2.x + t1.y) / s;
        q.z = 0.25 * s;
        q.w = (n.y + t2.z) / s;
    } else {
        float s = 2.0 * sqrt(1.0 + n.z - t1.x - t2.y);
        q.x = (t1.y - t2.x) / s;
        q.y = (n.x + t1.z) / s;
        q.z = (n.y + t2.z) / s;
        q.w = 0.25 * s;
    }

    return normalize(q);
}

// ═══════════════════════════════════════════════════════════════════════════
// Main Initialization Kernel
// ═══════════════════════════════════════════════════════════════════════════

kernel void gaussian_init(
    device const TSDFVoxelSeed*   seeds       [[buffer(0)]],
    device PackedGaussianGPU*     gaussians   [[buffer(1)]],
    constant InitConfig&          config      [[buffer(2)]],
    device atomic_uint*           output_count [[buffer(3)]],
    uint tid [[thread_position_in_grid]]) {

    if (tid >= config.seed_count) return;

    device const TSDFVoxelSeed& seed = seeds[tid];

    // ── Filter: |SDF| must be below truncation threshold ──
    if (abs(seed.sdf_value) >= config.truncation_threshold) return;

    // ── Filter: confidence must exceed minimum ──
    if (seed.confidence < config.min_confidence) return;

    // ── Compute surface normal ──
    float3 normal = seed.normal;
    float normal_len = length(normal);

    if (normal_len < EPSILON_INIT) {
        // Degenerate normal — skip this voxel
        return;
    }
    normal /= normal_len;

    // ── Allocate output slot (atomic) ──
    uint slot = atomic_fetch_add_explicit(output_count, 1, memory_order_relaxed);

    // ── Compute tangent basis ──
    float3 tangent1, tangent2;
    compute_tangent_basis(normal, tangent1, tangent2);

    // ── Compute anisotropic scale ──
    float tangent_scale = seed.voxel_size;
    float normal_scale = seed.voxel_size * config.normal_scale_ratio;

    // ── Compute quaternion from basis ──
    float4 quat = quaternion_from_basis(tangent1, tangent2, normal);

    // ── Compute opacity ──
    float opacity = clamp(seed.confidence * config.opacity_scale,
                          config.opacity_min,
                          config.opacity_max);

    // ── Compute SH DC from RGB ──
    float3 sh_dc = float3(
        float(seed.rgb[0]) / 255.0 * INV_SQRT_4PI,
        float(seed.rgb[1]) / 255.0 * INV_SQRT_4PI,
        float(seed.rgb[2]) / 255.0 * INV_SQRT_4PI
    );

    // ── Write output PackedGaussian ──
    device PackedGaussianGPU& out = gaussians[slot];

    // Position
    out.position[0] = seed.position.x;
    out.position[1] = seed.position.y;
    out.position[2] = seed.position.z;

    // Log-scale (fp16)
    out.log_scale[0] = encode_log_scale(tangent_scale);
    out.log_scale[1] = encode_log_scale(tangent_scale);
    out.log_scale[2] = encode_log_scale(normal_scale);

    // Rotation quaternion (fp16)
    out.rotation[0] = half(quat.x);
    out.rotation[1] = half(quat.y);
    out.rotation[2] = half(quat.z);
    out.rotation[3] = half(quat.w);

    // Opacity (uint8 sigmoid-encoded)
    out.opacity_u8 = encode_opacity(opacity);

    // Evidence state: S0 (kBlack = 0)
    out.evidence_state = 0;

    // SH DC (fp16)
    out.sh_dc[0] = half(sh_dc.x);
    out.sh_dc[1] = half(sh_dc.y);
    out.sh_dc[2] = half(sh_dc.z);

    // SH rest: zero-initialize
    for (int i = 0; i < 24; i++) {
        out.sh_rest[i] = half(0.0);
    }

    // Flags: not dynamic, not frozen
    out.flags = 0;
    out.padding0 = 0;

    // Gaussian ID
    out.gaussian_id = config.base_gaussian_id + slot;

    // Hessian diagonal: zero
    out.hessian_diag[0] = half(0.0);
    out.hessian_diag[1] = half(0.0);
    out.hessian_diag[2] = half(0.0);

    out.padding1 = 0;
}

// ═══════════════════════════════════════════════════════════════════════════
// Variant: Initialize from SDF neighborhoods (with gradient computation)
// ═══════════════════════════════════════════════════════════════════════════
// When normals are not precomputed, this kernel computes them from 6 SDF
// neighbors using central differences.

kernel void gaussian_init_with_gradient(
    device const TSDFVoxelSeed*     seeds        [[buffer(0)]],
    device const SDFNeighborhood*   neighborhoods [[buffer(1)]],
    device PackedGaussianGPU*       gaussians    [[buffer(2)]],
    constant InitConfig&            config       [[buffer(3)]],
    device atomic_uint*             output_count [[buffer(4)]],
    uint tid [[thread_position_in_grid]]) {

    if (tid >= config.seed_count) return;

    device const TSDFVoxelSeed& seed = seeds[tid];

    if (abs(seed.sdf_value) >= config.truncation_threshold) return;
    if (seed.confidence < config.min_confidence) return;

    // Compute normal from SDF gradient (central differences)
    device const SDFNeighborhood& nb = neighborhoods[tid];
    float3 gradient = float3(
        (nb.sdf_plus_x - nb.sdf_minus_x) * 0.5,
        (nb.sdf_plus_y - nb.sdf_minus_y) * 0.5,
        (nb.sdf_plus_z - nb.sdf_minus_z) * 0.5
    );

    float grad_len = length(gradient);
    if (grad_len < EPSILON_INIT) return;

    float3 normal = gradient / grad_len;

    // Allocate output slot
    uint slot = atomic_fetch_add_explicit(output_count, 1, memory_order_relaxed);

    // Tangent basis
    float3 tangent1, tangent2;
    compute_tangent_basis(normal, tangent1, tangent2);

    // Scale
    float tangent_scale = seed.voxel_size;
    float normal_scale = seed.voxel_size * config.normal_scale_ratio;

    // Quaternion
    float4 quat = quaternion_from_basis(tangent1, tangent2, normal);

    // Opacity
    float opacity = clamp(seed.confidence * config.opacity_scale,
                          config.opacity_min, config.opacity_max);

    // SH DC
    float3 sh_dc = float3(
        float(seed.rgb[0]) / 255.0 * INV_SQRT_4PI,
        float(seed.rgb[1]) / 255.0 * INV_SQRT_4PI,
        float(seed.rgb[2]) / 255.0 * INV_SQRT_4PI
    );

    // Write output
    device PackedGaussianGPU& out = gaussians[slot];
    out.position[0] = seed.position.x;
    out.position[1] = seed.position.y;
    out.position[2] = seed.position.z;

    out.log_scale[0] = encode_log_scale(tangent_scale);
    out.log_scale[1] = encode_log_scale(tangent_scale);
    out.log_scale[2] = encode_log_scale(normal_scale);

    out.rotation[0] = half(quat.x);
    out.rotation[1] = half(quat.y);
    out.rotation[2] = half(quat.z);
    out.rotation[3] = half(quat.w);

    out.opacity_u8 = encode_opacity(opacity);
    out.evidence_state = 0;

    out.sh_dc[0] = half(sh_dc.x);
    out.sh_dc[1] = half(sh_dc.y);
    out.sh_dc[2] = half(sh_dc.z);

    for (int i = 0; i < 24; i++) out.sh_rest[i] = half(0.0);

    out.flags = 0;
    out.padding0 = 0;
    out.gaussian_id = config.base_gaussian_id + slot;
    out.hessian_diag[0] = half(0.0);
    out.hessian_diag[1] = half(0.0);
    out.hessian_diag[2] = half(0.0);
    out.padding1 = 0;
}
