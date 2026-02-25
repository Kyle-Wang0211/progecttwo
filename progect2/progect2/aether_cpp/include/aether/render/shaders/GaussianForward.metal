// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include <metal_stdlib>
using namespace metal;

// ═══════════════════════════════════════════════════════════════════════════
// GaussianForward.metal — Sort-Free Forward Rendering for 3D Gaussian
//                         Splatting with scaffold-anchored surfel pass
// ═══════════════════════════════════════════════════════════════════════════
// Two-pass rendering:
//   Pass 1 (Surfel): Opaque scaffold triangles → depth pre-pass
//   Pass 2 (Gaussian): Alpha-blended splats with raster_order_group(0)
// ═══════════════════════════════════════════════════════════════════════════

// ---------------------------------------------------------------------------
// Shared Constants (inline since Metal doesn't support cross-file #include)
// ---------------------------------------------------------------------------

constant float SH_C0_F   = 0.28209479177387814;
constant float SH_C1_F   = 0.4886025119029199;
constant float SH_C2_0_F = 1.0925484305920792;
constant float SH_C2_1_F = 0.31539156525252005;
constant float SH_C2_2_F = 0.5462742152960396;

// ---------------------------------------------------------------------------
// GPU Struct Definitions (must match C++ side exactly)
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

// 96-byte packed gaussian — matches PackedGaussian in C++
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

// Scaffold vertex for surfel pass
struct ScaffoldVertex {
    float3 position  [[attribute(0)]];
    float3 normal    [[attribute(1)]];
    float3 color     [[attribute(2)]];
};

// ---------------------------------------------------------------------------
// Inline Utility Functions
// ---------------------------------------------------------------------------

inline float decode_opacity_fwd(uint encoded) {
    float x = (float(encoded) / 255.0) * 12.0 - 6.0;
    return 1.0 / (1.0 + exp(-x));
}

inline float3 decode_log_scale_fwd(half3 log_s) {
    return exp(float3(log_s));
}

inline float safe_val(float v) {
    return isnan(v) || isinf(v) ? 0.0 : v;
}

inline float3 safe_val3(float3 v) {
    return float3(safe_val(v.x), safe_val(v.y), safe_val(v.z));
}

// SH evaluation (full L2) — inline for forward pass
inline float3 eval_sh(half3 sh_dc,
                       device const half* sh_rest,
                       float3 dir,
                       uint sh_order) {
    float3 result = float3(sh_dc) * SH_C0_F;

    if (sh_order >= 1) {
        for (int c = 0; c < 3; c++) {
            float s1 = float(sh_rest[c * 3 + 0]);
            float s2 = float(sh_rest[c * 3 + 1]);
            float s3 = float(sh_rest[c * 3 + 2]);
            result[c] += SH_C1_F * (-s1 * dir.y + s2 * dir.z - s3 * dir.x);
        }
    }

    if (sh_order >= 2) {
        float xx = dir.x * dir.x, yy = dir.y * dir.y, zz = dir.z * dir.z;
        float xy = dir.x * dir.y, yz = dir.y * dir.z, xz = dir.x * dir.z;

        for (int c = 0; c < 3; c++) {
            int off = 9 + c * 5;
            result[c] += SH_C2_0_F * float(sh_rest[off + 0]) * xy;
            result[c] += SH_C2_0_F * float(sh_rest[off + 1]) * yz;
            result[c] += SH_C2_1_F * float(sh_rest[off + 2]) * (2.0 * zz - xx - yy);
            result[c] += SH_C2_0_F * float(sh_rest[off + 3]) * xz;
            result[c] += SH_C2_2_F * float(sh_rest[off + 4]) * (xx - yy);
        }
    }

    return max(result, float3(0.0));
}

// Covariance from scale + quaternion
inline float3x3 compute_cov3d(float3 scale, float4 quat) {
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

// Project 3D covariance to 2D
inline float2x2 project_cov2d(float3x3 cov3D,
                                float4x4 viewMat,
                                float3 mean3D,
                                float fx, float fy,
                                float w, float h) {
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

// 2x2 inverse
inline float2x2 inv2x2(float2x2 m) {
    float det = m[0][0] * m[1][1] - m[0][1] * m[1][0];
    float inv_d = 1.0 / max(abs(det), 1e-10);
    return float2x2(m[1][1] * inv_d, -m[0][1] * inv_d,
                    -m[1][0] * inv_d, m[0][0] * inv_d);
}

// ═══════════════════════════════════════════════════════════════════════════
// Pass 1: Surfel (Opaque Scaffold) — Vertex/Fragment
// ═══════════════════════════════════════════════════════════════════════════

struct SurfelVertexOut {
    float4 position [[position]];
    float3 color;
    float3 world_normal;
    float  depth;
};

vertex SurfelVertexOut surfel_vertex(
    ScaffoldVertex in [[stage_in]],
    constant GaussianUniforms& uniforms [[buffer(1)]]) {

    SurfelVertexOut out;
    float4 world_pos = float4(in.position, 1.0);
    float4 view_pos = uniforms.view_matrix * world_pos;
    out.position = uniforms.proj_matrix * view_pos;
    out.color = in.color;
    out.world_normal = in.normal;
    out.depth = -view_pos.z;  // positive depth in view space
    return out;
}

struct SurfelFragmentOut {
    float4 color [[color(0)]];
    // depth is written automatically
};

fragment SurfelFragmentOut surfel_fragment(SurfelVertexOut in [[stage_in]]) {
    SurfelFragmentOut out;

    // Simple Lambertian shading
    float3 light_dir = normalize(float3(0.5, 1.0, 0.3));
    float ndotl = max(dot(normalize(in.world_normal), light_dir), 0.15);
    out.color = float4(in.color * ndotl, 1.0);

    return out;
}

// ═══════════════════════════════════════════════════════════════════════════
// Pass 2: Gaussian Splatting — Vertex/Fragment
// ═══════════════════════════════════════════════════════════════════════════
// Each gaussian generates a quad (4 vertices) via instanced drawing.
// Vertex IDs 0-3 map to the 4 corners of the ellipse bounding box.

struct GaussianVertexOut {
    float4 position    [[position]];
    float2 offset;           // offset from gaussian center in screen space
    float3 color;            // SH-evaluated color
    float  opacity;          // decoded opacity
    // Inverse 2D covariance — flattened from float2x2 to 4 scalars because
    // Metal does not support matrix types as vertex-to-fragment interpolants.
    float  inv_cov_00;       // inv_cov2D[0][0]
    float  inv_cov_01;       // inv_cov2D[0][1]
    float  inv_cov_10;       // inv_cov2D[1][0]
    float  inv_cov_11;       // inv_cov2D[1][1]
    uint   gaussian_idx;     // for gradient writeback in training
};

vertex GaussianVertexOut gaussian_vertex(
    uint vertex_id     [[vertex_id]],
    uint instance_id   [[instance_id]],
    constant GaussianUniforms& uniforms [[buffer(0)]],
    device const PackedGaussianGPU* gaussians [[buffer(1)]]) {

    GaussianVertexOut out;

    // Bounds check
    if (instance_id >= uniforms.gaussian_count) {
        out.position = float4(0, 0, -2, 1);  // behind camera, clipped
        return out;
    }

    device const PackedGaussianGPU& g = gaussians[instance_id];

    // Decode position
    float3 pos = float3(g.position[0], g.position[1], g.position[2]);

    // NaN guard on position
    if (isnan(pos.x) || isnan(pos.y) || isnan(pos.z)) {
        out.position = float4(0, 0, -2, 1);
        return out;
    }

    // Frustum test: project to clip space
    float4 clip_pos = uniforms.view_proj_matrix * float4(pos, 1.0);
    if (clip_pos.w < uniforms.near_plane) {
        out.position = float4(0, 0, -2, 1);
        return out;
    }

    float2 ndc = clip_pos.xy / clip_pos.w;

    // Cull if far outside NDC
    if (abs(ndc.x) > 1.5 || abs(ndc.y) > 1.5) {
        out.position = float4(0, 0, -2, 1);
        return out;
    }

    // Decode scale and rotation
    float3 scale = decode_log_scale_fwd(half3(g.log_scale[0], g.log_scale[1], g.log_scale[2]));
    float4 quat = float4(
        float(g.rotation[0]), float(g.rotation[1]),
        float(g.rotation[2]), float(g.rotation[3])
    );

    // Compute 3D and 2D covariance
    float3x3 cov3D = compute_cov3d(scale, quat);

    // Focal lengths from projection matrix (standard perspective)
    float fx = uniforms.proj_matrix[0][0] * uniforms.viewport_size.x * 0.5;
    float fy = uniforms.proj_matrix[1][1] * uniforms.viewport_size.y * 0.5;

    float2x2 cov2D = project_cov2d(cov3D, uniforms.view_matrix, pos,
                                     fx, fy,
                                     uniforms.viewport_size.x,
                                     uniforms.viewport_size.y);

    // Compute eigenvalues for bounding box radius (3-sigma)
    float a = cov2D[0][0], b = cov2D[0][1], d = cov2D[1][1];
    float det = a * d - b * b;
    if (det < 1e-10) {
        out.position = float4(0, 0, -2, 1);
        return out;
    }

    // Compute radius from diagonal (conservative bounding box)
    float sigma = 3.0;
    float radius_x = sigma * sqrt(max(a, 0.0001));
    float radius_y = sigma * sqrt(max(d, 0.0001));

    // Generate quad corners (4 vertices per gaussian instance)
    // vertex_id: 0=TL, 1=TR, 2=BL, 3=BR
    float2 corner_offsets[4] = {
        float2(-1, -1),
        float2( 1, -1),
        float2(-1,  1),
        float2( 1,  1)
    };
    float2 corner = corner_offsets[vertex_id % 4];
    float2 pixel_offset = float2(corner.x * radius_x, corner.y * radius_y);

    // Screen-space center of the gaussian
    float2 screen_center = (ndc * 0.5 + 0.5) * uniforms.viewport_size;

    // Final screen position of this vertex
    float2 screen_pos = screen_center + pixel_offset;
    float2 final_ndc = (screen_pos / uniforms.viewport_size) * 2.0 - 1.0;

    out.position = float4(final_ndc.x, final_ndc.y, clip_pos.z / clip_pos.w, 1.0);
    out.offset = pixel_offset;

    // Inverse 2D covariance for fragment evaluation (flattened for Metal)
    float2x2 inv_cov = inv2x2(cov2D);
    out.inv_cov_00 = inv_cov[0][0];
    out.inv_cov_01 = inv_cov[0][1];
    out.inv_cov_10 = inv_cov[1][0];
    out.inv_cov_11 = inv_cov[1][1];

    // Decode opacity
    out.opacity = decode_opacity_fwd(uint(g.opacity_u8));

    // Evaluate SH color
    float3 view_dir = normalize(pos - uniforms.camera_position);
    out.color = safe_val3(eval_sh(half3(g.sh_dc[0], g.sh_dc[1], g.sh_dc[2]),
                                  (device const half*)g.sh_rest,
                                  view_dir,
                                  uniforms.sh_order));

    out.gaussian_idx = instance_id;

    return out;
}

// Fragment shader with raster_order_group for correct order-independent
// alpha compositing. The raster_order_group(0) annotation ensures that
// fragments at the same pixel location are processed in submission order,
// providing the depth-ordering guarantee without explicit sorting.

struct GaussianFragmentOut {
    float4 color [[color(0), raster_order_group(0)]];
};

fragment GaussianFragmentOut gaussian_fragment(
    GaussianVertexOut in [[stage_in]]) {

    // Compute 2D Gaussian weight
    float2 offset = in.offset;
    float power = -0.5 * (
        offset.x * (in.inv_cov_00 * offset.x + in.inv_cov_01 * offset.y) +
        offset.y * (in.inv_cov_10 * offset.x + in.inv_cov_11 * offset.y)
    );

    // Discard fragments outside 3-sigma ellipse
    if (power < -4.5) {
        discard_fragment();
    }

    float alpha = in.opacity * exp(max(power, -20.0));
    alpha = clamp(alpha, 0.0, 0.999);

    // Skip near-transparent fragments
    if (alpha < 0.002) {
        discard_fragment();
    }

    GaussianFragmentOut out;
    // Output premultiplied alpha: (color * alpha, alpha)
    out.color = float4(in.color * alpha, alpha);

    return out;
}
