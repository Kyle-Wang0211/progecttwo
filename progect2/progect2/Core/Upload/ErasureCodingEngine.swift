// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// CONSTITUTIONAL CONTRACT - DO NOT EDIT WITHOUT RFC
// Contract Version: PR9-FEC-1.0
// Module: Upload Infrastructure - Erasure Coding Engine
// Cross-Platform: macOS + Linux (pure Foundation)

import Foundation
import CAetherNativeBridge

/// Erasure coding mode.
public enum ErasureCodingMode: Sendable {
    case reedSolomon(GaloisField)
    case raptorQ
    
    public enum GaloisField: Sendable {
        case gf256    // GF(2^8) for ≤255 chunks
        case gf65536  // GF(2^16) for >255 chunks
    }
}

/// Erasure coder protocol.
public protocol ErasureCoder: Sendable {
    func encode(data: [Data], redundancy: Double) async -> [Data]
    func decode(blocks: [Data?], originalCount: Int) async throws -> [Data]
}

/// Chunk priority level.
public enum ChunkPriority: Int, Sendable {
    case critical = 0  // First/last frame + intrinsics
    case high = 1       // Key frames, quality > 0.9
    case normal = 2    // Standard frames
    case low = 3       // Low-quality frames
}

/// Adaptive Reed-Solomon + RaptorQ erasure coding engine.
///
/// **Purpose**: Adaptive RS (GF(2^8)/GF(2^16)) + RaptorQ fallback, UEP per priority level.
///
/// **Adaptive Decision**:
/// - chunkCount ≤ 255 && lossRate < 0.08 → RS GF(256)
/// - chunkCount ≤ 255 && lossRate ≥ 0.08 → RaptorQ
/// - chunkCount > 255 && lossRate < 0.03 → RS GF(65536)
/// - chunkCount > 255 || lossRate ≥ 0.03 → RaptorQ
///
/// **RS Parameters**:
/// - Loss rate < 1% (WiFi): RS(20, 22) — 10% redundancy
/// - Loss rate 1-5% (4G): RS(20, 24) — 20% redundancy
/// - Loss rate 5-8% (weak): RS(20, 28) — 40% redundancy
/// - Loss rate > 8%: Switch to RaptorQ
///
/// **Unequal Error Protection (UEP)**:
/// - Priority 0: 3x redundancy
/// - Priority 1: 2.5x redundancy
/// - Priority 2: 1.5x redundancy
/// - Priority 3: 1x redundancy
public actor ErasureCodingEngine: ErasureCoder {
    public init() {}
    
    // MARK: - Mode Selection
    
    /// Select erasure coding mode based on chunk count and loss rate.
    ///
    /// - Parameters:
    ///   - chunkCount: Total number of chunks
    ///   - lossRate: Estimated loss rate (0.0-1.0)
    /// - Returns: Selected coding mode
    public func selectCoder(chunkCount: Int, lossRate: Double) -> ErasureCodingMode {
        var nativeSelection = aether_erasure_selection_t(mode: 1, field: 0)
        let safeChunkCount = max(0, min(chunkCount, Int(Int32.max)))
        let safeLossRate = max(0.0, min(1.0, lossRate))
        if aether_erasure_select_mode(Int32(safeChunkCount), safeLossRate, &nativeSelection) == 0 {
            if nativeSelection.mode == 0 {
                return .reedSolomon(nativeSelection.field == 0 ? .gf256 : .gf65536)
            }
            return .raptorQ
        }
        // Fail-closed: prefer rateless mode when native selector is unavailable.
        return .raptorQ
    }
    
    // MARK: - ErasureCoder Protocol
    
    /// Encode data with redundancy.
    ///
    /// - Parameters:
    ///   - data: Array of data blocks
    ///   - redundancy: Redundancy ratio (0.0-1.0)
    /// - Returns: Encoded blocks (original + parity)
    public func encode(data: [Data], redundancy: Double) async -> [Data] {
        // Select mode
        let mode = selectCoder(chunkCount: data.count, lossRate: 0.0)
        if let native = nativeEncode(data: data, redundancy: redundancy, mode: mode) {
            return native
        }
        // Fail-closed: keep upload pipeline moving without Swift-side FEC algorithm fallback.
        return data
    }
    
    /// Decode blocks to recover original data.
    ///
    /// - Parameters:
    ///   - blocks: Array of blocks (nil = erasure)
    ///   - originalCount: Original number of data blocks
    /// - Returns: Recovered data blocks
    /// - Throws: ErasureCodingError if decoding fails
    public func decode(blocks: [Data?], originalCount: Int) async throws -> [Data] {
        guard !blocks.isEmpty else {
            throw ErasureCodingError.insufficientBlocks
        }
        guard originalCount <= blocks.count else {
            throw ErasureCodingError.insufficientBlocks
        }
        guard originalCount > 0 else {
            return []
        }
        let missingOriginal = blocks[0..<originalCount].filter { $0 == nil }.count
        let mode = selectCoder(
            chunkCount: originalCount,
            lossRate: Double(missingOriginal) / Double(max(1, originalCount))
        )

        if let native = nativeDecode(blocks: blocks, originalCount: originalCount, mode: mode) {
            return native
        }
        throw ErasureCodingError.decodingFailed
    }

    // MARK: - Native Primary Path

    private func modeAndField(
        _ mode: ErasureCodingMode,
        chunkCount: Int
    ) -> (mode: Int32, field: Int32) {
        switch mode {
        case .reedSolomon(let field):
            return (0, field == .gf256 ? 0 : 1)
        case .raptorQ:
            return (1, chunkCount <= 255 ? 0 : 1)
        }
    }

    private func nativeEncode(
        data: [Data],
        redundancy: Double,
        mode: ErasureCodingMode
    ) -> [Data]? {
        guard !data.isEmpty else { return [] }
        var offsets = [UInt32](repeating: 0, count: data.count + 1)
        var flattened = Data()
        flattened.reserveCapacity(data.reduce(0) { $0 + $1.count })
        var cursor: UInt32 = 0
        for (i, block) in data.enumerated() {
            offsets[i] = cursor
            flattened.append(block)
            cursor += UInt32(block.count)
        }
        offsets[data.count] = cursor

        let parityCount = max(0, Int(Double(data.count) * max(0, redundancy)))
        let outBlockCapacity = data.count + parityCount
        var outOffsets = [UInt32](repeating: 0, count: outBlockCapacity + 1)
        var outBlockCount = Int32(outBlockCapacity)
        var outDataSize = UInt32(max(flattened.count + parityCount * 256, 1))
        var outData = Data(count: Int(outDataSize))
        let outDataCapacity = UInt32(outData.count)
        let modeField = modeAndField(mode, chunkCount: data.count)

        let rc = flattened.withUnsafeBytes { inRaw in
            outData.withUnsafeMutableBytes { outRaw in
                offsets.withUnsafeBufferPointer { inOff in
                    outOffsets.withUnsafeMutableBufferPointer { outOff in
                        aether_erasure_encode_with_mode(
                            inRaw.bindMemory(to: UInt8.self).baseAddress,
                            inOff.baseAddress,
                            Int32(data.count),
                            redundancy,
                            modeField.mode,
                            modeField.field,
                            outRaw.bindMemory(to: UInt8.self).baseAddress,
                            outDataCapacity,
                            outOff.baseAddress,
                            Int32(outBlockCapacity),
                            &outBlockCount,
                            &outDataSize
                        )
                    }
                }
            }
        }
        guard rc == 0 else { return nil }

        var result: [Data] = []
        result.reserveCapacity(Int(outBlockCount))
        for i in 0..<Int(outBlockCount) {
            let begin = Int(outOffsets[i])
            let end = Int(outOffsets[i + 1])
            if end >= begin && end <= outData.count {
                result.append(outData.subdata(in: begin..<end))
            }
        }
        return result
    }

    private func nativeDecode(
        blocks: [Data?],
        originalCount: Int,
        mode: ErasureCodingMode
    ) -> [Data]? {
        guard originalCount > 0, blocks.count >= originalCount else { return nil }
        func medianSize(_ values: [Int]) -> Int {
            guard !values.isEmpty else { return 0 }
            let sorted = values.sorted()
            return sorted[sorted.count / 2]
        }

        var observedSystematicSizes: [Int] = []
        observedSystematicSizes.reserveCapacity(originalCount)
        var observedParitySizes: [Int] = []
        observedParitySizes.reserveCapacity(max(0, blocks.count - originalCount))
        for i in 0..<blocks.count {
            guard let block = blocks[i] else { continue }
            if i < originalCount {
                observedSystematicSizes.append(block.count)
            } else {
                observedParitySizes.append(block.count)
            }
        }

        let defaultSystematicSize = max(
            medianSize(observedSystematicSizes),
            observedParitySizes.max() ?? 0
        )
        let defaultParitySize = max(observedParitySizes.max() ?? 0, defaultSystematicSize)

        var flattened = Data()
        var offsets = [UInt32](repeating: 0, count: blocks.count + 1)
        var present = [UInt8](repeating: 0, count: blocks.count)
        var cursor: UInt32 = 0
        for i in 0..<blocks.count {
            offsets[i] = cursor
            if let block = blocks[i] {
                present[i] = 1
                flattened.append(block)
                cursor += UInt32(block.count)
                continue
            }
            let padSize = i < originalCount ? defaultSystematicSize : defaultParitySize
            if padSize > 0 {
                flattened.append(Data(repeating: 0, count: padSize))
                cursor += UInt32(padSize)
            }
        }
        offsets[blocks.count] = cursor

        var outOffsets = [UInt32](repeating: 0, count: originalCount + 1)
        var outBlockCount = Int32(originalCount)
        var outDataSize = UInt32(max(flattened.count, 1))
        var outData = Data(count: Int(outDataSize))
        let outDataCapacity = UInt32(outData.count)
        let modeField = modeAndField(mode, chunkCount: originalCount)

        let rc = flattened.withUnsafeBytes { inRaw in
            outData.withUnsafeMutableBytes { outRaw in
                offsets.withUnsafeBufferPointer { inOff in
                    present.withUnsafeBufferPointer { presentPtr in
                        outOffsets.withUnsafeMutableBufferPointer { outOff in
                            aether_erasure_decode_systematic_with_mode(
                                inRaw.bindMemory(to: UInt8.self).baseAddress,
                                inOff.baseAddress,
                                presentPtr.baseAddress,
                                Int32(blocks.count),
                                Int32(originalCount),
                                modeField.mode,
                                modeField.field,
                                outRaw.bindMemory(to: UInt8.self).baseAddress,
                                outDataCapacity,
                                outOff.baseAddress,
                                Int32(originalCount),
                                &outBlockCount,
                                &outDataSize
                            )
                        }
                    }
                }
            }
        }
        guard rc == 0 else { return nil }

        var result: [Data] = []
        result.reserveCapacity(Int(outBlockCount))
        for i in 0..<Int(outBlockCount) {
            let begin = Int(outOffsets[i])
            let end = Int(outOffsets[i + 1])
            if end >= begin && end <= outData.count {
                result.append(outData.subdata(in: begin..<end))
            }
        }
        return result
    }
}

/// Erasure coding error.
public enum ErasureCodingError: Error, Sendable {
    case decodingFailed
    case insufficientBlocks
    case invalidRedundancy
}
