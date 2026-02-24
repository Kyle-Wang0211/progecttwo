// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
//  QualityDashboardModel.swift
//  Aether3D
//
//  ObservableObject that provides quality metrics to SwiftUI views.
//  Timer-based refresh from TrainingCoordinator (every 0.5s).
//  Provides floor/frontier SLO indicators and progress tracking.
//

import Foundation
import Combine
#if canImport(SwiftUI)
import SwiftUI
#endif

/// Quality dashboard view model that bridges TrainingCoordinator metrics
/// to SwiftUI views with SLO compliance indicators.
///
/// Architecture:
///   TrainingCoordinator (C API) -> QualityDashboardModel -> SwiftUI views
///
/// Pattern: @MainActor + ObservableObject + Timer + Combine
/// (consistent with project conventions)
@MainActor
final class QualityDashboardModel: ObservableObject {

    // MARK: - Quality Metrics (Published)

    @Published private(set) var psnr: Float = 0
    @Published private(set) var ssim: Float = 0
    @Published private(set) var chamfer: Float = 0
    @Published private(set) var normalConsistency: Float = 0
    @Published private(set) var scaleAccuracy: Float = 0
    @Published private(set) var coverage: Float = 0

    // Confidence interval
    @Published private(set) var psnrCILower: Float = 0
    @Published private(set) var psnrCIUpper: Float = 0

    // MARK: - Progress (Published)

    /// Overall training progress [0, 1] based on coverage.
    @Published private(set) var progress: Float = 0

    /// Phase description string for UI display.
    @Published private(set) var phaseDescription: String = "Idle"

    /// Whether the pipeline is actively training.
    @Published private(set) var isTraining: Bool = false

    /// Whether training has completed.
    @Published private(set) var isComplete: Bool = false

    // MARK: - Training State (Published)

    @Published private(set) var trainingStep: UInt32 = 0
    @Published private(set) var gaussianCount: UInt32 = 0
    @Published private(set) var currentLoss: Float = 0
    @Published private(set) var jankRate: Float = 0
    @Published private(set) var ttfs5: Double = -1

    // MARK: - SLO Floor Indicators (Published)

    /// Green if PSNR >= 28.0 dB, red otherwise.
    @Published private(set) var psnrMeetsFloor: Bool = false

    /// Green if SSIM >= 0.85, red otherwise.
    @Published private(set) var ssimMeetsFloor: Bool = false

    /// Green if coverage >= 0.90, red otherwise.
    @Published private(set) var coverageMeetsFloor: Bool = false

    /// Green if normal consistency >= 0.80, red otherwise.
    @Published private(set) var normalMeetsFloor: Bool = false

    /// Green if scale accuracy >= 0.85, red otherwise.
    @Published private(set) var scaleMeetsFloor: Bool = false

    /// Green if chamfer <= 0.005, red otherwise.
    @Published private(set) var chamferMeetsFloor: Bool = false

    // MARK: - SLO Thresholds

    /// Floor SLO thresholds (minimum acceptable quality).
    struct FloorSLO {
        static let psnr: Float = 28.0
        static let ssim: Float = 0.85
        static let coverage: Float = 0.90
        static let normalConsistency: Float = 0.80
        static let scaleAccuracy: Float = 0.85
        static let chamferMax: Float = 0.005
    }

    /// Frontier SLO thresholds (aspirational quality).
    struct FrontierSLO {
        static let psnr: Float = 32.0
        static let ssim: Float = 0.92
        static let coverage: Float = 0.95
        static let normalConsistency: Float = 0.90
        static let scaleAccuracy: Float = 0.95
        static let chamferMax: Float = 0.002
    }

    // MARK: - Private State

    private weak var coordinator: TrainingCoordinator?
    private var refreshTimer: Timer?
    private var cancellables = Set<AnyCancellable>()
    private let refreshInterval: TimeInterval = 0.5

    // MARK: - Init

    init(coordinator: TrainingCoordinator) {
        self.coordinator = coordinator
        setupBindings()
        startRefreshTimer()
    }

    deinit {
        refreshTimer?.invalidate()
    }

    // MARK: - Computed SLO Properties

    /// All floor SLO metrics are met.
    var meetsFloorSLO: Bool {
        psnrMeetsFloor && ssimMeetsFloor && coverageMeetsFloor &&
        normalMeetsFloor && scaleMeetsFloor && chamferMeetsFloor
    }

    /// All frontier SLO metrics are met.
    var meetsFrontierSLO: Bool {
        psnr >= FrontierSLO.psnr &&
        ssim >= FrontierSLO.ssim &&
        coverage >= FrontierSLO.coverage &&
        normalConsistency >= FrontierSLO.normalConsistency &&
        scaleAccuracy >= FrontierSLO.scaleAccuracy &&
        chamfer <= FrontierSLO.chamferMax
    }

    /// Number of floor metrics currently met (0-6).
    var floorMetricsMet: Int {
        var count = 0
        if psnrMeetsFloor { count += 1 }
        if ssimMeetsFloor { count += 1 }
        if coverageMeetsFloor { count += 1 }
        if normalMeetsFloor { count += 1 }
        if scaleMeetsFloor { count += 1 }
        if chamferMeetsFloor { count += 1 }
        return count
    }

    /// Formatted string showing met/total floor metrics.
    var floorComplianceText: String {
        "\(floorMetricsMet)/6 Floor Metrics"
    }

    /// Color representing overall floor SLO compliance.
    var floorComplianceColor: Color {
        if meetsFloorSLO { return .green }
        if floorMetricsMet >= 4 { return .yellow }
        return .red
    }

    /// Summary text for the current quality state.
    var qualitySummary: String {
        if isComplete {
            return meetsFloorSLO
                ? "Training complete - World Model Standard achieved"
                : "Training complete - Below target quality"
        }
        if isTraining {
            return "Training step \(trainingStep) - " +
                   "\(gaussianCount) gaussians"
        }
        return phaseDescription
    }

    // MARK: - Formatted Metric Strings

    var psnrText: String {
        String(format: "%.1f dB", psnr)
    }

    var ssimText: String {
        String(format: "%.3f", ssim)
    }

    var chamferText: String {
        String(format: "%.4f m", chamfer)
    }

    var normalConsistencyText: String {
        String(format: "%.2f", normalConsistency)
    }

    var scaleAccuracyText: String {
        String(format: "%.2f", scaleAccuracy)
    }

    var coverageText: String {
        String(format: "%.1f%%", coverage * 100.0)
    }

    var psnrCIText: String {
        String(format: "[%.1f, %.1f] dB", psnrCILower, psnrCIUpper)
    }

    var lossText: String {
        String(format: "%.6f", currentLoss)
    }

    var jankRateText: String {
        String(format: "%.1f%%", jankRate * 100.0)
    }

    var ttfs5Text: String {
        if ttfs5 < 0 { return "N/A" }
        return String(format: "%.1fs", ttfs5)
    }

    // MARK: - Setup

    private func setupBindings() {
        guard let coordinator = coordinator else { return }

        // Observe coordinator phase changes via Combine
        coordinator.$phase
            .receive(on: DispatchQueue.main)
            .sink { [weak self] phase in
                self?.phaseDescription = phase.displayName
                self?.isTraining = (phase == .training)
                self?.isComplete = (phase == .complete)
            }
            .store(in: &cancellables)

        // Observe loss changes
        coordinator.$lastLoss
            .receive(on: DispatchQueue.main)
            .sink { [weak self] loss in
                self?.currentLoss = loss
            }
            .store(in: &cancellables)

        // Observe training step
        coordinator.$trainingStep
            .receive(on: DispatchQueue.main)
            .sink { [weak self] step in
                self?.trainingStep = step
            }
            .store(in: &cancellables)

        // Observe gaussian count
        coordinator.$gaussianCount
            .receive(on: DispatchQueue.main)
            .sink { [weak self] count in
                self?.gaussianCount = count
            }
            .store(in: &cancellables)
    }

    private func startRefreshTimer() {
        refreshTimer = Timer.scheduledTimer(
            withTimeInterval: refreshInterval,
            repeats: true
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.refresh()
            }
        }
    }

    // MARK: - Refresh

    /// Manually trigger a quality metrics refresh.
    func refresh() {
        guard let coordinator = coordinator else { return }

        // Ask coordinator to refresh from native pipeline
        coordinator.refreshQuality()

        let quality = coordinator.currentQuality

        // Update published metrics
        psnr = quality.psnrEstimate
        ssim = quality.ssimEstimate
        chamfer = quality.chamferEstimate
        normalConsistency = quality.normalConsistency
        scaleAccuracy = quality.scaleAccuracy
        coverage = quality.coverageFScore
        psnrCILower = quality.psnrCILower
        psnrCIUpper = quality.psnrCIUpper

        // Update floor SLO indicators
        psnrMeetsFloor = psnr >= FloorSLO.psnr
        ssimMeetsFloor = ssim >= FloorSLO.ssim
        coverageMeetsFloor = coverage >= FloorSLO.coverage
        normalMeetsFloor = normalConsistency >= FloorSLO.normalConsistency
        scaleMeetsFloor = scaleAccuracy >= FloorSLO.scaleAccuracy
        chamferMeetsFloor = chamfer <= FloorSLO.chamferMax

        // Update progress (coverage-based, 0 to 1)
        progress = min(1.0, max(0.0, coverage / FloorSLO.coverage))

        // Update SLO timing metrics
        jankRate = coordinator.jankRate
        ttfs5 = coordinator.ttfs5
    }

    /// Stop the automatic refresh timer.
    func stopRefresh() {
        refreshTimer?.invalidate()
        refreshTimer = nil
    }

    /// Restart the automatic refresh timer (e.g., after app returns to foreground).
    func resumeRefresh() {
        stopRefresh()
        startRefreshTimer()
    }
}
