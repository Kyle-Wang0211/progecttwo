// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

import Foundation

public struct GeometryMLCaptureSignals: Codable, Sendable, Equatable {
    public let motionScore: Double
    public let overexposureRatio: Double
    public let underexposureRatio: Double
    public let hasLargeBlownRegion: Bool

    public init(
        motionScore: Double = 0,
        overexposureRatio: Double = 0,
        underexposureRatio: Double = 0,
        hasLargeBlownRegion: Bool = false
    ) {
        self.motionScore = motionScore
        self.overexposureRatio = overexposureRatio
        self.underexposureRatio = underexposureRatio
        self.hasLargeBlownRegion = hasLargeBlownRegion
    }
}

public struct GeometryMLEvidenceSignals: Codable, Sendable, Equatable {
    public let coverageScore: Double
    public let softEvidenceScore: Double
    public let persistentPizRegionCount: Int
    public let invariantViolationCount: Int
    public let replayStableRate: Double
    public let triTetBindingCoverage: Double
    public let merkleProofCoverage: Double
    public let occlusionExcludedAreaRatio: Double
    public let provenanceGapCount: Int

    public init(
        coverageScore: Double = 0,
        softEvidenceScore: Double = 0,
        persistentPizRegionCount: Int = 0,
        invariantViolationCount: Int = 0,
        replayStableRate: Double = 1.0,
        triTetBindingCoverage: Double = 1.0,
        merkleProofCoverage: Double = 1.0,
        occlusionExcludedAreaRatio: Double = 0,
        provenanceGapCount: Int = 0
    ) {
        self.coverageScore = coverageScore
        self.softEvidenceScore = softEvidenceScore
        self.persistentPizRegionCount = persistentPizRegionCount
        self.invariantViolationCount = invariantViolationCount
        self.replayStableRate = replayStableRate
        self.triTetBindingCoverage = triTetBindingCoverage
        self.merkleProofCoverage = merkleProofCoverage
        self.occlusionExcludedAreaRatio = occlusionExcludedAreaRatio
        self.provenanceGapCount = provenanceGapCount
    }
}

public struct GeometryMLTransportSignals: Codable, Sendable, Equatable {
    public let bandwidthMbps: Double
    public let rttMs: Double
    public let lossRate: Double
    public let chunkSizeBytes: Int
    public let dedupSavingsRatio: Double
    public let compressionSavingsRatio: Double
    public let byzantineCoverage: Double
    public let merkleProofSuccessRate: Double
    public let proofOfPossessionSuccessRate: Double
    public let chunkHmacMismatchRate: Double
    public let circuitBreakerOpenRatio: Double
    public let retryExhaustionRate: Double
    public let resumeCorruptionRate: Double

    public init(
        bandwidthMbps: Double = 0,
        rttMs: Double = 0,
        lossRate: Double = 0,
        chunkSizeBytes: Int = UploadConstants.CHUNK_SIZE_DEFAULT_BYTES,
        dedupSavingsRatio: Double = 0,
        compressionSavingsRatio: Double = 0,
        byzantineCoverage: Double = 1.0,
        merkleProofSuccessRate: Double = 1.0,
        proofOfPossessionSuccessRate: Double = 1.0,
        chunkHmacMismatchRate: Double = 0,
        circuitBreakerOpenRatio: Double = 0,
        retryExhaustionRate: Double = 0,
        resumeCorruptionRate: Double = 0
    ) {
        self.bandwidthMbps = bandwidthMbps
        self.rttMs = rttMs
        self.lossRate = lossRate
        self.chunkSizeBytes = chunkSizeBytes
        self.dedupSavingsRatio = dedupSavingsRatio
        self.compressionSavingsRatio = compressionSavingsRatio
        self.byzantineCoverage = byzantineCoverage
        self.merkleProofSuccessRate = merkleProofSuccessRate
        self.proofOfPossessionSuccessRate = proofOfPossessionSuccessRate
        self.chunkHmacMismatchRate = chunkHmacMismatchRate
        self.circuitBreakerOpenRatio = circuitBreakerOpenRatio
        self.retryExhaustionRate = retryExhaustionRate
        self.resumeCorruptionRate = resumeCorruptionRate
    }
}

public struct GeometryMLSecuritySignals: Codable, Sendable, Equatable {
    public let codeSignatureValid: Bool
    public let runtimeIntegrityValid: Bool
    public let telemetryHmacValid: Bool
    public let debuggerDetected: Bool
    public let environmentTampered: Bool
    public let certificatePinMismatchCount: Int
    public let bootChainValidated: Bool
    public let requestSignerValidRate: Double
    public let secureEnclaveAvailable: Bool

    public init(
        codeSignatureValid: Bool = true,
        runtimeIntegrityValid: Bool = true,
        telemetryHmacValid: Bool = true,
        debuggerDetected: Bool = false,
        environmentTampered: Bool = false,
        certificatePinMismatchCount: Int = 0,
        bootChainValidated: Bool = true,
        requestSignerValidRate: Double = 1.0,
        secureEnclaveAvailable: Bool = true
    ) {
        self.codeSignatureValid = codeSignatureValid
        self.runtimeIntegrityValid = runtimeIntegrityValid
        self.telemetryHmacValid = telemetryHmacValid
        self.debuggerDetected = debuggerDetected
        self.environmentTampered = environmentTampered
        self.certificatePinMismatchCount = certificatePinMismatchCount
        self.bootChainValidated = bootChainValidated
        self.requestSignerValidRate = requestSignerValidRate
        self.secureEnclaveAvailable = secureEnclaveAvailable
    }
}

public struct GeometryMLFusionInput: Sendable {
    public let runtimeMetrics: PureVisionRuntimeMetrics
    public let triTetReport: TriTetConsistencyReport?
    public let outlierOutcomes: [CrossValidationOutcome]
    public let calibrationOutcomes: [CrossValidationOutcome]
    public let captureSignals: GeometryMLCaptureSignals
    public let evidenceSignals: GeometryMLEvidenceSignals
    public let transportSignals: GeometryMLTransportSignals
    public let securitySignals: GeometryMLSecuritySignals

    public init(
        runtimeMetrics: PureVisionRuntimeMetrics,
        triTetReport: TriTetConsistencyReport? = nil,
        outlierOutcomes: [CrossValidationOutcome] = [],
        calibrationOutcomes: [CrossValidationOutcome] = [],
        captureSignals: GeometryMLCaptureSignals = .init(),
        evidenceSignals: GeometryMLEvidenceSignals = .init(),
        transportSignals: GeometryMLTransportSignals = .init(),
        securitySignals: GeometryMLSecuritySignals = .init()
    ) {
        self.runtimeMetrics = runtimeMetrics
        self.triTetReport = triTetReport
        self.outlierOutcomes = outlierOutcomes
        self.calibrationOutcomes = calibrationOutcomes
        self.captureSignals = captureSignals
        self.evidenceSignals = evidenceSignals
        self.transportSignals = transportSignals
        self.securitySignals = securitySignals
    }
}

public struct GeometryMLFusionComponentScores: Codable, Sendable, Equatable {
    public let geometry: Double
    public let crossValidation: Double
    public let capture: Double
    public let evidence: Double
    public let transport: Double
    public let security: Double

    public init(
        geometry: Double,
        crossValidation: Double,
        capture: Double,
        evidence: Double,
        transport: Double,
        security: Double
    ) {
        self.geometry = geometry
        self.crossValidation = crossValidation
        self.capture = capture
        self.evidence = evidence
        self.transport = transport
        self.security = security
    }
}

public struct GeometryMLCrossValidationStats: Codable, Sendable, Equatable {
    public let keepCount: Int
    public let downgradeCount: Int
    public let rejectCount: Int
    public let keepRatio: Double

    public init(
        keepCount: Int,
        downgradeCount: Int,
        rejectCount: Int,
        keepRatio: Double
    ) {
        self.keepCount = keepCount
        self.downgradeCount = downgradeCount
        self.rejectCount = rejectCount
        self.keepRatio = keepRatio
    }
}

public struct GeometryMLFusionResult: Codable, Sendable, Equatable {
    public let passes: Bool
    public let fusionScore: Double
    public let riskScore: Double
    public let securityPenalty: Double
    public let triTetMeasuredRatio: Double
    public let triTetUnknownRatio: Double
    public let crossValidationKeepRatio: Double
    public let captureExposurePenalty: Double
    public let componentScores: GeometryMLFusionComponentScores
    public let crossValidationStats: GeometryMLCrossValidationStats
    public let reasonCodes: [String]

    public var primaryReasonCode: String? {
        reasonCodes.first
    }

    public init(
        passes: Bool,
        fusionScore: Double,
        riskScore: Double,
        securityPenalty: Double,
        triTetMeasuredRatio: Double,
        triTetUnknownRatio: Double,
        crossValidationKeepRatio: Double,
        captureExposurePenalty: Double,
        componentScores: GeometryMLFusionComponentScores,
        crossValidationStats: GeometryMLCrossValidationStats,
        reasonCodes: [String]
    ) {
        self.passes = passes
        self.fusionScore = fusionScore
        self.riskScore = riskScore
        self.securityPenalty = securityPenalty
        self.triTetMeasuredRatio = triTetMeasuredRatio
        self.triTetUnknownRatio = triTetUnknownRatio
        self.crossValidationKeepRatio = crossValidationKeepRatio
        self.captureExposurePenalty = captureExposurePenalty
        self.componentScores = componentScores
        self.crossValidationStats = crossValidationStats
        self.reasonCodes = reasonCodes
    }
}

public enum GeometryMLFusionEngine {
    public static func evaluate(
        input: GeometryMLFusionInput,
        thresholds: PureVisionGeometryMLThresholds,
        weights: PureVisionGeometryMLWeights,
        uploadThresholds: PureVisionUploadCDCThresholds
    ) -> GeometryMLFusionResult {
        if let native = NativePureVisionRuntimeBridge.evaluateGeometryML(
            input: input,
            thresholds: thresholds,
            weights: weights,
            uploadThresholds: uploadThresholds
        ) {
            return native
        }
        return GeometryMLFusionResult(
            passes: false,
            fusionScore: 0.0,
            riskScore: 1.0,
            securityPenalty: 1.0,
            triTetMeasuredRatio: 0.0,
            triTetUnknownRatio: 1.0,
            crossValidationKeepRatio: 0.0,
            captureExposurePenalty: 1.0,
            componentScores: GeometryMLFusionComponentScores(
                geometry: 0.0,
                crossValidation: 0.0,
                capture: 0.0,
                evidence: 0.0,
                transport: 0.0,
                security: 0.0
            ),
            crossValidationStats: GeometryMLCrossValidationStats(
                keepCount: 0,
                downgradeCount: 0,
                rejectCount: 0,
                keepRatio: 0.0
            ),
            reasonCodes: ["FUSION_NATIVE_RUNTIME_UNAVAILABLE"]
        )
    }

}
