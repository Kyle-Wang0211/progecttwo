//
// ThermalQualityAdapter.swift
// Aether3D
//
// PR#7 Scan Guidance UI — Thermal Quality Adapter
// v8.0: Thin bridge over C++ ThermalQualityDecision engine.
// All algorithm logic (proactive escalation, cool-down, percentile analysis,
// hysteresis, pass mask) now lives in aether_cpp/src/quality/thermal_quality_decision.cpp.
// Swift layer: OS thermal state mapping + C API delegation only.
//

import Foundation
import CAetherNativeBridge

public final class ThermalQualityAdapter {

    /// Render quality tiers — Swift enum for WedgeGeometryGenerator.LODLevel interop.
    public enum RenderTier: Int, CaseIterable, Sendable {
        case nominal = 0
        case fair = 1
        case serious = 2
        case critical = 3

        public var lodLevel: WedgeGeometryGenerator.LODLevel {
            switch self {
            case .nominal:  return .full
            case .fair:     return .medium
            case .serious:  return .low
            case .critical: return .flat
            }
        }

        public var maxTriangles: Int {
            switch self {
            case .nominal:  return ScanGuidanceConstants.thermalNominalMaxTriangles
            case .fair:     return ScanGuidanceConstants.thermalFairMaxTriangles
            case .serious:  return ScanGuidanceConstants.thermalSeriousMaxTriangles
            case .critical: return ScanGuidanceConstants.thermalCriticalMaxTriangles
            }
        }

        public var targetFPS: Int {
            switch self {
            case .nominal:  return 60
            case .fair:     return 60
            case .serious:  return 30
            case .critical: return 24
            }
        }

        public var enableFlipAnimation: Bool { self.rawValue <= 1 }
        public var enableRipple: Bool { self.rawValue <= 1 }
        public var enableMetallicBRDF: Bool { self.rawValue <= 1 }
        public var enableHaptics: Bool { self.rawValue <= 2 }
    }

    /// Current render tier — read from C++ engine.
    public var currentTier: RenderTier {
        var state = aether_thermal_quality_state_t()
        aether_thermal_quality_state(handle, &state)
        return RenderTier(rawValue: Int(state.current_tier)) ?? .nominal
    }

    /// Pass mask bitmask from C++ engine.
    public var passMask: UInt32 {
        var state = aether_thermal_quality_state_t()
        aether_thermal_quality_state(handle, &state)
        return state.pass_mask
    }

    private var handle: OpaquePointer?

    public init() {
        var config = aether_thermal_quality_config_t(
            hysteresis_s: Float(ScanGuidanceConstants.thermalHysteresisS),
            overshoot_ratio: Float(ScanGuidanceConstants.frameBudgetOvershootRatio),
            window_frames: Int32(ScanGuidanceConstants.frameBudgetWindowFrames),
            proactive_threshold: Float(ScanGuidanceConstants.proactiveFairThresholdRatio),
            cooldown_threshold: Float(ScanGuidanceConstants.coolDownThresholdRatio),
            cooldown_multiplier: Float(ScanGuidanceConstants.coolDownHysteresisMultiplier),
            tier_max_triangles: (
                Int32(ScanGuidanceConstants.thermalNominalMaxTriangles),
                Int32(ScanGuidanceConstants.thermalFairMaxTriangles),
                Int32(ScanGuidanceConstants.thermalSeriousMaxTriangles),
                Int32(ScanGuidanceConstants.thermalCriticalMaxTriangles)
            ),
            tier_target_fps: (60, 60, 30, 24)
        )
        aether_thermal_quality_create(&config, &handle)
    }

    deinit {
        aether_thermal_quality_destroy(handle)
    }

    #if os(iOS) || os(macOS)
    public func updateThermalState(_ state: ProcessInfo.ThermalState) {
        let osLevel: Int32
        switch state {
        case .nominal:  osLevel = 0
        case .fair:     osLevel = 1
        case .serious:  osLevel = 2
        case .critical: osLevel = 3
        @unknown default: osLevel = 1
        }
        aether_thermal_quality_update_os(handle, osLevel, ProcessInfo.processInfo.systemUptime)
    }
    #endif

    public func updateFrameTiming(gpuDurationMs: Double) {
        aether_thermal_quality_update_frame(handle, Float(gpuDurationMs), ProcessInfo.processInfo.systemUptime)
    }

    public func evaluateProactiveThermal() {
        aether_thermal_quality_evaluate(handle, ProcessInfo.processInfo.systemUptime)
    }

    public func evaluateCoolDown() {
        // C++ evaluate() handles both proactive and cool-down in one call.
    }

    public func forceRenderTier(_ tier: RenderTier) {
        aether_thermal_quality_force_tier(handle, Int32(tier.rawValue), ProcessInfo.processInfo.systemUptime)
    }
}
