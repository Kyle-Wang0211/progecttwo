import Foundation

enum ObjectModeV2RemoteStageState: Sendable, Equatable {
    case pending
    case processing(progress: Double?)
    case completed
    case failed(reason: String)
}

struct ObjectModeV2StageStatus: Sendable, Equatable {
    let stage: ObjectModeV2Stage
    let state: ObjectModeV2RemoteStageState
}

struct ObjectModeV2JobStatus: Sendable, Equatable {
    let stages: [ObjectModeV2StageStatus]

    func status(for stage: ObjectModeV2Stage) -> ObjectModeV2RemoteStageState {
        stages.first(where: { $0.stage == stage })?.state ?? .pending
    }
}

protocol ObjectModeV2RemoteClient {
    func upload(videoURL: URL) async throws -> String
    func startSession(assetId: String) async throws -> String
    func pollStatus(sessionId: String) async throws -> ObjectModeV2JobStatus
    func downloadStage(sessionId: String, stage: ObjectModeV2Stage) async throws -> (data: Data, format: ArtifactFormat)
}
