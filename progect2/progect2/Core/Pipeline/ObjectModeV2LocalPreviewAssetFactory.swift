import Foundation

enum ObjectModeV2LocalPreviewAssetFactory {
    static func generateDeterministicPLY(from inputURL: URL, stage: ObjectModeV2Stage) throws -> Data {
        let inputData = try Data(contentsOf: inputURL)
        guard !inputData.isEmpty else {
            throw RemoteB1ClientError.uploadFailed("Input file is empty")
        }

        let digest = _SHA256.hash(data: inputData + Data(stage.rawValue.utf8))
        let digestBytes = Array(digest)
        let stageScale: Int
        switch stage {
        case .preview: stageScale = 1
        case .defaultStage: stageScale = 2
        case .hq: stageScale = 3
        }
        let vertexCount = max(400, min(8000, (inputData.count / 2048 + 300) * stageScale))

        var lines: [String] = [
            "ply",
            "format ascii 1.0",
            "element vertex \(vertexCount)",
            "property float x",
            "property float y",
            "property float z",
            "property float nx",
            "property float ny",
            "property float nz",
            "property uchar red",
            "property uchar green",
            "property uchar blue",
            "end_header"
        ]

        for i in 0..<vertexCount {
            let b0 = Double(digestBytes[i % digestBytes.count])
            let b1 = Double(digestBytes[(i * 3 + 11) % digestBytes.count])
            let b2 = Double(digestBytes[(i * 7 + 19) % digestBytes.count])

            let phase = Double(i % 360) * .pi / 180.0
            let radius = 0.18 + (b0 / 255.0) * (0.45 + Double(stageScale) * 0.3)
            let x = radius * cos(phase)
            let y = radius * sin(phase)
            let z = (Double(i) / Double(max(vertexCount - 1, 1))) * 1.8 - 0.9

            let nx = x / max(radius, 1e-6)
            let ny = y / max(radius, 1e-6)
            let nz = 0.0

            lines.append(
                String(
                    format: "%.6f %.6f %.6f %.6f %.6f %.6f %d %d %d",
                    x, y, z, nx, ny, nz, Int(b0), Int(b1), Int(b2)
                )
            )
        }

        return Data((lines.joined(separator: "\n") + "\n").utf8)
    }
}
