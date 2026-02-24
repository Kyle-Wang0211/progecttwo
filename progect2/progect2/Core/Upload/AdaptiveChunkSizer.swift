// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

// ============================================================================
// CONSTITUTIONAL CONTRACT - DO NOT EDIT WITHOUT RFC
// Contract Version: PR3-API-1.0
// Module: Upload Infrastructure - Adaptive Chunk Sizer
// Cross-Platform: macOS + Linux (pure Foundation)
// ============================================================================

import Foundation
import CAetherNativeBridge

/// Chunk sizing strategy enumeration.
public enum ChunkSizingStrategy: String, Codable {
    case fixed = "fixed"
    case adaptive = "adaptive"
    case aggressive = "aggressive"
}

/// Adaptive chunk sizer configuration.
public struct AdaptiveChunkConfig: Codable, Equatable {
    public let strategy: ChunkSizingStrategy
    public let minChunkSize: Int
    public let maxChunkSize: Int
    public let targetUploadTime: TimeInterval

    public init(
        strategy: ChunkSizingStrategy = .adaptive,
        minChunkSize: Int = UploadConstants.CHUNK_SIZE_MIN_BYTES,
        maxChunkSize: Int = UploadConstants.CHUNK_SIZE_MAX_BYTES,
        targetUploadTime: TimeInterval = 10.0
    ) {
        self.strategy = strategy
        self.minChunkSize = minChunkSize
        self.maxChunkSize = maxChunkSize
        self.targetUploadTime = targetUploadTime
    }
}

/// Adaptive chunk sizer for network-aware chunk sizing.
public final class AdaptiveChunkSizer {

    private let config: AdaptiveChunkConfig
    private let speedMonitor: NetworkSpeedMonitor

    public init(config: AdaptiveChunkConfig = AdaptiveChunkConfig(), speedMonitor: NetworkSpeedMonitor) {
        self.config = config
        self.speedMonitor = speedMonitor
    }

    /// Calculate optimal chunk size based on current network conditions.
    public func calculateChunkSize() -> Int {
        let speedClassCode = nativeSpeedClassCode(speedMonitor.getSpeedClass())
        let recommended = speedMonitor.getRecommendedChunkSize()
        var outChunk: Int32 = Int32(recommended)
        let rc = aether_upload_calculate_chunk_size(
            nativeStrategyCode(config.strategy),
            speedClassCode,
            Int32(recommended),
            Int32(clamping: UploadConstants.CHUNK_SIZE_DEFAULT_BYTES),
            Int32(clamping: config.maxChunkSize),
            &outChunk
        )
        if rc == 0 {
            return Int(outChunk)
        }
        return recommended
    }

    /// Calculate optimal chunk size for a specific file size.
    public func calculateChunkSize(forFileSize fileSize: Int64) -> Int {
        let baseSize = calculateChunkSize()
        var outChunk: Int32 = Int32(clamping: baseSize)
        let rc = aether_upload_calculate_chunk_size_for_file(
            Int32(clamping: baseSize),
            Int32(clamping: config.minChunkSize),
            fileSize,
            &outChunk
        )
        if rc == 0 {
            return Int(outChunk)
        }
        return baseSize
    }

    /// Get recommended parallel upload count.
    public func getRecommendedParallelCount() -> Int {
        return speedMonitor.getRecommendedParallelCount()
    }

    private func nativeStrategyCode(_ strategy: ChunkSizingStrategy) -> Int32 {
        switch strategy {
        case .fixed:
            return Int32(AETHER_UPLOAD_CHUNK_STRATEGY_FIXED)
        case .adaptive:
            return Int32(AETHER_UPLOAD_CHUNK_STRATEGY_ADAPTIVE)
        case .aggressive:
            return Int32(AETHER_UPLOAD_CHUNK_STRATEGY_AGGRESSIVE)
        }
    }

    private func nativeSpeedClassCode(_ speedClass: NetworkSpeedClass) -> Int32 {
        switch speedClass {
        case .slow:
            return Int32(AETHER_NETWORK_SPEED_CLASS_SLOW)
        case .normal:
            return Int32(AETHER_NETWORK_SPEED_CLASS_NORMAL)
        case .fast:
            return Int32(AETHER_NETWORK_SPEED_CLASS_FAST)
        case .ultrafast:
            return Int32(AETHER_NETWORK_SPEED_CLASS_ULTRAFAST)
        case .unknown:
            return Int32(AETHER_NETWORK_SPEED_CLASS_UNKNOWN)
        }
    }
}
