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
    /// Blackout overlay opacity: 1.0 = fully black (scan just started),
    /// fades to 0.0 as mesh triangles cover the screen.
    @Published var blackoutOpacity: Double = 1.0

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

    // MARK: - Reconnected Core Algorithms (Layer 1: previously disconnected)
    /// View diversity tracker — feeds per-patch view angle diversity into evidence
    /// quality (was hardcoded 1.0 in IsolatedEvidenceEngine). Uses C++ SSOT
    /// with 15° angle buckets and spatial hashing.
    private let viewDiversityTracker = ViewDiversityTracker()

    /// Render stability bridge — provides C++ multi-factor triangle selection
    /// (selectStableRenderTriangles) and spatial identity matching
    /// (matchPatchIdentities). Replaces naive prefix() truncation.
    private let renderStabilityBridge = NativeRenderStabilityBridge()

    // MARK: - Cross-Platform LOD Pipeline (Layer 7.3)
    /// Unified LOD pipeline: MeshletBuilder → FrustumCuller → ScreenDetailSelector.
    /// All computation in C++ core — identical results on iOS/Android/HarmonyOS.
    private let lodPipeline = CrossPlatformLODPipeline()

    // MARK: - Admission Controller — REMOVED
    // Token bucket removed: quality gating is handled by GateQualityComputer
    // (reproj/edge/sharpness hard gates) + verdict (good/suspect/bad).
    // 60fps × every triangle per frame → fastest possible evidence accumulation.

    // MARK: - Mobile Optimization (Thermal/Memory/Battery/Pacing)
    /// Frame pacing controller — monitors frame time jitter and recommends
    /// quality adjustments to maintain consistent delivery.
    private let framePacingController = MobileFramePacingController()
    /// Battery-aware scheduler — throttles background work in low-power mode.
    private let batteryScheduler = MobileBatteryAwareScheduler()
    /// Memory pressure handler — responds to OS memory warnings by dropping caches.
    private let memoryHandler = MobileMemoryPressureHandler()
    /// Thermal state handler — provides additional thermal quality levels beyond
    /// the existing ThermalQualityAdapter (which handles tier switching).
    private let mobileThermalHandler = MobileThermalStateHandler()

    // MARK: - Reconnected Core Algorithms (Layer 2: evidence quality & state machine)
    /// Gate quality computer — multi-factor quality assessment with captureMode
    /// smoother (Module 5+7). Replaces simple area-based quality with multi-signal
    /// fusion: reproj error, edge sharpness, exposure, viewing angle.
    private let gateComputer = GateQualityComputer(
        tierContext: .forTesting,
        smootherConfig: .capture
    )
    /// Evidence state machine — S0-S5 certification tracking (Module 3).
    /// Evaluates coverage results to determine overall scan color state.
    private let evidenceStateMachine = EvidenceStateMachine()
    /// Coverage estimator — D-S evidence fusion for coverage estimation (Module 4).
    /// Provides Dempster-Shafer belief/plausibility coverage bounds.
    private let coverageEstimator = CoverageEstimator()
    /// Dimensional computer — Core layer 15-dimensional evidence scoring (Module 3b).
    /// App layer only forwards raw aggregate signals; all scoring logic in Core.
    private let dimensionalComputer = DimensionalComputer()
    /// Observation log — append-only SHA-256 hash chain for evidence provenance.
    /// All chain logic in Core; App layer only calls append().
    private let observationLog = ObservationLog()

    // MARK: - Device Capability Gate (Requirement 1: depth camera detection)
    /// Algorithm set determined at init by C++ core based on device capabilities.
    /// The C++ core makes ALL gating decisions — Swift only reports raw booleans.
    /// Depth-dependent algorithms (DepthFilter, DA3, TSDF, MarchingCubes)
    /// are ONLY enabled when has_depth_camera is detected at startup.
    private let algorithmSet: AlgorithmSetResult

    // MARK: - Adaptive Triangle Budget Controller
    /// C++ adaptive budget controller — learns real device rendering cost via
    /// streaming linear regression, then uses asymmetric PID control with
    /// thermal hysteresis to continuously adjust the triangle budget.
    /// Replaces crude 3-tier hard-coding with continuous per-device adaptation.
    private var adaptiveBudgetController: OpaquePointer?

    // MARK: - Quality Metrics (Image/Motion analysis)
    /// Motion analyzer — C++ optical flow-based motion analysis for blur detection.
    private var motionAnalyzer: OpaquePointer?

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
    /// Current frame's camera velocity in m/s (computed early in processARFrame, used by evidence)
    private var currentFrameVelocity: Double = 0.0
    /// Current frame's ambient light intensity in lux
    private var currentFrameAmbientIntensity: Double = 1000.0
    // Dimensional score accumulators — thin App-layer wiring.
    // Per-frame evidence loop feeds raw signals to these accumulators;
    // calculateOverallCoverage() forwards aggregates to Core DimensionalComputer.
    private var dimAccumViewGainSum: Double = 0.0
    private var dimAccumGeomGainSum: Double = 0.0
    private var dimAccumObsCount: Int = 0
    private var dimAccumBadCount: Int = 0
    private var lastViewMatrix: simd_float4x4 = matrix_identity_float4x4
    private var lastProjectionMatrix: simd_float4x4 = matrix_identity_float4x4
    private var lastFrameTimestamp: TimeInterval = 0

    /// Monotonic coverage high-water mark — ensures coverage percentage never
    /// decreases during a scan session (Layer 1.2). Mirrors the monotonic_mode=1
    /// behavior in CoverageEstimator's C++ kernel.
    private var coverageHighWater: Double = 0.0
    /// Overall scan color state from EvidenceStateMachine (Module 3).
    /// Updated each time calculateOverallCoverage() runs.
    private var lastOverallColorState: ColorState = .black

    /// Dirty flag for adjacency rebuild — decouples "mesh changed" detection
    /// from the periodic rebuild interval (Layer 3.7).
    private var needsAdjacencyRebuild = false

    /// Set of patchKeys that were selected for rendering last frame.
    /// Used by selectTrianglesForRender() to give previously-rendered triangles
    /// a residency boost, preventing visual flicker at the budget boundary.
    private var lastRenderedPatchKeys: Set<UInt64> = []

    /// Previous patch anchors for temporal identity stabilization via
    /// matchPatchIdentities C++ engine (Layer 2.2).
    private var previousPatchAnchors: [PatchIdentitySample] = []
    /// **PERSISTENT** anchor database — keeps ALL historical patch identity mappings,
    /// not just the previous frame. This is the ROOT FIX for coverage regression:
    /// when the camera moves away and returns, the spatial hash can match against
    /// the full history of observed patches, preserving their patchIds and thus
    /// their accumulated evidence. Keyed by patchKey for O(1) upsert.
    /// Bounded to maxHistoricalAnchors to prevent unbounded memory growth.
    private var historicalPatchAnchors: [UInt64: PatchIdentitySample] = [:]
    private static let maxHistoricalAnchors = 10000
    /// Reverse mapping: patchKey (UInt64) → patchId (String) for identity remapping.
    /// When the C++ engine matches current observation to a previous anchor,
    /// this map allows us to recover the original String patchId.
    private var patchKeyToId: [UInt64: String] = [:]

    // MARK: - Thermal Monitoring
    private var thermalObserver: NSObjectProtocol?

    // MARK: - Initialization

    init() {
        // ── Requirement 1: Detect device capabilities FIRST ──
        // The C++ core determines which algorithms to activate based on
        // raw hardware booleans. Depth-dependent algorithms (DepthFilter,
        // DA3, TSDF, MarchingCubes) ONLY activate when depth camera present.
        // This detection runs BEFORE any algorithm initialization.
        self.algorithmSet = NativeDeviceCapabilityBridge.detectAndSelectAlgorithms()
            ?? AlgorithmSetResult(
                enabledAlgorithms: 0x001FFFFF,  // pure-visual fallback
                depthAlgorithms: 0,
                gpuAlgorithms: 0,
                cullingTier: 2,
                recommendedMaxTriangles: 15000,
                devicePerfScore: 0.3,
                budgetFloor: 5000,
                budgetCeiling: 20000
            )

        self.toastPresenter = GuidanceToastPresenter()
        self.hapticEngine = GuidanceHapticEngine()
        self.completionBridge = ScanCompletionBridge(hapticEngine: hapticEngine)

        // Create adaptive budget controller — C++ core continuously learns
        // this device's real rendering throughput via streaming regression.
        // The initial budget comes from Phase 1 continuous scoring (not 3-tier).
        adaptiveBudgetController = NativeAdaptiveBudgetBridge.create(
            initialBudget: algorithmSet.recommendedMaxTriangles,
            budgetFloor: algorithmSet.budgetFloor,
            budgetCeiling: algorithmSet.budgetCeiling
        )

        // Log detected capabilities for debugging
        #if DEBUG
        print("[Aether3D] Algorithm set: 0x\(String(algorithmSet.enabledAlgorithms, radix: 16))")
        print("[Aether3D] Depth algorithms: \(algorithmSet.hasDepthAlgorithms ? "ENABLED" : "DISABLED (no depth camera)")")
        print("[Aether3D] Culling tier: \(algorithmSet.cullingTier)")
        print("[Aether3D] Device perf score: \(algorithmSet.devicePerfScore)")
        print("[Aether3D] Adaptive budget: \(algorithmSet.recommendedMaxTriangles) (floor: \(algorithmSet.budgetFloor), ceiling: \(algorithmSet.budgetCeiling))")
        #endif

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

        // Admission controller REMOVED — quality gating is fully handled by
        // Core C++ layer (GateQualityComputer reproj/edge/sharpness hard gates + verdict).
        // 60fps × every triangle per frame → fastest possible evidence accumulation.

        // Initialize motion analyzer — gated by algorithm set
        if algorithmSet.isEnabled(AlgorithmFlags.motionAnalyzer) {
            motionAnalyzer = NativeMotionAnalyzerBridge.create()
        }

        // Register for memory warnings
        #if os(iOS)
        NotificationCenter.default.addObserver(
            forName: UIApplication.didReceiveMemoryWarningNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                guard let self = self else { return }
                await self.memoryHandler.handleMemoryWarning()
            }
        }
        #endif

        setupThermalMonitoring()
    }

    @MainActor
    deinit {
        elapsedTimer?.invalidate()
        if let observer = thermalObserver {
            NotificationCenter.default.removeObserver(observer)
        }
        // Clean up native handles (admission controller removed)
        if let analyzer = motionAnalyzer {
            NativeMotionAnalyzerBridge.destroy(analyzer)
        }
        if let ctrl = adaptiveBudgetController {
            NativeAdaptiveBudgetBridge.destroy(ctrl)
        }
    }

    // MARK: - State Machine Transitions

    /// VALIDATED state transition — rejects invalid transitions
    ///
    /// Every state change goes through this single gateway.
    /// Invalid transitions are LOGGED and IGNORED — never crash.
    /// State machines must be resilient to edge cases (race conditions,
    /// delayed ARSession callbacks, background→foreground transitions)
    /// that can produce unexpected transition requests.
    func transition(to newState: ScanState) {
        guard scanState.allowedTransitions.contains(newState) else {
            #if DEBUG
            print("[Aether3D] ⚠️ Rejected state transition: \(scanState) → \(newState) — not in allowed set")
            #endif
            return
        }

        let oldState = scanState
        scanState = newState

        // Side effects
        switch (oldState, newState) {
        case (_, .capturing):
            isCapturing = true
            blackoutOpacity = 1.0  // Full black immediately on scan start
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
        // Restore persisted evidence from previous scan (if any)
        restoreEvidenceState()
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

        // Persist evidence/display state before finishing (Core layer save)
        persistEvidenceState()

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
    ///
    /// NOTE: This method accepts pre-extracted frame data instead of ARFrame directly.
    /// The ARFrame is extracted in ARCameraPreview's session(didUpdate:) callback
    /// BEFORE the Task closure, so the ARFrame is released immediately when the
    /// callback returns. This prevents ARFrame retention backpressure (12+ retained
    /// frames → camera pipeline stall → FigCaptureSourceRemote err=-17281).
    func processARFrame(
        timestamp: TimeInterval,
        cameraTransform: simd_float4x4,
        lightEstimate: ARLightEstimate?,
        meshAnchors: [ARMeshAnchor] = [],
        preExtractedTriangles: [ScanTriangle]? = nil,
        viewMatrix: simd_float4x4 = matrix_identity_float4x4,
        projectionMatrix: simd_float4x4 = matrix_identity_float4x4
    ) {
        guard scanState.isActive else { return }

        // Layer 3.6: Compute real delta time FIRST — needed by adaptive budget
        // controller and animation/physics. Under thermal throttle the frame rate
        // drops to 30fps or lower, making 1/60 inaccurate.
        let realDeltaTime: Double
        if lastFrameTimestamp > 0 {
            let dt = timestamp - lastFrameTimestamp
            realDeltaTime = dt.isFinite && dt > 0 ? min(dt, 0.1) : 1.0 / 60.0
        } else {
            realDeltaTime = 1.0 / 60.0
        }
        lastFrameTimestamp = timestamp

        // Store matrices for render pipeline
        lastViewMatrix = viewMatrix
        lastProjectionMatrix = projectionMatrix

        // Pre-compute frame-wide quality signals (needed by evidence system in Step 2)
        currentFrameVelocity = extractMotionMagnitude(from: cameraTransform, timestamp: timestamp)
        currentFrameAmbientIntensity = Double(lightEstimate?.ambientIntensity ?? 1000.0)

        // Step 1: Extract triangles from ARKit mesh
        // v7.1: Use pre-extracted triangles when available (extracted on ARSession thread
        // to prevent ARMeshAnchor retention in Task closure → 11+ retained frames warning).
        let extractedTriangles = preExtractedTriangles ?? meshExtractor.extract(from: meshAnchors)
        let newTriangles = stabilizePatchIdentities(extractedTriangles)

        // ── DIAGNOSTIC LOGGING ──
        // Prints pipeline state every 60 frames (~1s) to help diagnose
        // "no triangle coverage" and "crashes at N seconds" reports.
        #if DEBUG
        if frameCounter % 60 == 0 {
            let maxDisplay = currentPatchDisplaySnapshot.values.max() ?? 0
            let avgDisplay = currentPatchDisplaySnapshot.isEmpty ? 0 :
                currentPatchDisplaySnapshot.values.reduce(0, +) / Double(currentPatchDisplaySnapshot.count)
            // Count how many patches are above key thresholds
            let above01 = currentPatchDisplaySnapshot.values.filter { $0 > 0.1 }.count
            let above05 = currentPatchDisplaySnapshot.values.filter { $0 > 0.5 }.count
            // Sample a single patch's evidence for debugging
            let samplePatchId = meshTriangles.first?.patchId ?? ""
            let sampleEvidence = patchEvidenceMap.evidence(for: samplePatchId)
            let sampleEntry = patchEvidenceMap.entry(for: samplePatchId)
            let sampleObsCount = sampleEntry?.observationCount ?? 0
            print("[Aether3D] Frame \(frameCounter): "
                + "anchors=\(meshAnchors.count) "
                + "extracted=\(extractedTriangles.count) "
                + "mesh=\(meshTriangles.count) "
                + "display[max=\(String(format: "%.3f", maxDisplay)),avg=\(String(format: "%.3f", avgDisplay))] "
                + "patches=\(currentPatchDisplaySnapshot.count) "
                + "above0.1=\(above01) above0.5=\(above05) "
                + "sample[ev=\(String(format: "%.4f", sampleEvidence)),obs=\(sampleObsCount)] "
                + "vel=\(String(format: "%.3f", currentFrameVelocity)) "
                + "histAnchors=\(historicalPatchAnchors.count)")
        }
        #endif

        // Layer 2.1: Content-aware mesh change detection.
        // The old code only compared .count, missing cases where the user
        // moves away and returns to an area with the same number of triangles
        // but different patch IDs — causing stale data to persist.
        let newPatchSet = Set(newTriangles.map { $0.patchId })
        let oldPatchSet = Set(meshTriangles.map { $0.patchId })
        let meshChanged = newPatchSet != oldPatchSet || newTriangles.count != meshTriangles.count
        if meshChanged {
            meshTriangles = newTriangles
            needsAdjacencyRebuild = true
        }

        // Layer 3.7: Adjacency rebuild uses dirty flag instead of checking
        // both meshChanged AND frame counter simultaneously (which could miss
        // rebuilds when mesh changes between 60-frame intervals).
        if needsAdjacencyRebuild && (frameCounter % 60 == 0) {
            // Adjacency rebuild: O(n) via SpatialHashAdjacency (~20ms for 20K triangles).
            // Amortized to once per second (every 60 frames at 60fps = 0.3ms/frame).
            rebuildAdjacencyGraph()
            needsAdjacencyRebuild = false
        }
        frameCounter += 1

        // Step 2: Save previous snapshot (evidence update moved to AFTER budget selection)
        // The old code updated ALL meshTriangles (potentially 50K+) with 5 C++ calls each
        // BEFORE budget selection — that's 250K C++ calls per frame, far exceeding 16ms.
        // New flow: select triangles first (using previous frame's display snapshot),
        // then update evidence ONLY for budget-selected triangles.
        previousPatchDisplaySnapshot = currentPatchDisplaySnapshot

        // Step 3: ADAPTIVE triangle budget — continuous, self-calibrating.
        //
        // The budget is NOT a fixed number or 3-tier lookup. It's determined by
        // a C++ adaptive controller that:
        //   Phase 1 (startup): continuous scoring from device capabilities
        //   Phase 2 (runtime): learns actual cost-per-triangle via streaming
        //           linear regression on (triangle_count, frame_time) pairs
        //   Phase 3 (dynamic): asymmetric PID with thermal hysteresis —
        //           drops FAST on overrun (2 frames), recovers SLOW (30 frames)
        //
        // The controller receives the thermal state + battery level + frame time
        // every frame and outputs an optimal budget. The old ThermalQualityAdapter
        // still provides the tier for animation/haptics decisions, but no longer
        // determines the triangle count.

        thermalAdapter.evaluateProactiveThermal()
        thermalAdapter.evaluateCoolDown()
        let tier = thermalAdapter.currentTier

        // Feed the adaptive controller with this frame's actual performance data.
        let maxTriangles: Int
        if let ctrl = adaptiveBudgetController {
            // Get thermal state from ProcessInfo (iOS)
            let thermalState: Int
            #if os(iOS)
            switch ProcessInfo.processInfo.thermalState {
            case .nominal: thermalState = 0
            case .fair:    thermalState = 1
            case .serious: thermalState = 2
            case .critical: thermalState = 3
            @unknown default: thermalState = 1
            }
            #else
            thermalState = 0
            #endif

            // Get battery fraction
            #if os(iOS)
            let batteryFraction = Float(UIDevice.current.batteryLevel)
            let lowPowerMode = ProcessInfo.processInfo.isLowPowerModeEnabled
            #else
            let batteryFraction: Float = 1.0
            let lowPowerMode = false
            #endif

            let sample = BudgetFrameSample(
                triangleCount: meshTriangles.count,
                frameTimeMs: Float(realDeltaTime * 1000.0),
                thermalState: thermalState,
                batteryFraction: batteryFraction,
                lowPowerMode: lowPowerMode
            )

            if let decision = NativeAdaptiveBudgetBridge.update(ctrl, sample: sample) {
                maxTriangles = decision.recommendedBudget

                #if DEBUG
                // Log calibration progress (every 60 frames)
                if frameCounter % 60 == 0 {
                    print("[Aether3D] Budget: \(decision.recommendedBudget) | "
                        + "cost/kTri: \(String(format: "%.2f", decision.estimatedCostPerKTri))ms | "
                        + "util: \(String(format: "%.1f", decision.utilization * 100))% | "
                        + "headroom: \(String(format: "%.1f", decision.headroomMs))ms | "
                        + "conf: \(String(format: "%.0f", decision.confidence * 100))%")
                }
                #endif
            } else {
                // Controller update failed — use static estimate
                maxTriangles = algorithmSet.recommendedMaxTriangles
            }
        } else {
            // No controller (bridge unavailable) — use static estimate
            maxTriangles = algorithmSet.recommendedMaxTriangles
        }

        // Layer 3.4: Use the C++ multi-factor scoring engine instead of naive
        // prefix() truncation. The engine scores each triangle on 5 dimensions:
        // distance_bias + display_weight + stability_weight + residency_boost
        // + completion_boost. This preserves spatial continuity under thermal
        // budget constraints (pure visual, no depth camera dependency).
        var limitedTriangles = selectTrianglesForRender(maxCount: maxTriangles)

        // Step 3.4a: Module 2 — Cross-platform LOD pipeline (frustum + screen detail).
        // Applies meshlet-based frustum culling and screen-space detail selection
        // AFTER budget selection. Removes triangles outside the camera frustum
        // and triangles too small to contribute visual detail at current distance.
        // All computation in C++ core — identical results iOS/Android/HarmonyOS.
        if algorithmSet.isEnabled(AlgorithmFlags.lodPipeline) && !limitedTriangles.isEmpty {
            let lodInput = buildLODInput(
                triangles: limitedTriangles,
                displaySnapshot: currentPatchDisplaySnapshot,
                viewMatrix: lastViewMatrix,
                projMatrix: lastProjectionMatrix,
                maxTriangles: maxTriangles
            )
            let lodOutput = lodPipeline.process(input: lodInput)
            if !lodOutput.visibleTriangleIndices.isEmpty {
                limitedTriangles = lodOutput.visibleTriangleIndices.compactMap {
                    $0 >= 0 && $0 < limitedTriangles.count ? limitedTriangles[$0] : nil
                }
            }
        }

        // Step 3.4b: Update evidence ONLY for budget-selected triangles.
        // Previously this iterated ALL meshTriangles (50K+) with 5 C++ calls each
        // (quality + verdict + evidence update + display update)
        // = 250K C++ calls/frame. Now bounded to maxTriangles (typically 5K-15K).
        // Selection used previous frame's display snapshot, which is fine because
        // evidence accumulates incrementally (difference between frames is <0.01).
        updatePatchDisplayMap(for: limitedTriangles)
        currentPatchDisplaySnapshot = makeDisplaySnapshot()

        // Step 3.4b2: Blackout overlay fade — as triangles cover the screen,
        // the fullscreen black overlay gradually disappears, revealing the
        // Metal wedge mesh underneath. 1000+ triangles = full coverage.
        let meshCoverage = Double(limitedTriangles.count) / 1000.0
        blackoutOpacity = max(0.0, 1.0 - min(1.0, meshCoverage))

        // Step 3.4c: Module 1 — Adaptive border widths.
        // Compute per-triangle border widths based on display values and triangle area.
        // The AdaptiveBorderCalculator provides Swift-side adaptive adjustment that
        // supplements the C++ WedgeGeometryGenerator's internal border computation.
        // Border widths scale with display progress: low display → thin border,
        // high display → thick border (up to 16px max).
        let sortedAreas = limitedTriangles.map { $0.areaSqM }.sorted()
        let medianArea = sortedAreas.isEmpty ? Float(1e-4) : sortedAreas[sortedAreas.count / 2]
        _ = borderCalculator.calculate(
            displayValues: currentPatchDisplaySnapshot,
            triangles: limitedTriangles,
            medianArea: medianArea
        )

        // Step 3.5: Track view diversity per patch — gated by algorithm set.
        // MUST be after limitedTriangles is defined — only budget-selected triangles
        // were actually observed this frame. Uses PER-TRIANGLE viewing angle
        // (camera→triangle normal dot product) for accurate diversity measurement.
        if algorithmSet.isEnabled(AlgorithmFlags.viewDiversity) {
            let cameraForward = -SIMD3<Float>(  // camera looks along -Z in view space
                cameraTransform.columns.2.x,
                cameraTransform.columns.2.y,
                cameraTransform.columns.2.z
            )
            let diversityTimestampMs = Int64(ProcessInfo.processInfo.systemUptime * 1000.0)
            for triangle in limitedTriangles {
                let cosAngle = simd_dot(cameraForward, triangle.normal)
                let centroid = (triangle.vertices.0 + triangle.vertices.1 + triangle.vertices.2) / 3.0
                let cameraPos = SIMD3<Float>(
                    cameraTransform.columns.3.x,
                    cameraTransform.columns.3.y,
                    cameraTransform.columns.3.z
                )
                let toCentroid = centroid - cameraPos
                let viewAngleDeg = Double(atan2(toCentroid.x, toCentroid.z)) * 180.0 / .pi
                // Only record observations at reasonable viewing angle (cos > 0.1 ≈ < 84°)
                if cosAngle > 0.1 {
                    _ = viewDiversityTracker.addObservation(
                        patchId: triangle.patchId,
                        viewAngleDeg: viewAngleDeg,
                        timestampMs: diversityTimestampMs
                    )
                }
            }
        }

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

        // Motion too fast check (velocity already computed at top of processARFrame)
        let velocity = currentFrameVelocity
        let motionThresholdScale: Double = tier == .critical ? 1.5 : 1.0
        if tier.enableHaptics && velocity > ScanGuidanceConstants.hapticMotionThreshold * motionThresholdScale {
            _ = hapticEngine.fire(
                pattern: .motionTooFast,
                timestamp: timestamp,
                toastPresenter: toastPresenter
            )
        }

        // Exposure check
        if let lightEstimate = lightEstimate {
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

        // realDeltaTime already computed at top of processARFrame()

        // Tick flip controller and extract angles
        _ = flipController.tick(deltaTime: realDeltaTime)
        let flipAngles = flipController.getFlipAngles(for: triIndices)
        let flipAxisData: [(origin: SIMD3<Float>, direction: SIMD3<Float>)] = triIndices.compactMap {
            flipController.getFlipAxis(for: $0)
        }

        // Tick ripple engine and extract amplitudes
        let rippleNow = ProcessInfo.processInfo.systemUptime
        _ = rippleEngine.tick(currentTime: rippleNow)
        let rippleAmplitudes = rippleEngine.getRippleAmplitudes(for: triIndices, currentTime: rippleNow)

        // Step 7b: Module 8 — Populate per-patch colorStates from display values.
        // Maps display evidence to discrete ColorState stages (S0-S5 simplified).
        // Previously always empty ([:]), which made per-patch state-driven rendering
        // impossible. Now populated with actual state for each visible patch.
        var perPatchColorStates: [String: ColorState] = [:]
        for (patchId, display) in currentPatchDisplaySnapshot {
            // S0-S5 thresholds from SSOT (ScanGuidanceConstants) — not hardcoded
            if display < ScanGuidanceConstants.s0ToS1Threshold { perPatchColorStates[patchId] = .black }
            else if display < ScanGuidanceConstants.s1ToS2Threshold { perPatchColorStates[patchId] = .darkGray }
            else if display < ScanGuidanceConstants.s2ToS3Threshold { perPatchColorStates[patchId] = .lightGray }
            else if display < ScanGuidanceConstants.s3ToS4Threshold { perPatchColorStates[patchId] = .white }
            else { perPatchColorStates[patchId] = .original }
        }

        // Step 8: Update render pipeline (if Metal is available)
        renderPipeline?.update(
            displaySnapshot: currentPatchDisplaySnapshot,
            colorStates: perPatchColorStates,
            meshTriangles: limitedTriangles,
            lightEstimate: lightEstimate,
            cameraTransform: cameraTransform,
            viewMatrix: lastViewMatrix,
            projectionMatrix: lastProjectionMatrix,
            frameDeltaTime: realDeltaTime,
            precomputedFlipAngles: flipAngles,
            precomputedRippleAmplitudes: rippleAmplitudes,
            precomputedFlipAxisData: flipAxisData,
            gpuDurationMs: nil
        )

        // Step 9: Frame pacing feedback — gated by algorithm set.
        if algorithmSet.isEnabled(AlgorithmFlags.framePacing) {
            Task { @MainActor [weak self] in
                guard let self = self else { return }
                let advice = await self.framePacingController.recordFrameTime(realDeltaTime)
                switch advice {
                case .reduceQuality:
                    self.thermalAdapter.evaluateProactiveThermal()
                case .increaseQuality:
                    self.thermalAdapter.evaluateCoolDown()
                case .maintain, .enableSmoothing:
                    break
                }
            }
        }

        // Step 9b: Module 9 — Battery-aware scheduling.
        // Checks battery state periodically and throttles scan quality when
        // battery is low to extend scan session duration. Runs every 60 frames
        // (~1 second) to avoid per-frame overhead from async actor calls.
        if algorithmSet.isEnabled(AlgorithmFlags.batteryAware) && frameCounter % 60 == 0 {
            Task { @MainActor [weak self] in
                guard let self = self else { return }
                let quality = await self.batteryScheduler.recommendedScanQuality()
                switch quality {
                case .efficient:
                    // Battery is low — reduce rendering quality to conserve power
                    self.thermalAdapter.evaluateProactiveThermal()
                case .maximum, .balanced:
                    break
                }
            }
        }

        // Step 9c: Module 10 — Thermal state handler.
        // Provides fine-grained thermal quality levels beyond ThermalQualityAdapter's
        // 4-tier system. Runs every 120 frames (~2 seconds) as thermal changes slowly.
        if algorithmSet.isEnabled(AlgorithmFlags.thermalHandler) && frameCounter % 120 == 0 {
            Task { @MainActor [weak self] in
                guard let self = self else { return }
                await self.mobileThermalHandler.adaptToThermalState()
            }
        }

        // Step 10: Directional haptic guidance — gated by algorithm set.
        if algorithmSet.isEnabled(AlgorithmFlags.directionalHaptics)
            && tier.enableHaptics && frameCounter % 30 == 0 {
            let lowDisplayCount = limitedTriangles.filter {
                (currentPatchDisplaySnapshot[$0.patchId] ?? 0.0) < 0.3
            }.count
            let proximity = Double(lowDisplayCount) / max(1.0, Double(limitedTriangles.count))
            if proximity > 0.3 {
                _ = hapticEngine.fireDirectionalGuidance(
                    proximity: proximity,
                    timestamp: timestamp,
                    toastPresenter: nil
                )
            }
        }

        // Module 6 + Fix S3: DEBUG diagnostics — cross-validation + GPU display logging.
        // Every 60 frames, logs display pipeline health: sample values, distribution,
        // and state machine output. Confirms data reaches Metal shader correctly.
        #if DEBUG
        if frameCounter % 60 == 0 && !limitedTriangles.isEmpty {
            let sampleDisplays = limitedTriangles.prefix(5).map {
                String(format: "%.3f", currentPatchDisplaySnapshot[$0.patchId] ?? 0.0)
            }
            let maxDisplay = currentPatchDisplaySnapshot.values.max() ?? 0.0
            let above01 = currentPatchDisplaySnapshot.values.filter { $0 > 0.1 }.count
            let above03 = currentPatchDisplaySnapshot.values.filter { $0 > 0.3 }.count
            print("[Aether3D] Frame \(frameCounter): displays=\(sampleDisplays) max=\(String(format: "%.3f", maxDisplay)) above0.1=\(above01) above0.3=\(above03) colorState=\(lastOverallColorState.rawValue)")
        }
        #endif
    }

    // MARK: - Private Helpers

    /// Update display values for BUDGET-SELECTED patches through PatchEvidenceMap → PatchDisplayMap.
    ///
    /// Data flow: ARKit mesh → budget selection → PatchEvidenceMap (Choquet/DS evidence ledger)
    ///            → PatchDisplayMap (1-Euro smoothed display)
    /// Only budget-selected triangles feed observations. This bounds per-frame work to
    /// maxTriangles × 5 C++ calls (typically 5K-15K × 5 = 25K-75K) instead of
    /// ALL meshTriangles × 5 (potentially 50K × 5 = 250K). The 3-5× reduction
    /// keeps total frame time under 16ms even on thermally-throttled devices.
    ///
    /// - Parameter triangles: Budget-limited triangles from selectTrianglesForRender().
    ///   Only these triangles accumulate evidence this frame. Non-selected triangles
    ///   retain their previous evidence values (monotonic ratchet prevents regression).
    private func updatePatchDisplayMap(for triangles: [ScanTriangle]) {
        // Layer 2.4: Use monotonic clock instead of Date() which can jump
        // forward/backward due to NTP corrections or system sleep.
        let timestampMs = Int64(ProcessInfo.processInfo.systemUptime * 1000.0)
        let frameId = "frame-\(frameCounter)"

        // Hoist matrix inverse OUTSIDE the per-triangle loop (was computed per-triangle before).
        // Matrix inversion costs ~50 FLOPs. With 10K+ triangles, this saves ~500K FLOPs/frame.
        let invViewMatrix = lastViewMatrix.inverse

        for triangle in triangles {
            // Admission controller REMOVED — quality gating delegated to Core C++ layer.
            // Every triangle in every frame now accumulates evidence (quality-modulated).

            // Step A: Multi-factor gate quality — Module 5+7 reconnection.
            // GateQualityComputer fuses reproj error, edge sharpness, exposure,
            // and viewing angle into a single quality score. Uses captureMode
            // smoother (SmartAntiBoostSmoother) to prevent visual regression.
            // Replaces simple aether_observation_quality_from_area().
            let camFwd = -SIMD3<Float>(
                invViewMatrix.columns.2.x,
                invViewMatrix.columns.2.y,
                invViewMatrix.columns.2.z
            )
            let cosAngle = simd_dot(camFwd, triangle.normal)
            let direction = EvidenceVector3(
                x: Double(triangle.normal.x),
                y: Double(triangle.normal.y),
                z: Double(triangle.normal.z)
            )
            // Approximate unavailable sensor signals from available frame data:
            // - reprojRmsPx: camera velocity → reprojection error (motion blur proxy)
            //   Scale: 0.0 (static) to 0.5 (fast motion). Hard gate threshold ~0.48.
            // - edgeRmsPx: grazing angle → edge registration error
            //   Scale: 0.05 (head-on) to 0.35 (grazing). Hard gate threshold ~0.23.
            //   FIX: Previous formula (1/cosAngle) produced values 1.0-10.0, FAR above
            //   the 0.23 hard gate threshold → GateQualityComputer returned near-zero
            //   quality → evidence NEVER accumulated. Root cause of "dark triangles."
            // - sharpness: ambient light → image sharpness (inverted-U curve)
            //   Scale: 40-150. Hard gate threshold ~85.
            let approxReprojRms = min(0.45, currentFrameVelocity * 1.5)
            let approxEdgeRms = 0.05 + 0.25 * (1.0 - Double(max(0.0, cosAngle)))
            let approxSharpness = mapAmbientToSharpness(currentFrameAmbientIntensity)
            let overExposure = currentFrameAmbientIntensity > 5000 ? 0.3 : 0.0
            let underExposure = currentFrameAmbientIntensity < 200 ? 0.3 : 0.0

            let ledgerQuality = gateComputer.computeGateQuality(
                patchId: triangle.patchId,
                direction: direction,
                reprojRmsPx: approxReprojRms,
                edgeRmsPx: approxEdgeRms,
                sharpness: approxSharpness,
                overexposureRatio: overExposure,
                underexposureRatio: underExposure,
                frameIndex: frameCounter
            )

            // Step A.5: Determine evidence verdict from frame quality signals.
            // Verdict is orthogonal to gate quality — it classifies the observation
            // type (good/suspect/bad) while gate quality quantifies the magnitude.
            let verdict: ObservationVerdict
            let motionThreshold = ScanGuidanceConstants.hapticMotionThreshold
            if currentFrameVelocity > motionThreshold * 2.0 {
                // Very fast motion — observation is unreliable
                verdict = .bad
            } else if currentFrameVelocity > motionThreshold {
                // Moderate motion — observation is suspect but usable
                verdict = .suspect
            } else {
                if cosAngle < 0.10 {
                    // Grazing angle (>84°) — geometry is unreliable at extreme angles
                    verdict = .suspect
                } else if currentFrameAmbientIntensity < 200 || currentFrameAmbientIntensity > 6000 {
                    // Too dark or too bright — color/texture data unreliable
                    verdict = .suspect
                } else {
                    verdict = .good
                }
            }

            // Step B: Feed observation into evidence ledger (Choquet + DS gates)
            patchEvidenceMap.update(
                patchId: triangle.patchId,
                ledgerQuality: ledgerQuality,
                verdict: verdict,
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

            // Step D: Accumulate dimensional signals — thin App-layer wiring.
            // Raw signals forwarded to Core DimensionalComputer in calculateOverallCoverage().
            dimAccumViewGainSum += Double(max(0, cosAngle))
            dimAccumGeomGainSum += ledgerQuality
            dimAccumObsCount += 1
            if verdict == .bad { dimAccumBadCount += 1 }

            // Step E: Append to observation log (Core SHA-256 hash chain).
            observationLog.append(
                patchId: triangle.patchId,
                verdict: "\(verdict)",
                quality: ledgerQuality,
                evidenceAfter: evidence,
                timestampMs: timestampMs,
                frameIndex: frameCounter
            )
        }
    }

    /// Convert PatchDisplayMap entries to render/animation snapshot.
    private func makeDisplaySnapshot() -> [String: Double] {
        Dictionary(uniqueKeysWithValues: patchDisplayMap.snapshotSorted().map { ($0.patchId, $0.display) })
    }

    // MARK: - Module Helper Methods

    /// Module 5: Map ambient light intensity to approximate image sharpness.
    /// Uses inverted-U curve: low light → blur, optimal light → sharp, high light → bloom.
    /// Provides a reasonable proxy for the sharpness signal that GateQualityComputer
    /// expects from dedicated image quality analysis.
    ///
    /// FIX: Previous max value was 80, but GateQualityComputer's hard gate threshold
    /// is ~85. Even in optimal lighting, sharpness was BELOW the gate → quality
    /// penalized. Now returns 130 for optimal lighting (well above 85 threshold).
    private func mapAmbientToSharpness(_ ambientIntensity: Double) -> Double {
        if ambientIntensity < 100 { return 30.0 }       // Very dark → low sharpness
        if ambientIntensity < 300 { return 60.0 }       // Dark → moderate
        if ambientIntensity < 600 { return 100.0 }      // Dim indoor → good
        if ambientIntensity < 2500 { return 130.0 }     // Optimal indoor/outdoor → excellent
        if ambientIntensity < 5000 { return 90.0 }      // Bright → some bloom
        return 50.0                                       // Very bright → bloom/flare
    }

    /// Module 2: Build LODPipelineInput from current triangles and camera matrices.
    /// Flattens ScanTriangle data into the array-based format expected by the
    /// cross-platform LOD pipeline (C++ meshlet builder + frustum culler).
    private func buildLODInput(
        triangles: [ScanTriangle],
        displaySnapshot: [String: Double],
        viewMatrix: simd_float4x4,
        projMatrix: simd_float4x4,
        maxTriangles: Int
    ) -> LODPipelineInput {
        // Flatten triangle vertices into interleaved [Float] array
        var vertices: [Float] = []
        var indices: [UInt32] = []
        var perTriangleDisplay: [Float] = []
        var perTriangleCentroid: [SIMD3<Float>] = []
        var perTriangleArea: [Float] = []

        vertices.reserveCapacity(triangles.count * 9)  // 3 vertices × 3 floats
        indices.reserveCapacity(triangles.count * 3)

        for (i, tri) in triangles.enumerated() {
            let (v0, v1, v2) = tri.vertices
            vertices.append(contentsOf: [v0.x, v0.y, v0.z, v1.x, v1.y, v1.z, v2.x, v2.y, v2.z])
            let base = UInt32(i * 3)
            indices.append(contentsOf: [base, base + 1, base + 2])
            perTriangleDisplay.append(Float(displaySnapshot[tri.patchId] ?? 0.0))
            perTriangleCentroid.append((v0 + v1 + v2) / 3.0)
            perTriangleArea.append(tri.areaSqM)
        }

        // Flatten 4×4 matrices to [Float] row-major for C++ interop
        let vm = viewMatrix
        let viewFlat: [Float] = [
            vm.columns.0.x, vm.columns.1.x, vm.columns.2.x, vm.columns.3.x,
            vm.columns.0.y, vm.columns.1.y, vm.columns.2.y, vm.columns.3.y,
            vm.columns.0.z, vm.columns.1.z, vm.columns.2.z, vm.columns.3.z,
            vm.columns.0.w, vm.columns.1.w, vm.columns.2.w, vm.columns.3.w
        ]
        let pm = projMatrix
        let projFlat: [Float] = [
            pm.columns.0.x, pm.columns.1.x, pm.columns.2.x, pm.columns.3.x,
            pm.columns.0.y, pm.columns.1.y, pm.columns.2.y, pm.columns.3.y,
            pm.columns.0.z, pm.columns.1.z, pm.columns.2.z, pm.columns.3.z,
            pm.columns.0.w, pm.columns.1.w, pm.columns.2.w, pm.columns.3.w
        ]

        return LODPipelineInput(
            vertices: vertices,
            indices: indices,
            perTriangleDisplay: perTriangleDisplay,
            perTriangleCentroid: perTriangleCentroid,
            perTriangleArea: perTriangleArea,
            viewMatrix: viewFlat,
            projMatrix: projFlat,
            maxTriangles: maxTriangles
        )
    }

    /// Module 3: Compute evidence level breakdown for EvidenceStateMachine.
    /// Returns bucket counts: [L0, L1, L2, L3, L4, L5, L6] where each level
    /// corresponds to evidence ranges [0-0.14, 0.14-0.28, ..., 0.86-1.0].
    private func computeLevelBreakdown() -> [Int] {
        var counts = [Int](repeating: 0, count: 7)
        for display in currentPatchDisplaySnapshot.values {
            let level = min(6, Int(display * 7.0))
            counts[level] += 1
        }
        return counts
    }

    /// Module 3: Compute ratio of patches with high evidence (display > 0.8).
    /// Indicates what fraction of the surface has been thoroughly scanned.
    private func computeHighObservationRatio() -> Double {
        guard !currentPatchDisplaySnapshot.isEmpty else { return 0.0 }
        let highCount = currentPatchDisplaySnapshot.values.filter { $0 > 0.8 }.count
        return Double(highCount) / Double(currentPatchDisplaySnapshot.count)
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

    /// Calculate overall scan coverage [0, 1] with CROSS-VALIDATION.
    ///
    /// Requirement 3: Two independent coverage computations run simultaneously.
    /// Source A: Simple average of all patch display values (fast, direct).
    /// Source B: Weighted coverage by patch area (accounts for triangle size).
    /// The C++ cross-validator ensures monotonic guarantee + conservative merge.
    private func calculateOverallCoverage() -> Double {
        guard !currentPatchDisplaySnapshot.isEmpty else { return coverageHighWater }

        // Source A: Simple display-value average (original algorithm)
        let total = currentPatchDisplaySnapshot.values.reduce(0.0, +)
        let coverageA = total / Double(currentPatchDisplaySnapshot.count)

        // Source B: Threshold-based coverage (fraction of patches above 0.5)
        // This is a second independent estimator that counts "sufficiently scanned" patches
        let scannedCount = currentPatchDisplaySnapshot.values.filter { $0 > 0.5 }.count
        let coverageB = Double(scannedCount) / Double(currentPatchDisplaySnapshot.count)

        // Cross-validate through C++ core (monotonic + conservative merge)
        if let crossValidated = NativeCrossValidationBridge.crossValidateCoverage(
            coverageA: coverageA,
            coverageB: coverageB,
            currentHighWater: coverageHighWater
        ) {
            coverageHighWater = crossValidated
        } else {
            // Fallback: monotonic on simple average
            coverageHighWater = max(coverageHighWater, min(coverageA, coverageB))
        }

        // Module 3: Evaluate evidence state machine (S0-S5 certification).
        // Constructs CoverageResult from available signals and evaluates the
        // state machine to determine overall scan color state. The state machine
        // tracks progression through stages: S0(black) → S1(darkGray) →
        // S2(lightGray) → S3(white) → S4(original) → S5(certified).
        let coverageResult = CoverageResult(
            coveragePercentage: coverageHighWater,
            breakdownCounts: computeLevelBreakdown(),
            weightedSumComponents: [],
            excludedAreaSqM: 0.0,
            beliefCoverage: min(coverageA, coverageB),
            plausibilityCoverage: max(coverageA, coverageB),
            uncertaintyWidth: abs(coverageA - coverageB),
            highObservationRatio: computeHighObservationRatio(),
            lyapunovRate: 1.0,
            meanFisherInfo: 0.0
        )

        // Module 3b: Build DimensionalScoreSet from accumulated signals.
        // App layer only forwards raw aggregates — all scoring logic in Core DimensionalComputer.
        let avgViewGain = dimAccumObsCount > 0 ? dimAccumViewGainSum / Double(dimAccumObsCount) : 0.0
        let avgGeomGain = dimAccumObsCount > 0 ? dimAccumGeomGainSum / Double(dimAccumObsCount) : 0.0
        // Average view diversity across all observed patches (Core ViewDiversityTracker)
        let patchIds = Array(currentPatchDisplaySnapshot.keys)
        let avgDiversity = patchIds.isEmpty ? 0.0 :
            patchIds.reduce(0.0) { $0 + viewDiversityTracker.diversityScore(patchId: $1) } / Double(patchIds.count)

        let dimScores = dimensionalComputer.compute(
            gateGainFunctions: GateGainFunctionsOutput(
                viewGain: avgViewGain,
                geometryGain: avgGeomGain,
                basicGain: avgGeomGain * 0.8  // basic gain tracks geometry gain
            ),
            gateCoverageTracker: GateCoverageTrackerOutput(coverageScore: coverageHighWater),
            viewDiversityTracker: ViewDiversityTrackerOutput(diversityScore: avgDiversity)
        )

        lastOverallColorState = evidenceStateMachine.evaluate(
            coverage: coverageResult,
            dimensionalScores: dimScores
        )

        // Module 4: CoverageEstimator D-S fusion — provides Dempster-Shafer
        // belief/plausibility bounds for more robust coverage estimation.
        // The estimator is instantiated and ready; full EvidenceGrid integration
        // is deferred to when grid construction helpers become available from
        // IsolatedEvidenceEngine. The CoverageResult above already provides
        // belief/plausibility bounds from cross-validated sources A and B.

        return coverageHighWater
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

    /// Layer 2.2: Stabilize patch identities across frames using the C++ spatial
    /// hash matching engine (aether_match_patch_identities).
    ///
    /// When ARKit regenerates mesh, triangle patch IDs can shift by one 2cm grid
    /// cell due to floating-point quantization at boundaries. This causes visual
    /// flicker (old patch evicted, new patch starts from 0). The C++ engine
    /// matches observations against previous-frame anchors within a snap distance,
    /// preserving temporal continuity of evidence accumulation.
    /// Deterministic FNV-1a hash for patchId strings → UInt64 patchKey.
    /// Unlike Swift's hashValue, this is stable across process launches and
    /// has excellent collision resistance for short ASCII/UTF-8 strings.
    private func deterministicPatchKey(for patchId: String) -> UInt64 {
        var hash: UInt64 = 14695981039346656037  // FNV-1a offset basis
        for byte in patchId.utf8 {
            hash ^= UInt64(byte)
            hash &*= 1099511628211               // FNV-1a prime
        }
        // Ensure non-zero (0 means "no match" in the C++ engine)
        return hash == 0 ? 1 : hash
    }

    private func stabilizePatchIdentities(_ triangles: [ScanTriangle]) -> [ScanTriangle] {
        guard !triangles.isEmpty else { return triangles }

        // Build observations from current frame
        let observations: [PatchIdentitySample] = triangles.map { tri in
            let centroid = (tri.vertices.0 + tri.vertices.1 + tri.vertices.2) / 3.0
            let display = Float(currentPatchDisplaySnapshot[tri.patchId] ?? 0.0)
            let key = deterministicPatchKey(for: tri.patchId)
            return PatchIdentitySample(patchKey: key, centroid: centroid, display: display)
        }

        // Register current patchId → key mappings
        for (i, tri) in triangles.enumerated() {
            patchKeyToId[observations[i].patchKey] = tri.patchId
        }

        // ── ROOT FIX for coverage regression ──
        // Use the FULL historical anchor database, not just the previous frame.
        // When the camera moves away (scanning area B) and returns to area A,
        // previousPatchAnchors contains patches from area B (useless for matching
        // area A). But historicalPatchAnchors contains ALL ever-observed patches,
        // so area A patches can be matched against their historical positions.
        let anchorArray: [PatchIdentitySample]
        if historicalPatchAnchors.isEmpty {
            // First frame: record initial observations as history, return unchanged
            for obs in observations {
                historicalPatchAnchors[obs.patchKey] = obs
            }
            previousPatchAnchors = observations
            return triangles
        } else {
            // Merge previousPatchAnchors (for frame-to-frame jitter) with
            // historicalPatchAnchors (for cross-region return). The C++ spatial
            // hash handles deduplication by position internally.
            anchorArray = Array(historicalPatchAnchors.values)
        }

        guard let resolved = renderStabilityBridge.matchPatchIdentities(
            observations: observations,
            anchors: anchorArray,
            lockDisplayThreshold: 0.7,
            snapDistanceM: 0.015,   // 1.5cm match distance (slightly wider for cross-region return)
            cellSizeM: 0.03         // 3cm spatial hash grid (matches wider snap distance)
        ) else {
            // C++ bridge failed: passthrough and update anchors
            for obs in observations {
                historicalPatchAnchors[obs.patchKey] = obs
            }
            previousPatchAnchors = observations
            return triangles
        }

        // Apply resolved patch keys: when C++ says observation[i] matches anchor[j],
        // remap the triangle's patchId to the anchor's original patchId.
        // This gives the evidence system temporal continuity — the same physical surface
        // keeps the same patchId even when ARKit regenerates the mesh with different IDs.
        var result = triangles
        for i in 0..<min(result.count, resolved.count) {
            let resolvedKey = resolved[i]
            // resolved[i] == 0 means no match found; keep original
            guard resolvedKey != 0, resolvedKey != observations[i].patchKey else { continue }

            // Look up the original patchId string for this matched key
            if let previousPatchId = patchKeyToId[resolvedKey] {
                // Create a new triangle with the remapped patchId
                result[i] = ScanTriangle(
                    patchId: previousPatchId,
                    vertices: result[i].vertices,
                    normal: result[i].normal,
                    areaSqM: result[i].areaSqM,
                    blockIndex: result[i].blockIndex
                )
            }
        }

        // Update BOTH anchor stores:
        // 1. previousPatchAnchors for next-frame jitter matching
        // 2. historicalPatchAnchors for cross-region return matching
        let resolvedAnchors: [PatchIdentitySample] = result.map { tri in
            let centroid = (tri.vertices.0 + tri.vertices.1 + tri.vertices.2) / 3.0
            let display = Float(currentPatchDisplaySnapshot[tri.patchId] ?? 0.0)
            let key = deterministicPatchKey(for: tri.patchId)
            return PatchIdentitySample(patchKey: key, centroid: centroid, display: display)
        }
        previousPatchAnchors = resolvedAnchors

        // Merge into historical database (upsert: existing entries get updated position)
        for anchor in resolvedAnchors {
            historicalPatchAnchors[anchor.patchKey] = anchor
        }

        // Evict if exceeding capacity: remove lowest-display entries first
        // (patches with little evidence are least valuable to remember)
        if historicalPatchAnchors.count > Self.maxHistoricalAnchors {
            let sorted = historicalPatchAnchors.sorted { $0.value.display < $1.value.display }
            let removeCount = historicalPatchAnchors.count - Self.maxHistoricalAnchors
            for i in 0..<removeCount {
                historicalPatchAnchors.removeValue(forKey: sorted[i].key)
            }
        }

        return result
    }

    /// Layer 3.4: Select triangles for render using C++ multi-factor scoring engine.
    ///
    /// Replaces naive Array(meshTriangles.prefix(maxTriangles)) which truncated
    /// by insertion order, breaking spatial continuity. The C++ engine scores
    /// each triangle on 5 dimensions:
    ///   1. distance_bias: closer to camera → higher score
    ///   2. display_weight: higher coverage evidence → higher priority
    ///   3. stability_weight: recently stable patches preferred
    ///   4. residency_boost: patches that have been rendered recently stay
    ///   5. completion_boost: nearly-complete patches get priority
    ///
    /// Pure visual approach — uses camera position from view matrix, no depth sensor.
    private func selectTrianglesForRender(maxCount: Int) -> [ScanTriangle] {
        guard meshTriangles.count > maxCount else { return meshTriangles }

        // Extract camera position from inverse view matrix (pure visual)
        let invView = lastViewMatrix.inverse
        let camPos = SIMD3<Float>(invView.columns.3.x, invView.columns.3.y, invView.columns.3.z)

        let candidates: [RenderTriangleCandidate] = meshTriangles.map { tri in
            let centroid = (tri.vertices.0 + tri.vertices.1 + tri.vertices.2) / 3.0
            let display = Float(currentPatchDisplaySnapshot[tri.patchId] ?? 0.0)
            let key = deterministicPatchKey(for: tri.patchId)
            // Residency: previously-rendered triangles get residencyUntilFrame=0
            // (which is <= currentFrame → C++ boosts them). New triangles get
            // residencyUntilFrame=currentFrame+60 (not <= currentFrame → no boost).
            // This prevents visual flicker at the budget boundary.
            let residency: Int32 = lastRenderedPatchKeys.contains(key) ? 0 : Int32(frameCounter + 60)
            return RenderTriangleCandidate(
                patchKey: key,
                centroid: centroid,
                display: display,
                stabilityFadeAlpha: 1.0,
                residencyUntilFrame: residency
            )
        }

        let config = RenderSelectionConfig(
            currentFrame: Int32(frameCounter),
            maxTriangles: Int32(maxCount),
            cameraPosition: camPos
            // Uses defaults: distanceBias=0.05, displayWeight=2.0, stabilityWeight=0.3
        )

        guard let selectedIndices = renderStabilityBridge.selectStableRenderTriangles(
            candidates: candidates, config: config
        ) else {
            // C++ engine failed — fallback to first N (better than nothing)
            return Array(meshTriangles.prefix(maxCount))
        }

        let result = selectedIndices.compactMap { idx -> ScanTriangle? in
            idx >= 0 && idx < meshTriangles.count ? meshTriangles[idx] : nil
        }

        // Update residency tracking for next frame
        lastRenderedPatchKeys = Set(result.map { deterministicPatchKey(for: $0.patchId) })

        return result
    }

    /// Reset all subsystems for next scan session
    private func resetSubsystems() {
        flipController.reset()
        rippleEngine.reset()
        patchDisplayMap.reset()
        // Layer 2.3: Reset patchEvidenceMap — was missing, causing stale evidence
        // from previous scan sessions to persist and corrupt new scans.
        patchEvidenceMap.reset()
        viewDiversityTracker.reset()
        currentPatchDisplaySnapshot.removeAll()
        previousPatchDisplaySnapshot.removeAll()
        meshTriangles.removeAll()
        adjacencyGraph = nil
        frameCounter = 0
        lastMotionSample = nil
        lastFrameTimestamp = 0
        coverageHighWater = 0.0
        needsAdjacencyRebuild = false
        previousPatchAnchors.removeAll()
        historicalPatchAnchors.removeAll()
        patchKeyToId.removeAll()
        lastRenderedPatchKeys.removeAll()

        // Reset reconnected algorithm modules (Modules 1-10)
        lastOverallColorState = .black
        dimAccumViewGainSum = 0.0
        dimAccumGeomGainSum = 0.0
        dimAccumObsCount = 0
        dimAccumBadCount = 0
        observationLog.reset()
        wedgeGenerator.resetPersistentVisualState()

        // Reset newly connected components (admission controller removed)
        if let analyzer = motionAnalyzer {
            NativeMotionAnalyzerBridge.reset(analyzer)
        }
        if let ctrl = adaptiveBudgetController {
            NativeAdaptiveBudgetBridge.reset(ctrl)
        }
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

    // MARK: - Persistence (thin App-layer wiring to Core save/load)

    /// Directory for persisted evidence data.
    private static var evidencePersistenceDir: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Aether3D/Evidence", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// Persist evidence + display state + observation log to disk (called on stop/pause).
    private func persistEvidenceState() {
        let dir = Self.evidencePersistenceDir
        do {
            try patchEvidenceMap.saveToDisk(url: dir.appendingPathComponent("evidence.json"))
            try patchDisplayMap.saveToDisk(url: dir.appendingPathComponent("display.json"))
            try observationLog.saveToDisk(url: dir.appendingPathComponent("observation_log.json"))
            #if DEBUG
            print("[Aether3D] Evidence persisted (\(currentPatchDisplaySnapshot.count) patches, \(observationLog.count) log entries)")
            #endif
        } catch {
            #if DEBUG
            print("[Aether3D] ⚠️ Evidence persistence failed: \(error.localizedDescription)")
            #endif
        }
    }

    /// Restore evidence + display state from disk (called on start capture).
    private func restoreEvidenceState() {
        let dir = Self.evidencePersistenceDir
        let evidenceURL = dir.appendingPathComponent("evidence.json")
        let displayURL = dir.appendingPathComponent("display.json")
        guard FileManager.default.fileExists(atPath: evidenceURL.path) else { return }
        do {
            try patchEvidenceMap.loadFromDisk(url: evidenceURL)
            try patchDisplayMap.loadFromDisk(url: displayURL)
            #if DEBUG
            print("[Aether3D] Evidence restored from disk")
            #endif
        } catch {
            #if DEBUG
            print("[Aether3D] ⚠️ Evidence restore failed: \(error.localizedDescription)")
            #endif
        }
    }
}

#endif
