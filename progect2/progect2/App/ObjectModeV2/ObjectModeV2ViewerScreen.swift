import Foundation

#if canImport(SwiftUI) && canImport(UIKit)
import SwiftUI

struct ObjectModeV2ViewerScreen: UIViewControllerRepresentable {
    let manifestURL: URL

    func makeUIViewController(context: Context) -> ObjectModeV2ViewerViewController {
        ObjectModeV2ViewerViewController(manifestURL: manifestURL)
    }

    func updateUIViewController(_ uiViewController: ObjectModeV2ViewerViewController, context: Context) {
    }
}

#elseif canImport(SwiftUI)
import SwiftUI

struct ObjectModeV2ViewerScreen: View {
    let manifestURL: URL

    var body: some View {
        Text("Object Mode V2 viewer is unavailable on this platform.\n\(manifestURL.lastPathComponent)")
            .foregroundColor(.white.opacity(0.75))
            .multilineTextAlignment(.center)
            .padding()
            .background(Color.black.ignoresSafeArea())
    }
}

#endif
