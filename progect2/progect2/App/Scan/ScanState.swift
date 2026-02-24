//
// ScanState.swift
// Aether3D
//
// PR#7 Scan Guidance UI — Scan Lifecycle State Machine
// Pure enum — Foundation only, no platform imports
//

import Foundation
#if canImport(CAetherNativeBridge)
import CAetherNativeBridge
#endif

/// Scan lifecycle state machine
///
/// State transitions:
///   initializing → ready → capturing ⇄ paused → finishing → completed
///   capturing → failed
///   paused → ready (cancel)
///   failed → ready (retry)
///
/// All transitions are validated via `allowedTransitions`.
/// ScanViewModel MUST use the validated `transition(to:)` method.
public enum ScanState: String, Sendable {
    case initializing   // ARKit session starting
    case ready          // Session ready, waiting for user tap
    case capturing      // Actively recording frames
    case paused         // User paused, can resume or stop
    case finishing      // Processing final data
    case completed      // Scan saved, ready to return home
    case failed         // Unrecoverable error

    private static let allStates: [ScanState] = [
        .initializing, .ready, .capturing, .paused, .finishing, .completed, .failed
    ]

    private var nativeCode: Int32 {
        switch self {
        case .initializing: return Int32(AETHER_SCAN_STATE_INITIALIZING)
        case .ready: return Int32(AETHER_SCAN_STATE_READY)
        case .capturing: return Int32(AETHER_SCAN_STATE_CAPTURING)
        case .paused: return Int32(AETHER_SCAN_STATE_PAUSED)
        case .finishing: return Int32(AETHER_SCAN_STATE_FINISHING)
        case .completed: return Int32(AETHER_SCAN_STATE_COMPLETED)
        case .failed: return Int32(AETHER_SCAN_STATE_FAILED)
        }
    }

    /// Valid transitions from this state
    public var allowedTransitions: Set<ScanState> {
        #if canImport(CAetherNativeBridge)
        var result = Set<ScanState>()
        for target in Self.allStates {
            var allowed: Int32 = 0
            let rc = aether_scan_state_can_transition(nativeCode, target.nativeCode, &allowed)
            if rc == 0 && allowed != 0 {
                result.insert(target)
            }
        }
        return result
        #else
        return []
        #endif
    }

    /// Whether scanning is actively in progress
    public var isActive: Bool {
        #if canImport(CAetherNativeBridge)
        var active: Int32 = 0
        return aether_scan_state_is_active(nativeCode, &active) == 0 && active != 0
        #else
        return false
        #endif
    }

    /// Whether the scan can be saved
    public var canFinish: Bool {
        #if canImport(CAetherNativeBridge)
        var canFinish: Int32 = 0
        return aether_scan_state_can_finish(nativeCode, &canFinish) == 0 && canFinish != 0
        #else
        return false
        #endif
    }
}
