// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// SPZCompressor.swift
// Aether3D
//
// v8.0: Thin bridge over C++ SpzCompressor engine.
// All algorithm logic (Morton sort, quantization, delta encoding, zlib)
// now lives in aether_cpp/src/render/spz_compressor.cpp.
// Swift layer: Data↔pointer marshalling only.
//

import Foundation
import CAetherNativeBridge

/// SPZ file header
public struct SPZHeader {
    public static let magic: UInt32 = 0x53505A31  // "SPZ1"
    public static let version: UInt16 = 1

    public var numSplats: UInt32
    public var shDegree: UInt8
    public var flags: UInt8

    public var minX: Float
    public var minY: Float
    public var minZ: Float
    public var maxX: Float
    public var maxY: Float
    public var maxZ: Float

    public init(numSplats: UInt32, shDegree: UInt8, flags: UInt8 = 0,
                minX: Float = 0, minY: Float = 0, minZ: Float = 0,
                maxX: Float = 0, maxY: Float = 0, maxZ: Float = 0) {
        self.numSplats = numSplats
        self.shDegree = shDegree
        self.flags = flags
        self.minX = minX; self.minY = minY; self.minZ = minZ
        self.maxX = maxX; self.maxY = maxY; self.maxZ = maxZ
    }
}

/// SPZ Compressor — thin bridge to C++ SpzCompressor.
public struct SPZCompressor {

    public init() {}

    // MARK: - Compress

    public func compress(splatData: GaussianSplatData, shDegree: Int = 0) -> Data {
        let splatCount = splatData.positions.count / 3
        guard splatCount > 0 else { return Data() }

        var outData: UnsafeMutablePointer<UInt8>?
        var outSize: Int = 0

        let shPtr: UnsafePointer<Float>? = splatData.sphericalHarmonics.flatMap {
            $0.withUnsafeBufferPointer { $0.baseAddress }
        }

        let rc = splatData.positions.withUnsafeBufferPointer { posPtr in
            splatData.scales.withUnsafeBufferPointer { scalePtr in
                splatData.rotations.withUnsafeBufferPointer { rotPtr in
                    splatData.opacities.withUnsafeBufferPointer { opPtr in
                        aether_spz_compress(
                            posPtr.baseAddress,
                            scalePtr.baseAddress,
                            rotPtr.baseAddress,
                            opPtr.baseAddress,
                            shPtr,
                            Int32(splatCount),
                            Int32(shDegree),
                            &outData,
                            &outSize
                        )
                    }
                }
            }
        }

        guard rc == 0, let ptr = outData, outSize > 0 else { return Data() }
        let data = Data(bytes: ptr, count: outSize)
        aether_spz_free(ptr)
        return data
    }

    // MARK: - Decompress

    public func decompress(data: Data) -> GaussianSplatData? {
        var outPositions: UnsafeMutablePointer<Float>?
        var outScales: UnsafeMutablePointer<Float>?
        var outRotations: UnsafeMutablePointer<Float>?
        var outOpacities: UnsafeMutablePointer<Float>?
        var outSH: UnsafeMutablePointer<Float>?
        var outColors: UnsafeMutablePointer<Float>?
        var header = aether_spz_header_t()

        let rc = data.withUnsafeBytes { rawPtr -> Int32 in
            guard let baseAddress = rawPtr.baseAddress else { return -1 }
            return aether_spz_decompress(
                baseAddress.assumingMemoryBound(to: UInt8.self),
                data.count,
                &outPositions,
                &outScales,
                &outRotations,
                &outOpacities,
                &outSH,
                &outColors,
                &header
            )
        }

        guard rc == 0 else { return nil }
        let count = Int(header.num_splats)

        func toArray(_ ptr: UnsafeMutablePointer<Float>?, _ n: Int) -> [Float] {
            guard let ptr = ptr, n > 0 else { return [] }
            let arr = Array(UnsafeBufferPointer(start: ptr, count: n))
            aether_spz_free(ptr)
            return arr
        }

        let positions = toArray(outPositions, count * 3)
        let scales = toArray(outScales, count * 3)
        let rotations = toArray(outRotations, count * 4)
        let opacities = toArray(outOpacities, count)
        let colors = toArray(outColors, count * 3)

        let shDeg = Int(header.sh_degree)
        let shCount: Int
        switch shDeg {
        case 0: shCount = 1
        case 1: shCount = 4
        case 2: shCount = 9
        case 3: shCount = 16
        default: shCount = 1
        }
        let sh = toArray(outSH, count * shCount * 3)

        return GaussianSplatData(
            positions: positions,
            colors: colors,
            opacities: opacities,
            scales: scales,
            rotations: rotations,
            sphericalHarmonics: sh.isEmpty ? nil : sh
        )
    }
}
