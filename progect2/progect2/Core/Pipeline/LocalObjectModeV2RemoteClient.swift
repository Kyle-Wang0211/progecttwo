import Foundation

actor LocalObjectModeV2RemoteClient: ObjectModeV2RemoteClient {
    private struct LocalSession {
        let assetURL: URL
        var pollCount: Int
    }

    private var assets: [String: URL] = [:]
    private var sessions: [String: LocalSession] = [:]

    func upload(videoURL: URL) async throws -> String {
        guard FileManager.default.fileExists(atPath: videoURL.path) else {
            throw RemoteB1ClientError.uploadFailed("Input file does not exist")
        }
        let assetId = "omv2-asset-\(UUID().uuidString)"
        assets[assetId] = videoURL
        return assetId
    }

    func startSession(assetId: String) async throws -> String {
        guard let assetURL = assets[assetId] else {
            throw RemoteB1ClientError.invalidResponse
        }
        let sessionId = "omv2-session-\(UUID().uuidString)"
        sessions[sessionId] = LocalSession(assetURL: assetURL, pollCount: 0)
        return sessionId
    }

    func pollStatus(sessionId: String) async throws -> ObjectModeV2JobStatus {
        guard var session = sessions[sessionId] else {
            throw RemoteB1ClientError.invalidResponse
        }
        session.pollCount += 1
        sessions[sessionId] = session

        let statuses: [ObjectModeV2StageStatus] = [
            .init(stage: .preview, state: previewState(for: session.pollCount)),
            .init(stage: .defaultStage, state: defaultState(for: session.pollCount)),
            .init(stage: .hq, state: hqState(for: session.pollCount))
        ]
        return ObjectModeV2JobStatus(stages: statuses)
    }

    func downloadStage(sessionId: String, stage: ObjectModeV2Stage) async throws -> (data: Data, format: ArtifactFormat) {
        guard let session = sessions[sessionId] else {
            throw RemoteB1ClientError.invalidResponse
        }
        let data = try ObjectModeV2LocalPreviewAssetFactory.generateDeterministicPLY(from: session.assetURL, stage: stage)
        return (data, .splatPly)
    }

    private func previewState(for pollCount: Int) -> ObjectModeV2RemoteStageState {
        switch pollCount {
        case ..<1: return .pending
        case 1: return .processing(progress: 0.35)
        case 2: return .processing(progress: 0.8)
        default: return .completed
        }
    }

    private func defaultState(for pollCount: Int) -> ObjectModeV2RemoteStageState {
        switch pollCount {
        case ..<3: return .pending
        case 3: return .processing(progress: 0.25)
        case 4: return .processing(progress: 0.55)
        case 5: return .processing(progress: 0.85)
        case 6...: return .completed
        }
    }

    private func hqState(for pollCount: Int) -> ObjectModeV2RemoteStageState {
        switch pollCount {
        case ..<6: return .pending
        case 6: return .processing(progress: 0.2)
        case 7: return .processing(progress: 0.45)
        case 8: return .processing(progress: 0.72)
        case 9: return .processing(progress: 0.92)
        case 10...: return .completed
        }
    }

}
