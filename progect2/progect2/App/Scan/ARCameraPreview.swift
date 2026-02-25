//
// ARCameraPreview.swift
// Aether3D
//
// PR#7 Scan Guidance UI — AR Camera Preview
// UIViewRepresentable wrapping ARSCNView with delegate forwarding
// Apple-platform only (ARKit + SwiftUI)
//

import Foundation

#if canImport(ARKit) && canImport(SwiftUI)
import SwiftUI
import ARKit
import SceneKit
#if canImport(MetalKit)
import MetalKit
#endif

/// UIViewRepresentable wrapping ARSCNView
///
/// Architecture:
///   - ARSCNView handles real-time camera + SceneKit rendering
///   - Coordinator acts as both ARSCNViewDelegate and ARSessionDelegate
///   - Per-frame ARSession delegate forwards to ScanViewModel.processARFrame()
///   - Metal mesh overlay will be injected via ARSCNView delegate (future PR)
///
/// Safety:
///   - supportsSceneReconstruction(.mesh) check before enabling LiDAR mesh
///   - dismantleUIView pauses AR session (prevents resource leak)
///   - sessionWasInterrupted auto-pauses capture (phone call safety)
///   - session(didFailWithError:) transitions to .failed (prevents stuck state)
///   - Task { @MainActor } for ALL delegate→ViewModel calls (thread safety)
struct ARCameraPreview: UIViewRepresentable {
    @ObservedObject var viewModel: ScanViewModel

    func makeUIView(context: Context) -> ARSCNView {
        let arView = ARSCNView()
        arView.delegate = context.coordinator
        arView.session.delegate = context.coordinator
        arView.automaticallyUpdatesLighting = true
        arView.rendersCameraGrain = false
        arView.debugOptions = []  // No debug overlay in production

        // Configure AR session
        let configuration = ARWorldTrackingConfiguration()
        if ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) {
            configuration.sceneReconstruction = .mesh
        }
        configuration.environmentTexturing = .automatic
        configuration.isLightEstimationEnabled = true

        // Enable per-frame depth map for TSDF fusion (PR#6 dependency)
        // sceneDepth provides 256×192 depth CVPixelBuffer at 60fps on LiDAR devices
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
            configuration.frameSemantics.insert(.sceneDepth)
        }

        // Start session
        arView.session.run(configuration, options: [.resetTracking, .removeExistingAnchors])

        #if canImport(MetalKit)
        // Transparent Metal overlay for ScanGuidanceRenderPipeline output.
        if let device = MTLCreateSystemDefaultDevice() {
            let overlay = MTKView(frame: .zero, device: device)
            overlay.translatesAutoresizingMaskIntoConstraints = false
            overlay.clearColor = MTLClearColorMake(0, 0, 0, 0)
            overlay.colorPixelFormat = .bgra8Unorm
            overlay.depthStencilPixelFormat = .depth32Float
            overlay.isOpaque = false
            overlay.backgroundColor = .clear
            overlay.framebufferOnly = false
            overlay.enableSetNeedsDisplay = false
            overlay.isPaused = false
            overlay.preferredFramesPerSecond = 60
            overlay.isUserInteractionEnabled = false

            context.coordinator.configureOverlay(mtkView: overlay, device: device)
            arView.addSubview(overlay)
            NSLayoutConstraint.activate([
                overlay.leadingAnchor.constraint(equalTo: arView.leadingAnchor),
                overlay.trailingAnchor.constraint(equalTo: arView.trailingAnchor),
                overlay.topAnchor.constraint(equalTo: arView.topAnchor),
                overlay.bottomAnchor.constraint(equalTo: arView.bottomAnchor)
            ])
        }
        #endif

        // Notify ViewModel that ARKit is ready
        Task { @MainActor in
            viewModel.transition(to: .ready)
        }

        return arView
    }

    func updateUIView(_ uiView: ARSCNView, context: Context) {
        // No dynamic updates needed — delegate handles everything
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(viewModel: viewModel)
    }

    static func dismantleUIView(_ uiView: ARSCNView, coordinator: Coordinator) {
        uiView.session.pause()
    }

    // MARK: - Coordinator

    /// Bridges ARKit delegate callbacks to ScanViewModel
    class Coordinator: NSObject, ARSCNViewDelegate, ARSessionDelegate, @unchecked Sendable {
        let viewModel: ScanViewModel
        #if canImport(MetalKit)
        private weak var overlayView: MTKView?
        private var overlayCommandQueue: MTLCommandQueue?
        private var overlayPipeline: ScanGuidanceRenderPipeline?
        #endif

        /// Frame-dropping guard — prevents ARFrame backpressure.
        /// When processARFrame() takes >16ms (thermal throttle, complex mesh,
        /// heavy evidence computation), 60fps dispatch queues frames faster than
        /// they drain. ARKit retains each ARFrame until its last reference is
        /// released → 11+ retained ARFrames → memory pressure → crash.
        /// With this flag, we process at most ONE frame at a time. Skipped
        /// frames are simply dropped (ARKit releases them immediately).
        ///
        /// Thread safety: Accessed from ARSession delegate (background) and
        /// @MainActor (completion). Uses os_unfair_lock for atomic test-and-set.
        private let frameLock = NSLock()
        private var _isProcessingFrame = false

        /// Thread-safe reset of frame processing flag.
        /// Extracted to a nonisolated synchronous method because NSLock.lock()/unlock()
        /// are unavailable from async contexts (Swift concurrency safety).
        private nonisolated func releaseFrameProcessingLock() {
            frameLock.lock()
            _isProcessingFrame = false
            frameLock.unlock()
        }

        init(viewModel: ScanViewModel) {
            self.viewModel = viewModel
        }

        #if canImport(MetalKit)
        func configureOverlay(mtkView: MTKView, device: MTLDevice) {
            overlayView = mtkView
            overlayCommandQueue = device.makeCommandQueue()
            mtkView.delegate = self
        }
        #endif

        // ARSessionDelegate — called per frame (~60 FPS)
        func session(_ session: ARSession, didUpdate frame: ARFrame) {
            // ── Frame dropping ──
            // If the previous frame is still being processed on the main thread,
            // DROP this frame entirely. This is the #1 defense against ARFrame
            // retention backpressure. Without this, 60fps dispatch into a handler
            // that takes >16ms causes unbounded frame accumulation:
            //   Frame 1 dispatched → still processing
            //   Frame 2 dispatched → queued behind Frame 1
            //   Frame 3 dispatched → queued behind Frame 2
            //   ...
            //   Frame 11 dispatched → 11 ARFrames retained → crash
            //
            // With frame dropping, we process at most 1 frame at a time.
            // Effective FPS = min(60, 1/processTime). At 25ms process time,
            // effective FPS = 40, which is perfectly smooth for AR overlay.
            // Atomic test-and-set: check if idle and claim the slot in one lock region.
            // ARSession delegate calls from a background thread; the reset happens
            // on @MainActor. Without the lock, two frames could both pass the guard.
            frameLock.lock()
            let busy = _isProcessingFrame
            if !busy { _isProcessingFrame = true }
            frameLock.unlock()
            guard !busy else { return }

            // ── Extract ALL needed data from ARFrame ON THIS THREAD ──
            // CRITICAL: Do NOT capture `frame` OR `ARMeshAnchor` in the Task closure!
            // ARMeshAnchor holds strong references to ARKit's Metal geometry buffers.
            // Each retained anchor prevents ARKit from recycling ~500KB of mesh data.
            // At 60fps dispatch, this rapidly accumulates 11+ retained frames → memory
            // pressure → camera pipeline stall → "retaining N ARFrames" warning → freeze.
            //
            // FIX (v7.1): Run MeshExtractor.extract() HERE on the ARSession thread.
            // MeshExtractor is a pure struct with no mutable state — thread-safe.
            // extract() reads vertex/face/normal data from ARMeshAnchor Metal buffers
            // and produces lightweight [ScanTriangle] (just value types: SIMD3, Float, String).
            // Once extract() returns, all ARMeshAnchor references are released immediately.
            // The Task closure captures ONLY the lightweight ScanTriangle array.
            let meshAnchors = frame.anchors.compactMap { $0 as? ARMeshAnchor }
            let extractedTriangles = MeshExtractor().extract(from: meshAnchors)
            // meshAnchors goes out of scope here → ARKit can recycle mesh buffers

            let frameTimestamp = frame.timestamp
            let cameraTransform = frame.camera.transform
            let lightEstimate = frame.lightEstimate
            #if os(iOS)
            let orientation = overlayView?.window?.windowScene?.interfaceOrientation ?? .portrait
            let viewportSize = overlayView?.drawableSize ?? CGSize(width: 1080, height: 1920)
            let viewMatrix = frame.camera.viewMatrix(for: orientation)
            let projectionMatrix = frame.camera.projectionMatrix(
                for: orientation,
                viewportSize: viewportSize,
                zNear: 0.001,
                zFar: 1000.0
            )
            #else
            let viewMatrix = simd_inverse(cameraTransform)
            let projectionMatrix = matrix_identity_float4x4
            #endif

            // Task closure captures ONLY lightweight value types — no ARKit objects.
            Task { @MainActor [weak self] in
                guard let self = self else { return }
                self.viewModel.processARFrame(
                    timestamp: frameTimestamp,
                    cameraTransform: cameraTransform,
                    lightEstimate: lightEstimate,
                    preExtractedTriangles: extractedTriangles,
                    viewMatrix: viewMatrix,
                    projectionMatrix: projectionMatrix
                )
                #if canImport(MetalKit)
                self.overlayPipeline = self.viewModel.currentRenderPipelineForOverlay()
                #endif
                // Release frame-processing lock AFTER all work completes.
                // Next ARFrame callback can now proceed.
                self.releaseFrameProcessingLock()
            }
        }

        // Suppress ARSCNView default mesh rendering — we render via custom Metal overlay
        func renderer(_ renderer: SCNSceneRenderer, nodeFor anchor: ARAnchor) -> SCNNode? {
            if anchor is ARMeshAnchor {
                return SCNNode()  // Empty node suppresses SceneKit's default mesh visualization
            }
            return nil
        }

        // ARSession error handling
        func session(_ session: ARSession, didFailWithError error: Error) {
            Task { @MainActor in
                viewModel.transition(to: .failed)
            }
        }

        // Session interrupted (phone call, notification, etc.)
        func sessionWasInterrupted(_ session: ARSession) {
            Task { @MainActor in
                if viewModel.scanState.isActive {
                    viewModel.pauseCapture()
                }
            }
        }

        // Session interruption ended
        func sessionInterruptionEnded(_ session: ARSession) {
            // Session automatically resumes — user can tap to continue
        }
    }
}

#if canImport(MetalKit)
extension ARCameraPreview.Coordinator: MTKViewDelegate {
    func mtkView(_ view: MTKView, drawableSizeWillChange size: CGSize) {}

    func draw(in view: MTKView) {
        guard let pipeline = overlayPipeline,
              let renderPass = view.currentRenderPassDescriptor,
              let drawable = view.currentDrawable else {
            return
        }
        if overlayCommandQueue == nil {
            overlayCommandQueue = view.device?.makeCommandQueue()
        }
        guard let queue = overlayCommandQueue,
              let commandBuffer = queue.makeCommandBuffer() else {
            return
        }
        pipeline.encode(into: commandBuffer, renderPassDescriptor: renderPass)
        commandBuffer.present(drawable)
        commandBuffer.commit()
    }
}
#endif

#endif
