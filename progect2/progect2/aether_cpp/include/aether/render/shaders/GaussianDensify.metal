// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

#include <metal_stdlib>
using namespace metal;

// ═══════════════════════════════════════════════════════════════════════════
// GaussianDensify.metal — GPU Densification Evaluation Kernel
// ═══════════════════════════════════════════════════════════════════════════
// Evaluates each gaussian in parallel to determine a densification action:
//   keep  (0) — no change
//   clone (1) — duplicate with small positional jitter
//   split (2) — split into two with reduced scale
//   prune (3) — mark for removal
//
// Decision criteria:
//   - Gradient norm: high gradient → clone or split
//   - Scale: large gaussians split, small ones clone
//   - Opacity: very low opacity → prune candidate
//   - Evidence state: S5 (frozen) gaussians are never densified
//   - Uncertainty: high uncertainty → keep for potential densification
//   - Budget: atomic counter enforces a per-step budget cap
// ═══════════════════════════════════════════════════════════════════════════

// ---------------------------------------------------------------------------
// Action Flags
// ---------------------------------------------------------------------------

constant uint ACTION_KEEP  = 0;
constant uint ACTION_CLONE = 1;
constant uint ACTION_SPLIT = 2;
constant uint ACTION_PRUNE = 3;

// ---------------------------------------------------------------------------
// Densification Configuration
// ---------------------------------------------------------------------------

struct DensifyConfig {
    float grad_threshold;             // position gradient norm threshold
    float scale_split_threshold;      // split if max_scale > this
    float opacity_prune_threshold;    // prune if opacity < this
    float uncertainty_keep_threshold; // keep if uncertainty > this
    float uncertainty_prune_threshold;// prune if uncertainty < this
    uint  budget;                     // max new gaussians this step
    uint  total_gaussians;            // current gaussian count
    uint  max_gaussians;              // absolute maximum
    float min_opacity_after_split;    // opacity floor after split
    float scale_reduction_factor;     // scale divisor for split (e.g., 1.6)
    uint  min_step_for_prune;         // don't prune before this training step
    uint  current_step;               // current training step
};

// ---------------------------------------------------------------------------
// Per-Gaussian Input Data
// ---------------------------------------------------------------------------

struct GaussianDensifyInput {
    float  grad_norm;           // position gradient L2 norm
    float  opacity;             // decoded opacity [0,1]
    float  max_scale;           // max(scale_x, scale_y, scale_z)
    float  mean_scale;          // mean(scale_x, scale_y, scale_z)
    uchar  evidence_state;      // S0-S5 (0-4)
    float  uncertainty;         // DS uncertainty width (Pl - Bel)
    float  observation_count;   // number of frames this gaussian contributed to
    uint   gaussian_id;
};

// ═══════════════════════════════════════════════════════════════════════════
// Densification Evaluation Kernel
// ═══════════════════════════════════════════════════════════════════════════

kernel void gaussian_densify(
    device const GaussianDensifyInput*  inputs       [[buffer(0)]],
    device uint*                         actions      [[buffer(1)]],
    constant DensifyConfig&              config       [[buffer(2)]],
    device atomic_uint*                  clone_count  [[buffer(3)]],
    device atomic_uint*                  split_count  [[buffer(4)]],
    device atomic_uint*                  prune_count  [[buffer(5)]],
    uint tid [[thread_position_in_grid]]) {

    if (tid >= config.total_gaussians) return;

    device const GaussianDensifyInput& g = inputs[tid];

    // Default action: keep
    uint action = ACTION_KEEP;

    // ── Rule 0: S5 (kOriginal) gaussians are NEVER modified ──
    if (g.evidence_state == 4) {
        actions[tid] = ACTION_KEEP;
        return;
    }

    // ── Rule 1: Frozen flag check ──
    // (flags bit1 = frozen; if set, skip)
    // We check evidence_state >= 4 as a proxy since flags aren't in input

    // ── Rule 2: Pruning evaluation ──
    // Prune candidates: low opacity AND low uncertainty AND sufficient training
    bool prune_candidate = false;

    if (config.current_step >= config.min_step_for_prune) {
        // Very low opacity → likely not contributing
        if (g.opacity < config.opacity_prune_threshold) {
            prune_candidate = true;
        }

        // Low uncertainty AND low observation count → redundant
        if (g.uncertainty < config.uncertainty_prune_threshold &&
            g.observation_count < 3.0) {
            prune_candidate = true;
        }

        // Override: high uncertainty means the gaussian may still be useful
        if (g.uncertainty > config.uncertainty_keep_threshold) {
            prune_candidate = false;
        }
    }

    if (prune_candidate) {
        action = ACTION_PRUNE;
        atomic_fetch_add_explicit(prune_count, 1, memory_order_relaxed);
        actions[tid] = action;
        return;
    }

    // ── Rule 3: Densification evaluation (clone or split) ──
    // Only consider gaussians with high position gradient norm
    if (g.grad_norm < config.grad_threshold) {
        actions[tid] = ACTION_KEEP;
        return;
    }

    // Check budget availability
    uint total_budget = config.budget;
    uint current_total = config.total_gaussians;
    uint max_total = config.max_gaussians;

    // Don't exceed absolute maximum
    uint allocated_so_far = atomic_load_explicit(clone_count, memory_order_relaxed) +
                            atomic_load_explicit(split_count, memory_order_relaxed);
    if (current_total + allocated_so_far >= max_total) {
        actions[tid] = ACTION_KEEP;
        return;
    }

    // ── Rule 3a: Split large gaussians ──
    if (g.max_scale > config.scale_split_threshold) {
        // Attempt to allocate 1 new gaussian (split produces 2 from 1)
        uint slot = atomic_fetch_add_explicit(split_count, 1, memory_order_relaxed);
        if (slot < total_budget) {
            action = ACTION_SPLIT;
        } else {
            // Budget exhausted, undo the atomic
            atomic_fetch_sub_explicit(split_count, 1, memory_order_relaxed);
            action = ACTION_KEEP;
        }
    }
    // ── Rule 3b: Clone small gaussians ──
    else {
        uint slot = atomic_fetch_add_explicit(clone_count, 1, memory_order_relaxed);
        if (slot < total_budget) {
            action = ACTION_CLONE;
        } else {
            atomic_fetch_sub_explicit(clone_count, 1, memory_order_relaxed);
            action = ACTION_KEEP;
        }
    }

    // ── Rule 4: Evidence-state priority boost ──
    // S0-S2 (low coverage) regions get priority in budget allocation
    // This is handled implicitly by the gradient threshold — low-coverage
    // regions tend to have higher gradients because they are under-trained.
    // Additional boost: relax threshold for S0-S1
    if (action == ACTION_KEEP && g.evidence_state <= 1) {
        // Lower threshold for low-coverage regions
        float relaxed_threshold = config.grad_threshold * 0.5;
        if (g.grad_norm >= relaxed_threshold) {
            if (g.max_scale > config.scale_split_threshold) {
                uint slot = atomic_fetch_add_explicit(split_count, 1, memory_order_relaxed);
                if (slot < total_budget) {
                    action = ACTION_SPLIT;
                } else {
                    atomic_fetch_sub_explicit(split_count, 1, memory_order_relaxed);
                }
            } else {
                uint slot = atomic_fetch_add_explicit(clone_count, 1, memory_order_relaxed);
                if (slot < total_budget) {
                    action = ACTION_CLONE;
                } else {
                    atomic_fetch_sub_explicit(clone_count, 1, memory_order_relaxed);
                }
            }
        }
    }

    actions[tid] = action;
}

// ═══════════════════════════════════════════════════════════════════════════
// Apply Densification Kernel — Execute clone/split/prune actions
// ═══════════════════════════════════════════════════════════════════════════
// Separate kernel to apply the decisions. Runs after gaussian_densify.
// Reads action flags and produces new gaussians / marks for removal.

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

inline float decode_opacity_den(uint encoded) {
    float x = (float(encoded) / 255.0) * 12.0 - 6.0;
    return 1.0 / (1.0 + exp(-x));
}

inline uchar encode_opacity_den(float opacity) {
    opacity = clamp(opacity, 0.01, 0.99);
    float logit = log(opacity / (1.0 - opacity));
    float encoded = (logit + 6.0) / 12.0 * 255.0;
    return uchar(clamp(encoded, 0.0, 255.0));
}

kernel void gaussian_apply_densify(
    device const uint*              actions          [[buffer(0)]],
    device const PackedGaussianGPU* source_gaussians [[buffer(1)]],
    device PackedGaussianGPU*       new_gaussians    [[buffer(2)]],
    constant DensifyConfig&         config           [[buffer(3)]],
    device atomic_uint*             new_count        [[buffer(4)]],
    device const float*             grad_buffer      [[buffer(5)]],
    uint tid [[thread_position_in_grid]]) {

    if (tid >= config.total_gaussians) return;

    uint action = actions[tid];

    if (action == ACTION_KEEP || action == ACTION_PRUNE) return;

    device const PackedGaussianGPU& src = source_gaussians[tid];

    // Allocate slot in new_gaussians
    uint slot = atomic_fetch_add_explicit(new_count, 1, memory_order_relaxed);

    device PackedGaussianGPU& dst = new_gaussians[slot];

    // Copy base gaussian
    dst.position[0] = src.position[0];
    dst.position[1] = src.position[1];
    dst.position[2] = src.position[2];
    dst.rotation[0] = src.rotation[0];
    dst.rotation[1] = src.rotation[1];
    dst.rotation[2] = src.rotation[2];
    dst.rotation[3] = src.rotation[3];
    dst.evidence_state = 0;  // new gaussians start at S0
    dst.sh_dc[0] = src.sh_dc[0];
    dst.sh_dc[1] = src.sh_dc[1];
    dst.sh_dc[2] = src.sh_dc[2];
    for (int i = 0; i < 24; i++) dst.sh_rest[i] = src.sh_rest[i];
    dst.flags = 0;
    dst.padding0 = 0;
    dst.gaussian_id = config.max_gaussians + slot;  // temp ID
    dst.hessian_diag[0] = half(0.0);
    dst.hessian_diag[1] = half(0.0);
    dst.hessian_diag[2] = half(0.0);
    dst.padding1 = 0;

    if (action == ACTION_SPLIT) {
        // Split: reduce scale by reduction_factor, offset position along gradient
        float reduction = 1.0 / config.scale_reduction_factor;
        dst.log_scale[0] = half(float(src.log_scale[0]) + log(reduction));
        dst.log_scale[1] = half(float(src.log_scale[1]) + log(reduction));
        dst.log_scale[2] = half(float(src.log_scale[2]) + log(reduction));

        // Offset position using gradient direction
        uint grad_base = tid * 38;  // GRAD_STRIDE
        float3 grad_pos = float3(grad_buffer[grad_base + 0],
                                  grad_buffer[grad_base + 1],
                                  grad_buffer[grad_base + 2]);
        float grad_len = length(grad_pos);
        if (grad_len > 1e-7) {
            float3 offset_dir = grad_pos / grad_len;
            float max_scale = max(max(exp(float(src.log_scale[0])),
                                      exp(float(src.log_scale[1]))),
                                  exp(float(src.log_scale[2])));
            float offset_dist = max_scale * 0.5;
            dst.position[0] += offset_dir.x * offset_dist;
            dst.position[1] += offset_dir.y * offset_dist;
            dst.position[2] += offset_dir.z * offset_dist;
        }

        // Reduce opacity for both parent and child
        float parent_opacity = decode_opacity_den(uint(src.opacity_u8));
        float new_opacity = max(parent_opacity * 0.7, config.min_opacity_after_split);
        dst.opacity_u8 = encode_opacity_den(new_opacity);
    }
    else if (action == ACTION_CLONE) {
        // Clone: keep same scale, small random position offset
        dst.log_scale[0] = src.log_scale[0];
        dst.log_scale[1] = src.log_scale[1];
        dst.log_scale[2] = src.log_scale[2];
        dst.opacity_u8 = src.opacity_u8;

        // Deterministic jitter based on gaussian_id
        uint hash = src.gaussian_id;
        hash = hash * 2654435761u;  // Knuth multiplicative hash
        float jx = (float(hash & 0xFFu) / 128.0 - 1.0) * 0.001;
        hash >>= 8;
        float jy = (float(hash & 0xFFu) / 128.0 - 1.0) * 0.001;
        hash >>= 8;
        float jz = (float(hash & 0xFFu) / 128.0 - 1.0) * 0.001;

        dst.position[0] += jx;
        dst.position[1] += jy;
        dst.position[2] += jz;
    }
}
