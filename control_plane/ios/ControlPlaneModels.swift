import Foundation

public enum ControlPlaneJobState: String, Codable, Sendable {
    case created
    case uploading
    case uploaded
    case queued
    case assigned
    case reconstructing
    case trainingProbe = "training_probe"
    case trainingFull = "training_full"
    case exporting
    case completed
    case failed
    case cancelled
}

public struct ControlPlaneCreateJobRequest: Codable, Sendable {
    public var tenantId: String
    public var userId: String?
    public var fileName: String
    public var fileSizeBytes: Int64
    public var contentType: String
    public var captureOrigin: String
    public var clientRecordId: String?

    enum CodingKeys: String, CodingKey {
        case tenantId = "tenant_id"
        case userId = "user_id"
        case fileName = "file_name"
        case fileSizeBytes = "file_size_bytes"
        case contentType = "content_type"
        case captureOrigin = "capture_origin"
        case clientRecordId = "client_record_id"
    }
}

public struct ControlPlaneCreateJobResponse: Codable, Sendable {
    public var jobId: String
    public var state: ControlPlaneJobState
    public var uploadInitPath: String
    public var pollPath: String
    public var cancelPath: String

    enum CodingKeys: String, CodingKey {
        case jobId = "job_id"
        case state
        case uploadInitPath = "upload_init_path"
        case pollPath = "poll_path"
        case cancelPath = "cancel_path"
    }
}

public struct ControlPlaneUploadSpec: Codable, Sendable {
    public var method: String
    public var url: URL
    public var headers: [String: String]
    public var storageKey: String

    enum CodingKeys: String, CodingKey {
        case method
        case url
        case headers
        case storageKey = "storage_key"
    }
}

public struct ControlPlaneUploadInitResponse: Codable, Sendable {
    public var upload: ControlPlaneUploadSpec
}

public struct ControlPlaneUploadCompleteRequest: Codable, Sendable {
    public var etag: String?
    public var sizeBytes: Int64

    enum CodingKeys: String, CodingKey {
        case etag
        case sizeBytes = "size_bytes"
    }
}

public struct ControlPlaneArtifactDescriptor: Codable, Sendable {
    public var type: String
    public var storageKey: String
    public var downloadURL: URL?
    public var sizeBytes: Int64?
    public var checksumSHA256: String?

    enum CodingKeys: String, CodingKey {
        case type
        case storageKey = "storage_key"
        case downloadURL = "download_url"
        case sizeBytes = "size_bytes"
        case checksumSHA256 = "checksum_sha256"
    }
}

public struct ControlPlaneArtifactManifest: Codable, Sendable {
    public var primaryArtifact: ControlPlaneArtifactDescriptor?
    public var preview: ControlPlaneArtifactDescriptor?
    public var metrics: ControlPlaneArtifactDescriptor?
    public var viewerManifest: ControlPlaneArtifactDescriptor?

    enum CodingKeys: String, CodingKey {
        case primaryArtifact = "primary_artifact"
        case preview
        case metrics
        case viewerManifest = "viewer_manifest"
    }
}

public struct ControlPlaneJobStatus: Codable, Sendable {
    public var jobId: String
    public var state: ControlPlaneJobState
    public var stage: String?
    public var phaseName: String?
    public var currentTier: String?
    public var title: String
    public var detail: String
    public var progressFraction: Double?
    public var progressBasis: String?
    public var elapsedSeconds: Int?
    public var estimatedRemainingSeconds: Int?
    public var failureReason: String?
    public var failureDetail: String?
    public var artifact: ControlPlaneArtifactManifest?
    public var assignedWorkerId: String?
    public var updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case jobId = "job_id"
        case state
        case stage
        case phaseName = "phase_name"
        case currentTier = "current_tier"
        case title
        case detail
        case progressFraction = "progress_fraction"
        case progressBasis = "progress_basis"
        case elapsedSeconds = "elapsed_seconds"
        case estimatedRemainingSeconds = "estimated_remaining_seconds"
        case failureReason = "failure_reason"
        case failureDetail = "failure_detail"
        case artifact
        case assignedWorkerId = "assigned_worker_id"
        case updatedAt = "updated_at"
    }
}
