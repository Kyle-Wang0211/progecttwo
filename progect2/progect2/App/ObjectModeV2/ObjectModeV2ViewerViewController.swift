import Foundation

#if canImport(UIKit) && canImport(SceneKit) && canImport(ModelIO)
import UIKit
import SceneKit
import ModelIO
import SceneKit.ModelIO
import Aether3DCore

final class ObjectModeV2ViewerViewController: UIViewController {
    private let manifestURL: URL
    private var manifest: ObjectModeV2AssetManifest?
    private let sceneView = SCNView()
    private let stageControl = UISegmentedControl(items: ObjectModeV2Stage.allCases.map(\.displayName))
    private let statusLabel = UILabel()
    private let cameraNode = SCNNode()
    private let contentRoot = SCNNode()

    private var azimuth: Float = 0
    private var elevation: Float = 0.3
    private var distance: Float = 2.6
    private var target = SCNVector3Zero
    private var interactionPreset = ObjectModeV2InteractionPreset()

    init(manifestURL: URL) {
        self.manifestURL = manifestURL
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        buildSceneView()
        buildOverlay()
        loadManifestAndScene(preferredStage: nil)
    }

    private func buildSceneView() {
        sceneView.translatesAutoresizingMaskIntoConstraints = false
        sceneView.backgroundColor = .black
        sceneView.autoenablesDefaultLighting = true
        sceneView.allowsCameraControl = false
        let scene = SCNScene()
        scene.rootNode.addChildNode(cameraNode)
        scene.rootNode.addChildNode(contentRoot)
        sceneView.scene = scene
        view.addSubview(sceneView)

        NSLayoutConstraint.activate([
            sceneView.topAnchor.constraint(equalTo: view.topAnchor),
            sceneView.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            sceneView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            sceneView.trailingAnchor.constraint(equalTo: view.trailingAnchor)
        ])

        let pan = UIPanGestureRecognizer(target: self, action: #selector(handlePan(_:)))
        let pinch = UIPinchGestureRecognizer(target: self, action: #selector(handlePinch(_:)))
        let doubleTap = UITapGestureRecognizer(target: self, action: #selector(handleDoubleTap(_:)))
        doubleTap.numberOfTapsRequired = 2
        sceneView.addGestureRecognizer(pan)
        sceneView.addGestureRecognizer(pinch)
        sceneView.addGestureRecognizer(doubleTap)
    }

    private func buildOverlay() {
        stageControl.translatesAutoresizingMaskIntoConstraints = false
        stageControl.selectedSegmentIndex = 0
        stageControl.addTarget(self, action: #selector(stageChanged(_:)), for: .valueChanged)
        view.addSubview(stageControl)

        statusLabel.translatesAutoresizingMaskIntoConstraints = false
        statusLabel.textColor = .white
        statusLabel.font = .systemFont(ofSize: 13, weight: .medium)
        statusLabel.numberOfLines = 2
        view.addSubview(statusLabel)

        NSLayoutConstraint.activate([
            stageControl.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 12),
            stageControl.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 16),
            stageControl.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -16),
            statusLabel.topAnchor.constraint(equalTo: stageControl.bottomAnchor, constant: 10),
            statusLabel.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 20),
            statusLabel.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -20)
        ])
    }

    private func loadManifestAndScene(preferredStage: ObjectModeV2Stage?) {
        guard let data = try? Data(contentsOf: manifestURL) else {
            statusLabel.text = "无法读取新版资产 manifest。"
            return
        }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        guard let manifest = try? decoder.decode(ObjectModeV2AssetManifest.self, from: data) else {
            statusLabel.text = "manifest 解析失败。"
            return
        }
        self.manifest = manifest
        self.interactionPreset = manifest.interactionPreset
        self.azimuth = manifest.cameraPreset.azimuth
        self.elevation = manifest.cameraPreset.elevation
        self.distance = manifest.cameraPreset.distance
        if manifest.cameraPreset.target.count == 3 {
            self.target = SCNVector3(
                manifest.cameraPreset.target[0],
                manifest.cameraPreset.target[1],
                manifest.cameraPreset.target[2]
            )
        }

        let stage = preferredStage ?? highestAvailableStage(in: manifest)
        stageControl.selectedSegmentIndex = stageIndex(for: stage)
        updateStageAvailability(using: manifest)
        loadStage(stage, manifest: manifest)
    }

    private func highestAvailableStage(in manifest: ObjectModeV2AssetManifest) -> ObjectModeV2Stage {
        if manifest.hq != nil { return .hq }
        if manifest.defaultAsset != nil { return .defaultStage }
        return .preview
    }

    private func updateStageAvailability(using manifest: ObjectModeV2AssetManifest) {
        for stage in ObjectModeV2Stage.allCases {
            stageControl.setEnabled(manifest.asset(for: stage) != nil, forSegmentAt: stageIndex(for: stage))
        }
    }

    private func stageIndex(for stage: ObjectModeV2Stage) -> Int {
        switch stage {
        case .preview: return 0
        case .defaultStage: return 1
        case .hq: return 2
        }
    }

    private func stageForIndex(_ index: Int) -> ObjectModeV2Stage {
        switch index {
        case 1: return .defaultStage
        case 2: return .hq
        default: return .preview
        }
    }

    private func loadStage(_ stage: ObjectModeV2Stage, manifest: ObjectModeV2AssetManifest) {
        guard let asset = manifest.asset(for: stage) else { return }
        let assetURL = manifestURL.deletingLastPathComponent().appendingPathComponent(asset.relativePath)
        guard let node = loadSceneNode(from: assetURL) else {
            statusLabel.text = "\(stage.displayName) 加载失败。"
            return
        }

        contentRoot.childNodes.forEach { $0.removeFromParentNode() }
        contentRoot.addChildNode(node)
        fitCamera(to: node)
        updateCamera()
        statusLabel.text = "当前阶段：\(stage.displayName)"
    }

    private func fitCamera(to node: SCNNode) {
        let (minVec, maxVec) = node.boundingBox
        target = SCNVector3(
            (minVec.x + maxVec.x) * 0.5,
            (minVec.y + maxVec.y) * 0.5,
            (minVec.z + maxVec.z) * 0.5
        )
        let extentX = maxVec.x - minVec.x
        let extentY = maxVec.y - minVec.y
        let extentZ = maxVec.z - minVec.z
        let maxExtent = max(extentX, max(extentY, extentZ))
        distance = max(interactionPreset.minDistance, min(interactionPreset.maxDistance, maxExtent * 2.2 + 1.0))
    }

    private func updateCamera() {
        elevation = min(max(elevation, interactionPreset.minPitch), interactionPreset.maxPitch)
        distance = min(max(distance, interactionPreset.minDistance), interactionPreset.maxDistance)

        let x = target.x + distance * cos(elevation) * sin(azimuth)
        let y = target.y + distance * sin(elevation)
        let z = target.z + distance * cos(elevation) * cos(azimuth)
        cameraNode.position = SCNVector3(x, y, z)
        cameraNode.camera = cameraNode.camera ?? SCNCamera()
        cameraNode.look(at: target)
    }

    @objc private func stageChanged(_ sender: UISegmentedControl) {
        guard let manifest else { return }
        loadStage(stageForIndex(sender.selectedSegmentIndex), manifest: manifest)
    }

    @objc private func handlePan(_ gesture: UIPanGestureRecognizer) {
        let translation = gesture.translation(in: sceneView)
        azimuth -= Float(translation.x) * 0.005
        elevation -= Float(translation.y) * 0.003
        updateCamera()
        gesture.setTranslation(.zero, in: sceneView)
    }

    @objc private func handlePinch(_ gesture: UIPinchGestureRecognizer) {
        let scale = Float(gesture.scale)
        distance /= max(scale, 0.001)
        updateCamera()
        gesture.scale = 1
    }

    @objc private func handleDoubleTap(_ gesture: UITapGestureRecognizer) {
        guard let manifest else { return }
        azimuth = manifest.cameraPreset.azimuth
        elevation = manifest.cameraPreset.elevation
        distance = manifest.cameraPreset.distance
        if manifest.cameraPreset.target.count == 3 {
            target = SCNVector3(
                manifest.cameraPreset.target[0],
                manifest.cameraPreset.target[1],
                manifest.cameraPreset.target[2]
            )
        }
        updateCamera()
    }

    private func loadSceneNode(from artifactURL: URL) -> SCNNode? {
        if let loadedScene = try? SCNScene(url: artifactURL, options: nil),
           !loadedScene.rootNode.childNodes.isEmpty {
            let root = SCNNode()
            for node in loadedScene.rootNode.childNodes {
                root.addChildNode(node.clone())
            }
            return root
        }

        let asset = MDLAsset(url: artifactURL)
        asset.loadTextures()
        guard let mdlScene = try? SCNScene(mdlAsset: asset),
              !mdlScene.rootNode.childNodes.isEmpty else {
            return nil
        }
        let root = SCNNode()
        for node in mdlScene.rootNode.childNodes {
            root.addChildNode(node.clone())
        }
        return root
    }
}

#endif
