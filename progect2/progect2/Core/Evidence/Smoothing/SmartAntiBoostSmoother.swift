// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// SmartAntiBoostSmoother.swift
// Aether3D
//
// PR3 - Smart Anti-Boost Dual Channel Smoother
// Conditional anti-boost, not punishment
//

import Foundation
#if canImport(CAetherNativeBridge)
import CAetherNativeBridge
#endif

/// Smart Anti-Boost Dual Channel Smoother
///
/// DESIGN:
/// - Anti-boost ONLY when jump exceeds jitterBand (suspicious)
/// - Normal improvement: faster recovery (configurable)
/// - K consecutive invalid: force worst-case fallback
/// - Hysteresis: prevent oscillation at boundaries
public final class SmartAntiBoostSmoother: @unchecked Sendable {

    // ═══════════════════════════════════════════════════════════════════════
    // MARK: - State
    // ═══════════════════════════════════════════════════════════════════════

    private let config: SmootherConfig
    private let nativeHandle: OpaquePointer?

    /// Previous smoothed value (for change detection)
    private var previousSmoothed: Double?

    /// Consecutive invalid frame counter
    private var consecutiveInvalidCount: Int = 0

    // ═══════════════════════════════════════════════════════════════════════
    // MARK: - Initialization
    // ═══════════════════════════════════════════════════════════════════════

    /// Initialize smoother
    ///
    /// - Parameters:
    ///   - windowSize: Window size for median computation
    ///   - config: Configuration (default if not specified)
    public init(windowSize: Int = 5, config: SmootherConfig = .default) {
        let resolvedWindowSize = max(1, windowSize)
        self.config = config
#if canImport(CAetherNativeBridge)
        var nativeConfig = aether_smart_smoother_config_t(
            window_size: Int32(resolvedWindowSize),
            jitter_band: config.jitterBand,
            anti_boost_factor: config.antiBoostFactor,
            normal_improve_factor: config.normalImproveFactor,
            degrade_factor: config.degradeFactor,
            max_consecutive_invalid: Int32(max(1, config.maxConsecutiveInvalid)),
            worst_case_fallback: config.worstCaseFallback,
            capture_mode: 0
        )
        var handle: OpaquePointer?
        let rc = aether_smart_smoother_create(&nativeConfig, &handle)
        self.nativeHandle = (rc == 0) ? handle : nil
#else
        self.nativeHandle = nil
#endif
    }

    deinit {
#if canImport(CAetherNativeBridge)
        if let nativeHandle {
            _ = aether_smart_smoother_destroy(nativeHandle)
        }
#endif
    }

    // ═══════════════════════════════════════════════════════════════════════
    // MARK: - Core API
    // ═══════════════════════════════════════════════════════════════════════

    /// Add value and return smoothed result
    ///
    /// BEHAVIOR:
    /// 1. If invalid (NaN/Inf): increment invalidCount, return previous or fallback
    /// 2. If valid: reset invalidCount, add to history, compute smoothed
    /// 3. If consecutiveInvalidCount >= maxConsecutiveInvalid: return worst-case
    ///
    /// - Parameter value: Input value
    /// - Returns: Smoothed value
    public func addAndSmooth(_ value: Double) -> Double {
#if canImport(CAetherNativeBridge)
        if let nativeHandle {
            var smoothed = 0.0
            if aether_smart_smoother_add(nativeHandle, value, &smoothed) == 0 {
                return smoothed
            }
        }
#endif
        // Fail-closed bridge behavior when native kernel is unavailable.
        guard value.isFinite else {
            return handleInvalidInput()
        }
        consecutiveInvalidCount = 0
        previousSmoothed = value
        return value
    }

    // ═══════════════════════════════════════════════════════════════════════
    // MARK: - Internal Logic
    // ═══════════════════════════════════════════════════════════════════════

    private func handleInvalidInput() -> Double {
        consecutiveInvalidCount += 1

        // If too many consecutive invalid, force worst-case
        if consecutiveInvalidCount >= config.maxConsecutiveInvalid {
            previousSmoothed = config.worstCaseFallback
            return config.worstCaseFallback
        }

        // Otherwise return previous smoothed (or worst-case if none)
        return previousSmoothed ?? config.worstCaseFallback
    }

    /// Reset all state
    public func reset() {
#if canImport(CAetherNativeBridge)
        if let nativeHandle {
            _ = aether_smart_smoother_reset(nativeHandle)
        }
#endif
        previousSmoothed = nil
        consecutiveInvalidCount = 0
    }
}
