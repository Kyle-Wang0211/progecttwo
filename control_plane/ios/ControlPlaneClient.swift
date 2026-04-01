import Foundation

public final class ControlPlaneClient: NSObject, Sendable {
    private let baseURL: URL
    private let foregroundSession: URLSession
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    public init(baseURL: URL, foregroundSession: URLSession = .shared) {
        self.baseURL = baseURL
        self.foregroundSession = foregroundSession
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        self.encoder = encoder

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        self.decoder = decoder
        super.init()
    }

    public func createJob(_ requestBody: ControlPlaneCreateJobRequest) async throws -> ControlPlaneCreateJobResponse {
        var request = URLRequest(url: baseURL.appending(path: "/v1/jobs"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try encoder.encode(requestBody)
        let (data, response) = try await foregroundSession.data(for: request)
        try Self.ensureHTTP200(response)
        return try decoder.decode(ControlPlaneCreateJobResponse.self, from: data)
    }

    public func prepareUpload(jobID: String) async throws -> ControlPlaneUploadInitResponse {
        var request = URLRequest(url: baseURL.appending(path: "/v1/jobs/\(jobID)/upload-init"))
        request.httpMethod = "POST"
        let (data, response) = try await foregroundSession.data(for: request)
        try Self.ensureHTTP200(response)
        return try decoder.decode(ControlPlaneUploadInitResponse.self, from: data)
    }

    public func markUploadComplete(jobID: String, body: ControlPlaneUploadCompleteRequest) async throws -> ControlPlaneJobStatus {
        var request = URLRequest(url: baseURL.appending(path: "/v1/jobs/\(jobID)/upload-complete"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try encoder.encode(body)
        let (data, response) = try await foregroundSession.data(for: request)
        try Self.ensureHTTP200(response)
        return try decoder.decode(ControlPlaneJobStatus.self, from: data)
    }

    public func fetchJob(jobID: String) async throws -> ControlPlaneJobStatus {
        let (data, response) = try await foregroundSession.data(from: baseURL.appending(path: "/v1/jobs/\(jobID)"))
        try Self.ensureHTTP200(response)
        return try decoder.decode(ControlPlaneJobStatus.self, from: data)
    }

    public func cancelJob(jobID: String) async throws -> ControlPlaneJobStatus {
        var request = URLRequest(url: baseURL.appending(path: "/v1/jobs/\(jobID)"))
        request.httpMethod = "DELETE"
        let (data, response) = try await foregroundSession.data(for: request)
        try Self.ensureHTTP200(response)
        return try decoder.decode(ControlPlaneJobStatus.self, from: data)
    }

    private static func ensureHTTP200(_ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
    }
}
