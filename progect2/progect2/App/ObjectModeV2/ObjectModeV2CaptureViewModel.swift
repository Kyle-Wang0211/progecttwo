import Foundation

#if canImport(SwiftUI)
import SwiftUI
import Aether3DCore
#if canImport(UIKit)
import UIKit
#endif
#if canImport(AVFoundation)
import AVFoundation
#endif

enum ObjectModeV2StageUIState: Equatable {
    case idle
    case processing(Double?)
    case ready
}

struct ObjectModeV2StageCard: Identifiable, Equatable {
    let id: ObjectModeV2Stage
    let title: String
    let subtitle: String
    var state: ObjectModeV2StageUIState
}

enum ObjectModeV2TargetZoneMode: String, CaseIterable {
    case subject
    case group

    var title: String {
        switch self {
        case .subject:
            return "Subject"
        case .group:
            return "Group"
        }
    }

    var subtitle: String {
        switch self {
        case .subject:
            return "单主体"
        case .group:
            return "小群组"
        }
    }
}

#if canImport(UIKit)
struct ObjectModeV2AcceptedFrameThumbnail: Identifiable, Equatable {
    let id: UUID
    let image: UIImage

    static func == (lhs: ObjectModeV2AcceptedFrameThumbnail, rhs: ObjectModeV2AcceptedFrameThumbnail) -> Bool {
        lhs.id == rhs.id
    }
}
#endif

@MainActor
final class ObjectModeV2CaptureViewModel: ObservableObject {
    @Published var previewSession: AVCaptureSession?
    @Published var isPreparingCamera = true
    @Published var cameraError: String?
    @Published var isRecording = false
    @Published var acceptedFrames = 0
    @Published var orbitCompletion = 0.0
    @Published var stabilityScore = 1.0
    @Published var guidanceText = "将物体放在画面中央，开始后沿着主体缓慢绕一圈。"
    @Published var recordingSeconds = 0
    @Published var isRunning = false
    @Published var statusText = "旧版云端高质量不动，这里直接走新版对象模式 Beta。"
    @Published var manifestURL: URL?
    @Published var recordToOpen: ScanRecord?
    @Published var batteryPercentageText = "100%"
    @Published var targetZoneMode: ObjectModeV2TargetZoneMode = .subject
    @Published var isTargetLocked = false
    @Published var targetZoneAnchor = CGPoint(x: 0.5, y: 0.64)
    @Published var acceptedFrameFeedbackTick = 0
    #if canImport(UIKit)
    @Published var acceptedFrameThumbnails: [ObjectModeV2AcceptedFrameThumbnail] = []
    #endif
    @Published var stageCards: [ObjectModeV2StageCard] = [
        .init(id: .preview, title: "Preview", subtitle: "秒级预览", state: .idle),
        .init(id: .defaultStage, title: "Default", subtitle: "默认成品", state: .idle),
        .init(id: .hq, title: "HQ", subtitle: "高清成品", state: .idle)
    ]

    private let pipelineRunner = ObjectModeV2PipelineRunner()
    private let store = ScanRecordStore()
    private let recorder = ObjectModeV2CaptureRecorder()
    private let guidanceEngine = ObjectModeV2GuidanceEngine()
    #if canImport(UIKit)
    let previewBridge = ObjectModeV2PreviewBridge()
    private var batteryObserver: NSObjectProtocol?
    #endif

    private var currentRecordId = UUID()
    private var durationTask: Task<Void, Never>?

    init() {
        guidanceEngine.onUpdate = { [weak self] snapshot in
            guard let self else { return }
            let previousAcceptedFrames = self.acceptedFrames
            self.acceptedFrames = snapshot.acceptedFrames
            self.orbitCompletion = snapshot.orbitCompletion
            self.stabilityScore = snapshot.stabilityScore
            self.guidanceText = self.resolvedGuidanceText(for: snapshot)
            #if canImport(UIKit)
            if snapshot.acceptedFrames > previousAcceptedFrames {
                self.captureAcceptedFrameThumbnail()
                self.registerAcceptedFrameFeedback()
            }
            #endif
        }
    }

    var canStopCapture: Bool {
        isRecording
    }

    var canStartCapture: Bool {
        !isPreparingCamera && cameraError == nil && !isRecording && !isRunning
    }

    func onAppear() {
        #if canImport(UIKit)
        startBatteryMonitoring()
        #endif
        Task {
            await prepareCameraIfNeeded()
        }
    }

    func onDisappear() {
        durationTask?.cancel()
        guidanceEngine.stopMonitoring()
        recorder.shutdown()
        #if canImport(UIKit)
        stopBatteryMonitoring()
        #endif
    }

    func toggleCapture() {
        if isRecording {
            stopCaptureAndGenerate()
        } else {
            startCapture()
        }
    }

    func setTargetZoneMode(_ mode: ObjectModeV2TargetZoneMode) {
        targetZoneMode = mode
        guard isTargetLocked else { return }
        guidanceText = mode == .subject
            ? "主体已锁定，开始后围绕这个目标缓慢移动。"
            : "主体组已锁定，开始后保持整组都在目标区内。"
    }

    func lockTarget(at point: CGPoint, in size: CGSize) {
        guard size.width > 0, size.height > 0 else { return }
        targetZoneAnchor = CGPoint(
            x: min(max(point.x / size.width, 0.18), 0.82),
            y: min(max(point.y / size.height, 0.18), 0.86)
        )
        isTargetLocked = true
        guidanceText = targetZoneMode == .subject
            ? "主体已锁定，开始后围绕这个目标缓慢移动。"
            : "主体组已锁定，开始后保持整组都在目标区内。"
    }

    func resetTargetLock() {
        isTargetLocked = false
        targetZoneAnchor = CGPoint(x: 0.5, y: 0.64)
        guidanceText = "将物体放在画面中央，开始后沿着主体缓慢绕一圈。"
    }

    func openRecord() {
        if let manifestURL {
            recordToOpen = ScanRecord(
                id: currentRecordId,
                name: "对象模式 Beta",
                createdAt: Date(),
                thumbnailPath: nil,
                artifactPath: manifestURL.path,
                pipelineKind: .objectModeV2,
                pipelineStage: "查看成品"
            )
        }
    }

    private func prepareCameraIfNeeded() async {
        if previewSession != nil || !isPreparingCamera {
            return
        }

        do {
            try await recorder.prepare()
            previewSession = recorder.previewSession
            guidanceEngine.startMonitoring()
            isPreparingCamera = false
            cameraError = nil
            statusText = "准备就绪。\(ObjectModeV2RemoteClientFactory.defaultBackendLabel())。开始后系统会自动挑选有效关键帧，并先给出 Preview / Default，再等 HQ。"
        } catch {
            cameraError = error.localizedDescription
            isPreparingCamera = false
            statusText = "相机准备失败"
        }
    }

    private func startCapture() {
        guard canStartCapture else { return }

        currentRecordId = UUID()
        manifestURL = nil
        recordToOpen = nil
        recordingSeconds = 0
        acceptedFrames = 0
        orbitCompletion = 0
        stabilityScore = 1
        guidanceText = isTargetLocked
            ? (targetZoneMode == .subject
                ? "主体已锁定，开始后围绕这个目标缓慢移动。"
                : "主体组已锁定，开始后保持整组都在目标区内。")
            : "将物体放在画面中央，开始后沿着主体缓慢绕一圈。"
        #if canImport(UIKit)
        acceptedFrameThumbnails = []
        #endif
        stageCards = stageCards.map { .init(id: $0.id, title: $0.title, subtitle: $0.subtitle, state: .idle) }

        do {
            try recorder.startRecording()
            isRecording = true
            guidanceEngine.beginRecording()
            statusText = "正在采集对象素材…"
            startDurationTicker()
        } catch {
            cameraError = error.localizedDescription
            statusText = "开始录制失败"
        }
    }

    private func stopCaptureAndGenerate() {
        guard isRecording else { return }
        isRecording = false
        guidanceEngine.endRecording()
        durationTask?.cancel()

        Task {
            do {
                let clip = try await recorder.stopRecording()
                await runPipeline(with: clip)
            } catch {
                statusText = "停止录制失败：\(error.localizedDescription)"
            }
        }
    }

    private func runPipeline(with clip: ObjectModeV2RecordedClip) async {
        isRunning = true
        manifestURL = nil
        recordToOpen = nil
        statusText = acceptedFrames < 20
            ? "素材偏少，仍会继续尝试生成预览与成品。"
            : "素材已锁定，正在启动新版对象模式管线…"
        stageCards = stageCards.map { .init(id: $0.id, title: $0.title, subtitle: $0.subtitle, state: .idle) }

        do {
            #if canImport(AVFoundation)
            let asset = AVAsset(url: clip.fileURL)
            let request = BuildRequest(source: .video(asset: asset), requestedMode: .enter, deviceTier: DeviceTier.current())
            #else
            let request = BuildRequest(source: .file(url: clip.fileURL), requestedMode: .enter, deviceTier: .medium)
            #endif

            let thumbnailPath = try await generateThumbnail(for: clip.fileURL, recordId: currentRecordId)

            let result = try await pipelineRunner.runGenerate(
                request: request,
                displayName: "对象模式 Beta \(DateFormatter.localizedString(from: Date(), dateStyle: .short, timeStyle: .short))"
            ) { [weak self] event in
                guard let self else { return }
                await MainActor.run {
                    switch event {
                    case .stageProgress(let stage, let progress):
                        self.updateStage(stage, state: .processing(progress))
                        self.statusText = "\(stage.displayName) 处理中…"
                    case .stageReady(let stage, let manifestURL):
                        self.manifestURL = manifestURL
                        self.updateStage(stage, state: .ready)
                        self.statusText = "\(stage.displayName) 已就绪"
                        self.upsertRecord(
                            manifestURL: manifestURL,
                            thumbnailPath: thumbnailPath,
                            stage: stage,
                            durationSeconds: clip.duration
                        )
                    }
                }
            }

            if let manifestURL {
                statusText = "高清成品已完成"
                upsertRecord(
                    manifestURL: manifestURL,
                    thumbnailPath: thumbnailPath,
                    stage: result.highestReadyStage,
                    durationSeconds: clip.duration
                )
            }
        } catch {
            statusText = "生成失败：\(error.localizedDescription)"
        }

        isRunning = false
    }

    private func updateStage(_ stage: ObjectModeV2Stage, state: ObjectModeV2StageUIState) {
        stageCards = stageCards.map {
            guard $0.id == stage else { return $0 }
            return .init(id: $0.id, title: $0.title, subtitle: $0.subtitle, state: state)
        }
    }

    private func upsertRecord(
        manifestURL: URL,
        thumbnailPath: String?,
        stage: ObjectModeV2Stage,
        durationSeconds: TimeInterval
    ) {
        let record = ScanRecord(
            id: currentRecordId,
            name: "对象模式 Beta",
            createdAt: Date(),
            thumbnailPath: thumbnailPath,
            artifactPath: manifestURL.path,
            pipelineKind: .objectModeV2,
            pipelineStage: stage.displayName,
            coveragePercentage: max(orbitCompletion, stage == .preview ? 0.35 : (stage == .defaultStage ? 0.7 : 0.95)),
            triangleCount: stage == .preview ? 1200 : (stage == .defaultStage ? 4800 : 9600),
            durationSeconds: durationSeconds
        )
        store.upsertRecord(record)
    }

    private func startDurationTicker() {
        durationTask?.cancel()
        durationTask = Task { [weak self] in
            while let self, !Task.isCancelled, self.isRecording {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                if self.isRecording {
                    self.recordingSeconds += 1
                }
            }
        }
    }

    #if canImport(UIKit)
    private func captureAcceptedFrameThumbnail() {
        guard isRecording else { return }
        guard let image = previewBridge.captureSnapshotImage() else { return }
        let thumbnail = ObjectModeV2AcceptedFrameThumbnail(id: UUID(), image: image)
        acceptedFrameThumbnails.append(thumbnail)
        if acceptedFrameThumbnails.count > 8 {
            acceptedFrameThumbnails.removeFirst(acceptedFrameThumbnails.count - 8)
        }
    }

    private func registerAcceptedFrameFeedback() {
        acceptedFrameFeedbackTick += 1
        let generator = UIImpactFeedbackGenerator(style: .light)
        generator.prepare()
        generator.impactOccurred(intensity: 0.82)
    }

    private func startBatteryMonitoring() {
        UIDevice.current.isBatteryMonitoringEnabled = true
        refreshBatteryPercentage()
        guard batteryObserver == nil else { return }
        batteryObserver = NotificationCenter.default.addObserver(
            forName: UIDevice.batteryLevelDidChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            self?.refreshBatteryPercentage()
        }
    }

    private func stopBatteryMonitoring() {
        if let batteryObserver {
            NotificationCenter.default.removeObserver(batteryObserver)
            self.batteryObserver = nil
        }
        UIDevice.current.isBatteryMonitoringEnabled = false
    }

    private func refreshBatteryPercentage() {
        let level = UIDevice.current.batteryLevel
        guard level >= 0 else {
            batteryPercentageText = "100%"
            return
        }
        batteryPercentageText = "\(max(1, Int(round(level * 100))))%"
    }
    #endif

    private func resolvedGuidanceText(for snapshot: ObjectModeV2GuidanceSnapshot) -> String {
        guard isTargetLocked else { return snapshot.hintText }

        if snapshot.stabilityScore < 0.28 {
            return targetZoneMode == .subject
                ? "主体已锁定，先稳住手机，再继续围绕主体移动。"
                : "主体组已锁定，先稳住手机，再继续围绕整组移动。"
        }

        if snapshot.orbitCompletion < 0.3 {
            return targetZoneMode == .subject
                ? "主体已锁定，先补正面和侧面，保持目标在锁定框附近。"
                : "主体组已锁定，先补正面和侧面，保持整组都在锁定框附近。"
        }

        if snapshot.acceptedFrames < 20 {
            return targetZoneMode == .subject
                ? "主体已锁定，继续补新的角度，先把一圈拍完整。"
                : "主体组已锁定，继续补新的角度，先把整组一圈拍完整。"
        }

        if snapshot.acceptedFrames < 60 {
            return targetZoneMode == .subject
                ? "主体已锁定，质量已经不错，可以继续补顶部和边缘细节。"
                : "主体组已锁定，质量已经不错，可以继续补整组顶部和边缘细节。"
        }

        return targetZoneMode == .subject
            ? "主体已锁定，成品质量已经很好，可以结束或继续补更细节角度。"
            : "主体组已锁定，成品质量已经很好，可以结束或继续补更细节角度。"
    }

    private func generateThumbnail(for videoURL: URL, recordId: UUID) async throws -> String? {
        #if canImport(AVFoundation) && canImport(UIKit)
        let asset = AVAsset(url: videoURL)
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        let cgImage = try generator.copyCGImage(at: .zero, actualTime: nil)
        let image = UIImage(cgImage: cgImage)
        guard let data = image.jpegData(compressionQuality: 0.82) else { return nil }
        return store.saveThumbnail(data, for: recordId)
        #else
        return nil
        #endif
    }
}

#endif
