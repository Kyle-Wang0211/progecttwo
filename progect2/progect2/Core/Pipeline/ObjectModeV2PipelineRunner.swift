import Foundation
#if canImport(AVFoundation)
import AVFoundation
#endif

enum ObjectModeV2PipelineEvent: Sendable {
    case stageProgress(stage: ObjectModeV2Stage, progress: Double?)
    case stageReady(stage: ObjectModeV2Stage, manifestURL: URL)
}

struct ObjectModeV2GenerateResult: Sendable {
    let manifestURL: URL
    let highestReadyStage: ObjectModeV2Stage
    let elapsedMs: Int
}

final class ObjectModeV2PipelineRunner: @unchecked Sendable {
    private let remoteClient: ObjectModeV2RemoteClient

    init(remoteClient: ObjectModeV2RemoteClient = ObjectModeV2RemoteClientFactory.makeDefault()) {
        self.remoteClient = remoteClient
    }

    func runGenerate(
        request: BuildRequest,
        displayName: String,
        onEvent: @escaping @Sendable (ObjectModeV2PipelineEvent) async -> Void
    ) async throws -> ObjectModeV2GenerateResult {
        let startTime = Date()
        let videoURL = try resolveVideoURL(from: request)

        let assetId = try await remoteClient.upload(videoURL: videoURL)
        let sessionId = try await remoteClient.startSession(assetId: assetId)

        let packageDir = try makePackageDirectory(sessionId: sessionId)
        let manifestURL = packageDir.appendingPathComponent("manifest.json")
        var manifest = ObjectModeV2AssetManifest(sessionId: sessionId, displayName: displayName)
        try writeManifest(manifest, to: manifestURL)

        var downloadedStages = Set<ObjectModeV2Stage>()
        var highestReadyStage: ObjectModeV2Stage = .preview

        while downloadedStages.count < ObjectModeV2Stage.allCases.count {
            let status = try await remoteClient.pollStatus(sessionId: sessionId)

            for stage in ObjectModeV2Stage.allCases {
                switch status.status(for: stage) {
                case .pending:
                    break
                case .processing(let progress):
                    await onEvent(.stageProgress(stage: stage, progress: progress))
                case .completed:
                    if !downloadedStages.contains(stage) {
                        let (data, format) = try await remoteClient.downloadStage(sessionId: sessionId, stage: stage)
                        let asset = try writeStageAsset(
                            data: data,
                            format: format,
                            stage: stage,
                            packageDir: packageDir
                        )
                        manifest = updateManifest(manifest, with: asset)
                        highestReadyStage = stage
                        try writeManifest(manifest, to: manifestURL)
                        downloadedStages.insert(stage)
                        await onEvent(.stageReady(stage: stage, manifestURL: manifestURL))
                    }
                case .failed(let reason):
                    throw RemoteB1ClientError.jobFailed(reason)
                }
            }

            if downloadedStages.count == ObjectModeV2Stage.allCases.count {
                break
            }
            try await Task.sleep(nanoseconds: 700_000_000)
        }

        let elapsedMs = Int(Date().timeIntervalSince(startTime) * 1000)
        return ObjectModeV2GenerateResult(
            manifestURL: manifestURL,
            highestReadyStage: highestReadyStage,
            elapsedMs: elapsedMs
        )
    }

    private func resolveVideoURL(from request: BuildRequest) throws -> URL {
        switch request.source {
        case .file(let url):
            return url
        case .video(let asset):
            #if canImport(AVFoundation)
            guard let urlAsset = asset as? AVURLAsset else {
                throw FailReason.inputInvalid
            }
            return urlAsset.url
            #else
            throw FailReason.inputInvalid
            #endif
        }
    }

    private func makePackageDirectory(sessionId: String) throws -> URL {
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let base = documents
            .appendingPathComponent("Aether3D")
            .appendingPathComponent("ObjectModeV2", isDirectory: true)
        try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        let sessionDir = base.appendingPathComponent(sessionId, isDirectory: true)
        try FileManager.default.createDirectory(at: sessionDir, withIntermediateDirectories: true)
        return sessionDir
    }

    private func writeStageAsset(
        data: Data,
        format: ArtifactFormat,
        stage: ObjectModeV2Stage,
        packageDir: URL
    ) throws -> ObjectModeV2StageAsset {
        let fileExtension = format == .splat ? "splat" : "ply"
        let filename = "\(stage.rawValue).\(fileExtension)"
        let fileURL = packageDir.appendingPathComponent(filename)
        try data.write(to: fileURL, options: .atomic)
        return ObjectModeV2StageAsset(stage: stage, relativePath: filename, format: fileExtension)
    }

    private func updateManifest(
        _ manifest: ObjectModeV2AssetManifest,
        with asset: ObjectModeV2StageAsset
    ) -> ObjectModeV2AssetManifest {
        var updated = manifest
        updated.currentStage = asset.stage
        switch asset.stage {
        case .preview:
            updated.preview = asset
        case .defaultStage:
            updated.defaultAsset = asset
        case .hq:
            updated.hq = asset
        }
        return updated
    }

    private func writeManifest(_ manifest: ObjectModeV2AssetManifest, to url: URL) throws {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(manifest)
        try data.write(to: url, options: .atomic)
    }
}
