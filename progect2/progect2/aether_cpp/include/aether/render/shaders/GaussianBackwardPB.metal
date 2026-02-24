// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include <metal_stdlib>
using namespace metal;

// ═══════════════════════════════════════════════════════════════════════════
// GaussianBackwardPB.metal — Fragment Shader Backward Pass (Path A)
//                            Using Programmable Blending
// ═══════════════════════════════════════════════════════════════════════════
// Metal-only backward pass that uses raster_order_group(0) for fragment-
// level gradient accumulation. Simpler than the tiled compute path but
// less efficient for large gaussian counts.
//
// Each fragment:
//   1. Reads current framebuffer color via programmable blending
//   2. Computes the gaussian's alpha and SH color
//   3. Computes gradient of loss w.r.t. this gaussian's contribution
//   4. Writes gradients to a buffer indexed by gaussian_id
// ═══════════════════════════════════════════════════════════════════════════

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

constant float SH_C0_PB   = 0.28209479177387814;
constant float SH_C1_PB   = 0.4886025119029199;
constant float SH_C2_0_PB = 1.0925484305920792;
constant float SH_C2_1_PB = 0.31539156525252005;
constant float SH_C2_2_PB = 0.5462742152960396;

// ---------------------------------------------------------------------------
// Struct Definitions
// ---------------------------------------------------------------------------

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

// Gradient layout (38 floats per gaussian)
constant uint GRAD_STRIDE_PB = 38;
constant uint GRAD_OFF_POS_PB = 0;
constant uint GRAD_OFF_SCALE_PB = 3;
constant uint GRAD_OFF_OPACITY_PB = 6;
constant uint GRAD_OFF_ROT_PB = 7;
constant uint GRAD_OFF_SHDC_PB = 11;
constant uint GRAD_OFF_SHREST_PB = 14;

// ---------------------------------------------------------------------------
// Inline Utilities
// ---------------------------------------------------------------------------

inline float decode_opacity_pb(uint encoded) {
    float x = (float(encoded) / 255.0) * 12.0 - 6.0;
    return 1.0 / (1.0 + exp(-x));
}

inline float3 decode_log_scale_pb(half3 log_s) {
    return exp(float3(log_s));
}

inline float3 eval_sh_pb(half3 sh_dc,
                          device const half* sh_rest,
                          float3 dir,
                          uint sh_order) {
    float3 result = float3(sh_dc) * SH_C0_PB;

    if (sh_order >= 1) {
        for (int c = 0; c < 3; c++) {
            result[c] += SH_C1_PB * (
                -float(sh_rest[c*3 + 0]) * dir.y +
                 float(sh_rest[c*3 + 1]) * dir.z -
                 float(sh_rest[c*3 + 2]) * dir.x);
        }
    }

    if (sh_order >= 2) {
        float xx = dir.x * dir.x, yy = dir.y * dir.y, zz = dir.z * dir.z;
        float xy = dir.x * dir.y, yz = dir.y * dir.z, xz = dir.x * dir.z;

        for (int c = 0; c < 3; c++) {
            int off = 9 + c * 5;
            result[c] += SH_C2_0_PB * float(sh_rest[off+0]) * xy;
            result[c] += SH_C2_0_PB * float(sh_rest[off+1]) * yz;
            result[c] += SH_C2_1_PB * float(sh_rest[off+2]) * (2.0*zz - xx - yy);
            result[c] += SH_C2_0_PB * float(sh_rest[off+3]) * xz;
            result[c] += SH_C2_2_PB * float(sh_rest[off+4]) * (xx - yy);
        }
    }

    return max(result, float3(0.0));
}

inline float3x3 compute_cov3d_pb(float3 scale, float4 quat) {
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

inline float2x2 project_cov2d_pb(float3x3 cov3D, float4x4 viewMat, float3 mean3D,
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

inline float2x2 inv2x2_pb(float2x2 m) {
    float det = m[0][0] * m[1][1] - m[0][1] * m[1][0];
    float inv_d = 1.0 / max(abs(det), 1e-10);
    return float2x2(m[1][1] * inv_d, -m[0][1] * inv_d,
                    -m[1][0] * inv_d, m[0][0] * inv_d);
}

// ═══════════════════════════════════════════════════════════════════════════
// Vertex Shader (same as forward pass)
// ═══════════════════════════════════════════════════════════════════════════

struct PBVertexOut {
    float4   position    [[position]];
    float2   offset;
    float3   color;
    float    opacity;
    float2x2 inv_cov2D;
    uint     gaussian_idx;
    float3   world_pos;
    float3   view_dir;
    float    alpha_weight;  // precomputed opacity for gradient
};

vertex PBVertexOut gaussian_backward_pb_vertex(
    uint vertex_id     [[vertex_id]],
    uint instance_id   [[instance_id]],
    constant GaussianUniforms& uniforms [[buffer(0)]],
    device const PackedGaussianGPU* gaussians [[buffer(1)]]) {

    PBVertexOut out;

    if (instance_id >= uniforms.gaussian_count) {
        out.position = float4(0, 0, -2, 1);
        return out;
    }

    device const PackedGaussianGPU& g = gaussians[instance_id];
    float3 pos = float3(g.position[0], g.position[1], g.position[2]);

    if (isnan(pos.x) || isnan(pos.y) || isnan(pos.z)) {
        out.position = float4(0, 0, -2, 1);
        return out;
    }

    float4 clip_pos = uniforms.view_proj_matrix * float4(pos, 1.0);
    if (clip_pos.w < uniforms.near_plane) {
        out.position = float4(0, 0, -2, 1);
        return out;
    }

    float2 ndc = clip_pos.xy / clip_pos.w;
    if (abs(ndc.x) > 1.5 || abs(ndc.y) > 1.5) {
        out.position = float4(0, 0, -2, 1);
        return out;
    }

    float3 scale = decode_log_scale_pb(half3(g.log_scale[0], g.log_scale[1], g.log_scale[2]));
    float4 quat = float4(float(g.rotation[0]), float(g.rotation[1]),
                          float(g.rotation[2]), float(g.rotation[3]));

    float3x3 cov3D = compute_cov3d_pb(scale, quat);
    float fx = uniforms.proj_matrix[0][0] * uniforms.viewport_size.x * 0.5;
    float fy = uniforms.proj_matrix[1][1] * uniforms.viewport_size.y * 0.5;

    float2x2 cov2D = project_cov2d_pb(cov3D, uniforms.view_matrix, pos,
                                        fx, fy,
                                        uniforms.viewport_size.x,
                                        uniforms.viewport_size.y);

    float a = cov2D[0][0], d = cov2D[1][1];
    float det = a * d - cov2D[0][1] * cov2D[0][1];
    if (det < 1e-10) {
        out.position = float4(0, 0, -2, 1);
        return out;
    }

    float sigma = 3.0;
    float rx = sigma * sqrt(max(a, 0.0001));
    float ry = sigma * sqrt(max(d, 0.0001));

    float2 corners[4] = { float2(-1,-1), float2(1,-1), float2(-1,1), float2(1,1) };
    float2 corner = corners[vertex_id % 4];
    float2 pixel_offset = float2(corner.x * rx, corner.y * ry);

    float2 screen_center = (ndc * 0.5 + 0.5) * uniforms.viewport_size;
    float2 screen_pos = screen_center + pixel_offset;
    float2 final_ndc = (screen_pos / uniforms.viewport_size) * 2.0 - 1.0;

    out.position = float4(final_ndc.x, final_ndc.y, clip_pos.z / clip_pos.w, 1.0);
    out.offset = pixel_offset;
    out.inv_cov2D = inv2x2_pb(cov2D);
    out.opacity = decode_opacity_pb(uint(g.opacity_u8));

    float3 view_dir = normalize(pos - uniforms.camera_position);
    out.color = max(eval_sh_pb(half3(g.sh_dc[0], g.sh_dc[1], g.sh_dc[2]),
                                (device const half*)g.sh_rest,
                                view_dir,
                                uniforms.sh_order),
                    float3(0.0));
    out.view_dir = view_dir;
    out.world_pos = pos;
    out.gaussian_idx = instance_id;

    return out;
}

// ═══════════════════════════════════════════════════════════════════════════
// Fragment Shader with Programmable Blending
// ═══════════════════════════════════════════════════════════════════════════
// Uses [[raster_order_group(0)]] to read the framebuffer state at this pixel.
// The framebuffer stores:
//   .rgb = accumulated color (premultiplied alpha)
//   .a   = accumulated transmittance (1 - total opacity so far)

struct PBFragmentOut {
    float4 color [[color(0), raster_order_group(0)]];
};

fragment PBFragmentOut gaussian_backward_pb_fragment(
    PBVertexOut in [[stage_in]],
    float4 fb_color [[color(0), raster_order_group(0)]],
    constant GaussianUniforms& uniforms [[buffer(0)]],
    constant TrainingUniforms& train_uniforms [[buffer(1)]],
    device const PackedGaussianGPU* gaussians [[buffer(2)]],
    texture2d<float, access::read> gt_image [[texture(0)]],
    device atomic_float* gradient_buffer [[buffer(3)]]) {

    // Compute 2D Gaussian weight
    float2 offset = in.offset;
    float power = -0.5 * (
        offset.x * (in.inv_cov2D[0][0] * offset.x + in.inv_cov2D[0][1] * offset.y) +
        offset.y * (in.inv_cov2D[1][0] * offset.x + in.inv_cov2D[1][1] * offset.y)
    );

    if (power < -4.5) {
        discard_fragment();
    }

    float alpha = in.opacity * exp(max(power, -20.0));
    alpha = clamp(alpha, 0.0, 0.999);

    if (alpha < 0.002) {
        discard_fragment();
    }

    // Read current framebuffer state
    float3 accumulated_rgb = fb_color.rgb;
    float  transmittance   = fb_color.a;  // remaining transmittance

    // Early termination
    if (transmittance < 0.001) {
        PBFragmentOut out;
        out.color = fb_color;  // pass through unchanged
        return out;
    }

    // This gaussian's contribution
    float3 color_k = in.color;
    float3 contribution = transmittance * alpha * color_k;

    // Read ground truth for this pixel
    uint2 px = uint2(in.position.xy);
    if (px.x < uint(train_uniforms.gt_image_size.x) &&
        px.y < uint(train_uniforms.gt_image_size.y)) {

        float3 gt_color = gt_image.read(px).rgb;

        // dL/d_contribution = loss_weight * sign(accumulated + contribution - gt)
        // (L1 gradient)
        float3 final_color = accumulated_rgb + contribution;
        float3 dL_dC = train_uniforms.loss_weight_rgb * (final_color - gt_color);

        // dL/d_alpha = transmittance * dot(color_k, dL_dC)
        float dL_dalpha = transmittance * dot(color_k, dL_dC);

        // dL/d_color = transmittance * alpha * dL_dC
        float3 dL_dcolor = transmittance * alpha * dL_dC;

        // dL/d_opacity = dL_dalpha * alpha / opacity
        float dL_dopacity = (in.opacity > 1e-6) ?
            dL_dalpha * (alpha / in.opacity) : 0.0;

        // Write gradients to buffer via atomics
        uint base = in.gaussian_idx * GRAD_STRIDE_PB;

        // Opacity
        if (!isnan(dL_dopacity) && !isinf(dL_dopacity)) {
            atomic_fetch_add_explicit(&gradient_buffer[base + GRAD_OFF_OPACITY_PB],
                                      dL_dopacity, memory_order_relaxed);
        }

        // SH DC (dL/dcolor maps to SH DC through C0 coefficient)
        for (uint c = 0; c < 3; c++) {
            float dc_grad = dL_dcolor[c] * SH_C0_PB;
            if (!isnan(dc_grad) && !isinf(dc_grad)) {
                atomic_fetch_add_explicit(&gradient_buffer[base + GRAD_OFF_SHDC_PB + c],
                                          dc_grad, memory_order_relaxed);
            }
        }

        // SH L1 gradients (if sh_order >= 1)
        if (uniforms.sh_order >= 1) {
            float3 dir = in.view_dir;
            for (uint c = 0; c < 3; c++) {
                float dL_dc = dL_dcolor[c];
                // dcolor/d_sh1 = SH_C1 * (-y), dcolor/d_sh2 = SH_C1 * z, etc.
                float g1 = dL_dc * SH_C1_PB * (-dir.y);
                float g2 = dL_dc * SH_C1_PB * dir.z;
                float g3 = dL_dc * SH_C1_PB * (-dir.x);

                if (!isnan(g1) && !isinf(g1))
                    atomic_fetch_add_explicit(&gradient_buffer[base + GRAD_OFF_SHREST_PB + c*3 + 0],
                                              g1, memory_order_relaxed);
                if (!isnan(g2) && !isinf(g2))
                    atomic_fetch_add_explicit(&gradient_buffer[base + GRAD_OFF_SHREST_PB + c*3 + 1],
                                              g2, memory_order_relaxed);
                if (!isnan(g3) && !isinf(g3))
                    atomic_fetch_add_explicit(&gradient_buffer[base + GRAD_OFF_SHREST_PB + c*3 + 2],
                                              g3, memory_order_relaxed);
            }
        }

        // SH L2 gradients (if sh_order >= 2)
        if (uniforms.sh_order >= 2) {
            float3 dir = in.view_dir;
            float xx = dir.x*dir.x, yy = dir.y*dir.y, zz = dir.z*dir.z;
            float xy = dir.x*dir.y, yz = dir.y*dir.z, xz = dir.x*dir.z;

            for (uint c = 0; c < 3; c++) {
                float dL_dc = dL_dcolor[c];
                uint sh_off = GRAD_OFF_SHREST_PB + 9 + c * 5;

                float g0 = dL_dc * SH_C2_0_PB * xy;
                float g1 = dL_dc * SH_C2_0_PB * yz;
                float g2 = dL_dc * SH_C2_1_PB * (2.0*zz - xx - yy);
                float g3 = dL_dc * SH_C2_0_PB * xz;
                float g4 = dL_dc * SH_C2_2_PB * (xx - yy);

                if (!isnan(g0) && !isinf(g0))
                    atomic_fetch_add_explicit(&gradient_buffer[base + sh_off + 0],
                                              g0, memory_order_relaxed);
                if (!isnan(g1) && !isinf(g1))
                    atomic_fetch_add_explicit(&gradient_buffer[base + sh_off + 1],
                                              g1, memory_order_relaxed);
                if (!isnan(g2) && !isinf(g2))
                    atomic_fetch_add_explicit(&gradient_buffer[base + sh_off + 2],
                                              g2, memory_order_relaxed);
                if (!isnan(g3) && !isinf(g3))
                    atomic_fetch_add_explicit(&gradient_buffer[base + sh_off + 3],
                                              g3, memory_order_relaxed);
                if (!isnan(g4) && !isinf(g4))
                    atomic_fetch_add_explicit(&gradient_buffer[base + sh_off + 4],
                                              g4, memory_order_relaxed);
            }
        }

        // Position gradients via projection chain rule
        float3 pos = in.world_pos;
        float3 view_pos = (uniforms.view_matrix * float4(pos, 1.0)).xyz;
        float tz = max(view_pos.z, 1e-6);
        float2x2 inv_cov = in.inv_cov2D;

        float2 dp_dscreen = float2(
            -(inv_cov[0][0] * offset.x + inv_cov[0][1] * offset.y),
            -(inv_cov[1][0] * offset.x + inv_cov[1][1] * offset.y)
        );

        float fxp = uniforms.proj_matrix[0][0] * uniforms.viewport_size.x * 0.5;
        float fyp = uniforms.proj_matrix[1][1] * uniforms.viewport_size.y * 0.5;

        float d_alpha_d_power = alpha;
        float pos_grad_scale = dL_dalpha * d_alpha_d_power;

        float3 vr0 = uniforms.view_matrix[0].xyz;
        float3 vr1 = uniforms.view_matrix[1].xyz;
        float3 vr2 = uniforms.view_matrix[2].xyz;

        float tz2 = tz * tz;
        float3 dL_dpos = pos_grad_scale * (
            dp_dscreen.x * (fxp / tz * vr0 - fxp * view_pos.x / tz2 * vr2) +
            dp_dscreen.y * (fyp / tz * vr1 - fyp * view_pos.y / tz2 * vr2)
        ) * (uniforms.viewport_size.x * 0.5);

        for (uint d = 0; d < 3; d++) {
            if (!isnan(dL_dpos[d]) && !isinf(dL_dpos[d])) {
                atomic_fetch_add_explicit(&gradient_buffer[base + GRAD_OFF_POS_PB + d],
                                          dL_dpos[d], memory_order_relaxed);
            }
        }
    }

    // Update framebuffer: accumulate this gaussian's contribution
    PBFragmentOut out;
    out.color = float4(
        accumulated_rgb + contribution,
        transmittance * (1.0 - alpha)
    );

    return out;
}
