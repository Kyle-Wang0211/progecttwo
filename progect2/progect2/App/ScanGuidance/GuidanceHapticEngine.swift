//
// GuidanceHapticEngine.swift
// Aether3D
//
// PR#7 Scan Guidance UI — Haptic Feedback Engine
// Apple-platform only (CoreHaptics)
// Phase 4: Full implementation
//

import Foundation
import Aether3DCore
#if canImport(CAetherNativeBridge)
import CAetherNativeBridge
#endif

#if canImport(CoreHaptics)
import CoreHaptics
#endif

#if canImport(UIKit)
import UIKit
#endif

/// Haptic feedback patterns for scan guidance
@MainActor
public final class GuidanceHapticEngine {
    
    public enum HapticPattern: String, CaseIterable {
        case motionTooFast
        case blurDetected
        case exposureAbnormal
        case scanComplete
        /// Layer 7.4: Directional guidance — rhythmic pulses whose intensity
        /// encodes proximity to uncovered areas.  Closer = stronger pulses.
        case directionalGuidance
    }
    
    /// nonisolated(unsafe) because policyHandle is accessed in deinit (which is
    /// always nonisolated). It's set once in init and destroyed in deinit — no
    /// concurrent access risk.
    nonisolated(unsafe) private let policyHandle: OpaquePointer?
    
    #if canImport(CoreHaptics)
    /// CoreHaptics engine
    private var hapticEngine: CHHapticEngine?
    #endif
    
    public init() {
        #if canImport(CAetherNativeBridge)
        var config = aether_haptic_policy_config_t(
            debounce_seconds: ScanGuidanceConstants.hapticDebounceS,
            max_per_minute: Int32(ScanGuidanceConstants.hapticMaxPerMinute)
        )
        var handle: OpaquePointer?
        if aether_haptic_policy_create(&config, &handle) == 0 {
            policyHandle = handle
        } else {
            policyHandle = nil
        }
        #else
        policyHandle = nil
        #endif

        #if canImport(CoreHaptics)
        initializeHapticEngine()
        #endif
    }

    deinit {
        #if canImport(CAetherNativeBridge)
        if let policyHandle {
            _ = aether_haptic_policy_destroy(policyHandle)
        }
        #endif
    }
    
    #if canImport(CoreHaptics)
    /// Layer 4.7: Restart attempt counter to prevent recursive restart loops.
    /// The stoppedHandler was calling initializeHapticEngine() unconditionally,
    /// which could fail repeatedly and cause an infinite recursion if the
    /// device's haptic hardware is permanently unavailable.
    ///
    /// Layer 4.7b: After max consecutive failures, haptics are paused for a
    /// cooldown period (30s) then retried. This prevents permanent disable
    /// from transient audio-session conflicts while still avoiding spin loops.
    private var hapticRestartAttempts: Int = 0
    private var lastHapticFailureTime: TimeInterval = 0
    private static let maxHapticRestartAttempts: Int = 3
    private static let hapticRetryCooldownS: TimeInterval = 30.0

    /// Initialize CoreHaptics engine with restart backoff + cooldown retry
    private func initializeHapticEngine() {
        if hapticRestartAttempts >= Self.maxHapticRestartAttempts {
            // Max attempts exceeded — check if cooldown has elapsed.
            let now = ProcessInfo.processInfo.systemUptime
            guard now - lastHapticFailureTime >= Self.hapticRetryCooldownS else {
                // Still in cooldown — don't retry yet.
                hapticEngine = nil
                return
            }
            // Cooldown elapsed — reset counter and allow one more round of retries.
            hapticRestartAttempts = 0
        }
        hapticRestartAttempts += 1

        do {
            hapticEngine = try CHHapticEngine()
            hapticEngine?.stoppedHandler = { [weak self] _ in
                // Engine stopped — retry with exponential backoff.
                // Uses Task { @MainActor } instead of DispatchQueue.main.asyncAfter
                // to avoid Swift concurrency data race warnings on `self`.
                let attempt = self?.hapticRestartAttempts ?? 0
                let delay = min(10.0, pow(2.0, Double(attempt)))
                Task { @MainActor in
                    try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                    self?.initializeHapticEngine()
                }
            }
            try hapticEngine?.start()
            hapticRestartAttempts = 0  // Reset on success
        } catch {
            // Haptic engine initialization failed — record failure time for cooldown.
            hapticEngine = nil
            lastHapticFailureTime = ProcessInfo.processInfo.systemUptime
        }
    }
    #endif
    
    /// Fire haptic pattern with debounce and rate limiting
    ///
    /// - Parameters:
    ///   - pattern: Haptic pattern to fire
    ///   - timestamp: Current timestamp
    ///   - toastPresenter: Optional toast presenter for message display
    /// - Returns: true if haptic was fired, false if suppressed
    @MainActor
    public func fire(
        pattern: HapticPattern,
        timestamp: TimeInterval,
        toastPresenter: GuidanceToastPresenter?
    ) -> Bool {
        guard shouldFire(pattern: pattern, at: timestamp) else {
            return false
        }

        // Fire haptic
        fireHapticPattern(pattern)
        
        // Show toast message
        toastPresenter?.show(message: toastMessage(for: pattern))
        
        return true
    }
    
    /// Fire completion haptic (scan complete)
    @MainActor
    public func fireCompletion() {
        fireHapticPattern(.scanComplete)
    }

    /// Layer 7.4: Fire directional guidance haptic.
    ///
    /// Produces rhythmic pulses whose intensity encodes how close the
    /// camera is pointing to an uncovered region.  The user "feels" which
    /// direction to scan next — stronger pulses mean "almost there".
    ///
    /// - Parameters:
    ///   - proximity: [0,1] — 0 = uncovered area far away, 1 = directly ahead.
    ///   - timestamp: current time for debounce.
    ///   - toastPresenter: optional toast presenter.
    /// - Returns: true if fired.
    @MainActor
    public func fireDirectionalGuidance(
        proximity: Double,
        timestamp: TimeInterval,
        toastPresenter: GuidanceToastPresenter?
    ) -> Bool {
        guard shouldFire(pattern: .directionalGuidance, at: timestamp) else {
            return false
        }

        #if canImport(CoreHaptics)
        guard let engine = hapticEngine else { return false }

        do {
            // Intensity proportional to proximity: closer = stronger
            let intensity = Float(max(0.2, min(1.0, proximity)))
            // Pulse rate: closer = faster (2 pulses at high proximity, 1 at low)
            let pulseCount = proximity > 0.6 ? 2 : 1
            var events: [CHHapticEvent] = []
            for i in 0..<pulseCount {
                let param = CHHapticEventParameter(
                    parameterID: .hapticIntensity,
                    value: intensity
                )
                let sharpness = CHHapticEventParameter(
                    parameterID: .hapticSharpness,
                    value: Float(proximity) * 0.8
                )
                let event = CHHapticEvent(
                    eventType: .hapticTransient,
                    parameters: [param, sharpness],
                    relativeTime: Double(i) * 0.12
                )
                events.append(event)
            }
            let pattern = try CHHapticPattern(events: events, parameters: [])
            let player = try engine.makePlayer(with: pattern)
            try player.start(atTime: 0)
        } catch {
            // Haptic playback failed, continue silently
        }
        #endif

        return true
    }
    
    /// Check if haptic should fire (debounce + rate limit)
    ///
    /// - Parameters:
    ///   - pattern: Pattern to check
    ///   - time: Current timestamp
    /// - Returns: true if haptic should fire
    @MainActor
    internal func shouldFire(pattern: HapticPattern, at time: TimeInterval) -> Bool {
        #if canImport(CAetherNativeBridge)
        guard let policyHandle else {
            return false
        }
        var shouldFire: Int32 = 0
        let rc = aether_haptic_policy_should_fire(
            policyHandle,
            pattern.nativeCode,
            time,
            &shouldFire
        )
        return rc == 0 && shouldFire != 0
        #else
        return false
        #endif
    }
    
    /// Get toast message for pattern
    private func toastMessage(for pattern: HapticPattern) -> String {
        switch pattern {
        case .motionTooFast:
            return "请您放慢移动速度"
        case .blurDetected:
            return "请您保持手机稳定"
        case .exposureAbnormal:
            return "请您调整光线环境"
        case .scanComplete:
            return "扫描完成！"
        case .directionalGuidance:
            return "请朝振动方向继续扫描"
        }
    }
    
    /// Fire haptic pattern
    private func fireHapticPattern(_ pattern: HapticPattern) {
        #if canImport(CoreHaptics)
        // Lazy retry: if engine is nil and cooldown has elapsed, attempt reinitialization.
        // This recovers from transient failures (audio session conflict, phone call end)
        // without spinning on every frame — the cooldown gate limits retry frequency.
        if hapticEngine == nil {
            let now = ProcessInfo.processInfo.systemUptime
            if now - lastHapticFailureTime >= Self.hapticRetryCooldownS {
                initializeHapticEngine()
            }
        }
        guard let engine = hapticEngine else {
            // Fallback to UINotificationFeedbackGenerator if CoreHaptics unavailable
            #if os(iOS)
            let generator = UINotificationFeedbackGenerator()
            generator.notificationOccurred(.warning)
            #endif
            return
        }
        
        do {
            guard let hapticPattern = createHapticPattern(for: pattern) else { return }
            let player = try engine.makePlayer(with: hapticPattern)
            try player.start(atTime: 0)
        } catch {
            // Haptic playback failed, continue silently
        }
        #else
        // No haptic support on this platform
        #endif
    }
    
    #if canImport(CoreHaptics)
    /// Create CHHapticPattern for pattern type
    ///
    /// Layer 4.6: Changed from try! to do/try/catch — try! caused fatal crashes
    /// in production when CHHapticPattern initialization failed (e.g., on
    /// devices without haptic hardware or in certain audio session states).
    private func createHapticPattern(for pattern: HapticPattern) -> CHHapticPattern? {
        do {
            switch pattern {
            case .motionTooFast:
                // Sharp double tap
                let event1 = CHHapticEvent(eventType: .hapticTransient, parameters: [], relativeTime: 0)
                let event2 = CHHapticEvent(eventType: .hapticTransient, parameters: [], relativeTime: 0.1)
                return try CHHapticPattern(events: [event1, event2], parameters: [])

            case .blurDetected:
                // Medium intensity single tap
                let intensity = CHHapticEventParameter(parameterID: .hapticIntensity, value: 0.7)
                let event = CHHapticEvent(eventType: .hapticTransient, parameters: [intensity], relativeTime: 0)
                return try CHHapticPattern(events: [event], parameters: [])

            case .exposureAbnormal:
                // Low intensity double tap
                let intensity = CHHapticEventParameter(parameterID: .hapticIntensity, value: 0.5)
                let event1 = CHHapticEvent(eventType: .hapticTransient, parameters: [intensity], relativeTime: 0)
                let event2 = CHHapticEvent(eventType: .hapticTransient, parameters: [intensity], relativeTime: 0.15)
                return try CHHapticPattern(events: [event1, event2], parameters: [])

            case .scanComplete:
                // Continuous haptic for completion
                let intensity = CHHapticEventParameter(parameterID: .hapticIntensity, value: 1.0)
                let sharpness = CHHapticEventParameter(parameterID: .hapticSharpness, value: 0.5)
                let event = CHHapticEvent(
                    eventType: .hapticContinuous,
                    parameters: [intensity, sharpness],
                    relativeTime: 0,
                    duration: 0.3
                )
                return try CHHapticPattern(events: [event], parameters: [])

            case .directionalGuidance:
                // Layer 7.4: Default directional pattern (mid-intensity single tap).
                // The dedicated fireDirectionalGuidance() method builds custom
                // intensity/rate patterns; this is the fallback for the generic fire() path.
                let intensity = CHHapticEventParameter(parameterID: .hapticIntensity, value: 0.6)
                let sharpness = CHHapticEventParameter(parameterID: .hapticSharpness, value: 0.4)
                let event = CHHapticEvent(
                    eventType: .hapticTransient,
                    parameters: [intensity, sharpness],
                    relativeTime: 0
                )
                return try CHHapticPattern(events: [event], parameters: [])
            }
        } catch {
            return nil
        }
    }
    #endif
}

private extension GuidanceHapticEngine.HapticPattern {
    var nativeCode: Int32 {
        switch self {
        case .motionTooFast:
            return Int32(AETHER_HAPTIC_PATTERN_MOTION_TOO_FAST)
        case .blurDetected:
            return Int32(AETHER_HAPTIC_PATTERN_BLUR_DETECTED)
        case .exposureAbnormal:
            return Int32(AETHER_HAPTIC_PATTERN_EXPOSURE_ABNORMAL)
        case .scanComplete:
            return Int32(AETHER_HAPTIC_PATTERN_SCAN_COMPLETE)
        case .directionalGuidance:
            // Layer 7.4: Directional guidance reuses scanComplete code for
            // the native debounce policy (same rate limit characteristics).
            return Int32(AETHER_HAPTIC_PATTERN_SCAN_COMPLETE)
        }
    }
}
