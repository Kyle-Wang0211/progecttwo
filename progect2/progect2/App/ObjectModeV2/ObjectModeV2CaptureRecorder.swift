import Foundation

#if canImport(UIKit) && canImport(AVFoundation)
import UIKit
@preconcurrency import AVFoundation

enum ObjectModeV2CaptureRecorderError: LocalizedError {
    case permissionDenied
    case notPrepared
    case alreadyRecording
    case notRecording
    case recordingFailed(String)

    var errorDescription: String? {
        switch self {
        case .permissionDenied:
            return "请先允许相机权限，再使用对象模式。"
        case .notPrepared:
            return "相机尚未准备好。"
        case .alreadyRecording:
            return "已经在录制中。"
        case .notRecording:
            return "当前没有正在录制的内容。"
        case .recordingFailed(let message):
            return message
        }
    }
}

struct ObjectModeV2RecordedClip: Sendable {
    let fileURL: URL
    let duration: TimeInterval
    let fileSize: Int64
}

@MainActor
final class ObjectModeV2CaptureRecorder: NSObject, ObservableObject {
    private let cameraSession: CameraSessionProtocol
    private var stopContinuation: CheckedContinuation<ObjectModeV2RecordedClip, Error>?
    private var recordingStartedAt: Date?

    @Published private(set) var isPrepared = false
    @Published private(set) var isRecording = false

    var previewSession: AVCaptureSession {
        cameraSession.captureSession
    }

    init(cameraSession: CameraSessionProtocol = CameraSession()) {
        self.cameraSession = cameraSession
        super.init()
    }

    func prepare() async throws {
        if isPrepared {
            cameraSession.startRunning()
            return
        }

        let authorizationStatus = AVCaptureDevice.authorizationStatus(for: .video)
        switch authorizationStatus {
        case .authorized:
            break
        case .notDetermined:
            let granted = await withCheckedContinuation { continuation in
                AVCaptureDevice.requestAccess(for: .video) { granted in
                    continuation.resume(returning: granted)
                }
            }
            guard granted else {
                throw ObjectModeV2CaptureRecorderError.permissionDenied
            }
        case .denied, .restricted:
            throw ObjectModeV2CaptureRecorderError.permissionDenied
        @unknown default:
            throw ObjectModeV2CaptureRecorderError.permissionDenied
        }

        try cameraSession.configure(orientation: .portrait)
        cameraSession.startRunning()
        isPrepared = true
    }

    func startRecording() throws {
        guard isPrepared else {
            throw ObjectModeV2CaptureRecorderError.notPrepared
        }
        guard !isRecording else {
            throw ObjectModeV2CaptureRecorderError.alreadyRecording
        }

        let outputURL = try makeOutputURL()
        recordingStartedAt = Date()
        isRecording = true
        cameraSession.startRecording(to: outputURL, delegate: self)
    }

    func stopRecording() async throws -> ObjectModeV2RecordedClip {
        guard isRecording else {
            throw ObjectModeV2CaptureRecorderError.notRecording
        }

        return try await withCheckedThrowingContinuation { continuation in
            stopContinuation = continuation
            cameraSession.stopRecording()
        }
    }

    func shutdown() {
        if isRecording {
            cameraSession.stopRecording()
        }
        previewSession.stopRunning()
        isPrepared = false
        isRecording = false
    }

    private func makeOutputURL() throws -> URL {
        let base = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("ObjectModeV2Captures", isDirectory: true)
        try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        return base.appendingPathComponent("\(UUID().uuidString.lowercased()).mov")
    }

    private func finishRecording(outputFileURL: URL, error: Error?) {
        let continuation = stopContinuation
        stopContinuation = nil
        isRecording = false

        if let error {
            continuation?.resume(throwing: ObjectModeV2CaptureRecorderError.recordingFailed(error.localizedDescription))
            return
        }

        let end = Date()
        let duration = recordingStartedAt.map { end.timeIntervalSince($0) } ?? 0
        let fileSize = (try? FileManager.default.attributesOfItem(atPath: outputFileURL.path)[.size] as? NSNumber)?.int64Value ?? 0
        continuation?.resume(returning: ObjectModeV2RecordedClip(
            fileURL: outputFileURL,
            duration: duration,
            fileSize: fileSize
        ))
    }
}

extension ObjectModeV2CaptureRecorder: AVCaptureFileOutputRecordingDelegate {
    nonisolated func fileOutput(
        _ output: AVCaptureFileOutput,
        didFinishRecordingTo outputFileURL: URL,
        from connections: [AVCaptureConnection],
        error: Error?
    ) {
        Task { @MainActor [weak self] in
            self?.finishRecording(outputFileURL: outputFileURL, error: error)
        }
    }
}

#endif
