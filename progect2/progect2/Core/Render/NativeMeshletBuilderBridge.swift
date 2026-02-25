// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// NativeMeshletBuilderBridge.swift
// Aether3D
//
// Layer 7.3: Cross-platform meshlet construction bridge.
// Wraps C++ meshlet builder for consistent LOD grouping on iOS/Android/HarmonyOS.
//

import Foundation
#if canImport(CAetherNativeBridge)
import CAetherNativeBridge
#endif

/// Swift-friendly meshlet representation
public struct MeshletInfo: Sendable {
    /// Index of first triangle in the original mesh
    public let firstTriangleIndex: Int
    /// Number of triangles in this meshlet
    public let triangleCount: Int
    /// AABB bounds (min xyz, max xyz)
    public let boundsMin: SIMD3<Float>
    public let boundsMax: SIMD3<Float>
    /// LOD level (0 = highest detail)
    public let lodLevel: Int
    /// LOD error metric [0,1] — higher = lower quality acceptable
    public let lodError: Float
}

/// Native bridge for meshlet (GPU mesh shader) construction.
///
/// CROSS-PLATFORM: The C++ `meshlet_builder` partitions triangle meshes
/// into fixed-size groups with AABB bounds and LOD levels.  This runs
/// identically on iOS (Metal), Android (Vulkan), and HarmonyOS (Vulkan).
/// The output is a platform-agnostic meshlet list that each renderer
/// consumes via its native draw call.
public enum NativeMeshletBuilderBridge {

    /// Get default meshlet build configuration
    public static func defaultConfig() -> aether_meshlet_build_config_t {
        #if canImport(CAetherNativeBridge)
        var config = aether_meshlet_build_config_t()
        _ = aether_meshlet_default_config(&config)
        return config
        #else
        return aether_meshlet_build_config_t()
        #endif
    }

    /// Build meshlets from triangle mesh vertices and indices.
    ///
    /// - Parameters:
    ///   - vertices: Flat array of vertex positions [x0,y0,z0, x1,y1,z1, ...]
    ///   - indices: Triangle indices (3 per triangle)
    ///   - config: Build configuration (nil for defaults)
    /// - Returns: Array of meshlets, or nil on failure
    public static func buildMeshlets(
        vertices: [Float],
        indices: [UInt32],
        config: aether_meshlet_build_config_t? = nil
    ) -> [MeshletInfo]? {
        #if canImport(CAetherNativeBridge)
        let vertexCount = Int32(vertices.count / 3)
        let indexCount = Int32(indices.count)
        guard vertexCount > 0, indexCount >= 3 else { return nil }

        var cfg = config ?? defaultConfig()

        // Estimate max meshlets: ceil(triangles / min_per_meshlet)
        let triangleCount = indexCount / 3
        let maxMeshlets = Int(triangleCount / max(Int32(cfg.min_triangles_per_meshlet), 1)) + 1
        var meshlets = [aether_meshlet_t](repeating: aether_meshlet_t(), count: maxMeshlets)
        var count = Int32(maxMeshlets)

        let rc = vertices.withUnsafeBufferPointer { vBuf in
            indices.withUnsafeBufferPointer { iBuf in
                aether_meshlet_build(
                    vBuf.baseAddress, vertexCount,
                    iBuf.baseAddress, indexCount,
                    &cfg,
                    &meshlets, &count
                )
            }
        }

        guard rc == 0, count > 0 else { return nil }

        return (0..<Int(count)).map { i in
            let m = meshlets[i]
            return MeshletInfo(
                firstTriangleIndex: Int(m.first_triangle_index),
                triangleCount: Int(m.triangle_count),
                boundsMin: SIMD3<Float>(m.bounds.min_x, m.bounds.min_y, m.bounds.min_z),
                boundsMax: SIMD3<Float>(m.bounds.max_x, m.bounds.max_y, m.bounds.max_z),
                lodLevel: Int(m.lod_level),
                lodError: m.lod_error
            )
        }
        #else
        return nil
        #endif
    }
}
