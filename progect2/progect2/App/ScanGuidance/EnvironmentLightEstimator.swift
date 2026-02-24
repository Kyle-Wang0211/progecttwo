//
// EnvironmentLightEstimator.swift
// Aether3D
//
// PR#7 Scan Guidance UI — Environment Light Estimator
// Apple-platform only (ARKit)
// Phase 3: Full implementation
//

import Foundation
#if canImport(simd)
import simd
#endif
#if canImport(CAetherNativeBridge)
import CAetherNativeBridge
#endif

#if canImport(ARKit)
import ARKit
#endif

#if canImport(Vision)
import Vision
#endif

/// Light estimation tier
public enum EstimationTier: Int, Sendable {
    case arkit = 0
    case vision = 1
    case fallback = 2
}

/// Light state for rendering
public struct LightState: Sendable {
    public let direction: SIMD3<Float>
    public let intensity: Float
    public let shCoeffs: [SIMD3<Float>]  // 9 coefficients for L2 SH
    public let tier: EstimationTier
    
    public init(
        direction: SIMD3<Float>,
        intensity: Float,
        shCoeffs: [SIMD3<Float>],
        tier: EstimationTier
    ) {
        self.direction = direction
        self.intensity = intensity
        self.shCoeffs = shCoeffs
        self.tier = tier
    }
}

public final class EnvironmentLightEstimator {
    
    /// Fallback light direction (upward)
    public static let fallbackDirection = SIMD3<Float>(0.0, 1.0, 0.0)
    
    /// Fallback light intensity
    public static let fallbackIntensity: Float = 1.0

    #if canImport(CAetherNativeBridge)
    private let nativeHandle: OpaquePointer?
    #endif
    
    public init() {
        #if canImport(CAetherNativeBridge)
        var config = aether_light_estimator_config_t(
            fallback_direction: (
                Self.fallbackDirection.x,
                Self.fallbackDirection.y,
                Self.fallbackDirection.z
            ),
            fallback_intensity: Self.fallbackIntensity,
            min_intensity: 0.05,
            max_intensity: 4.0,
            rise_alpha: 0.35,
            fall_alpha: 0.12,
            direction_alpha: 0.20,
            sh_alpha: 0.18,
            missing_decay_per_s: 2.5,
            max_missing_hold_s: 0.6
        )
        var handle: OpaquePointer?
        if aether_light_estimator_create(&config, &handle) == 0 {
            nativeHandle = handle
        } else {
            nativeHandle = nil
        }
        #endif
    }

    deinit {
        #if canImport(CAetherNativeBridge)
        if let nativeHandle {
            _ = aether_light_estimator_destroy(nativeHandle)
        }
        #endif
    }
    
    /// Update light estimation with 3-tier fallback
    ///
    /// Tier 1: ARKit light estimate (if available)
    /// Tier 2: Vision framework (if ARKit unavailable)
    /// Tier 3: Fallback (default upward light)
    ///
    /// - Parameters:
    ///   - lightEstimate: ARKit ARLightEstimate (optional)
    ///   - cameraImage: Camera image for Vision framework (optional)
    ///   - timestamp: Current timestamp
    /// - Returns: LightState with direction, intensity, SH coefficients, and tier
    public func update(
        lightEstimate: Any?,
        cameraImage: Any?,
        timestamp: TimeInterval
    ) -> LightState {
        #if canImport(CAetherNativeBridge)
        if let nativeHandle {
            var state = aether_light_state_t()
            let rc: Int32
            if var observation = makeNativeObservation(lightEstimate: lightEstimate, cameraImage: cameraImage) {
                rc = aether_light_estimator_step(nativeHandle, &observation, timestamp, &state)
            } else {
                rc = aether_light_estimator_step(nativeHandle, nil, timestamp, &state)
            }
            if rc == 0 {
                return makeLightState(from: state)
            }
        }
        #endif

        // Tier 1: Try ARKit light estimate
        #if canImport(ARKit)
        if let arkitEstimate = lightEstimate as? ARLightEstimate {
            return extractARKitLight(estimate: arkitEstimate)
        }
        #endif
        
        // Tier 2: Try Vision framework
        #if canImport(Vision)
        if let image = cameraImage {
            if let visionLight = extractVisionLight(image: image) {
                return visionLight
            }
        }
        #endif
        
        // Tier 3: Fallback
        return createFallbackLight()
    }

    #if canImport(CAetherNativeBridge)
    private func makeNativeObservation(
        lightEstimate: Any?,
        cameraImage: Any?
    ) -> aether_light_observation_t? {
        #if canImport(ARKit)
        if let arkitEstimate = lightEstimate as? ARLightEstimate {
            var observation = aether_light_observation_t()
            observation.source_tier = Int32(EstimationTier.arkit.rawValue)
            observation.has_direction = 0
            observation.has_sh = 0
            observation.intensity = max(0.0, Float(arkitEstimate.ambientIntensity / 1000.0))
            return observation
        }
        #endif

        #if canImport(Vision)
        if let image = cameraImage, let visionLight = extractVisionLight(image: image) {
            var observation = aether_light_observation_t()
            observation.source_tier = Int32(visionLight.tier.rawValue)
            observation.has_direction = 1
            observation.has_sh = 1
            observation.direction.0 = visionLight.direction.x
            observation.direction.1 = visionLight.direction.y
            observation.direction.2 = visionLight.direction.z
            observation.intensity = visionLight.intensity
            withUnsafeMutablePointer(to: &observation.sh_coeffs_rgb) { ptr in
                ptr.withMemoryRebound(to: Float.self, capacity: 27) { coeffs in
                    for i in 0..<9 {
                        let c = i < visionLight.shCoeffs.count
                            ? visionLight.shCoeffs[i]
                            : SIMD3<Float>(0, 0, 0)
                        coeffs[i * 3 + 0] = c.x
                        coeffs[i * 3 + 1] = c.y
                        coeffs[i * 3 + 2] = c.z
                    }
                }
            }
            return observation
        }
        #endif

        return nil
    }

    private func makeLightState(from native: aether_light_state_t) -> LightState {
        var nativeState = native
        var shFlat = [Float](repeating: 0, count: 27)
        shFlat.withUnsafeMutableBufferPointer { buffer in
            guard let base = buffer.baseAddress else { return }
            _ = withUnsafePointer(to: &nativeState) { statePtr in
                aether_light_state_copy_sh9_rgb(statePtr, base, Int32(buffer.count))
            }
        }

        var shCoeffs: [SIMD3<Float>] = []
        shCoeffs.reserveCapacity(9)
        for i in 0..<9 {
            let base = i * 3
            shCoeffs.append(SIMD3<Float>(
                shFlat[base + 0],
                shFlat[base + 1],
                shFlat[base + 2]
            ))
        }

        return LightState(
            direction: SIMD3<Float>(
                nativeState.direction.0,
                nativeState.direction.1,
                nativeState.direction.2
            ),
            intensity: nativeState.intensity,
            shCoeffs: shCoeffs,
            tier: EstimationTier(rawValue: Int(nativeState.tier)) ?? .fallback
        )
    }
    #endif
    
    #if canImport(ARKit)
    /// Extract light from ARKit ARLightEstimate
    private func extractARKitLight(estimate: ARLightEstimate) -> LightState {
        // Normalize ARKit's ambientIntensity (typically around 1000 at nominal light) to ~[0, 2].
        let intensity = max(0.0, Float(estimate.ambientIntensity / 1000.0))
        let direction = Self.fallbackDirection

        // Ambient-only SH fallback: ARLightEstimate base type does not provide directional SH on all SDKs.
        var shCoeffs: [SIMD3<Float>] = Array(repeating: SIMD3<Float>(0.0, 0.0, 0.0), count: 9)
        shCoeffs[0] = SIMD3<Float>(intensity, intensity, intensity)
        
        return LightState(
            direction: direction,
            intensity: intensity,
            shCoeffs: shCoeffs,
            tier: .arkit
        )
    }
    #endif
    
    #if canImport(Vision)
    /// Extract light from Vision framework (simplified)
    private func extractVisionLight(image: Any) -> LightState? {
        // Vision framework light estimation would go here
        // For now, return nil to fall back to fallback tier
        // This is a placeholder for Phase 3 - full Vision implementation would analyze
        // the camera image to estimate lighting conditions
        return nil
    }
    #endif
    
    /// Create fallback light state
    private func createFallbackLight() -> LightState {
        var fallbackSH: [SIMD3<Float>] = Array(repeating: SIMD3<Float>(0.0, 0.0, 0.0), count: 9)
        // L0 (ambient) coefficient: uniform white light
        fallbackSH[0] = SIMD3<Float>(Self.fallbackIntensity, Self.fallbackIntensity, Self.fallbackIntensity)
        
        return LightState(
            direction: Self.fallbackDirection,
            intensity: Self.fallbackIntensity,
            shCoeffs: fallbackSH,
            tier: .fallback
        )
    }
}
