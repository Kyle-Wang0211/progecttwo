import Foundation

public enum ObjectModeV2Stage: String, Codable, CaseIterable, Sendable {
    case preview
    case defaultStage = "default"
    case hq

    public var displayName: String {
        switch self {
        case .preview: return "预览"
        case .defaultStage: return "默认成品"
        case .hq: return "高清成品"
        }
    }
}

public struct ObjectModeV2StageAsset: Codable, Sendable {
    public let stage: ObjectModeV2Stage
    public let relativePath: String
    public let format: String
    public let generatedAt: Date

    public init(stage: ObjectModeV2Stage, relativePath: String, format: String, generatedAt: Date = Date()) {
        self.stage = stage
        self.relativePath = relativePath
        self.format = format
        self.generatedAt = generatedAt
    }
}

public struct ObjectModeV2CameraPreset: Codable, Sendable {
    public var target: [Float]
    public var azimuth: Float
    public var elevation: Float
    public var distance: Float

    public init(target: [Float] = [0, 0, 0], azimuth: Float = 0, elevation: Float = 0.3, distance: Float = 2.6) {
        self.target = target
        self.azimuth = azimuth
        self.elevation = elevation
        self.distance = distance
    }
}

public struct ObjectModeV2InteractionPreset: Codable, Sendable {
    public var minDistance: Float
    public var maxDistance: Float
    public var minPitch: Float
    public var maxPitch: Float
    public var heroAutoOrbitSeconds: Double

    public init(
        minDistance: Float = 0.8,
        maxDistance: Float = 5.0,
        minPitch: Float = -0.35,
        maxPitch: Float = 1.05,
        heroAutoOrbitSeconds: Double = 2.0
    ) {
        self.minDistance = minDistance
        self.maxDistance = maxDistance
        self.minPitch = minPitch
        self.maxPitch = maxPitch
        self.heroAutoOrbitSeconds = heroAutoOrbitSeconds
    }
}

public struct ObjectModeV2AssetManifest: Codable, Sendable {
    public var schemaVersion: Int
    public var sessionId: String
    public var displayName: String
    public var createdAt: Date
    public var currentStage: ObjectModeV2Stage
    public var preview: ObjectModeV2StageAsset?
    public var defaultAsset: ObjectModeV2StageAsset?
    public var hq: ObjectModeV2StageAsset?
    public var cameraPreset: ObjectModeV2CameraPreset
    public var interactionPreset: ObjectModeV2InteractionPreset

    public init(
        schemaVersion: Int = 1,
        sessionId: String,
        displayName: String,
        createdAt: Date = Date(),
        currentStage: ObjectModeV2Stage = .preview,
        preview: ObjectModeV2StageAsset? = nil,
        defaultAsset: ObjectModeV2StageAsset? = nil,
        hq: ObjectModeV2StageAsset? = nil,
        cameraPreset: ObjectModeV2CameraPreset = ObjectModeV2CameraPreset(),
        interactionPreset: ObjectModeV2InteractionPreset = ObjectModeV2InteractionPreset()
    ) {
        self.schemaVersion = schemaVersion
        self.sessionId = sessionId
        self.displayName = displayName
        self.createdAt = createdAt
        self.currentStage = currentStage
        self.preview = preview
        self.defaultAsset = defaultAsset
        self.hq = hq
        self.cameraPreset = cameraPreset
        self.interactionPreset = interactionPreset
    }

    public func asset(for stage: ObjectModeV2Stage) -> ObjectModeV2StageAsset? {
        switch stage {
        case .preview:
            return preview
        case .defaultStage:
            return defaultAsset
        case .hq:
            return hq
        }
    }
}
