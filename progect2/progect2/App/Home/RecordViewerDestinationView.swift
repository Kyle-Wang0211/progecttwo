import Foundation

#if canImport(SwiftUI)
import SwiftUI
import Aether3DCore

struct RecordViewerDestinationView: View {
    let record: ScanRecord
    private let store = ScanRecordStore()

    var body: some View {
        Group {
            if let artifactPath = record.artifactPath {
                switch record.pipelineKind {
                case .objectModeV2:
                    ObjectModeV2ViewerScreen(manifestURL: store.artifactURL(for: artifactPath))
                case .legacyGuided, .none:
                    LegacyArtifactViewerScreen(
                        artifact: ArtifactRef(
                            localPath: store.artifactURL(for: artifactPath),
                            format: artifactFormat(for: artifactPath)
                        )
                    )
                }
            } else {
                ZStack {
                    Color.black.ignoresSafeArea()
                    Text("该作品还没有可打开的 3D 资产。")
                        .foregroundColor(.white.opacity(0.8))
                }
            }
        }
        .navigationTitle(record.name)
        .navigationBarTitleDisplayMode(.inline)
        .background(Color.black.ignoresSafeArea())
    }

    private func artifactFormat(for path: String) -> ArtifactFormat {
        let lowercased = path.lowercased()
        return lowercased.hasSuffix(".splat") ? .splat : .splatPly
    }
}

#if canImport(UIKit)
struct LegacyArtifactViewerScreen: UIViewControllerRepresentable {
    let artifact: ArtifactRef

    func makeUIViewController(context: Context) -> WhiteboxViewerViewController {
        let controller = WhiteboxViewerViewController()
        try? controller.configure(artifact: artifact)
        return controller
    }

    func updateUIViewController(_ uiViewController: WhiteboxViewerViewController, context: Context) {
        try? uiViewController.configure(artifact: artifact)
    }
}
#else
struct LegacyArtifactViewerScreen: View {
    let artifact: ArtifactRef

    var body: some View {
        Text("Legacy viewer is unavailable on this platform.\n\(artifact.localPath.lastPathComponent)")
            .foregroundColor(.white.opacity(0.75))
            .multilineTextAlignment(.center)
            .padding()
            .background(Color.black)
    }
}
#endif

#endif
