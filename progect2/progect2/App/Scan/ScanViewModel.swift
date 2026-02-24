//
// ScanViewModel.swift
// Aether3D
//
// PR#7 Scan Guidance UI — Scan ViewModel (THE ORCHESTRATOR)
// Wires all subsystems: ARKit → MeshExtractor → Core algorithms → Metal pipeline → SwiftUI
// Apple-platform only (ARKit + SwiftUI)
//

import Foundation

#if canImport(SwiftUI) && canImport(ARKit)
import SwiftUI
import ARKit
import simd
import Aether3DCore
import CAetherNativeBridge
#if canImport(QuartzCore)
import QuartzCore
#endif

/// THE ORCHESTRATOR — @MainActor ViewModel that wires ALL subsystems together
///
/// Architecture:
///   ARFrame (60fps) → MeshExtractor → [ScanTriangle]
///     → SpatialHashAdjacency (O(n), rebuilt every ~1s when mesh changes)
///     → FlipAnimationController (threshold crossing detection)
///     → RipplePropagationEngine (BFS wave propagation)
///     → GuidanceHapticEngine + GuidanceToastPresenter (multimodal feedback)
///     → ScanGuidanceRenderPipeline (Metal overlay, graceful nil if unavailable)
///     → @Published state → SwiftUI rerender
///
/// Pattern: @MainActor + ObservableObject + @Published + Task
/// (consistent with PipelineDemoViewModel)
@MainActor
final class ScanViewModel: ObservableObject {

    // MARK: - Published State (drives SwiftUI)
    @Published var scanState: ScanState = .initializing
    @Published var isCapturing: Bool = false
    @Published var elapsedTime: TimeInterval = 0

    // MARK: - Existing Components (REUSE, DO NOT RECREATE)
    let toastPresenter: GuidanceToastPresenter
    let hapticEngine: GuidanceHapticEngine
    private let completionBridge: ScanCompletionBridge

    // MARK: - Core Algorithm Subsystems (from Core/)
    private let wedgeGenerator = WedgeGeometryGenerator()
    private let flipController = FlipAnimationController()
    private let rippleEngine = RipplePropagationEngine()
    private let borderCalculator = AdaptiveBorderCalculator()
    private let thermalAdapter = ThermalQualityAdapter()

    // MARK: - App Platform Components
    private let grayscaleMapper = GrayscaleMapper()
    private let lightEstimator = EnvironmentLightEstimator()
    private let meshExtractor = MeshExtractor()

    // MARK: - Metal Pipeline (graceful degradation)
    // createRenderPipelines() contains fatalError() in Phase 2
    // Pipeline is nil — UI works perfectly without mesh overlay
    // When Metal shaders are ready, pipeline auto-activates
    private var renderPipeline: ScanGuidanceRenderPipeline?

    // MARK: - State
    private var meshTriangles: [ScanTriangle] = []
    private var adjacencyGraph: (any AdjacencyProvider)?
    private let patchEvidenceMap = PatchEvidenceMap()
    private var patchDisplayMap = PatchDisplayMap()
    private var currentPatchDisplaySnapshot: [String: Double] = [:]
    private var previousPatchDisplaySnapshot: [String: Double] = [:]
    private var captureStartTime: Date?
    private var elapsedTimer: Timer?
    private var frameCounter: Int = 0
    private var lastMotionSample: (position: SIMD3<Float>, timestamp: TimeInterval)?
    private var lastViewMatrix: simd_float4x4 = matrix_identity_float4x4
    private var lastProjectionMatrix: simd_float4x4 = matrix_identity_float4x4

    // MARK: - Thermal Monitoring
    private var thermalObserver: NSObjectProtocol?

    // MARK: - Initialization

    init() {
        self.toastPresenter = GuidanceToastPresenter()
        self.hapticEngine = GuidanceHapticEngine()
        self.completionBridge = ScanCompletionBridge(hapticEngine: hapticEngine)

        // Metal pipeline initialization
        #if canImport(Metal)
        if let device = MTLCreateSystemDefaultDevice() {
            self.renderPipeline = try? ScanGuidanceRenderPipeline(device: device)
        } else {
            self.renderPipeline = nil
        }
        #else
        self.renderPipeline = nil
        #endif

        // Unify flip animation time source with render pipeline's CACurrentMediaTime()
        #if canImport(QuartzCore)
        flipController.setTimeSource { CACurrentMediaTime() }
        #endif

        // Inject Oklab perceptual color mapper into wedge geometry generator
        wedgeGenerator.colorMapper = { [grayscaleMapper] display in
            grayscaleMapper.oklabColor(for: display)
        }

        setupThermalMonitoring()
    }

    @MainActor
    deinit {
        elapsedTimer?.invalidate()
        if let observer = thermalObserver {
            NotificationCenter.default.removeObserver(observer)
        }
    }

    // MARK: - State Machine Transitions

    /// VALIDATED state transition — rejects invalid transitions
    ///
    /// Every state change goes through this single gateway.
    /// Invalid transitions trigger assertionFailure in DEBUG, silently ignored in RELEASE.
    func transition(to newState: ScanState) {
        guard scanState.allowedTransitions.contains(newState) else {
            #if DEBUG
            assertionFailure("Invalid state transition: \(scanState) → \(newState)")
            #endif
            return
        }

        let oldState = scanState
        scanState = newState

        // Side effects
        switch (oldState, newState) {
        case (_, .capturing):
            isCapturing = true
            captureStartTime = captureStartTime ?? Date()
            startElapsedTimer()

        case (.capturing, .paused):
            isCapturing = false
            stopElapsedTimer()

        case (.paused, .capturing):
            isCapturing = true
            startElapsedTimer()

        case (_, .finishing):
            isCapturing = false
            stopElapsedTimer()
            NotificationCenter.default.post(name: .scanDidComplete, object: nil)

        case (_, .completed):
            isCapturing = false
            resetSubsystems()

        case (_, .failed):
            isCapturing = false
            stopElapsedTimer()
            resetSubsystems()

        default:
            break
        }
    }

    // MARK: - User Actions

    func startCapture() {
        transition(to: .capturing)
    }

    func pauseCapture() {
        transition(to: .paused)
    }

    func resumeCapture() {
        transition(to: .capturing)
    }

    /// Stop capture and produce a ScanRecord
    ///
    /// - Returns: ScanRecord with coverage/triangle/duration metadata, or nil if invalid state
    func stopCapture() -> ScanRecord? {
        guard scanState.canFinish else { return nil }

        transition(to: .finishing)

        let record = ScanRecord(
            coveragePercentage: calculateOverallCoverage(),
            triangleCount: meshTriangles.count,
            durationSeconds: elapsedTime
        )

        transition(to: .completed)
        return record
    }

    /// Exposes the render pipeline handle for overlay draw delegation.
    func currentRenderPipelineForOverlay() -> ScanGuidanceRenderPipeline? {
        renderPipeline
    }

    // MARK: - ARKit Frame Processing

    /// Called from ARSCNView delegate on EVERY frame (~60 FPS)
    /// PERFORMANCE CRITICAL — must complete within frame budget (~16ms)
    func processARFrame(
        frame: ARFrame,
        meshAnchors: [ARMeshAnchor],
        viewMatrix: simd_float4x4 = matrix_identity_float4x4,
        projectionMatrix: simd_float4x4 = matrix_identity_float4x4
    ) {
        guard scanState.isActive else { return }

        // Store matrices for render pipeline
        lastViewMatrix = viewMatrix
        lastProjectionMatrix = projectionMatrix

        // Step 1: Extract triangles from ARKit mesh
        let extractedTriangles = meshExtractor.extract(from: meshAnchors)
        let newTriangles = stabilizePatchIdentities(extractedTriangles)

        // Only rebuild adjacency if mesh changed significantly
        let meshChanged = newTriangles.count != meshTriangles.count
        if meshChanged {
            meshTriangles = newTriangles
        }

        // Rebuild adjacency graph using SpatialHashAdjacency (O(n), not O(n²))
        // Only rebuild every 60 frames (~1s) when mesh has changed
        // SpatialHashAdjacency handles ANY mesh size (50,000+ triangles) in ~50ms
        if meshChanged && (frameCounter % 60 == 0) {
            rebuildAdjacencyGraph()
        }
        frameCounter += 1

        // Step 2: Update patch display map and snapshots
        previousPatchDisplaySnapshot = currentPatchDisplaySnapshot
        updatePatchDisplayMap()
        currentPatchDisplaySnapshot = makeDisplaySnapshot()

        // Step 3: Thermal-aware quality control (proactive + reactive + cool-down)
        thermalAdapter.evaluateProactiveThermal()
        thermalAdapter.evaluateCoolDown()
        let tier = thermalAdapter.currentTier
        let maxTriangles = tier.maxTriangles
        let limitedTriangles = Array(meshTriangles.prefix(maxTriangles))

        // Step 4: Check flip thresholds (if animation enabled for this tier)
        if tier.enableFlipAnimation, let adj = adjacencyGraph {
            let crossedIndices = flipController.checkThresholdCrossings(
                previousDisplay: previousPatchDisplaySnapshot,
                currentDisplay: currentPatchDisplaySnapshot,
                triangles: limitedTriangles,
                adjacencyGraph: adj
            )

            // Step 5: Spawn ripples for crossed triangles (if enabled)
            if tier.enableRipple {
                let now = ProcessInfo.processInfo.systemUptime
                for triIndex in crossedIndices {
                    rippleEngine.spawn(
                        sourceTriangle: triIndex,
                        adjacencyGraph: adj,
                        timestamp: now
                    )
                }
            }
        }

        // Step 6: Haptic/Toast triggers (condition-based)
        let timestamp = ProcessInfo.processInfo.systemUptime

        // Motion too fast check
        let velocity = extractMotionMagnitude(from: frame.camera.transform, timestamp: frame.timestamp)
        let motionThresholdScale: Double = tier == .critical ? 1.5 : 1.0
        if tier.enableHaptics && velocity > ScanGuidanceConstants.hapticMotionThreshold * motionThresholdScale {
            _ = hapticEngine.fire(
                pattern: .motionTooFast,
                timestamp: timestamp,
                toastPresenter: toastPresenter
            )
        }

        // Exposure check
        if let lightEstimate = frame.lightEstimate {
            let ambientIntensity = lightEstimate.ambientIntensity
            // Normal range: 250-2000 lux
            if tier.enableHaptics && (ambientIntensity < 250 || ambientIntensity > 5000) {
                _ = hapticEngine.fire(
                    pattern: .exposureAbnormal,
                    timestamp: timestamp,
                    toastPresenter: toastPresenter
                )
            }
        }

        // Step 7: Query animation state for render pipeline
        let triIndices = Array(0..<limitedTriangles.count)

        // Tick flip controller and extract angles
        _ = flipController.tick(deltaTime: 1.0 / 60.0)
        let flipAngles = flipController.getFlipAngles(for: triIndices)
        let flipAxisData: [(origin: SIMD3<Float>, direction: SIMD3<Float>)] = triIndices.compactMap {
            flipController.getFlipAxis(for: $0)
        }

        // Tick ripple engine and extract amplitudes
        let rippleNow = ProcessInfo.processInfo.systemUptime
        _ = rippleEngine.tick(currentTime: rippleNow)
        let rippleAmplitudes = rippleEngine.getRippleAmplitudes(for: triIndices, currentTime: rippleNow)

        // Step 8: Update render pipeline (if Metal is available)
        renderPipeline?.update(
            displaySnapshot: currentPatchDisplaySnapshot,
            colorStates: [:],
            meshTriangles: limitedTriangles,
            lightEstimate: frame.lightEstimate,
            cameraTransform: frame.camera.transform,
            viewMatrix: lastViewMatrix,
            projectionMatrix: lastProjectionMatrix,
            frameDeltaTime: 1.0 / 60.0,
            precomputedFlipAngles: flipAngles,
            precomputedRippleAmplitudes: rippleAmplitudes,
            precomputedFlipAxisData: flipAxisData,
            gpuDurationMs: nil
        )
    }

    // MARK: - Private Helpers

    /// Update display values for visible patches through PatchEvidenceMap → PatchDisplayMap.
    ///
    /// Data flow: ARKit mesh → PatchEvidenceMap (Choquet/DS evidence ledger) → PatchDisplayMap (1-Euro smoothed display)
    /// Each visible triangle feeds an observation into the evidence system. The evidence value
    /// (computed via Choquet integral + DS theory) then drives the display map with proper locking.
    private func updatePatchDisplayMap() {
        let timestampMs = Int64(Date().timeIntervalSince1970 * 1000.0)
        let frameId = "frame-\(frameCounter)"

        for triangle in meshTriangles {
            // Step A: Compute observation quality from triangle geometry (C++ core)
            let ledgerQuality = Double(aether_observation_quality_from_area(
                triangle.areaSqM,
                Float(ScanGuidanceConstants.areaFactorReference)
            ))

            // Step B: Feed observation into evidence ledger (Choquet + DS gates)
            patchEvidenceMap.update(
                patchId: triangle.patchId,
                ledgerQuality: ledgerQuality,
                verdict: .good,  // ARKit mesh triangles are pre-validated geometry
                frameId: frameId,
                timestampMs: timestampMs
            )

            // Step C: Read evidence → feed into display map with lock state
            let evidence = patchEvidenceMap.evidence(for: triangle.patchId)
            let isLocked = patchEvidenceMap.entry(for: triangle.patchId)?.isLocked ?? false

            _ = patchDisplayMap.update(
                patchId: triangle.patchId,
                target: evidence,
                timestampMs: timestampMs,
                isLocked: isLocked
            )
        }
    }

    /// Convert PatchDisplayMap entries to render/animation snapshot.
    private func makeDisplaySnapshot() -> [String: Double] {
        Dictionary(uniqueKeysWithValues: patchDisplayMap.snapshotSorted().map { ($0.patchId, $0.display) })
    }

    /// Extract frame-to-frame camera translation speed in m/s.
    private func extractMotionMagnitude(from transform: simd_float4x4, timestamp: TimeInterval) -> Double {
        let position = SIMD3<Float>(
            transform.columns.3.x,
            transform.columns.3.y,
            transform.columns.3.z
        )
        defer {
            lastMotionSample = (position: position, timestamp: timestamp)
        }

        guard let previous = lastMotionSample else {
            return 0
        }
        var currentPos = aether_float3_t(
            x: position.x,
            y: position.y,
            z: position.z
        )
        var previousPos = aether_float3_t(
            x: previous.position.x,
            y: previous.position.y,
            z: previous.position.z
        )
        let speed = aether_camera_translation_speed(
            &currentPos,
            &previousPos,
            timestamp,
            previous.timestamp,
            1.0 / 240.0
        )
        return speed.isFinite ? max(speed, 0.0) : 0.0
    }

    /// Calculate overall scan coverage [0, 1]
    private func calculateOverallCoverage() -> Double {
        guard !currentPatchDisplaySnapshot.isEmpty else { return 0.0 }
        let total = currentPatchDisplaySnapshot.values.reduce(0.0, +)
        return total / Double(currentPatchDisplaySnapshot.count)
    }

    /// Start elapsed time timer (0.1s resolution)
    private func startElapsedTimer() {
        elapsedTimer?.invalidate()
        elapsedTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self = self, let start = self.captureStartTime else { return }
                self.elapsedTime = Date().timeIntervalSince(start)
            }
        }
    }

    /// Stop elapsed time timer
    private func stopElapsedTimer() {
        elapsedTimer?.invalidate()
        elapsedTimer = nil
    }

    /// Rebuild adjacency graph using SpatialHashAdjacency — O(n) for ANY mesh size
    ///
    /// Unlike MeshAdjacencyGraph (O(n²)), SpatialHashAdjacency uses spatial hashing
    /// to build adjacency in O(n) time. Safe for 50,000+ triangle meshes.
    /// 20,000 triangles → ~20ms (vs MeshAdjacencyGraph's ~3 seconds)
    private func rebuildAdjacencyGraph() {
        adjacencyGraph = SpatialHashAdjacency(triangles: meshTriangles)
    }

    /// Stabilize patch identities across frames.
    /// MeshExtractor.stablePatchIdentity() already generates spatially-quantized patch IDs.
    /// This method is the hook for future temporal consistency improvements.
    private func stabilizePatchIdentities(_ triangles: [ScanTriangle]) -> [ScanTriangle] {
        return triangles
    }

    /// Reset all subsystems for next scan session
    private func resetSubsystems() {
        flipController.reset()
        rippleEngine.reset()
        patchDisplayMap.reset()
        currentPatchDisplaySnapshot.removeAll()
        previousPatchDisplaySnapshot.removeAll()
        meshTriangles.removeAll()
        adjacencyGraph = nil
        frameCounter = 0
        lastMotionSample = nil
    }

    /// Setup thermal state monitoring (iOS only)
    private func setupThermalMonitoring() {
        #if os(iOS)
        thermalObserver = NotificationCenter.default.addObserver(
            forName: ProcessInfo.thermalStateDidChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.thermalAdapter.updateThermalState(ProcessInfo.processInfo.thermalState)
            }
        }
        // Set initial thermal state
        thermalAdapter.updateThermalState(ProcessInfo.processInfo.thermalState)
        #endif
    }
}

#endif
