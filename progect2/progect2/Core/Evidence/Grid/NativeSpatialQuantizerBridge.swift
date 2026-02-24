// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

import Foundation
#if canImport(CAetherNativeBridge)
import CAetherNativeBridge
#endif

/// Native bridge for Morton code / Z-order spatial quantization.
/// Delegates to optimized C++ bit-interleaving when available.
enum NativeSpatialQuantizerBridge {
    @inline(__always)
    private static func failClosed<T>(_ fallback: T) -> T {
        assertionFailure("CAetherNativeBridge is required for spatial quantizer kernels")
        return fallback
    }

    static func quantizeWorld(
        worldPos: EvidenceVector3,
        origin: EvidenceVector3,
        cellSize: Float
    ) -> (x: Int32, y: Int32, z: Int32) {
        #if canImport(CAetherNativeBridge)
        var config = aether_spatial_quantizer_config_t(
            origin_x: Float(origin.x),
            origin_y: Float(origin.y),
            origin_z: Float(origin.z),
            cell_size: cellSize
        )
        var gx: Int32 = 0
        var gy: Int32 = 0
        var gz: Int32 = 0
        aether_spatial_quantize(
            &config,
            Float(worldPos.x),
            Float(worldPos.y),
            Float(worldPos.z),
            &gx,
            &gy,
            &gz
        )
        return (gx, gy, gz)
        #else
        _ = worldPos
        _ = origin
        _ = cellSize
        return failClosed((0, 0, 0))
        #endif
    }

    static func mortonCode(x: Int32, y: Int32, z: Int32) -> UInt64 {
        #if canImport(CAetherNativeBridge)
        return aether_morton_encode(x, y, z)
        #else
        _ = x
        _ = y
        _ = z
        return failClosed(0)
        #endif
    }

    static func decodeMortonCode(_ code: UInt64) -> (x: Int32, y: Int32, z: Int32) {
        #if canImport(CAetherNativeBridge)
        var ox: Int32 = 0, oy: Int32 = 0, oz: Int32 = 0
        aether_morton_decode(code, &ox, &oy, &oz)
        return (ox, oy, oz)
        #else
        _ = code
        return failClosed((0, 0, 0))
        #endif
    }

    static func mortonCode(from worldPos: EvidenceVector3, origin: EvidenceVector3, cellSize: Float) -> UInt64 {
        #if canImport(CAetherNativeBridge)
        var config = aether_spatial_quantizer_config_t(
            origin_x: Float(origin.x), origin_y: Float(origin.y), origin_z: Float(origin.z),
            cell_size: cellSize
        )
        return aether_spatial_morton_code(&config, Float(worldPos.x), Float(worldPos.y), Float(worldPos.z))
        #else
        _ = worldPos
        _ = origin
        _ = cellSize
        return failClosed(0)
        #endif
    }

    static func dequantize(
        x: Int32,
        y: Int32,
        z: Int32,
        origin: EvidenceVector3,
        cellSize: Float
    ) -> EvidenceVector3 {
        #if canImport(CAetherNativeBridge)
        var config = aether_spatial_quantizer_config_t(
            origin_x: Float(origin.x),
            origin_y: Float(origin.y),
            origin_z: Float(origin.z),
            cell_size: cellSize
        )
        var position = aether_quantized_position_t(x: x, y: y, z: z)
        var wx: Double = 0
        var wy: Double = 0
        var wz: Double = 0
        let rc = aether_spatial_dequantize_world_position(
            &position,
            Double(config.origin_x),
            Double(config.origin_y),
            Double(config.origin_z),
            Double(config.cell_size),
            &wx,
            &wy,
            &wz
        )
        if rc == 0 {
            return EvidenceVector3(x: wx, y: wy, z: wz)
        }
        #endif
        _ = x
        _ = y
        _ = z
        _ = cellSize
        return failClosed(EvidenceVector3(x: origin.x, y: origin.y, z: origin.z))
    }
}
