//
// GuidanceHints.swift
// Aether3D
//
// PR#7 Scan Guidance UI — Directional Guidance Hints
// Apple-platform only (SwiftUI)
// Phase 4: Full implementation
//

import Foundation

#if canImport(SwiftUI)
import SwiftUI
#endif

#if canImport(simd)
import simd
#endif

/// Directional affordance hints for scan guidance
public struct GuidanceHints {
    
    /// Direction vector (normalized)
    public let direction: SIMD3<Float>
    
    /// Hint intensity [0, 1]
    public let intensity: Float
    
    public init(direction: SIMD3<Float>, intensity: Float) {
        self.direction = direction
        self.intensity = intensity
    }
}

#if canImport(SwiftUI)
/// SwiftUI view for directional hints
public struct DirectionalAffordanceView: View {
    let hints: GuidanceHints
    
    public var body: some View {
        // Directional arrow/indicator
        // White arrow pointing in the direction vector
        GeometryReader { geometry in
            let centerX = geometry.size.width / 2
            let centerY = geometry.size.height / 2
            
            // Convert 3D direction to 2D screen direction
            let screenDir = projectToScreen(direction: hints.direction)
            let angle = CGFloat(atan2(screenDir.y, screenDir.x))
            
            // Arrow shape
            let arrowPath = Path { path in
                let arrowLength: CGFloat = 30 * CGFloat(hints.intensity)
                let arrowWidth: CGFloat = 15
                let cosA = cos(angle)
                let sinA = sin(angle)
                let halfLength = arrowLength * 0.5
                let perpX = -sinA
                let perpY = cosA
                
                // Arrow tip
                let tipX = centerX + cosA * arrowLength
                let tipY = centerY + sinA * arrowLength
                
                // Arrow base
                let baseX = centerX - cosA * halfLength
                let baseY = centerY - sinA * halfLength
                
                // Left wing
                let leftWingX = baseX + perpX * arrowWidth
                let leftWingY = baseY + perpY * arrowWidth
                
                // Right wing
                let rightWingX = baseX - perpX * arrowWidth
                let rightWingY = baseY - perpY * arrowWidth
                
                path.move(to: CGPoint(x: tipX, y: tipY))
                path.addLine(to: CGPoint(x: leftWingX, y: leftWingY))
                path.addLine(to: CGPoint(x: baseX, y: baseY))
                path.addLine(to: CGPoint(x: rightWingX, y: rightWingY))
                path.closeSubpath()
            }
            let opacity = Double(hints.intensity)
            ZStack {
                arrowPath.fill(Color.white.opacity(opacity))
                arrowPath.stroke(Color.white.opacity(opacity), lineWidth: 2)
            }
        }
    }
    
    /// Project 3D direction to 2D screen space
    private func projectToScreen(direction: SIMD3<Float>) -> SIMD2<Float> {
        // Simple projection: ignore Z, use X and Y
        return SIMD2<Float>(direction.x, direction.y)
    }
}
#endif
