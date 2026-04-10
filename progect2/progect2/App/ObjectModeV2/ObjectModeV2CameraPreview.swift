import Foundation

#if canImport(SwiftUI) && canImport(UIKit) && canImport(AVFoundation)
import SwiftUI
import UIKit
@preconcurrency import AVFoundation

@MainActor
final class ObjectModeV2PreviewBridge: ObservableObject {
    weak var previewView: PreviewView?

    func attach(_ previewView: PreviewView) {
        self.previewView = previewView
    }

    func captureSnapshotImage() -> UIImage? {
        previewView?.snapshotImage()
    }
}

struct ObjectModeV2CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession?
    let bridge: ObjectModeV2PreviewBridge

    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        view.previewLayer.videoGravity = .resizeAspectFill
        view.previewLayer.session = session
        bridge.attach(view)
        return view
    }

    func updateUIView(_ uiView: PreviewView, context: Context) {
        uiView.previewLayer.session = session
        bridge.attach(uiView)
        if let connection = uiView.previewLayer.connection, connection.isVideoOrientationSupported {
            connection.videoOrientation = .portrait
        }
    }
}

final class PreviewView: UIView {
    override class var layerClass: AnyClass {
        AVCaptureVideoPreviewLayer.self
    }

    var previewLayer: AVCaptureVideoPreviewLayer {
        layer as! AVCaptureVideoPreviewLayer
    }

    func snapshotImage() -> UIImage? {
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = UIScreen.main.scale
        let renderer = UIGraphicsImageRenderer(bounds: bounds, format: format)
        return renderer.image { _ in
            drawHierarchy(in: bounds, afterScreenUpdates: false)
        }
    }
}

#endif
