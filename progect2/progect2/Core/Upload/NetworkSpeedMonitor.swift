// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

// ============================================================================
// CONSTITUTIONAL CONTRACT - DO NOT EDIT WITHOUT RFC
// Contract Version: PR3-API-1.0
// Module: Upload Infrastructure - Network Speed Monitor
// Cross-Platform: macOS + Linux (pure Foundation)
// ============================================================================

import Foundation
import CAetherNativeBridge

// ============================================================================
// MARK: - Network Speed Classification
// ============================================================================

/// Network speed classification for adaptive chunk sizing.
/// Used to categorize current network conditions.
public enum NetworkSpeedClass: String, Codable, CaseIterable {
    /// < 3 Mbps - Typical 3G, poor WiFi
    case slow = "slow"

    /// 3-30 Mbps - Typical 4G, good WiFi
    case normal = "normal"

    /// 30-200 Mbps - 5G, fiber
    case fast = "fast"

    /// >= 200 Mbps - Excellent connectivity
    case ultrafast = "ultrafast"

    /// Insufficient data for classification
    case unknown = "unknown"

    /// Human-readable description
    public var displayName: String {
        switch self {
        case .slow: return "Slow (<3 Mbps)"
        case .normal: return "Normal (3-30 Mbps)"
        case .fast: return "Fast (30-200 Mbps)"
        case .ultrafast: return "Ultra Fast (>=200 Mbps)"
        case .unknown: return "Unknown"
        }
    }

    /// Recommended chunk size for this speed class
    /// [P1-3修正] fast 档原推荐 10MB 超过服务端 5MB 上限，已改为 4MB
    public var recommendedChunkSize: Int {
        switch self {
        case .slow:
            return UploadConstants.CHUNK_SIZE_MIN_BYTES    // 256KB
        case .normal:
            return UploadConstants.CHUNK_SIZE_DEFAULT_BYTES // 2MB
        case .fast:
            return 4 * 1024 * 1024  // 4MB (服务端上限5MB, 留1MB安全余量)
        case .ultrafast:
            return UploadConstants.CHUNK_SIZE_MAX_BYTES     // 5MB (= 服务端上限)
        case .unknown:
            return UploadConstants.CHUNK_SIZE_DEFAULT_BYTES // 2MB
        }
    }

    /// Recommended parallel upload count
    public var recommendedParallelCount: Int {
        switch self {
        case .slow:
            return 2
        case .normal:
            return 3
        case .fast, .ultrafast:
            return UploadConstants.MAX_PARALLEL_CHUNK_UPLOADS
        case .unknown:
            return 2
        }
    }

    /// Whether this class allows aggressive optimization
    public var allowsAggressiveOptimization: Bool {
        switch self {
        case .fast, .ultrafast:
            return true
        default:
            return false
        }
    }
}

// ============================================================================
// MARK: - Speed Sample
// ============================================================================

/// Individual speed measurement sample.
/// Immutable value type for thread safety.
public struct SpeedSample: Codable, Equatable {
    /// Bytes transferred in this sample
    public let bytesTransferred: Int64

    /// Duration of the transfer in seconds
    public let durationSeconds: TimeInterval

    /// Timestamp when sample was recorded
    public let timestamp: Date

    /// Calculated speed in bytes per second
    public var speedBps: Double {
        guard durationSeconds > 0 else { return 0 }
        return Double(bytesTransferred) / durationSeconds
    }

    /// Calculated speed in megabits per second (SI: 1 Mbps = 1,000,000 bps)
    /// [P1-1修正] 原代码用 1024² (MiB/s 换算), 与 UploadConstants 的 SI Mbps 不一致
    /// 网络行业标准: 1 Mbps = 10⁶ bps (IEEE 802, RFC, 运营商均用 SI)
    public var speedMbps: Double {
        return (speedBps * 8.0) / 1_000_000.0
    }

    /// Calculated speed in kilobytes per second
    public var speedKBps: Double {
        return speedBps / 1024.0
    }

    /// Initialize a new speed sample
    /// - Parameters:
    ///   - bytesTransferred: Number of bytes transferred
    ///   - durationSeconds: Time taken for transfer
    ///   - timestamp: When sample was recorded (defaults to now)
    public init(
        bytesTransferred: Int64,
        durationSeconds: TimeInterval,
        timestamp: Date = Date()
    ) {
        self.bytesTransferred = max(0, bytesTransferred)
        self.durationSeconds = max(0, durationSeconds)
        self.timestamp = timestamp
    }

    /// Check if sample is recent (within window)
    /// - Parameter window: Time window in seconds
    /// - Returns: True if sample is within window
    public func isRecent(window: TimeInterval = UploadConstants.NETWORK_SPEED_WINDOW_SECONDS) -> Bool {
        return Date().timeIntervalSince(timestamp) <= window
    }
}

// ============================================================================
// MARK: - Network Speed Monitor
// ============================================================================

/// Real-time network speed monitor with adaptive classification.
///
/// ## Thread Safety
/// All public methods are thread-safe, using a serial dispatch queue
/// for synchronization.
///
/// ## Usage
/// ```swift
/// let monitor = NetworkSpeedMonitor()
///
/// // Record samples from chunk uploads
/// monitor.recordSample(bytesTransferred: chunkSize, durationSeconds: elapsed)
///
/// // Get current classification
/// let speedClass = monitor.getSpeedClass()
/// let chunkSize = speedClass.recommendedChunkSize
/// ```
///
/// ## Cross-Platform
/// Uses only Foundation types. No Apple-specific frameworks.
public final class NetworkSpeedMonitor {

    // =========================================================================
    // MARK: - Properties
    // =========================================================================

    /// Thread synchronization queue
    private let queue = DispatchQueue(
        label: "com.app.upload.networkspeedmonitor",
        qos: .userInitiated
    )

    /// Recorded speed samples (debug/inspection only; decision logic in native core).
    private var samples: [SpeedSample] = []

    private let maxSamples: Int

    private let windowSeconds: TimeInterval

    private var _currentClass: NetworkSpeedClass = .unknown

    private var _currentSpeedMbps: Double = 0.0

    private var _currentSampleCount: Int = 0
    private var _currentReliable: Bool = false
    private var nativeEnabled = true
    private var nativeState = aether_network_speed_state_t()

    // =========================================================================
    // MARK: - Initialization
    // =========================================================================

    /// Initialize network speed monitor
    /// - Parameters:
    ///   - maxSamples: Maximum samples to retain (default: 20)
    ///   - windowSeconds: Sample validity window (default: 30s)
    public init(
        maxSamples: Int = UploadConstants.NETWORK_SPEED_MAX_SAMPLES,
        windowSeconds: TimeInterval = UploadConstants.NETWORK_SPEED_WINDOW_SECONDS
    ) {
        self.maxSamples = max(1, maxSamples)
        self.windowSeconds = max(1, windowSeconds)
        nativeEnabled = aether_network_speed_reset(
            &nativeState,
            Int32(self.maxSamples),
            self.windowSeconds
        ) == 0
    }

    // =========================================================================
    // MARK: - Public Methods
    // =========================================================================

    /// Record a speed measurement sample.
    /// - Parameters:
    ///   - bytesTransferred: Bytes transferred in this measurement
    ///   - durationSeconds: Time taken for the transfer
    /// - Note: Invalid samples (zero or negative values) are ignored.
    public func recordSample(bytesTransferred: Int64, durationSeconds: TimeInterval) {
        guard bytesTransferred > 0, durationSeconds > 0 else { return }

        let sample = SpeedSample(
            bytesTransferred: bytesTransferred,
            durationSeconds: durationSeconds
        )
        recordSample(sample)
    }

    /// Record a speed sample from a SpeedSample struct.
    /// - Parameter sample: The sample to record
    public func recordSample(_ sample: SpeedSample) {
        guard sample.bytesTransferred > 0, sample.durationSeconds > 0 else { return }
        queue.sync {
            samples.append(sample)
            pruneOldSamples()
            recordNativeSampleLocked(sample)
            refreshSnapshotLocked(now: sample.timestamp.timeIntervalSince1970)
        }
    }

    /// Get current speed classification.
    /// - Returns: Network speed class based on recent measurements
    public func getSpeedClass() -> NetworkSpeedClass {
        return queue.sync {
            refreshSnapshotLocked(now: Date().timeIntervalSince1970)
            return _currentClass
        }
    }

    /// Get current estimated speed in Mbps.
    /// - Returns: Speed in megabits per second
    public func getSpeedMbps() -> Double {
        return queue.sync {
            refreshSnapshotLocked(now: Date().timeIntervalSince1970)
            return _currentSpeedMbps
        }
    }

    /// Get current estimated speed in bytes per second.
    /// - Returns: Speed in bytes per second
    public func getSpeedBps() -> Double {
        return queue.sync {
            refreshSnapshotLocked(now: Date().timeIntervalSince1970)
            return (_currentSpeedMbps * 1_000_000.0) / 8.0
        }
    }

    /// Get recommended chunk size based on current network conditions.
    /// - Returns: Recommended chunk size in bytes
    public func getRecommendedChunkSize() -> Int {
        return queue.sync {
            refreshSnapshotLocked(now: Date().timeIntervalSince1970)
            guard nativeEnabled else {
                return UploadConstants.CHUNK_SIZE_DEFAULT_BYTES
            }
            var recommended: Int32 = Int32(UploadConstants.CHUNK_SIZE_DEFAULT_BYTES)
            let rc = aether_network_speed_recommended_chunk_size(
                nativeClassCode(_currentClass),
                Int32(UploadConstants.CHUNK_SIZE_MIN_BYTES),
                Int32(UploadConstants.CHUNK_SIZE_DEFAULT_BYTES),
                Int32(UploadConstants.CHUNK_SIZE_MAX_BYTES),
                &recommended
            )
            guard rc == 0 else {
                nativeEnabled = false
                return UploadConstants.CHUNK_SIZE_DEFAULT_BYTES
            }
            return Int(recommended)
        }
    }

    /// Get recommended parallel upload count.
    /// - Returns: Recommended number of parallel uploads
    public func getRecommendedParallelCount() -> Int {
        return queue.sync {
            refreshSnapshotLocked(now: Date().timeIntervalSince1970)
            guard nativeEnabled else {
                return 2
            }
            var recommended: Int32 = 2
            let rc = aether_network_speed_recommended_parallel_count(
                nativeClassCode(_currentClass),
                Int32(UploadConstants.MAX_PARALLEL_CHUNK_UPLOADS),
                &recommended
            )
            guard rc == 0 else {
                nativeEnabled = false
                return 2
            }
            return Int(recommended)
        }
    }

    /// Check if we have enough samples for reliable estimation.
    /// - Returns: True if estimation is statistically reliable
    public func hasReliableEstimate() -> Bool {
        return queue.sync {
            refreshSnapshotLocked(now: Date().timeIntervalSince1970)
            return _currentReliable
        }
    }

    /// Get current sample count.
    /// - Returns: Number of valid samples in window
    public func getSampleCount() -> Int {
        return queue.sync {
            refreshSnapshotLocked(now: Date().timeIntervalSince1970)
            return _currentSampleCount
        }
    }

    /// Get all recent samples (for debugging/display).
    /// - Returns: Copy of recent samples
    public func getRecentSamples() -> [SpeedSample] {
        return queue.sync {
            samples.filter { $0.isRecent(window: windowSeconds) }
        }
    }

    /// Reset all samples and classification.
    /// - Note: Useful when network conditions change dramatically (e.g., WiFi to cellular)
    public func reset() {
        queue.sync {
            samples.removeAll()
            _currentClass = .unknown
            _currentSpeedMbps = 0.0
            _currentSampleCount = 0
            _currentReliable = false
            nativeState = aether_network_speed_state_t()
            nativeEnabled = aether_network_speed_reset(
                &nativeState,
                Int32(maxSamples),
                windowSeconds
            ) == 0
        }
    }

    /// Force recalculation of speed (for testing).
    public func forceRecalculate() {
        queue.sync {
            refreshSnapshotLocked(now: Date().timeIntervalSince1970)
        }
    }

    // =========================================================================
    // MARK: - Statistics
    // =========================================================================

    /// Get speed statistics for recent samples.
    /// - Returns: Statistics tuple (min, max, avg, stddev) or nil if insufficient data
    public func getSpeedStatistics() -> (min: Double, max: Double, avg: Double, stddev: Double)? {
        return queue.sync {
            guard nativeEnabled else {
                return nil
            }
            var stats = aether_network_speed_statistics_t(
                min_mbps: 0,
                max_mbps: 0,
                avg_mbps: 0,
                stddev_mbps: 0,
                sample_count: 0
            )
            let now = Date().timeIntervalSince1970
            let rc = aether_network_speed_statistics(&nativeState, now, &stats)
            guard rc == 0, stats.sample_count >= 2 else {
                return nil
            }
            return (
                min: stats.min_mbps,
                max: stats.max_mbps,
                avg: stats.avg_mbps,
                stddev: stats.stddev_mbps
            )
        }
    }

    // =========================================================================
    // MARK: - Private Methods
    // =========================================================================

    /// Remove old samples outside the time window.
    /// Must be called within queue.sync block.
    private func pruneOldSamples() {
        let cutoff = Date().addingTimeInterval(-windowSeconds)
        samples = samples.filter { $0.timestamp > cutoff }

        // Also limit total samples
        if samples.count > maxSamples {
            samples = Array(samples.suffix(maxSamples))
        }
    }

    /// Refresh snapshot from native core.
    /// Must be called inside queue.sync.
    private func refreshSnapshotLocked(now: TimeInterval) {
        guard nativeEnabled else {
            _currentClass = .unknown
            _currentSpeedMbps = 0.0
            _currentSampleCount = 0
            _currentReliable = false
            return
        }

        var rawClass: Int32 = Int32(AETHER_NETWORK_SPEED_CLASS_UNKNOWN)
        var rawSpeedMbps: Double = 0
        var rawSampleCount: Int32 = 0
        var rawReliable: Int32 = 0
        let rc = aether_network_speed_snapshot(
            &nativeState,
            now,
            &rawClass,
            &rawSpeedMbps,
            &rawSampleCount,
            &rawReliable
        )
        guard rc == 0 else {
            nativeEnabled = false
            _currentClass = .unknown
            _currentSpeedMbps = 0.0
            _currentSampleCount = 0
            _currentReliable = false
            return
        }
        _currentClass = fromNativeClass(rawClass)
        _currentSpeedMbps = max(0, rawSpeedMbps.isFinite ? rawSpeedMbps : 0)
        _currentSampleCount = max(0, Int(rawSampleCount))
        _currentReliable = rawReliable != 0
    }

    private func recordNativeSampleLocked(_ sample: SpeedSample) {
        guard nativeEnabled else { return }
        let rcRecord = aether_network_speed_record_sample(
            &nativeState,
            sample.bytesTransferred,
            sample.durationSeconds,
            sample.timestamp.timeIntervalSince1970
        )
        if rcRecord != 0 {
            nativeEnabled = false
        }
    }

    private func fromNativeClass(_ raw: Int32) -> NetworkSpeedClass {
        switch raw {
        case Int32(AETHER_NETWORK_SPEED_CLASS_SLOW):
            return .slow
        case Int32(AETHER_NETWORK_SPEED_CLASS_NORMAL):
            return .normal
        case Int32(AETHER_NETWORK_SPEED_CLASS_FAST):
            return .fast
        case Int32(AETHER_NETWORK_SPEED_CLASS_ULTRAFAST):
            return .ultrafast
        default:
            return .unknown
        }
    }

    private func nativeClassCode(_ speedClass: NetworkSpeedClass) -> Int32 {
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

// ============================================================================
// MARK: - CustomStringConvertible
// ============================================================================

extension NetworkSpeedMonitor: CustomStringConvertible {
    public var description: String {
        let speedClass = getSpeedClass()
        let speedMbps = getSpeedMbps()
        let sampleCount = getSampleCount()
        return "NetworkSpeedMonitor(class: \(speedClass.rawValue), speed: \(String(format: "%.2f", speedMbps)) Mbps, samples: \(sampleCount))"
    }
}
