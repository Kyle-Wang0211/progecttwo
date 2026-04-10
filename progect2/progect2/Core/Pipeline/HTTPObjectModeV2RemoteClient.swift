import Foundation

#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

#if canImport(UIKit)
import UIKit
#endif

struct ObjectModeV2RemoteConfiguration: Sendable {
    let baseURL: URL
    let apiKey: String?
    let deviceId: String

    static func loadDefault() -> ObjectModeV2RemoteConfiguration? {
        let env = ProcessInfo.processInfo.environment
        let rawURL =
            env["AETHER_OBJECTMODEV2_BASE_URL"] ??
            env["AETHER_OBJECT_MODE_V2_BASE_URL"] ??
            env["AETHER_OBJECTMODE_V2_BASE_URL"] ??
            (Bundle.main.object(forInfoDictionaryKey: "AetherObjectModeV2BaseURL") as? String)

        guard let rawURL, let baseURL = URL(string: rawURL) else {
            return nil
        }

        let apiKey =
            env["AETHER_OBJECTMODEV2_API_KEY"] ??
            env["AETHER_OBJECT_MODE_V2_API_KEY"] ??
            env["AETHER_API_KEY"] ??
            (Bundle.main.object(forInfoDictionaryKey: "AetherObjectModeV2APIKey") as? String)

        return ObjectModeV2RemoteConfiguration(
            baseURL: baseURL,
            apiKey: apiKey,
            deviceId: resolveStableDeviceId()
        )
    }

    private static func resolveStableDeviceId() -> String {
        let defaults = UserDefaults.standard
        let key = "object_mode_v2.remote.device_id"
        if let existing = defaults.string(forKey: key) {
            return existing
        }
        let generated = UUID().uuidString.lowercased()
        defaults.set(generated, forKey: key)
        return generated
    }
}

enum ObjectModeV2RemoteClientFactory {
    static func makeDefault() -> ObjectModeV2RemoteClient {
        if let configuration = ObjectModeV2RemoteConfiguration.loadDefault() {
            return HTTPObjectModeV2RemoteClient(configuration: configuration)
        }
        return LocalObjectModeV2RemoteClient()
    }

    static func defaultBackendLabel() -> String {
        if let configuration = ObjectModeV2RemoteConfiguration.loadDefault() {
            return "真实远端：\(configuration.baseURL.absoluteString)"
        }
        return "本地 staged stub（未配置新版远端地址）"
    }
}

actor HTTPObjectModeV2RemoteClient: ObjectModeV2RemoteClient {
    private struct SessionState: Sendable {
        let localInputURL: URL
        let remoteJobId: String
        var artifactId: String?
        var lastProgress: Double?
        var lastObservedState: String?
    }

    private enum UploadBootstrap {
        case uploadSession(CreateUploadResponse)
        case existingJob(CompleteUploadResponse)
    }

    private let configuration: ObjectModeV2RemoteConfiguration
    private let urlSession: URLSession
    private var sessions: [String: SessionState] = [:]

    init(
        configuration: ObjectModeV2RemoteConfiguration,
        urlSession: URLSession = .shared
    ) {
        self.configuration = configuration
        self.urlSession = urlSession
    }

    func upload(videoURL: URL) async throws -> String {
        guard FileManager.default.fileExists(atPath: videoURL.path) else {
            throw RemoteB1ClientError.uploadFailed("Input file does not exist")
        }

        let hashResult = try HashCalculator.sha256OfFile(at: videoURL)
        guard hashResult.byteCount > 0 else {
            throw RemoteB1ClientError.uploadFailed("Input file is empty")
        }

        let captureSessionId = UUID().uuidString.lowercased()
        let idempotencyKey = IdempotencyManager.generateKey(
            bundleHash: hashResult.sha256Hex,
            captureSessionId: captureSessionId
        )
        let bundleSize = Int(hashResult.byteCount)

        let bootstrap = try await createUploadSession(
            captureSessionId: captureSessionId,
            bundleHash: hashResult.sha256Hex,
            bundleSize: bundleSize,
            idempotencyKey: idempotencyKey
        )

        let remoteJobId: String
        switch bootstrap {
        case .existingJob(let response):
            remoteJobId = response.jobId
        case .uploadSession(let response):
            try await uploadChunks(
                fileURL: videoURL,
                uploadPath: response.uploadUrl,
                chunkSize: response.chunkSize
            )
            let completeResponse = try await completeUpload(
                uploadId: response.uploadId,
                bundleHash: hashResult.sha256Hex
            )
            remoteJobId = completeResponse.jobId
        }

        let localSessionId = "omv2-http-\(UUID().uuidString.lowercased())"
        sessions[localSessionId] = SessionState(
            localInputURL: videoURL,
            remoteJobId: remoteJobId,
            artifactId: nil,
            lastProgress: nil,
            lastObservedState: nil
        )
        return localSessionId
    }

    func startSession(assetId: String) async throws -> String {
        guard sessions[assetId] != nil else {
            throw RemoteB1ClientError.invalidResponse
        }
        return assetId
    }

    func pollStatus(sessionId: String) async throws -> ObjectModeV2JobStatus {
        guard var session = sessions[sessionId] else {
            throw RemoteB1ClientError.invalidResponse
        }

        let response = try await getJob(jobId: session.remoteJobId)
        session.artifactId = response.artifactId ?? session.artifactId
        session.lastProgress = response.progress.map { Double($0.percentage) / 100.0 } ?? session.lastProgress
        session.lastObservedState = response.state
        sessions[sessionId] = session

        let previewState: ObjectModeV2RemoteStageState = .completed
        let defaultState = synthesizeDefaultState(from: response)
        let hqState = synthesizeHQState(from: response)

        return ObjectModeV2JobStatus(stages: [
            .init(stage: .preview, state: previewState),
            .init(stage: .defaultStage, state: defaultState),
            .init(stage: .hq, state: hqState)
        ])
    }

    func downloadStage(
        sessionId: String,
        stage: ObjectModeV2Stage
    ) async throws -> (data: Data, format: ArtifactFormat) {
        guard let session = sessions[sessionId] else {
            throw RemoteB1ClientError.invalidResponse
        }

        switch stage {
        case .preview, .defaultStage:
            let data = try ObjectModeV2LocalPreviewAssetFactory.generateDeterministicPLY(
                from: session.localInputURL,
                stage: stage
            )
            return (data, .splatPly)
        case .hq:
            guard let artifactId = session.artifactId else {
                throw RemoteB1ClientError.downloadFailed("HQ artifact is not ready")
            }
            let artifact = try await getArtifact(artifactId: artifactId)
            let downloadURL = resolveRelativePath(artifact.downloadUrl)
            let data = try await performBinaryRequest(
                url: downloadURL,
                method: "GET",
                expectedStatusCodes: [200, 206]
            )
            return (data, artifact.format == "splat" ? .splat : .splatPly)
        }
    }

    private func synthesizeDefaultState(from response: GetJobResponse) -> ObjectModeV2RemoteStageState {
        switch response.state {
        case "failed", "cancelled":
            return .failed(reason: response.failureReason ?? response.cancelReason ?? "default stage failed")
        case "completed":
            return .completed
        case "processing", "packaging":
            return .completed
        case "queued", "pending", "uploading":
            return .processing(progress: 0.12)
        default:
            return .pending
        }
    }

    private func synthesizeHQState(from response: GetJobResponse) -> ObjectModeV2RemoteStageState {
        switch response.state {
        case "failed", "cancelled":
            return .failed(reason: response.failureReason ?? response.cancelReason ?? "hq stage failed")
        case "completed":
            return .completed
        case "processing", "packaging":
            return .processing(progress: response.progress.map { Double($0.percentage) / 100.0 })
        case "queued", "pending", "uploading":
            return .processing(progress: 0.02)
        default:
            return .pending
        }
    }

    private func createUploadSession(
        captureSessionId: String,
        bundleHash: String,
        bundleSize: Int,
        idempotencyKey: String
    ) async throws -> UploadBootstrap {
        let requestedChunkCount = max(1, Int((Int64(bundleSize) + 5_000_000 - 1) / 5_000_000))
        let request = CreateUploadRequest(
            captureSource: "aether_camera",
            captureSessionId: captureSessionId,
            bundleHash: bundleHash,
            bundleSize: bundleSize,
            chunkCount: requestedChunkCount,
            idempotencyKey: idempotencyKey,
            deviceInfo: makeDeviceInfo()
        )

        let url = apiURL(path: "uploads")
        let data = try JSONEncoder().encode(request)
        let (responseData, statusCode) = try await performJSONRequest(
            url: url,
            method: "POST",
            body: data,
            expectedStatusCodes: [200, 201]
        )

        let decoder = JSONDecoder()
        if statusCode == 201 {
            let envelope = try decoder.decode(APIResponse<CreateUploadResponse>.self, from: responseData)
            guard envelope.success, let payload = envelope.data else {
                throw RemoteB1ClientError.invalidResponse
            }
            return .uploadSession(payload)
        }

        let envelope = try decoder.decode(APIResponse<CompleteUploadResponse>.self, from: responseData)
        guard envelope.success, let payload = envelope.data else {
            throw RemoteB1ClientError.invalidResponse
        }
        return .existingJob(payload)
    }

    private func uploadChunks(fileURL: URL, uploadPath: String, chunkSize: Int) async throws {
        let uploadURL = resolveRelativePath(uploadPath)
        let handle = try FileHandle(forReadingFrom: fileURL)
        defer { try? handle.close() }

        var chunkIndex = 0
        while true {
            let chunk = handle.readData(ofLength: chunkSize)
            if chunk.isEmpty {
                break
            }

            var request = baseRequest(url: uploadURL, method: "PATCH")
            request.httpBody = chunk
            request.setValue(String(chunk.count), forHTTPHeaderField: "Content-Length")
            request.setValue(String(chunkIndex), forHTTPHeaderField: "X-Chunk-Index")
            request.setValue(HashCalculator.sha256(of: chunk), forHTTPHeaderField: "X-Chunk-Hash")

            let (responseData, _) = try await performRequest(
                request,
                expectedStatusCodes: [200]
            )
            let envelope = try JSONDecoder().decode(APIResponse<UploadChunkResponse>.self, from: responseData)
            guard envelope.success else {
                throw RemoteB1ClientError.uploadFailed(envelope.error?.message ?? "Chunk upload failed")
            }
            chunkIndex += 1
        }
    }

    private func completeUpload(uploadId: String, bundleHash: String) async throws -> CompleteUploadResponse {
        let request = CompleteUploadRequest(bundleHash: bundleHash)
        let url = apiURL(path: "uploads/\(uploadId)/complete")
        let responseData = try await performJSONRequest(
            url: url,
            method: "POST",
            body: try JSONEncoder().encode(request),
            expectedStatusCodes: [200]
        ).0
        let envelope = try JSONDecoder().decode(APIResponse<CompleteUploadResponse>.self, from: responseData)
        guard envelope.success, let payload = envelope.data else {
            throw RemoteB1ClientError.invalidResponse
        }
        return payload
    }

    private func getJob(jobId: String) async throws -> GetJobResponse {
        let url = apiURL(path: "jobs/\(jobId)")
        let responseData = try await performJSONRequest(
            url: url,
            method: "GET",
            expectedStatusCodes: [200]
        ).0
        let envelope = try JSONDecoder().decode(APIResponse<GetJobResponse>.self, from: responseData)
        guard envelope.success, let payload = envelope.data else {
            throw RemoteB1ClientError.invalidResponse
        }
        return payload
    }

    private func getArtifact(artifactId: String) async throws -> GetArtifactResponse {
        let url = apiURL(path: "artifacts/\(artifactId)")
        let responseData = try await performJSONRequest(
            url: url,
            method: "GET",
            expectedStatusCodes: [200]
        ).0
        let envelope = try JSONDecoder().decode(APIResponse<GetArtifactResponse>.self, from: responseData)
        guard envelope.success, let payload = envelope.data else {
            throw RemoteB1ClientError.invalidResponse
        }
        return payload
    }

    private func makeDeviceInfo() -> DeviceInfo {
        #if canImport(UIKit)
        let model = UIDevice.current.model
        let osVersion = UIDevice.current.systemVersion
        #else
        let model = ProcessInfo.processInfo.hostName
        let osVersion = ProcessInfo.processInfo.operatingSystemVersionString
        #endif

        let appVersion =
            Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ??
            "dev"
        return DeviceInfo(model: model, osVersion: osVersion, appVersion: appVersion)
    }

    private func apiURL(path: String) -> URL {
        let normalizedBase: URL
        if configuration.baseURL.lastPathComponent == "v1" {
            normalizedBase = configuration.baseURL
        } else {
            normalizedBase = configuration.baseURL.appendingPathComponent("v1")
        }
        return normalizedBase.appendingPathComponent(path)
    }

    private func resolveRelativePath(_ path: String) -> URL {
        if let absolute = URL(string: path), absolute.scheme != nil {
            return absolute
        }
        return URL(string: path, relativeTo: configuration.baseURL)?.absoluteURL
            ?? configuration.baseURL.appendingPathComponent(path)
    }

    private func performJSONRequest(
        url: URL,
        method: String,
        body: Data? = nil,
        expectedStatusCodes: [Int]
    ) async throws -> (Data, Int) {
        let request = baseRequest(url: url, method: method, body: body)
        return try await performRequest(request, expectedStatusCodes: expectedStatusCodes)
    }

    private func performBinaryRequest(
        url: URL,
        method: String,
        expectedStatusCodes: [Int]
    ) async throws -> Data {
        let request = baseRequest(url: url, method: method)
        return try await performRequest(request, expectedStatusCodes: expectedStatusCodes).0
    }

    private func baseRequest(url: URL, method: String, body: Data? = nil) -> URLRequest {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.httpBody = body
        request.timeoutInterval = 120
        request.setValue(configuration.deviceId, forHTTPHeaderField: "X-Device-Id")
        if let apiKey = configuration.apiKey, !apiKey.isEmpty {
            request.setValue(apiKey, forHTTPHeaderField: "X-API-Key")
        }
        if body != nil {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    private func performRequest(
        _ request: URLRequest,
        expectedStatusCodes: [Int]
    ) async throws -> (Data, Int) {
        do {
            let (data, response) = try await urlSession.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                throw RemoteB1ClientError.invalidResponse
            }
            guard expectedStatusCodes.contains(http.statusCode) else {
                throw decodeServerError(from: data, statusCode: http.statusCode)
            }
            return (data, http.statusCode)
        } catch let error as RemoteB1ClientError {
            throw error
        } catch {
            throw RemoteB1ClientError.networkError(error.localizedDescription)
        }
    }

    private func decodeServerError(from data: Data, statusCode: Int) -> RemoteB1ClientError {
        if let envelope = try? JSONDecoder().decode(APIResponse<[String: String]>.self, from: data),
           let message = envelope.error?.message {
            if statusCode == 409 {
                return .jobFailed(message)
            }
            return .networkError(message)
        }
        return .networkError("Unexpected HTTP \(statusCode)")
    }
}
