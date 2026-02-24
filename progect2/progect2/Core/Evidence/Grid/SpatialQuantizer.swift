// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// SpatialQuantizer.swift
// Aether3D
//
// PR6 Evidence Grid System - Spatial Quantizer
// 3D world coordinates → Morton code mapping
//

import Foundation

/// **Rule ID:** PR6_GRID_QUANTIZER_001
/// Spatial quantizer: maps 3D world coordinates to Morton codes
/// Uses integer-based quantization for determinism
public struct SpatialQuantizer: Sendable {
    
    /// Grid cell size (from GridResolutionPolicy)
    public let cellSize: LengthQ
    
    /// World space origin (for coordinate system)
    public let origin: EvidenceVector3
    
    public init(cellSize: LengthQ, origin: EvidenceVector3 = EvidenceVector3(x: 0, y: 0, z: 0)) {
        self.cellSize = cellSize
        self.origin = origin
    }
    
    /// **Rule ID:** PR6_GRID_QUANTIZER_002
    /// Quantize 3D world coordinates to integer grid coordinates
    ///
    /// - Parameter worldPos: World position in meters
    /// - Returns: Integer grid coordinates (x, y, z)
    public func quantize(_ worldPos: EvidenceVector3) -> GridCell.QuantizedPosition {
        let native = NativeSpatialQuantizerBridge.quantizeWorld(
            worldPos: worldPos,
            origin: origin,
            cellSize: Float(cellSize.toMeters())
        )
        return GridCell.QuantizedPosition(x: native.x, y: native.y, z: native.z)
    }
    
    /// **Rule ID:** PR6_GRID_QUANTIZER_003
    /// Compute Morton code from integer grid coordinates
    ///
    /// Morton code (Z-order curve) interleaves bits: zyxzyxzyx...
    ///
    /// - Parameters:
    ///   - x: Grid X coordinate
    ///   - y: Grid Y coordinate
    ///   - z: Grid Z coordinate
    /// - Returns: Morton code (UInt64)
    public func mortonCode(x: Int32, y: Int32, z: Int32) -> UInt64 {
        NativeSpatialQuantizerBridge.mortonCode(x: x, y: y, z: z)
    }
    
    /// Compute Morton code from world position
    public func mortonCode(from worldPos: EvidenceVector3) -> UInt64 {
        NativeSpatialQuantizerBridge.mortonCode(
            from: worldPos,
            origin: origin,
            cellSize: Float(cellSize.toMeters())
        )
    }
    
    /// Decode Morton code back to grid coordinates
    public func decodeMortonCode(_ code: UInt64) -> GridCell.QuantizedPosition {
        let native = NativeSpatialQuantizerBridge.decodeMortonCode(code)
        return GridCell.QuantizedPosition(x: native.x, y: native.y, z: native.z)
    }
    
    /// Convert grid coordinates back to world position
    public func worldPosition(_ pos: GridCell.QuantizedPosition) -> EvidenceVector3 {
        return worldPosition(x: pos.x, y: pos.y, z: pos.z)
    }
    
    /// Convert grid coordinates back to world position
    public func worldPosition(x: Int32, y: Int32, z: Int32) -> EvidenceVector3 {
        NativeSpatialQuantizerBridge.dequantize(
            x: x,
            y: y,
            z: z,
            origin: origin,
            cellSize: Float(cellSize.toMeters())
        )
    }
}

// Note: LengthQ already has toMeters() method, no need to add extension
