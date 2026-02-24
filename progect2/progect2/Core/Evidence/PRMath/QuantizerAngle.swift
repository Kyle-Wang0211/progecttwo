// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// QuantizerAngle.swift
// Aether3D
//
// PR3 - Type-Safe Angle Quantization
// For angle values in degrees
//

import Foundation
#if canImport(CAetherNativeBridge)
import CAetherNativeBridge
#endif

/// Type-safe quantizer for angle values
///
/// USAGE: Span degrees, angle degrees
/// SCALE: 1e9 (9 decimal places, angles don't need 12)
public enum QuantizerAngle {

    /// Scale for 9 decimal places
    public static let scale: Double = 1e9
    public static let scaleInt64: Int64 = 1_000_000_000

    @inline(__always)
    private static func failClosed<T>(_ fallback: T) -> T {
        assertionFailure("CAetherNativeBridge is required for QuantizerAngle kernels")
        return fallback
    }

    /// Quantize angle in degrees to Int64
    ///
    /// PRECONDITION: value is finite
    /// OUTPUT: Int64 representation
    ///
    /// - Parameter degrees: Angle in degrees
    /// - Returns: Quantized Int64 value
    @inlinable
    public static func quantize(_ degrees: Double) -> Int64 {
        #if canImport(CAetherNativeBridge)
        return aether_quantize_angle_deg(degrees)
        #else
        _ = degrees
        return failClosed(0)
        #endif
    }

    /// Dequantize Int64 back to Double
    ///
    /// - Parameter q: Quantized Int64 value
    /// - Returns: Angle in degrees
    @inlinable
    public static func dequantize(_ q: Int64) -> Double {
        #if canImport(CAetherNativeBridge)
        return aether_dequantize_angle_deg(q)
        #else
        _ = q
        return failClosed(0.0)
        #endif
    }
}
