// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include <metal_stdlib>
using namespace metal;

// ═══════════════════════════════════════════════════════════════════════════
// GaussianBackwardTiled.metal — Tiled Compute Backward Pass (Path B)
// ═══════════════════════════════════════════════════════════════════════════
// Universal backward pass using compute shaders with tiled processing.
// Each threadgroup handles one tile of the image.
//
// Algorithm:
//   Phase 1: Gather — test all gaussians for tile intersection (AABB)
//   Phase 2: Sort   — bitonic sort by depth in threadgroup memory
//   Phase 3: Forward — alpha-composite per pixel, track transmittance
//   Phase 4: Backward — reverse-order gradient accumulation per pixel
//
// Gradient outputs are accumulated via atomic_fetch_add to a global buffer.
// ═══════════════════════════════════════════════════════════════════════════

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

constant uint MAX_TILE_GS [[function_constant(0)]];
// Default fallback if function constant not provided
constant uint MAX_GAUSSIANS_PER_TILE = 200;

constant float SH_C0_B   = 0.28209479177387814;
constant float SH_C1_B   = 0.4886025119029199;
constant float SH_C2_0_B = 1.0925484305920792;
constant float SH_C2_1_B = 0.31539156525252005;
constant float SH_C2_2_B = 0.5462742152960396;

constant float TRANSMITTANCE_THRESHOLD = 0.001;

// ---------------------------------------------------------------------------
// Struct Definitions
// ---------------------------------------------------------------------------

struct TileConstants {
    uint image_width;
    uint image_height;
    uint tile_size;
    uint max_gaussians_per_tile;
    uint total_gaussians;
    uint pad[3];
};

struct GaussianUniforms {
    float4x4 view_matrix;
    float4x4 proj_matrix;
    float4x4 view_proj_matrix;
    float3   camera_position;
    float    pad0;
    float2   viewport_size;
    float    near_plane;
    float    far_plane;
    uint     gaussian_count;
    uint     sh_order;
    uint     tile_size;
    uint     pad1;
};

struct TrainingUniforms {
    float2 gt_image_size;
    float  loss_weight_rgb;
    float  loss_weight_depth;
    float  loss_weight_normal;
    float  loss_weight_pbr;
    float  loss_weight_reg;
    uint   training_step;
    uint   total_steps;
    float  transmittance_threshold;
    float  pad[2];
};

struct PackedGaussianGPU {
    float    position[3];
    half     log_scale[3];
    half     rotation[4];
    uchar    opacity_u8;
    uchar    evidence_state;
    half     sh_dc[3];
    half     sh_rest[24];
    uchar    flags;
    uchar    padding0;
    uint     gaussian_id;
    half     hessian_diag[3];
    ushort   padding1;
};

// Per-gaussian gradient layout: 38 floats
// position(3) + scale(3) + opacity(1) + rotation(4) + sh_dc(3) + sh_rest(24)
constant uint GRAD_STRIDE = 38;
constant uint GRAD_OFF_POS = 0;
constant uint GRAD_OFF_SCALE = 3;
constant uint GRAD_OFF_OPACITY = 6;
constant uint GRAD_OFF_ROT = 7;
constant uint GRAD_OFF_SHDC = 11;
constant uint GRAD_OFF_SHREST = 14;

// Tile gaussian entry: packed as (gaussian_index, depth)
struct TileGaussianEntry {
    uint  index;
    float depth;
};

// ---------------------------------------------------------------------------
// Inline Utilities
// ---------------------------------------------------------------------------

inline float decode_opacity_bw(uint encoded) {
    float x = (float(encoded) / 255.0) * 12.0 - 6.0;
    return 1.0 / (1.0 + exp(-x));
}

inline float3 decode_log_scale_bw(half3 log_s) {
    return exp(float3(log_s));
}

inline float3 eval_sh_bw(half3 sh_dc,
                          device const half* sh_rest,
                          float3 dir,
                          uint sh_order) {
    float3 result = float3(sh_dc) * SH_C0_B;

    if (sh_order >= 1) {
        for (int c = 0; c < 3; c++) {
            float s1 = float(sh_rest[c * 3 + 0]);
            float s2 = float(sh_rest[c * 3 + 1]);
            float s3 = float(sh_rest[c * 3 + 2]);
            result[c] += SH_C1_B * (-s1 * dir.y + s2 * dir.z - s3 * dir.x);
        }
    }

    if (sh_order >= 2) {
        float xx = dir.x * dir.x, yy = dir.y * dir.y, zz = dir.z * dir.z;
        float xy = dir.x * dir.y, yz = dir.y * dir.z, xz = dir.x * dir.z;

        for (int c = 0; c < 3; c++) {
            int off = 9 + c * 5;
            result[c] += SH_C2_0_B * float(sh_rest[off + 0]) * xy;
            result[c] += SH_C2_0_B * float(sh_rest[off + 1]) * yz;
            result[c] += SH_C2_1_B * float(sh_rest[off + 2]) * (2.0 * zz - xx - yy);
            result[c] += SH_C2_0_B * float(sh_rest[off + 3]) * xz;
            result[c] += SH_C2_2_B * float(sh_rest[off + 4]) * (xx - yy);
        }
    }

    return max(result, float3(0.0));
}

inline float3x3 compute_cov3d_bw(float3 scale, float4 quat) {
    float4 q = normalize(quat);
    float r = q.x, x = q.y, y = q.z, z = q.w;

    float3x3 R = float3x3(
        float3(1.0 - 2.0*(y*y + z*z), 2.0*(x*y + r*z),     2.0*(x*z - r*y)),
        float3(2.0*(x*y - r*z),        1.0 - 2.0*(x*x + z*z), 2.0*(y*z + r*x)),
        float3(2.0*(x*z + r*y),        2.0*(y*z - r*x),     1.0 - 2.0*(x*x + y*y))
    );

    float3x3 M = float3x3(R[0] * scale.x, R[1] * scale.y, R[2] * scale.z);
    return M * transpose(M);
}

inline float2x2 project_cov2d_bw(float3x3 cov3D, float4x4 viewMat, float3 mean3D,
                                   float fx, float fy, float w, float h) {
    float3 t = (viewMat * float4(mean3D, 1.0)).xyz;

    float limx = 1.3 * fx / w;
    float limy = 1.3 * fy / h;
    t.x = clamp(t.x / t.z, -limx, limx) * t.z;
    t.y = clamp(t.y / t.z, -limy, limy) * t.z;

    float tz_inv = 1.0 / max(t.z, 1e-6);
    float tz_inv2 = tz_inv * tz_inv;

    float3x3 J = float3x3(
        float3(fx * tz_inv,  0.0,          -fx * t.x * tz_inv2),
        float3(0.0,          fy * tz_inv,   -fy * t.y * tz_inv2),
        float3(0.0,          0.0,           0.0)
    );

    float3x3 W = float3x3(viewMat[0].xyz, viewMat[1].xyz, viewMat[2].xyz);
    float3x3 T = W * J;
    float3x3 cov = transpose(T) * cov3D * T;

    return float2x2(cov[0][0] + 0.3, cov[0][1], cov[1][0], cov[1][1] + 0.3);
}

inline float2x2 inv2x2_bw(float2x2 m) {
    float det = m[0][0] * m[1][1] - m[0][1] * m[1][0];
    float inv_d = 1.0 / max(abs(det), 1e-10);
    return float2x2(m[1][1] * inv_d, -m[0][1] * inv_d,
                    -m[1][0] * inv_d, m[0][0] * inv_d);
}

// Threadgroup-memory bitonic sort by depth
inline void bitonic_sort_tg(threadgroup float2* data,
                             uint count,
                             uint lid,
                             uint group_size) {
    // Round up count to next power of 2 for bitonic sort
    uint n = 1;
    while (n < count) n <<= 1;

    for (uint k = 2; k <= n; k <<= 1) {
        for (uint j = k >> 1; j > 0; j >>= 1) {
            uint i = lid;
            while (i < n) {
                uint ixj = i ^ j;
                if (ixj > i) {
                    // Only compare valid entries
                    bool i_valid = (i < count);
                    bool ixj_valid = (ixj < count);

                    if (i_valid && ixj_valid) {
                        bool ascending = ((i & k) == 0);
                        if ((ascending && data[i].y > data[ixj].y) ||
                            (!ascending && data[i].y < data[ixj].y)) {
                            float2 tmp = data[i];
                            data[i] = data[ixj];
                            data[ixj] = tmp;
                        }
                    }
                }
                i += group_size;
            }
            threadgroup_barrier(mem_flags::mem_threadgroup);
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Main Backward Tiled Compute Kernel
// ═══════════════════════════════════════════════════════════════════════════

kernel void gaussian_backward_tiled(
    device const PackedGaussianGPU*  gaussians       [[buffer(0)]],
    constant GaussianUniforms&       uniforms        [[buffer(1)]],
    constant TrainingUniforms&       train_uniforms  [[buffer(2)]],
    constant TileConstants&          tile_consts     [[buffer(3)]],
    texture2d<float, access::read>   rendered_image  [[texture(0)]],
    texture2d<float, access::read>   gt_image        [[texture(1)]],
    device atomic_float*             gradient_buffer [[buffer(4)]],
    uint2  group_id       [[threadgroup_position_in_grid]],
    uint2  thread_in_group [[thread_position_in_threadgroup]],
    uint   lid            [[thread_index_in_threadgroup]],
    uint2  threads_per_group [[threads_per_threadgroup]]) {

    // Tile dimensions
    uint tile_w = tile_consts.tile_size;
    uint tile_h = tile_consts.tile_size;
    uint tile_x = group_id.x * tile_w;
    uint tile_y = group_id.y * tile_h;

    // Check if this tile is within image bounds
    if (tile_x >= tile_consts.image_width || tile_y >= tile_consts.image_height) {
        return;
    }

    uint group_size = threads_per_group.x * threads_per_group.y;
    uint max_gs_tile = min(tile_consts.max_gaussians_per_tile,
                           MAX_GAUSSIANS_PER_TILE);

    // ── Shared memory ──
    threadgroup float2 tile_gs[MAX_GAUSSIANS_PER_TILE];  // (index, depth)
    threadgroup atomic_uint tile_gs_count;

    // Initialize counter
    if (lid == 0) {
        atomic_store_explicit(&tile_gs_count, 0, memory_order_relaxed);
    }
    threadgroup_barrier(mem_flags::mem_threadgroup);

    // ── Phase 1: Gather — AABB intersection test ──
    float fx = uniforms.proj_matrix[0][0] * uniforms.viewport_size.x * 0.5;
    float fy = uniforms.proj_matrix[1][1] * uniforms.viewport_size.y * 0.5;

    uint total_gs = tile_consts.total_gaussians;

    for (uint gi = lid; gi < total_gs; gi += group_size) {
        device const PackedGaussianGPU& g = gaussians[gi];

        float3 pos = float3(g.position[0], g.position[1], g.position[2]);
        if (isnan(pos.x) || isnan(pos.y) || isnan(pos.z)) continue;

        // Project to screen
        float4 clip = uniforms.view_proj_matrix * float4(pos, 1.0);
        if (clip.w < uniforms.near_plane) continue;

        float2 ndc = clip.xy / clip.w;
        float2 screen = (ndc * 0.5 + 0.5) * uniforms.viewport_size;
        float depth = clip.z / clip.w;

        // Compute 2D covariance for AABB
        float3 scale = decode_log_scale_bw(half3(g.log_scale[0], g.log_scale[1], g.log_scale[2]));
        float4 quat = float4(float(g.rotation[0]), float(g.rotation[1]),
                              float(g.rotation[2]), float(g.rotation[3]));
        float3x3 cov3D = compute_cov3d_bw(scale, quat);
        float2x2 cov2D = project_cov2d_bw(cov3D, uniforms.view_matrix, pos,
                                            fx, fy,
                                            uniforms.viewport_size.x,
                                            uniforms.viewport_size.y);

        // 3-sigma bounding box
        float sigma = 3.0;
        float rx = sigma * sqrt(max(cov2D[0][0], 0.0001f));
        float ry = sigma * sqrt(max(cov2D[1][1], 0.0001f));

        // AABB overlap test with tile
        float gs_min_x = screen.x - rx;
        float gs_max_x = screen.x + rx;
        float gs_min_y = screen.y - ry;
        float gs_max_y = screen.y + ry;

        float tile_max_x = float(tile_x + tile_w);
        float tile_max_y = float(tile_y + tile_h);

        if (gs_max_x < float(tile_x) || gs_min_x > tile_max_x) continue;
        if (gs_max_y < float(tile_y) || gs_min_y > tile_max_y) continue;

        // Append to tile list (atomic)
        uint slot = atomic_fetch_add_explicit(&tile_gs_count, 1, memory_order_relaxed);
        if (slot < max_gs_tile) {
            tile_gs[slot] = float2(as_type<float>(gi), depth);
        }
    }

    threadgroup_barrier(mem_flags::mem_threadgroup);

    uint actual_count = min(atomic_load_explicit(&tile_gs_count, memory_order_relaxed),
                            max_gs_tile);

    if (actual_count == 0) return;

    // ── Phase 2: Sort by depth (front to back) ──
    bitonic_sort_tg(tile_gs, actual_count, lid, group_size);

    threadgroup_barrier(mem_flags::mem_threadgroup);

    // ── Phase 3 + 4: Per-pixel forward + backward ──
    // Each thread processes one pixel within the tile
    uint px_in_tile_x = thread_in_group.x;
    uint px_in_tile_y = thread_in_group.y;
    uint px_x = tile_x + px_in_tile_x;
    uint px_y = tile_y + px_in_tile_y;

    if (px_x >= tile_consts.image_width || px_y >= tile_consts.image_height) {
        return;
    }
    if (px_in_tile_x >= tile_w || px_in_tile_y >= tile_h) {
        return;
    }

    float2 pixel_center = float2(float(px_x) + 0.5, float(px_y) + 0.5);

    // Read ground truth pixel
    float4 gt_pixel = gt_image.read(uint2(px_x, px_y));
    float3 gt_color = gt_pixel.rgb;

    // ── Phase 3: Forward render (accumulate color, track transmittance) ──
    float3 accumulated_color = float3(0.0);
    float  T = 1.0;  // transmittance

    // Store per-gaussian alpha and color for backward pass
    // We use registers since each thread handles one pixel
    // (with MAX_GAUSSIANS_PER_TILE = 200, this may spill to stack)
    float  per_alpha[MAX_GAUSSIANS_PER_TILE];
    float3 per_color[MAX_GAUSSIANS_PER_TILE];

    uint last_contributor = 0;

    for (uint k = 0; k < actual_count; k++) {
        if (T < TRANSMITTANCE_THRESHOLD) break;

        uint gi = as_type<uint>(tile_gs[k].x);
        device const PackedGaussianGPU& g = gaussians[gi];

        float3 pos = float3(g.position[0], g.position[1], g.position[2]);
        float4 clip = uniforms.view_proj_matrix * float4(pos, 1.0);
        float2 ndc = clip.xy / clip.w;
        float2 screen = (ndc * 0.5 + 0.5) * uniforms.viewport_size;

        // Compute 2D covariance
        float3 scale = decode_log_scale_bw(half3(g.log_scale[0], g.log_scale[1], g.log_scale[2]));
        float4 quat = float4(float(g.rotation[0]), float(g.rotation[1]),
                              float(g.rotation[2]), float(g.rotation[3]));
        float3x3 cov3D = compute_cov3d_bw(scale, quat);
        float2x2 cov2D = project_cov2d_bw(cov3D, uniforms.view_matrix, pos,
                                            fx, fy,
                                            uniforms.viewport_size.x,
                                            uniforms.viewport_size.y);
        float2x2 inv_cov = inv2x2_bw(cov2D);

        // Gaussian weight
        float2 offset = pixel_center - screen;
        float power = -0.5 * (
            offset.x * (inv_cov[0][0] * offset.x + inv_cov[0][1] * offset.y) +
            offset.y * (inv_cov[1][0] * offset.x + inv_cov[1][1] * offset.y)
        );

        if (power < -4.5) {
            per_alpha[k] = 0.0;
            per_color[k] = float3(0.0);
            continue;
        }

        float opacity = decode_opacity_bw(uint(g.opacity_u8));
        float alpha = opacity * exp(max(power, -20.0));
        alpha = clamp(alpha, 0.0, 0.999);

        // SH color
        float3 view_dir = normalize(pos - uniforms.camera_position);
        float3 color = eval_sh_bw(half3(g.sh_dc[0], g.sh_dc[1], g.sh_dc[2]),
                                   (device const half*)g.sh_rest,
                                   view_dir,
                                   uniforms.sh_order);

        per_alpha[k] = alpha;
        per_color[k] = color;

        accumulated_color += T * alpha * color;
        T *= (1.0 - alpha);
        last_contributor = k + 1;
    }

    // ── Phase 4: Backward pass (reverse order) ──
    // dL/dColor_rendered = (rendered_color - gt_color)  [L1 gradient]
    float3 dL_dC = accumulated_color - gt_color;
    dL_dC *= train_uniforms.loss_weight_rgb;

    // Reverse-order gradient accumulation
    float T_back = 1.0;

    // First compute remaining_color from back to front
    // remaining_color[k] = sum_{j>k} T_j * alpha_j * color_j
    // We compute this incrementally.
    float3 accumulated_so_far = float3(0.0);

    for (uint k = 0; k < last_contributor; k++) {
        float alpha_k = per_alpha[k];
        if (alpha_k < 1e-6) {
            T_back *= (1.0 - alpha_k);
            continue;
        }

        float3 color_k = per_color[k];

        // dL/d_alpha_k = T_back * dot(color_k - remaining_after_k, dL_dC)
        // remaining_after_k = accumulated_color - accumulated_so_far - T_back * alpha_k * color_k
        float3 remaining = accumulated_color - accumulated_so_far - T_back * alpha_k * color_k;

        float dL_dalpha = T_back * dot(color_k * dL_dC, float3(1.0))
                        - dot(remaining * dL_dC, float3(1.0)) / max(1.0 - alpha_k, 1e-6);

        // dL/d_color_k = T_back * alpha_k * dL_dC
        float3 dL_dcolor = T_back * alpha_k * dL_dC;

        // Chain rule: accumulate gradients to the global buffer
        uint gi = as_type<uint>(tile_gs[k].x);

        // dL/d_opacity via chain rule through alpha
        // alpha = opacity * exp(power) so dalpha/dopacity = exp(power) = alpha/opacity
        float opacity = decode_opacity_bw(uint(gaussians[gi].opacity_u8));
        float dL_dopacity = (opacity > 1e-6) ? dL_dalpha * (alpha_k / opacity) : 0.0;

        // Atomic accumulate to global gradient buffer
        uint base = gi * GRAD_STRIDE;

        // Opacity gradient
        atomic_fetch_add_explicit(&gradient_buffer[base + GRAD_OFF_OPACITY],
                                  dL_dopacity, memory_order_relaxed);

        // SH DC gradients (dL/dcolor maps directly to SH DC at order 0)
        for (uint c = 0; c < 3; c++) {
            float dc_grad = dL_dcolor[c] * SH_C0_B;
            atomic_fetch_add_explicit(&gradient_buffer[base + GRAD_OFF_SHDC + c],
                                      dc_grad, memory_order_relaxed);
        }

        // Position gradients (from projection chain rule)
        // Simplified: dL/d_pos ≈ dL_dalpha * d_alpha/d_power * d_power/d_screen * d_screen/d_pos
        device const PackedGaussianGPU& g = gaussians[gi];
        float3 pos = float3(g.position[0], g.position[1], g.position[2]);
        float4 clip = uniforms.view_proj_matrix * float4(pos, 1.0);
        float2 ndc = clip.xy / clip.w;
        float2 screen = (ndc * 0.5 + 0.5) * uniforms.viewport_size;
        float2 offset = pixel_center - screen;

        float3 scale_v = decode_log_scale_bw(half3(g.log_scale[0], g.log_scale[1], g.log_scale[2]));
        float4 quat_v = float4(float(g.rotation[0]), float(g.rotation[1]),
                                float(g.rotation[2]), float(g.rotation[3]));
        float3x3 cov3D = compute_cov3d_bw(scale_v, quat_v);
        float2x2 cov2D = project_cov2d_bw(cov3D, uniforms.view_matrix, pos,
                                            fx, fy,
                                            uniforms.viewport_size.x,
                                            uniforms.viewport_size.y);
        float2x2 inv_cov = inv2x2_bw(cov2D);

        // d_power/d_screen = -inv_cov * offset
        float2 dp_dscreen = float2(
            -(inv_cov[0][0] * offset.x + inv_cov[0][1] * offset.y),
            -(inv_cov[1][0] * offset.x + inv_cov[1][1] * offset.y)
        );

        // d_screen/d_pos (simplified via view-proj chain)
        float3 view_pos = (uniforms.view_matrix * float4(pos, 1.0)).xyz;
        float tz = max(view_pos.z, 1e-6);
        float tz2 = tz * tz;

        // dscreen_x/dpos = fx/tz * view_row0 - fx*vx/tz^2 * view_row2
        // (simplified approximation for gradient)
        float d_alpha_d_power = alpha_k;  // d(alpha)/d(power) = alpha (from exp)
        float pos_grad_scale = dL_dalpha * d_alpha_d_power;

        float3 view_row0 = uniforms.view_matrix[0].xyz;
        float3 view_row1 = uniforms.view_matrix[1].xyz;
        float3 view_row2 = uniforms.view_matrix[2].xyz;

        float3 dL_dpos = pos_grad_scale * (
            dp_dscreen.x * (fx / tz * view_row0 - fx * view_pos.x / tz2 * view_row2) +
            dp_dscreen.y * (fy / tz * view_row1 - fy * view_pos.y / tz2 * view_row2)
        ) * (uniforms.viewport_size.x * 0.5);

        // NaN guard
        if (!isnan(dL_dpos.x) && !isinf(dL_dpos.x)) {
            atomic_fetch_add_explicit(&gradient_buffer[base + GRAD_OFF_POS + 0],
                                      dL_dpos.x, memory_order_relaxed);
        }
        if (!isnan(dL_dpos.y) && !isinf(dL_dpos.y)) {
            atomic_fetch_add_explicit(&gradient_buffer[base + GRAD_OFF_POS + 1],
                                      dL_dpos.y, memory_order_relaxed);
        }
        if (!isnan(dL_dpos.z) && !isinf(dL_dpos.z)) {
            atomic_fetch_add_explicit(&gradient_buffer[base + GRAD_OFF_POS + 2],
                                      dL_dpos.z, memory_order_relaxed);
        }

        // Scale gradients (from covariance chain rule, simplified)
        // dL/d_scale ≈ dL/d_alpha * d_alpha/d_cov2D * d_cov2D/d_cov3D * d_cov3D/d_scale
        // Simplified: proportional to position gradient magnitude
        float scale_grad_mag = length(dL_dpos) * 0.1;
        for (uint s = 0; s < 3; s++) {
            float sg = scale_grad_mag * scale_v[s];
            if (!isnan(sg) && !isinf(sg)) {
                atomic_fetch_add_explicit(&gradient_buffer[base + GRAD_OFF_SCALE + s],
                                          sg, memory_order_relaxed);
            }
        }

        accumulated_so_far += T_back * alpha_k * color_k;
        T_back *= (1.0 - alpha_k);
    }
}
