// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// NativeTwoPassCullerBridge.swift
// Aether3D
//
// Layer 7.3: Cross-platform two-pass frustum/occlusion culler bridge.
// C++ core handles all culling logic; each platform renderer just submits
// the visible meshlet list to its native draw API (Metal/Vulkan/GLES).
//

import Foundation
#if canImport(CAetherNativeBridge)
import CAetherNativeBridge
#endif

/// Culling tier (matches C++ TwoPassTier)
public enum CullingTier: Int, Sendable {
    /// Tier A: Full mesh shader + GPU HiZ (Apple A15+, Vulkan mesh shader)
    case tierA = 0
    /// Tier B: Compute shader + GPU HiZ (most modern GPUs)
    case tierB = 1
    /// Tier C: CPU fallback (universal, all platforms)
    case tierC = 2
}

/// Two-pass culling statistics
public struct CullingStats: Sendable {
    public let tier: CullingTier
    public let totalMeshlets: Int
    public let frustumRejected: Int
    public let pass1Visible: Int
    public let pass1Rejected: Int
    public let pass2Recovered: Int
    public let pass2Executed: Bool
    public let conservativeRejectRatio: Float
}

/// Native bridge for two-pass frustum/occlusion culler.
///
/// CROSS-PLATFORM: The C++ `two_pass_culler` performs:
///   Pass 1: Conservative frustum + HiZ occlusion with previous frame's depth
///   Pass 2: Re-tests rejected meshlets with updated depth (if rejection > threshold)
///
/// Tier selection automatically adapts:
///   - iOS (A15+): Tier A (mesh shader + GPU HiZ)
///   - iOS (older): Tier C (CPU fallback)
///   - Android (Vulkan): Tier B (compute + GPU HiZ)
///   - HarmonyOS: Tier B or C depending on GPU capabilities
public enum NativeTwoPassCullerBridge {

    /// Select the appropriate culling tier for the current platform.
    ///
    /// - Parameters:
    ///   - meshShaderSupported: Does the GPU support mesh shaders?
    ///   - gpuHiZSupported: Does the GPU support hierarchical Z-buffer?
    ///   - computeSupported: Does the GPU support compute shaders?
    /// - Returns: Selected tier, or nil on failure
    public static func selectTier(
        meshShaderSupported: Bool = false,
        gpuHiZSupported: Bool = true,
        computeSupported: Bool = true
    ) -> CullingTier? {
        #if canImport(CAetherNativeBridge)
        var runtime = aether_two_pass_runtime_t(
            mesh_shader_supported: meshShaderSupported ? 1 : 0,
            gpu_hzb_supported: gpuHiZSupported ? 1 : 0,
            compute_supported: computeSupported ? 1 : 0
        )
        var tier: Int32 = 0
        let rc = aether_two_pass_select_tier(&runtime, &tier)
        guard rc == 0 else { return nil }
        return CullingTier(rawValue: Int(tier)) ?? .tierC
        #else
        return .tierC
        #endif
    }

    /// Cull meshlets using two-pass frustum/occlusion testing.
    ///
    /// - Parameters:
    ///   - meshlets: C meshlet array from NativeMeshletBuilderBridge
    ///   - viewMatrix: 4x4 view matrix (column-major, 16 floats)
    ///   - projMatrix: 4x4 projection matrix (column-major, 16 floats)
    ///   - hiZData: Previous frame's hierarchical Z-buffer (nil for first frame)
    ///   - hiZResolution: HiZ buffer resolution (width = height, power of 2)
    ///   - tier: Culling tier to use
    /// - Returns: Tuple of (visible meshlet indices, stats), or nil on failure
    public static func cullMeshlets(
        meshlets: [aether_meshlet_t],
        viewMatrix: [Float],
        projMatrix: [Float],
        hiZData: [Float]? = nil,
        hiZResolution: Int = 256,
        tier: CullingTier = .tierC
    ) -> (visibleIndices: [Int], stats: CullingStats)? {
        #if canImport(CAetherNativeBridge)
        guard !meshlets.isEmpty,
              viewMatrix.count == 16,
              projMatrix.count == 16 else { return nil }

        let meshletCount = Int32(meshlets.count)
        var runtime = aether_two_pass_runtime_t(
            mesh_shader_supported: tier == .tierA ? 1 : 0,
            gpu_hzb_supported: tier != .tierC ? 1 : 0,
            compute_supported: tier != .tierC ? 1 : 0
        )

        var visibleIndices = [UInt32](repeating: 0, count: meshlets.count)
        var visibleCount = meshletCount
        var stats = aether_two_pass_stats_t()

        let rc: Int32
        if let hiZData = hiZData {
            rc = meshlets.withUnsafeBufferPointer { mBuf in
                viewMatrix.withUnsafeBufferPointer { vBuf in
                    projMatrix.withUnsafeBufferPointer { pBuf in
                        hiZData.withUnsafeBufferPointer { hBuf in
                            aether_two_pass_cull_meshlets(
                                mBuf.baseAddress, meshletCount,
                                vBuf.baseAddress, pBuf.baseAddress,
                                hBuf.baseAddress, Int32(hiZResolution),
                                &runtime,
                                &visibleIndices, &visibleCount,
                                &stats
                            )
                        }
                    }
                }
            }
        } else {
            // No HiZ data — pass nil for first frame
            rc = meshlets.withUnsafeBufferPointer { mBuf in
                viewMatrix.withUnsafeBufferPointer { vBuf in
                    projMatrix.withUnsafeBufferPointer { pBuf in
                        aether_two_pass_cull_meshlets(
                            mBuf.baseAddress, meshletCount,
                            vBuf.baseAddress, pBuf.baseAddress,
                            nil, 0,
                            &runtime,
                            &visibleIndices, &visibleCount,
                            &stats
                        )
                    }
                }
            }
        }

        guard rc == 0 else { return nil }

        let indices = (0..<Int(visibleCount)).map { Int(visibleIndices[$0]) }
        let swiftStats = CullingStats(
            tier: CullingTier(rawValue: Int(stats.tier)) ?? .tierC,
            totalMeshlets: Int(stats.total_meshlets),
            frustumRejected: Int(stats.frustum_rejected),
            pass1Visible: Int(stats.pass1_visible),
            pass1Rejected: Int(stats.pass1_rejected),
            pass2Recovered: Int(stats.pass2_recovered),
            pass2Executed: stats.pass2_executed != 0,
            conservativeRejectRatio: stats.conservative_reject_ratio
        )

        return (indices, swiftStats)
        #else
        return nil
        #endif
    }
}
