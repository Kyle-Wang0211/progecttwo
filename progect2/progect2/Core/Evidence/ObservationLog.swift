// SPDX-License-Identifier: LicenseRef-Aether3D-Proprietary
// Copyright (c) 2024-2026 Aether3D. All rights reserved.

//
// ObservationLog.swift
// Aether3D
//
// Core Evidence — Append-only observation log with hash chain.
// Each entry is SHA-256 chained to the previous entry, providing
// tamper-evident provenance for evidence accumulation.
// All logic stays in Core layer; App layer only calls append/save.
//

import Foundation
import CryptoKit

/// Single chained observation log entry with SHA-256 hash link.
/// Renamed to avoid collision with EvidenceReplayEngine.ObservationLogEntry.
public struct ChainedLogEntry: Codable, Sendable {
    /// Monotonic sequence number
    public let sequenceNumber: Int
    /// Patch that was observed
    public let patchId: String
    /// Observation verdict (good/suspect/bad/unknown)
    public let verdict: String
    /// Ledger quality [0, 1]
    public let quality: Double
    /// Evidence value after this observation
    public let evidenceAfter: Double
    /// Timestamp (monotonic clock, milliseconds)
    public let timestampMs: Int64
    /// Frame index
    public let frameIndex: Int
    /// SHA-256 hash of this entry (hex string)
    public let entryHash: String
    /// SHA-256 hash of the previous entry (hex string, empty for first entry)
    public let previousHash: String
}

/// Append-only observation log with SHA-256 hash chain.
/// Provides tamper-evident provenance for the evidence pipeline.
/// Thread-safe: all mutations are serialized.
public final class ObservationLog: @unchecked Sendable {
    private var entries: [ChainedLogEntry] = []
    private var lastHash: String = ""
    private var sequenceCounter: Int = 0
    private let lock = NSLock()

    public init() {}

    /// Append an observation to the log with hash chain.
    /// - Parameters:
    ///   - patchId: Patch identifier
    ///   - verdict: Observation verdict string
    ///   - quality: Ledger quality [0, 1]
    ///   - evidenceAfter: Evidence value after update
    ///   - timestampMs: Monotonic timestamp in milliseconds
    ///   - frameIndex: Current frame index
    /// - Returns: The hash of this entry (for provenance tracking)
    @discardableResult
    public func append(
        patchId: String,
        verdict: String,
        quality: Double,
        evidenceAfter: Double,
        timestampMs: Int64,
        frameIndex: Int
    ) -> String {
        lock.lock()
        defer { lock.unlock() }

        let seq = sequenceCounter
        sequenceCounter += 1

        // Build the data to hash: sequence|patchId|verdict|quality|evidence|timestamp|previousHash
        let payload = "\(seq)|\(patchId)|\(verdict)|\(String(format: "%.6f", quality))|\(String(format: "%.6f", evidenceAfter))|\(timestampMs)|\(lastHash)"
        let hash = SHA256.hash(data: Data(payload.utf8))
        let hashHex = hash.compactMap { String(format: "%02x", $0) }.joined()

        let entry = ChainedLogEntry(
            sequenceNumber: seq,
            patchId: patchId,
            verdict: verdict,
            quality: quality,
            evidenceAfter: evidenceAfter,
            timestampMs: timestampMs,
            frameIndex: frameIndex,
            entryHash: hashHex,
            previousHash: lastHash
        )

        entries.append(entry)
        lastHash = hashHex
        return hashHex
    }

    /// Current entry count.
    public var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return entries.count
    }

    /// The hash of the most recent entry (chain head).
    public var headHash: String {
        lock.lock()
        defer { lock.unlock() }
        return lastHash
    }

    /// Save the complete log to disk as JSON.
    /// - Parameter url: File URL to write.
    /// - Throws: Encoding or I/O errors.
    public func saveToDisk(url: URL) throws {
        lock.lock()
        let snapshot = entries
        lock.unlock()

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let data = try encoder.encode(snapshot)
        try data.write(to: url, options: [.atomic])
    }

    /// Verify hash chain integrity.
    /// - Returns: true if all entries form a valid chain, false if tampered.
    public func verifyChain() -> Bool {
        lock.lock()
        let snapshot = entries
        lock.unlock()

        var expectedPreviousHash = ""
        for entry in snapshot {
            if entry.previousHash != expectedPreviousHash {
                return false
            }
            // Recompute hash
            let payload = "\(entry.sequenceNumber)|\(entry.patchId)|\(entry.verdict)|\(String(format: "%.6f", entry.quality))|\(String(format: "%.6f", entry.evidenceAfter))|\(entry.timestampMs)|\(entry.previousHash)"
            let hash = SHA256.hash(data: Data(payload.utf8))
            let hashHex = hash.compactMap { String(format: "%02x", $0) }.joined()
            if hashHex != entry.entryHash {
                return false
            }
            expectedPreviousHash = entry.entryHash
        }
        return true
    }

    /// Reset the log (new scan session).
    public func reset() {
        lock.lock()
        entries.removeAll()
        lastHash = ""
        sequenceCounter = 0
        lock.unlock()
    }
}
