//
// ScanGuidanceRenderPipeline.swift
// Aether3D
//
// PR#7 Scan Guidance UI — Metal Render Pipeline
// Metal 6-pass render orchestrator with triple buffering
// Apple-platform only (import Metal)
// Phase 2: Implements wedge fill + border passes only
//

#if canImport(Metal)
import Metal
import MetalKit
import simd
import QuartzCore  // for CACurrentMediaTime — OK in App/
import Aether3DCore

public final class ScanGuidanceRenderPipeline {

    public static let kMaxInflightBuffers: Int = ScanGuidanceConstants.kMaxInflightBuffers
    private let inflightSemaphore: DispatchSemaphore
    private var currentBufferIndex: Int = 0

    private let device: MTLDevice
    private let commandQueue: MTLCommandQueue
    private var wedgeFillPipeline: MTLRenderPipelineState?
    private var borderStrokePipeline: MTLRenderPipelineState?
    private var metallicLightingPipeline: MTLRenderPipelineState?
    private var colorCorrectionPipeline: MTLRenderPipelineState?
    private var ambientOcclusionPipeline: MTLRenderPipelineState?
    private var postProcessPipeline: MTLRenderPipelineState?
    private var depthStencilState: MTLDepthStencilState?
    private var currentVertexCount: Int = 0
    private var currentIndexCount: Int = 0
    private var lastWrittenBufferIndex: Int = 0

    /// Tracks whether update() has produced new data that encode() hasn't consumed yet.
    /// Prevents semaphore over-signaling: encode() only adds a GPU-completion signal()
    /// when there's a corresponding update() wait(). Without this, MTKView calling
    /// draw() faster than ARSession fires frames causes unbounded semaphore growth,
    /// breaking the triple-buffer protection guarantee.
    private var hasUnconsumedUpdate: Bool = false

    /// Protects currentIndexCount / lastWrittenBufferIndex / hasUnconsumedUpdate from
    /// concurrent read (encode on MTKView delegate thread) and write (update on main thread).
    /// Without this, encode() can read a partially-written bufferIndex or indexCount,
    /// causing Metal to draw from the wrong buffer or with a stale count.
    private let bufferLock = NSLock()

    // Sub-systems (Core/ pure algorithms)
    private let wedgeGenerator: WedgeGeometryGenerator
    private let borderCalculator: AdaptiveBorderCalculator
    private let thermalAdapter: ThermalQualityAdapter

    // Sub-systems (App/ platform-specific)
    private let lightEstimator: EnvironmentLightEstimator

    // Triple-buffered Metal buffers
    private var vertexBuffers: [MTLBuffer] = []
    private var indexBuffers: [MTLBuffer] = []
    private var uniformBuffers: [MTLBuffer] = []
    private var perTriangleBuffers: [MTLBuffer] = []

    public init(device: MTLDevice) throws {
        self.device = device
        guard let queue = device.makeCommandQueue() else {
            throw ScanGuidanceError.deviceInitializationFailed
        }
        self.commandQueue = queue
        self.inflightSemaphore = DispatchSemaphore(value: Self.kMaxInflightBuffers)
        self.wedgeGenerator = WedgeGeometryGenerator()
        self.borderCalculator = AdaptiveBorderCalculator()
        self.lightEstimator = EnvironmentLightEstimator()
        self.thermalAdapter = ThermalQualityAdapter()
        
        // Layer 4.3: Initialize triple buffers with guard-let instead of force unwrap.
        // device.makeBuffer() can return nil under memory pressure or with invalid
        // parameters, and force unwrap would crash the entire app.
        for _ in 0..<Self.kMaxInflightBuffers {
            // Wedge geometry expansion: each input triangle generates a 3D prism
            // with lod0TrianglesPerPrism (44) output triangles = 132 indices.
            // For 2000 input triangles: 2000 × 132 = 264K indices × 4 bytes = 1.06MB.
            // Use 4MB initial buffers to handle up to ~7500 input triangles without reallocation.
            guard let vb = device.makeBuffer(length: 4 * 1024 * 1024, options: []),   // 4MB vertex
                  let ib = device.makeBuffer(length: 4 * 1024 * 1024, options: []),   // 4MB index
                  let ub = device.makeBuffer(length: 1024, options: []),               // 1KB uniform
                  let ptb = device.makeBuffer(length: 256 * 1024, options: [])         // 256KB per-tri
            else {
                throw ScanGuidanceError.deviceInitializationFailed
            }
            vertexBuffers.append(vb)
            indexBuffers.append(ib)
            uniformBuffers.append(ub)
            perTriangleBuffers.append(ptb)
        }
        
        // Create render pipeline states
        try createRenderPipelines()
        
        // Create depth stencil state
        let depthDescriptor = MTLDepthStencilDescriptor()
        depthDescriptor.depthCompareFunction = .less
        depthDescriptor.isDepthWriteEnabled = true
        self.depthStencilState = device.makeDepthStencilState(descriptor: depthDescriptor)
    }

    /// Per-frame update — reads from PatchDisplayMap snapshot, no coverage dependency
    /// v7.0.2: displaySnapshot is [String: Double], converted from [DisplayEntry]
    ///         by caller (see Part A.2 for conversion pattern)
    /// v7.0.4: Thermal tier now drives LOD, triangle budget, animation toggles
    public func update(
        displaySnapshot: [String: Double],
        colorStates: [String: ColorState],
        meshTriangles: [ScanTriangle],
        lightEstimate: Any?,  // ARLightEstimate on iOS
        cameraTransform: simd_float4x4,
        viewMatrix: simd_float4x4? = nil,
        projectionMatrix: simd_float4x4? = nil,
        frameDeltaTime: TimeInterval,
        precomputedFlipAngles: [Float]? = nil,
        precomputedRippleAmplitudes: [Float]? = nil,
        precomputedFlipAxisData: [(origin: SIMD3<Float>, direction: SIMD3<Float>)]? = nil,
        gpuDurationMs: Double? = nil
    ) {
        // Layer 4.1: Acquire semaphore at start of update() to prevent writing
        // to a triple-buffer slot that the GPU is still reading from encode().
        // The signal() remains in encode()'s completedHandler.
        //
        // CRITICAL: Use wait(timeout:) instead of wait() to prevent deadlock.
        // If encode() is never called (MTKView paused, pipeline nil, app background),
        // the semaphore is never signaled → wait() blocks the main thread FOREVER.
        // With a 100ms timeout, we skip this frame instead of freezing the app.
        // 100ms = ~6 frames at 60fps, generous enough for GPU pipeline stalls.
        let waitResult = inflightSemaphore.wait(timeout: .now() + .milliseconds(100))
        if waitResult == .timedOut {
            // GPU is backed up — skip this update entirely instead of blocking.
            // The render pipeline will re-use the previous frame's data (stale but visible).
            #if DEBUG
            print("[Aether3D] ⚠️ Triple-buffer semaphore timed out — skipping frame update")
            #endif
            return
        }

        #if os(iOS) || os(macOS)
        thermalAdapter.updateThermalState(ProcessInfo.processInfo.thermalState)
        #endif

        if let gpuDuration = gpuDurationMs {
            thermalAdapter.updateFrameTiming(gpuDurationMs: gpuDuration)
        }

        // Note: ScanViewModel already performs C++ multi-factor selection
        // (selectStableRenderTriangles) before passing meshTriangles here.
        // The thermal adapter's own tier limit is still applied as a safety cap.
        let tier = thermalAdapter.currentTier

        // v7.2: Adaptive LOD-based cap replaces hardcoded 6000.
        // Index buffer = 4MB = 1,048,576 UInt32 slots. Indices per input varies by LOD:
        //   LOD 0 (full): ~44 subtriangles × 3 = 132 indices → max ~7943 inputs
        //   LOD 1 (medium): ~22 subtriangles × 3 = 66 indices → max ~15891 inputs
        //   LOD 2+ (low): ~11 subtriangles × 3 = 33 indices → max ~31775 inputs
        let indicesPerInput: Int
        switch tier.lodLevel {
        case .full:   indicesPerInput = 132  // ~44 subtriangles × 3
        case .medium: indicesPerInput = 66   // ~22 subtriangles × 3
        case .low:    indicesPerInput = 33   // ~11 subtriangles × 3
        case .flat:   indicesPerInput = 9    // ~3 subtriangles × 3
        }
        let indexBufferCapacity = 1_048_576  // 4MB / sizeof(UInt32)
        let maxSafeInputTriangles = min(tier.maxTriangles, indexBufferCapacity / indicesPerInput)
        let limitedTriangles = meshTriangles.count > maxSafeInputTriangles
            ? Array(meshTriangles.prefix(maxSafeInputTriangles))
            : meshTriangles

        let wedgeData = wedgeGenerator.generate(
            triangles: limitedTriangles,
            displayValues: displaySnapshot,
            lod: tier.lodLevel
        )

        let lightState = lightEstimator.update(
            lightEstimate: lightEstimate,
            cameraImage: nil,
            timestamp: CACurrentMediaTime()
        )

        let flipAngles: [Float]
        if tier.enableFlipAnimation, let provided = precomputedFlipAngles {
            flipAngles = normalizePerTriangleArray(
                provided,
                triangleCount: limitedTriangles.count
            )
        } else {
            flipAngles = Array(repeating: 0, count: limitedTriangles.count)
        }

        let rippleAmplitudes: [Float]
        if tier.enableRipple, let provided = precomputedRippleAmplitudes {
            rippleAmplitudes = normalizePerTriangleArray(
                provided,
                triangleCount: limitedTriangles.count
            )
        } else {
            rippleAmplitudes = Array(repeating: 0, count: limitedTriangles.count)
        }

        let borderWidths = normalizePerTriangleArray(
            wedgeGenerator.borderWidthsForLastGenerate(),
            triangleCount: limitedTriangles.count
        )

        // Compute per-triangle flip axis data
        let flipAxisData = normalizeFlipAxisData(
            precomputedFlipAxisData,
            triangleCount: limitedTriangles.count
        )

        let grayscaleColors = normalizeGrayscaleColors(
            wedgeGenerator.grayscaleForLastGenerate(),
            triangleCount: limitedTriangles.count
        )

        #if DEBUG
        // One-shot diagnostic: confirm buffer sizes on first frame
        if currentBufferIndex == 1 {  // After first advance (was 0 → 1)
            let ibLen = indexBuffers[0].length
            let vbLen = vertexBuffers[0].length
            print("[Aether3D] Pipeline: ib=\(ibLen) vb=\(vbLen) "
                + "wedge[v=\(wedgeData.vertices.count),i=\(wedgeData.indices.count)] "
                + "input=\(limitedTriangles.count) bufSlot=\(currentBufferIndex - 1)")
        }
        #endif

        uploadToBuffers(
            wedgeData: wedgeData,
            lightState: lightState,
            flipAngles: flipAngles,
            rippleAmplitudes: rippleAmplitudes,
            borderWidths: borderWidths,
            flipAxisData: flipAxisData,
            grayscaleColors: grayscaleColors,
            cameraTransform: cameraTransform,
            viewMatrix: viewMatrix ?? simd_inverse(cameraTransform),
            projectionMatrix: projectionMatrix ?? matrix_identity_float4x4,
            qualityTier: tier.rawValue
        )
    }

    /// Encode all render passes into command buffer
    /// Phase 2: Only encodes wedge fill + border stroke passes
    ///
    /// Layer 4.1: inflightSemaphore.wait() moved to update() to protect the WRITE
    /// side. Here we only register the signal handler — the semaphore was already
    /// acquired before uploadToBuffers() wrote data.
    public func encode(
        into commandBuffer: MTLCommandBuffer,
        renderPassDescriptor: MTLRenderPassDescriptor
    ) {
        // Read shared state under lock to prevent tearing with update() on main thread.
        // Also consume the update flag: only signal the semaphore if update() produced
        // new data (paired wait/signal). Without this, MTKView calling draw() faster
        // than ARSession produces frames → extra signal() calls → semaphore count
        // grows beyond kMaxInflightBuffers → triple-buffer protection breaks.
        bufferLock.lock()
        let bufferIndex = lastWrittenBufferIndex
        let indexCountSnapshot = currentIndexCount
        let shouldSignalSemaphore = hasUnconsumedUpdate
        hasUnconsumedUpdate = false
        bufferLock.unlock()

        // Safety: validate bufferIndex is in range. If it's stale or corrupted,
        // skip encoding entirely to prevent array-out-of-bounds crash.
        guard bufferIndex >= 0 && bufferIndex < Self.kMaxInflightBuffers else {
            if shouldSignalSemaphore {
                inflightSemaphore.signal()  // Balance the wait() from update()
            }
            return
        }

        // Safety: validate indexCount against actual buffer capacity.
        // If buffer growth failed (makeBuffer returned nil), indexCount may
        // exceed what the index buffer can hold → Metal validation crash.
        let maxSafeIndices = indexBuffers[bufferIndex].length / MemoryLayout<UInt32>.stride
        let safeIndexCount = min(indexCountSnapshot, maxSafeIndices)

        if shouldSignalSemaphore {
            commandBuffer.addCompletedHandler { [weak self] _ in
                self?.inflightSemaphore.signal()
            }
        }

        guard let encoder = commandBuffer.makeRenderCommandEncoder(
            descriptor: renderPassDescriptor
        ) else { return }

        encodeWedgeFill(encoder: encoder, bufferIndex: bufferIndex, indexCount: safeIndexCount)
        encodeBorderStroke(encoder: encoder, bufferIndex: bufferIndex, indexCount: safeIndexCount)

        // Pass 3-6: thermal-aware pass mask from C++ engine
        let rawMask = thermalAdapter.passMask
        let mask = rawMask == 0 ? UInt32(0x3F) : rawMask  // Safety: zero fallback → all passes enabled
        if mask & 0x04 != 0 {  // bit2: metallic lighting
            encodeAdditionalPass(encoder: encoder, bufferIndex: bufferIndex, indexCount: safeIndexCount, pipeline: metallicLightingPipeline)
        }
        if mask & 0x08 != 0 {  // bit3: color correction
            encodeAdditionalPass(encoder: encoder, bufferIndex: bufferIndex, indexCount: safeIndexCount, pipeline: colorCorrectionPipeline)
        }
        if mask & 0x10 != 0 {  // bit4: ambient occlusion
            encodeAdditionalPass(encoder: encoder, bufferIndex: bufferIndex, indexCount: safeIndexCount, pipeline: ambientOcclusionPipeline)
        }
        if mask & 0x20 != 0 {  // bit5: post-processing
            encodeAdditionalPass(encoder: encoder, bufferIndex: bufferIndex, indexCount: safeIndexCount, pipeline: postProcessPipeline)
        }

        encoder.endEncoding()
    }

    public func applyRenderTier(_ tier: ThermalQualityAdapter.RenderTier) {
        thermalAdapter.forceRenderTier(tier)
    }

    public func resetPersistentVisualState() {
        wedgeGenerator.resetPersistentVisualState()
        borderCalculator.resetPersistentBorderState()
    }

    // MARK: - Private Methods

    private func createRenderPipelines() throws {
        // Load Metal library from the app bundle
        guard let library = device.makeDefaultLibrary() else {
            throw ScanGuidanceError.pipelineCreationFailed("Failed to load Metal library")
        }
        
        // ── Pass 1: Wedge Fill Pipeline ──
        guard let wedgeVertexFn = library.makeFunction(name: "wedgeFillVertex"),
              let wedgeFragmentFn = library.makeFunction(name: "wedgeFillFragment") else {
            throw ScanGuidanceError.pipelineCreationFailed("Failed to load wedge fill shaders")
        }
        
        let wedgeDescriptor = MTLRenderPipelineDescriptor()
        wedgeDescriptor.label = "Aether3D Wedge Fill"
        wedgeDescriptor.vertexFunction = wedgeVertexFn
        wedgeDescriptor.fragmentFunction = wedgeFragmentFn
        wedgeDescriptor.vertexDescriptor = ScanGuidanceVertexDescriptor.create()
        
        // Color attachment: pre-multiplied alpha blending for AR overlay
        wedgeDescriptor.colorAttachments[0].pixelFormat = .bgra8Unorm
        wedgeDescriptor.colorAttachments[0].isBlendingEnabled = true
        wedgeDescriptor.colorAttachments[0].sourceRGBBlendFactor = .one  // pre-multiplied
        wedgeDescriptor.colorAttachments[0].destinationRGBBlendFactor = .oneMinusSourceAlpha
        wedgeDescriptor.colorAttachments[0].sourceAlphaBlendFactor = .one
        wedgeDescriptor.colorAttachments[0].destinationAlphaBlendFactor = .oneMinusSourceAlpha
        
        // Depth
        wedgeDescriptor.depthAttachmentPixelFormat = .depth32Float
        
        do {
            wedgeFillPipeline = try device.makeRenderPipelineState(descriptor: wedgeDescriptor)
        } catch {
            throw ScanGuidanceError.pipelineCreationFailed("Failed to create wedge fill pipeline: \(error)")
        }
        
        // ── Pass 2: Border Stroke Pipeline ──
        guard let borderFragmentFn = library.makeFunction(name: "borderStrokeFragment") else {
            throw ScanGuidanceError.pipelineCreationFailed("Failed to load border stroke shader")
        }
        
        let borderDescriptor = MTLRenderPipelineDescriptor()
        borderDescriptor.label = "Aether3D Border Stroke"
        borderDescriptor.vertexFunction = wedgeVertexFn  // Same vertex shader
        borderDescriptor.fragmentFunction = borderFragmentFn
        borderDescriptor.vertexDescriptor = ScanGuidanceVertexDescriptor.create()
        
        // Additive blending for borders
        borderDescriptor.colorAttachments[0].pixelFormat = .bgra8Unorm
        borderDescriptor.colorAttachments[0].isBlendingEnabled = true
        borderDescriptor.colorAttachments[0].sourceRGBBlendFactor = .one  // pre-multiplied
        borderDescriptor.colorAttachments[0].destinationRGBBlendFactor = .oneMinusSourceAlpha
        borderDescriptor.colorAttachments[0].sourceAlphaBlendFactor = .one
        borderDescriptor.colorAttachments[0].destinationAlphaBlendFactor = .oneMinusSourceAlpha
        
        borderDescriptor.depthAttachmentPixelFormat = .depth32Float
        
        do {
            borderStrokePipeline = try device.makeRenderPipelineState(descriptor: borderDescriptor)
        } catch {
            throw ScanGuidanceError.pipelineCreationFailed("Failed to create border stroke pipeline: \(error)")
        }

        // ── Pass 3-6: Additional rendering passes ──
        // All share wedgeFillVertex; differ only in fragment function and blend mode.
        // These passes are lightweight compositing layers — they fail gracefully if shader not found.

        let additionalPasses: [(name: String, fragmentFn: String, target: ReferenceWritableKeyPath<ScanGuidanceRenderPipeline, MTLRenderPipelineState?>)] = [
            ("Aether3D Metallic Lighting", "metallicLightingFragment", \.metallicLightingPipeline),
            ("Aether3D Color Correction", "colorCorrectionFragment", \.colorCorrectionPipeline),
            ("Aether3D Ambient Occlusion", "ambientOcclusionFragment", \.ambientOcclusionPipeline),
            ("Aether3D Post-Processing", "postProcessFragment", \.postProcessPipeline),
        ]

        for pass in additionalPasses {
            guard let fragmentFn = library.makeFunction(name: pass.fragmentFn) else {
                continue  // Graceful: skip passes whose shaders aren't compiled yet
            }
            let desc = MTLRenderPipelineDescriptor()
            desc.label = pass.name
            desc.vertexFunction = wedgeVertexFn
            desc.fragmentFunction = fragmentFn
            desc.vertexDescriptor = ScanGuidanceVertexDescriptor.create()
            desc.colorAttachments[0].pixelFormat = .bgra8Unorm
            desc.colorAttachments[0].isBlendingEnabled = true
            desc.colorAttachments[0].sourceRGBBlendFactor = .one
            desc.colorAttachments[0].destinationRGBBlendFactor = .oneMinusSourceAlpha
            desc.colorAttachments[0].sourceAlphaBlendFactor = .one
            desc.colorAttachments[0].destinationAlphaBlendFactor = .oneMinusSourceAlpha
            desc.depthAttachmentPixelFormat = .depth32Float

            if let pipelineState = try? device.makeRenderPipelineState(descriptor: desc) {
                self[keyPath: pass.target] = pipelineState
            }
        }
    }

    private func encodeWedgeFill(encoder: MTLRenderCommandEncoder, bufferIndex: Int, indexCount: Int) {
        guard let wedgeFillPipeline else { return }
        guard bufferIndex >= 0 && bufferIndex < vertexBuffers.count else { return }

        encoder.setRenderPipelineState(wedgeFillPipeline)
        encoder.setCullMode(.back)
        encoder.setDepthStencilState(depthStencilState)

        // Bind buffers
        encoder.setVertexBuffer(vertexBuffers[bufferIndex],
                               offset: 0,
                               index: ScanGuidanceVertexDescriptor.BufferIndex.vertexData)
        encoder.setVertexBuffer(uniformBuffers[bufferIndex],
                               offset: 0,
                               index: ScanGuidanceVertexDescriptor.BufferIndex.uniforms)
        encoder.setVertexBuffer(perTriangleBuffers[bufferIndex],
                               offset: 0,
                               index: ScanGuidanceVertexDescriptor.BufferIndex.perTriangleData)

        // Fragment buffers
        encoder.setFragmentBuffer(uniformBuffers[bufferIndex],
                                 offset: 0,
                                 index: ScanGuidanceVertexDescriptor.BufferIndex.uniforms)

        if indexCount > 0 {
            encoder.drawIndexedPrimitives(
                type: .triangle,
                indexCount: indexCount,
                indexType: .uint32,
                indexBuffer: indexBuffers[bufferIndex],
                indexBufferOffset: 0
            )
        }
    }

    private func encodeBorderStroke(encoder: MTLRenderCommandEncoder, bufferIndex: Int, indexCount: Int) {
        guard let borderStrokePipeline else { return }
        guard bufferIndex >= 0 && bufferIndex < vertexBuffers.count else { return }

        encoder.setRenderPipelineState(borderStrokePipeline)
        // Layer 3.1: Set depth stencil state for border pass — was missing, causing
        // Z-fighting where border fragments render behind wedge fill fragments.
        if let depthStencilState {
            encoder.setDepthStencilState(depthStencilState)
        }
        encoder.setCullMode(.back)

        // Same buffer bindings as wedge fill
        encoder.setVertexBuffer(vertexBuffers[bufferIndex],
                               offset: 0,
                               index: ScanGuidanceVertexDescriptor.BufferIndex.vertexData)
        encoder.setVertexBuffer(uniformBuffers[bufferIndex],
                               offset: 0,
                               index: ScanGuidanceVertexDescriptor.BufferIndex.uniforms)
        encoder.setVertexBuffer(perTriangleBuffers[bufferIndex],
                               offset: 0,
                               index: ScanGuidanceVertexDescriptor.BufferIndex.perTriangleData)
        encoder.setFragmentBuffer(uniformBuffers[bufferIndex],
                                 offset: 0,
                                 index: ScanGuidanceVertexDescriptor.BufferIndex.uniforms)

        if indexCount > 0 {
            encoder.drawIndexedPrimitives(
                type: .triangle,
                indexCount: indexCount,
                indexType: .uint32,
                indexBuffer: indexBuffers[bufferIndex],
                indexBufferOffset: 0
            )
        }
    }

    /// Encode an additional compositing pass (Pass 3-6).
    /// Reuses the same vertex/uniform/perTriangle buffers as wedge fill.
    private func encodeAdditionalPass(
        encoder: MTLRenderCommandEncoder,
        bufferIndex: Int,
        indexCount: Int,
        pipeline: MTLRenderPipelineState?
    ) {
        guard let pipeline else { return }
        guard indexCount > 0 else { return }
        // Safety: ensure bufferIndex is within bounds to prevent array-out-of-bounds crash.
        // This can happen if encode() reads a stale bufferIndex from before a buffer resize.
        guard bufferIndex >= 0 && bufferIndex < vertexBuffers.count else { return }

        encoder.setRenderPipelineState(pipeline)
        encoder.setCullMode(.back)
        // Layer 3.1: Set depth stencil state for additional passes — prevents Z-fighting
        if let depthStencilState {
            encoder.setDepthStencilState(depthStencilState)
        }

        encoder.setVertexBuffer(vertexBuffers[bufferIndex],
                               offset: 0,
                               index: ScanGuidanceVertexDescriptor.BufferIndex.vertexData)
        encoder.setVertexBuffer(uniformBuffers[bufferIndex],
                               offset: 0,
                               index: ScanGuidanceVertexDescriptor.BufferIndex.uniforms)
        encoder.setVertexBuffer(perTriangleBuffers[bufferIndex],
                               offset: 0,
                               index: ScanGuidanceVertexDescriptor.BufferIndex.perTriangleData)
        encoder.setFragmentBuffer(uniformBuffers[bufferIndex],
                                 offset: 0,
                                 index: ScanGuidanceVertexDescriptor.BufferIndex.uniforms)

        encoder.drawIndexedPrimitives(
            type: .triangle,
            indexCount: indexCount,
            indexType: .uint32,
            indexBuffer: indexBuffers[bufferIndex],
            indexBufferOffset: 0
        )
    }

    private func uploadToBuffers(
        wedgeData: WedgeVertexData,
        lightState: LightState,
        flipAngles: [Float],
        rippleAmplitudes: [Float],
        borderWidths: [Float],
        flipAxisData: [(origin: SIMD3<Float>, direction: SIMD3<Float>)],
        grayscaleColors: [(Float, Float, Float)],
        cameraTransform: simd_float4x4,
        viewMatrix: simd_float4x4,
        projectionMatrix: simd_float4x4,
        qualityTier: Int
    ) {
        let bufferIndex = currentBufferIndex
        // Advance to next triple-buffer slot for the NEXT frame.
        // Without this, all frames write to slot 0 — CPU writes collide with
        // GPU reads on the same buffer, causing torn geometry and visual glitches.
        currentBufferIndex = (currentBufferIndex + 1) % Self.kMaxInflightBuffers

        // CRITICAL: Do NOT publish lastWrittenBufferIndex yet!
        // encode() runs on the MTKView delegate thread and reads lastWrittenBufferIndex
        // to decide which buffer slot the GPU should draw from. If we publish the new
        // index here (before writing data), encode() will submit a draw call to a buffer
        // that the CPU is still filling → GPU reads half-written vertex/index/uniform
        // data → LLDB RPC server crash / GPU hang / visual corruption.
        // We publish AFTER all four buffer writes are complete (see end of method).

        // ── Vertex Buffer ──
        let vertexCount = wedgeData.vertices.count
        let stride = MemoryLayout<Float>.size * 10 + MemoryLayout<UInt32>.size  // 44 bytes
        let requiredVertexSize = vertexCount * stride

        // Grow buffer if needed
        if requiredVertexSize > vertexBuffers[bufferIndex].length {
            let newSize = max(requiredVertexSize, vertexBuffers[bufferIndex].length * 2)
            if let newBuffer = device.makeBuffer(length: newSize, options: []) {
                vertexBuffers[bufferIndex] = newBuffer
            }
        }

        // SAFETY CLAMP: ensure we don't write past buffer end
        let maxSafeVertexCount = vertexBuffers[bufferIndex].length / stride
        let safeVertexCount = min(vertexCount, maxSafeVertexCount)

        // Copy vertex data
        let vertexPtr = vertexBuffers[bufferIndex].contents()
        for i in 0..<safeVertexCount {
            let vertex = wedgeData.vertices[i]
            let base = vertexPtr + i * stride
            base.storeBytes(of: vertex.position.x, as: Float.self)
            (base + 4).storeBytes(of: vertex.position.y, as: Float.self)
            (base + 8).storeBytes(of: vertex.position.z, as: Float.self)
            (base + 12).storeBytes(of: vertex.normal.x, as: Float.self)
            (base + 16).storeBytes(of: vertex.normal.y, as: Float.self)
            (base + 20).storeBytes(of: vertex.normal.z, as: Float.self)
            (base + 24).storeBytes(of: vertex.metallic, as: Float.self)
            (base + 28).storeBytes(of: vertex.roughness, as: Float.self)
            (base + 32).storeBytes(of: vertex.display, as: Float.self)
            (base + 36).storeBytes(of: vertex.thickness, as: Float.self)
            (base + 40).storeBytes(of: vertex.triangleId, as: UInt32.self)
        }

        // ── Index Buffer ──
        let indexCount = wedgeData.indices.count
        let requiredIndexSize = indexCount * MemoryLayout<UInt32>.stride

        if requiredIndexSize > indexBuffers[bufferIndex].length {
            let newSize = max(requiredIndexSize, indexBuffers[bufferIndex].length * 2)
            if let newBuffer = device.makeBuffer(length: newSize, options: []) {
                indexBuffers[bufferIndex] = newBuffer
            }
        }

        // SAFETY CLAMP: if buffer growth failed (nil from makeBuffer), clamp indices
        // to what the current buffer can hold. Prevents Metal validation crash:
        // "indexBufferOffset + indexCount * 4 must be <= indexBuffer.length"
        let maxSafeIndexCount = indexBuffers[bufferIndex].length / MemoryLayout<UInt32>.stride
        let safeIndexCount = min(indexCount, maxSafeIndexCount)

        // VERTEX-INDEX COHERENCE: if vertices were clamped (safeVertexCount < vertexCount),
        // some indices may reference vertex IDs >= safeVertexCount. The GPU would read
        // uninitialized vertex memory, causing garbage rendering or validation crashes.
        // Solution: copy indices but clamp any out-of-range vertex references.
        if safeIndexCount > 0 {
            let maxVertexId = UInt32(max(0, safeVertexCount - 1))
            let ibPtr = indexBuffers[bufferIndex].contents().bindMemory(to: UInt32.self, capacity: safeIndexCount)
            for i in 0..<safeIndexCount {
                let idx = wedgeData.indices[i]
                ibPtr[i] = min(idx, maxVertexId)
            }
        }

        // ── Uniform Buffer ──
        // v7.0.3 FIX: Removed _pad0 — Metal float3 in struct is 16-byte aligned,
        // and Swift SIMD3<Float> also has 16-byte stride, so no manual padding needed
        // between consecutive SIMD3<Float> fields. _pad0 was SHIFTING all downstream
        // fields by 4 bytes, causing Metal↔Swift memory layout mismatch.
        //
        // Metal layout:
        //   offset 0:   viewProjectionMatrix (float4x4, 64 bytes)
        //   offset 64:  modelMatrix (float4x4, 64 bytes)
        //   offset 128: cameraPosition (float3, 16 bytes with padding)
        //   offset 144: primaryLightDirection (float3, 16 bytes with padding)
        //   offset 160: primaryLightIntensity (float, 4 bytes)
        //   offset 164: [12 bytes padding to align float3 array]
        //   offset 176: shCoeffs[9] (9 × float3 = 9 × 16 = 144 bytes)
        //   offset 320: qualityTier (uint, 4 bytes)
        //   offset 324: time (float, 4 bytes)
        //   offset 328: borderGamma (float, 4 bytes)
        //   offset 332: [4 bytes padding to align struct to 16]
        //   Total: 336 bytes
        struct GPUUniforms {
            var viewProjectionMatrix: simd_float4x4      // offset 0
            var modelMatrix: simd_float4x4               // offset 64
            var cameraPosition: SIMD3<Float>             // offset 128 (stride 16)
            var primaryLightDirection: SIMD3<Float>      // offset 144 (stride 16)
            var primaryLightIntensity: Float             // offset 160
            // [12 bytes implicit padding to align SIMD3<Float> to 16 bytes]
            var shCoeffs: (SIMD3<Float>, SIMD3<Float>, SIMD3<Float>, SIMD3<Float>, SIMD3<Float>,
                           SIMD3<Float>, SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)  // offset 176
            var qualityTier: UInt32                      // offset 320
            var time: Float                             // offset 324
            var borderGamma: Float                      // offset 328
            var _pad1: Float = 0                        // offset 332 (align to 336)
        }
        // Layer 4.5: Compile-time assertion to catch layout mismatches between
        // Swift GPUUniforms and Metal ScanGuidanceUniforms. Any change to either
        // side must keep them in sync — a mismatch silently corrupts SH lighting.
        assert(MemoryLayout<GPUUniforms>.size == 336,
               "GPUUniforms size mismatch: expected 336 bytes, got \(MemoryLayout<GPUUniforms>.size)")
        
        // Extract camera position from transform
        let camPos = SIMD3<Float>(cameraTransform.columns.3.x,
                                   cameraTransform.columns.3.y,
                                   cameraTransform.columns.3.z)
        
        // Build SH coefficients tuple
        let sh = lightState.shCoeffs
        let shTuple = (
            sh.count > 0 ? sh[0] : SIMD3<Float>(0,0,0),
            sh.count > 1 ? sh[1] : SIMD3<Float>(0,0,0),
            sh.count > 2 ? sh[2] : SIMD3<Float>(0,0,0),
            sh.count > 3 ? sh[3] : SIMD3<Float>(0,0,0),
            sh.count > 4 ? sh[4] : SIMD3<Float>(0,0,0),
            sh.count > 5 ? sh[5] : SIMD3<Float>(0,0,0),
            sh.count > 6 ? sh[6] : SIMD3<Float>(0,0,0),
            sh.count > 7 ? sh[7] : SIMD3<Float>(0,0,0),
            sh.count > 8 ? sh[8] : SIMD3<Float>(0,0,0)
        )
        
        var uniforms = GPUUniforms(
            viewProjectionMatrix: projectionMatrix * viewMatrix,
            modelMatrix: matrix_identity_float4x4,
            cameraPosition: camPos,
            primaryLightDirection: lightState.direction,
            primaryLightIntensity: lightState.intensity,
            shCoeffs: shTuple,
            qualityTier: UInt32(qualityTier),
            time: Float(CACurrentMediaTime()),
            borderGamma: Float(ScanGuidanceConstants.borderGamma)
        )
        
        // v7.0.3: Use .stride (not .size) to include trailing padding for Metal alignment
        memcpy(uniformBuffers[bufferIndex].contents(), &uniforms, MemoryLayout<GPUUniforms>.stride)
        
        // ── Per-Triangle Data Buffer ──
        let triCount = wedgeData.triangleCount
        let perTriStride = 48  // 12 floats: flipAngle(1) + rippleAmplitude(1) + borderWidth(1) + flipAxisOrigin(3) + flipAxisDirection(3) + grayscaleColor(3) = 12 floats = 48 bytes
        let requiredPerTriSize = triCount * perTriStride
        
        if requiredPerTriSize > perTriangleBuffers[bufferIndex].length {
            let newSize = max(requiredPerTriSize, perTriangleBuffers[bufferIndex].length * 2)
            if let newBuffer = device.makeBuffer(length: newSize, options: []) {
                perTriangleBuffers[bufferIndex] = newBuffer
            }
        }
        
        let triPtr = perTriangleBuffers[bufferIndex].contents()
        for i in 0..<triCount {
            let base = triPtr + i * perTriStride
            
            // flipAngle
            let flipAngle: Float = i < flipAngles.count ? flipAngles[i] : 0.0
            base.storeBytes(of: flipAngle, as: Float.self)
            
            // rippleAmplitude
            let rippleAmp: Float = i < rippleAmplitudes.count ? rippleAmplitudes[i] : 0.0
            (base + 4).storeBytes(of: rippleAmp, as: Float.self)
            
            // borderWidth
            let bw: Float = i < borderWidths.count ? borderWidths[i] : 0.0
            (base + 8).storeBytes(of: bw, as: Float.self)
            
            // flipAxisOrigin (3 floats, packed_float3)
            let axisOrigin = i < flipAxisData.count ? flipAxisData[i].origin : SIMD3<Float>(0,0,0)
            (base + 12).storeBytes(of: axisOrigin.x, as: Float.self)
            (base + 16).storeBytes(of: axisOrigin.y, as: Float.self)
            (base + 20).storeBytes(of: axisOrigin.z, as: Float.self)
            
            // flipAxisDirection (3 floats, packed_float3)
            let axisDir = i < flipAxisData.count ? flipAxisData[i].direction : SIMD3<Float>(1,0,0)
            (base + 24).storeBytes(of: axisDir.x, as: Float.self)
            (base + 28).storeBytes(of: axisDir.y, as: Float.self)
            (base + 32).storeBytes(of: axisDir.z, as: Float.self)
            
            // grayscaleColor (3 floats, packed_float3)
            let grayColor = i < grayscaleColors.count ? grayscaleColors[i] : (Float(0.5), Float(0.5), Float(0.5))
            (base + 36).storeBytes(of: grayColor.0, as: Float.self)
            (base + 40).storeBytes(of: grayColor.1, as: Float.self)
            (base + 44).storeBytes(of: grayColor.2, as: Float.self)
        }

        // ── ATOMIC PUBLISH ──
        // ALL four buffers (vertex, index, uniform, perTriangle) for this slot are
        // now fully written. Publish the new state under a single lock so encode()
        // sees a consistent snapshot: either the PREVIOUS frame's data (all old) or
        // THIS frame's data (all new). Never a mix of old counts with new buffers
        // or vice versa.
        //
        // This is the fix for the LLDB RPC server crash: previously,
        // lastWrittenBufferIndex was set at the TOP of this method, so encode()
        // could submit a draw call referencing a buffer mid-write.
        bufferLock.lock()
        self.lastWrittenBufferIndex = bufferIndex
        self.currentVertexCount = safeVertexCount
        self.currentIndexCount = safeIndexCount
        self.hasUnconsumedUpdate = true
        bufferLock.unlock()
    }

    private func normalizePerTriangleArray(
        _ values: [Float],
        triangleCount: Int
    ) -> [Float] {
        guard triangleCount > 0 else { return [] }
        if values.count == triangleCount {
            return values
        }
        if values.count > triangleCount {
            return Array(values.prefix(triangleCount))
        }
        return values + Array(repeating: 0, count: triangleCount - values.count)
    }

    private func normalizeFlipAxisData(
        _ data: [(origin: SIMD3<Float>, direction: SIMD3<Float>)]?,
        triangleCount: Int
    ) -> [(origin: SIMD3<Float>, direction: SIMD3<Float>)] {
        guard triangleCount > 0 else { return [] }
        var normalized = Array(
            repeating: (origin: SIMD3<Float>(0, 0, 0), direction: SIMD3<Float>(1, 0, 0)),
            count: triangleCount
        )
        guard let data, !data.isEmpty else { return normalized }
        for i in 0..<min(data.count, triangleCount) {
            normalized[i] = data[i]
        }
        return normalized
    }

    private func normalizeGrayscaleColors(
        _ colors: [(Float, Float, Float)],
        triangleCount: Int
    ) -> [(Float, Float, Float)] {
        guard triangleCount > 0 else { return [] }
        var normalized = Array(repeating: (Float(0), Float(0), Float(0)), count: triangleCount)
        for i in 0..<min(colors.count, triangleCount) {
            normalized[i] = colors[i]
        }
        return normalized
    }
}

/// Scan Guidance Error
public enum ScanGuidanceError: Error {
    case deviceInitializationFailed
    case pipelineCreationFailed(String)
    case bufferAllocationFailed
}

#endif
