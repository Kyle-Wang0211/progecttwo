// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
//  GaussianPreviewRenderer.swift
//  Aether3D
//
//  Metal-based preview renderer for gaussian splatting results during training.
//  Renders current gaussian state at 30fps preview rate with quality overlay.
//  Responds to user gestures (rotate, zoom) to change viewpoint.
//

#if canImport(Metal) && canImport(MetalKit)
import Metal
import MetalKit
import simd
#if canImport(QuartzCore)
import QuartzCore
#endif

// MARK: - Uniform Buffer Layout

/// Per-frame uniforms sent to the gaussian preview shaders.
struct GaussianPreviewUniforms {
    var viewMatrix: simd_float4x4
    var projectionMatrix: simd_float4x4
    var viewportSize: simd_float2
    var gaussianCount: UInt32
    var trainingStep: UInt32
    var psnrEstimate: Float
    var coverageEstimate: Float
    var padding: simd_float2
}

// MARK: - Camera Controller

/// Orbital camera for interactive gaussian preview.
/// Supports rotation via drag and zoom via pinch.
final class OrbitalCameraController {
    var distance: Float = 3.0
    var azimuth: Float = 0.0     // radians
    var elevation: Float = 0.3   // radians
    var target: simd_float3 = .zero

    // Limits
    private let minDistance: Float = 0.5
    private let maxDistance: Float = 20.0
    private let minElevation: Float = -Float.pi / 2.0 + 0.1
    private let maxElevation: Float = Float.pi / 2.0 - 0.1

    func rotate(deltaAzimuth: Float, deltaElevation: Float) {
        azimuth += deltaAzimuth
        elevation += deltaElevation
        elevation = max(minElevation, min(maxElevation, elevation))
    }

    func zoom(delta: Float) {
        distance *= (1.0 - delta * 0.01)
        distance = max(minDistance, min(maxDistance, distance))
    }

    var viewMatrix: simd_float4x4 {
        let cosE = cosf(elevation)
        let sinE = sinf(elevation)
        let cosA = cosf(azimuth)
        let sinA = sinf(azimuth)

        let eye = simd_float3(
            target.x + distance * cosE * sinA,
            target.y + distance * sinE,
            target.z + distance * cosE * cosA
        )

        return OrbitalCameraController.lookAt(eye: eye, center: target, up: simd_float3(0, 1, 0))
    }

    static func lookAt(eye: simd_float3, center: simd_float3, up: simd_float3) -> simd_float4x4 {
        let f = simd_normalize(center - eye)
        let s = simd_normalize(simd_cross(f, up))
        let u = simd_cross(s, f)

        var m = matrix_identity_float4x4
        m.columns.0 = simd_float4(s.x, u.x, -f.x, 0)
        m.columns.1 = simd_float4(s.y, u.y, -f.y, 0)
        m.columns.2 = simd_float4(s.z, u.z, -f.z, 0)
        m.columns.3 = simd_float4(
            -simd_dot(s, eye),
            -simd_dot(u, eye),
            simd_dot(f, eye),
            1
        )
        return m
    }

    static func perspective(fovY: Float, aspect: Float, near: Float, far: Float) -> simd_float4x4 {
        let yScale = 1.0 / tanf(fovY * 0.5)
        let xScale = yScale / aspect

        var m = simd_float4x4(0)
        m.columns.0.x = xScale
        m.columns.1.y = yScale
        m.columns.2.z = far / (near - far)
        m.columns.2.w = -1.0
        m.columns.3.z = (near * far) / (near - far)
        return m
    }
}

// MARK: - GaussianPreviewRenderer

/// Metal-based MTKViewDelegate that renders a gaussian splatting preview
/// during training. Shows quality overlay (PSNR, step count, coverage).
@MainActor
final class GaussianPreviewRenderer: NSObject, MTKViewDelegate {

    // MARK: - Metal State

    private let device: MTLDevice
    private let commandQueue: MTLCommandQueue
    private var splatRenderPipeline: MTLRenderPipelineState?
    private var overlayRenderPipeline: MTLRenderPipelineState?
    private var depthStencilState: MTLDepthStencilState?

    // Triple-buffered uniforms
    private static let maxInflightFrames = 3
    private var uniformBuffers: [MTLBuffer] = []
    private var currentBufferIndex: Int = 0
    private let inflightSemaphore = DispatchSemaphore(value: maxInflightFrames)

    // MARK: - Camera

    let camera = OrbitalCameraController()

    // MARK: - Coordinator Reference

    private weak var coordinator: TrainingCoordinator?

    // MARK: - Preview State

    private var lastDrawTime: CFTimeInterval = 0
    private let targetFrameInterval: CFTimeInterval = 1.0 / 30.0  // 30fps preview
    private var drawCallCount: UInt64 = 0

    // MARK: - Init

    init(device: MTLDevice, coordinator: TrainingCoordinator) {
        self.device = device
        guard let queue = device.makeCommandQueue() else {
            fatalError("GaussianPreviewRenderer: failed to create command queue")
        }
        self.commandQueue = queue
        self.coordinator = coordinator

        super.init()

        setupPipelines()
        setupBuffers()
        setupDepthStencil()
    }

    // MARK: - Setup

    private func setupPipelines() {
        guard let library = device.makeDefaultLibrary() else { return }

        // Splat render pipeline
        if let vertexFn = library.makeFunction(name: "gaussian_preview_vertex"),
           let fragmentFn = library.makeFunction(name: "gaussian_preview_fragment") {
            let desc = MTLRenderPipelineDescriptor()
            desc.vertexFunction = vertexFn
            desc.fragmentFunction = fragmentFn
            desc.colorAttachments[0].pixelFormat = .bgra8Unorm
            desc.colorAttachments[0].isBlendingEnabled = true
            desc.colorAttachments[0].sourceRGBBlendFactor = .sourceAlpha
            desc.colorAttachments[0].destinationRGBBlendFactor = .oneMinusSourceAlpha
            desc.colorAttachments[0].sourceAlphaBlendFactor = .one
            desc.colorAttachments[0].destinationAlphaBlendFactor = .oneMinusSourceAlpha
            desc.depthAttachmentPixelFormat = .depth32Float
            splatRenderPipeline = try? device.makeRenderPipelineState(descriptor: desc)
        }

        // Overlay pipeline (text/HUD) -- uses same vertex format, simple pass-through
        if let vertexFn = library.makeFunction(name: "gaussian_overlay_vertex"),
           let fragmentFn = library.makeFunction(name: "gaussian_overlay_fragment") {
            let desc = MTLRenderPipelineDescriptor()
            desc.vertexFunction = vertexFn
            desc.fragmentFunction = fragmentFn
            desc.colorAttachments[0].pixelFormat = .bgra8Unorm
            desc.colorAttachments[0].isBlendingEnabled = true
            desc.colorAttachments[0].sourceRGBBlendFactor = .sourceAlpha
            desc.colorAttachments[0].destinationRGBBlendFactor = .oneMinusSourceAlpha
            desc.colorAttachments[0].sourceAlphaBlendFactor = .one
            desc.colorAttachments[0].destinationAlphaBlendFactor = .oneMinusSourceAlpha
            desc.depthAttachmentPixelFormat = .depth32Float
            overlayRenderPipeline = try? device.makeRenderPipelineState(descriptor: desc)
        }
    }

    private func setupBuffers() {
        let uniformSize = MemoryLayout<GaussianPreviewUniforms>.stride
        for _ in 0..<Self.maxInflightFrames {
            guard let buffer = device.makeBuffer(
                length: uniformSize,
                options: .storageModeShared
            ) else { continue }
            buffer.label = "GaussianPreview.Uniforms"
            uniformBuffers.append(buffer)
        }
    }

    private func setupDepthStencil() {
        let desc = MTLDepthStencilDescriptor()
        desc.depthCompareFunction = .less
        desc.isDepthWriteEnabled = true
        depthStencilState = device.makeDepthStencilState(descriptor: desc)
    }

    // MARK: - MTKViewDelegate

    nonisolated func mtkView(_ view: MTKView, drawableSizeWillChange size: CGSize) {
        // Viewport resize handled in draw
    }

    nonisolated func draw(in view: MTKView) {
        // Rate limit to 30fps preview
        let now = CACurrentMediaTime()

        inflightSemaphore.wait()

        guard let drawable = view.currentDrawable,
              let renderPassDesc = view.currentRenderPassDescriptor else {
            inflightSemaphore.signal()
            return
        }

        guard let commandBuffer = commandQueue.makeCommandBuffer() else {
            inflightSemaphore.signal()
            return
        }

        // Update uniforms on the current buffer
        let bufferIndex = MainActor.assumeIsolated {
            updateUniforms(viewportSize: view.drawableSize)
        }

        // Configure render pass
        renderPassDesc.colorAttachments[0].clearColor =
            MTLClearColor(red: 0.05, green: 0.05, blue: 0.08, alpha: 1.0)
        renderPassDesc.colorAttachments[0].loadAction = .clear
        renderPassDesc.colorAttachments[0].storeAction = .store

        guard let encoder = commandBuffer.makeRenderCommandEncoder(
            descriptor: renderPassDesc) else {
            inflightSemaphore.signal()
            return
        }

        encoder.setDepthStencilState(depthStencilState)

        // Pass 1: Render gaussian splats
        if let pipeline = splatRenderPipeline,
           bufferIndex < uniformBuffers.count {
            encoder.setRenderPipelineState(pipeline)
            encoder.setVertexBuffer(
                uniformBuffers[bufferIndex], offset: 0, index: 0)
            encoder.setFragmentBuffer(
                uniformBuffers[bufferIndex], offset: 0, index: 0)

            // Instance-draw gaussians (each gaussian = 1 point sprite quad)
            let gaussianCount = MainActor.assumeIsolated {
                coordinator?.gaussianCount ?? 0
            }
            if gaussianCount > 0 {
                encoder.drawPrimitives(
                    type: .point,
                    vertexStart: 0,
                    vertexCount: 1,
                    instanceCount: Int(gaussianCount))
            }
        }

        // Pass 2: Quality overlay HUD
        if let pipeline = overlayRenderPipeline,
           bufferIndex < uniformBuffers.count {
            encoder.setRenderPipelineState(pipeline)
            encoder.setVertexBuffer(
                uniformBuffers[bufferIndex], offset: 0, index: 0)
            // Draw overlay quad (4 vertices, 2 triangles)
            encoder.drawPrimitives(
                type: .triangleStrip, vertexStart: 0, vertexCount: 4)
        }

        encoder.endEncoding()

        let semaphore = inflightSemaphore
        commandBuffer.addCompletedHandler { _ in
            semaphore.signal()
        }

        commandBuffer.present(drawable)
        commandBuffer.commit()
    }

    // MARK: - Uniform Update

    private func updateUniforms(viewportSize: CGSize) -> Int {
        let idx = currentBufferIndex
        currentBufferIndex = (currentBufferIndex + 1) % Self.maxInflightFrames

        guard idx < uniformBuffers.count else { return idx }

        let aspect = Float(viewportSize.width / viewportSize.height)
        let projection = OrbitalCameraController.perspective(
            fovY: Float.pi / 4.0, aspect: aspect, near: 0.01, far: 100.0)

        let quality = coordinator?.currentQuality ?? .zero
        let step = coordinator?.trainingStep ?? 0
        let gCount = coordinator?.gaussianCount ?? 0

        var uniforms = GaussianPreviewUniforms(
            viewMatrix: camera.viewMatrix,
            projectionMatrix: projection,
            viewportSize: simd_float2(
                Float(viewportSize.width), Float(viewportSize.height)),
            gaussianCount: gCount,
            trainingStep: step,
            psnrEstimate: quality.psnrEstimate,
            coverageEstimate: quality.coverageFScore,
            padding: .zero
        )

        let buffer = uniformBuffers[idx]
        memcpy(buffer.contents(), &uniforms,
               MemoryLayout<GaussianPreviewUniforms>.stride)

        return idx
    }

    // MARK: - Gesture Handling

    /// Handle rotation gesture (e.g., pan gesture recognizer).
    /// - Parameters:
    ///   - deltaX: Horizontal displacement in points.
    ///   - deltaY: Vertical displacement in points.
    func handleRotation(deltaX: Float, deltaY: Float) {
        let sensitivity: Float = 0.005
        camera.rotate(
            deltaAzimuth: -deltaX * sensitivity,
            deltaElevation: -deltaY * sensitivity
        )
    }

    /// Handle zoom gesture (e.g., pinch gesture recognizer).
    /// - Parameter scale: Pinch scale factor (1.0 = no change).
    func handleZoom(scale: Float) {
        let delta = (scale - 1.0) * 50.0
        camera.zoom(delta: delta)
    }

    /// Reset camera to default orbital position.
    func resetCamera() {
        camera.distance = 3.0
        camera.azimuth = 0.0
        camera.elevation = 0.3
        camera.target = .zero
    }

    // MARK: - Configuration

    /// Configure the MTKView for gaussian preview rendering.
    /// - Parameter view: The MTKView to configure.
    func configure(view: MTKView) {
        view.device = device
        view.colorPixelFormat = .bgra8Unorm
        view.depthStencilPixelFormat = .depth32Float
        view.clearColor = MTLClearColor(
            red: 0.05, green: 0.05, blue: 0.08, alpha: 1.0)
        view.preferredFramesPerSecond = 30
        view.delegate = self
        view.isPaused = false
        view.enableSetNeedsDisplay = false
    }
}

#endif // canImport(Metal) && canImport(MetalKit)
