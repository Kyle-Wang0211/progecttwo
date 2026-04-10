import Foundation

#if canImport(CoreMotion)
import CoreMotion

struct ObjectModeV2GuidanceSnapshot: Sendable, Equatable {
    var acceptedFrames: Int
    var orbitCompletion: Double
    var hintText: String
    var stabilityScore: Double

    static let idle = ObjectModeV2GuidanceSnapshot(
        acceptedFrames: 0,
        orbitCompletion: 0,
        hintText: "将物体放在画面中央，开始后沿着主体缓慢绕一圈。",
        stabilityScore: 1
    )
}

@MainActor
final class ObjectModeV2GuidanceEngine {
    private let maxAcceptedFrames = 150
    var onUpdate: ((ObjectModeV2GuidanceSnapshot) -> Void)?

    private let motionManager = CMMotionManager()
    private let motionQueue: OperationQueue = {
        let queue = OperationQueue()
        queue.name = "com.aether3d.objectmodev2.guidance"
        queue.maxConcurrentOperationCount = 1
        return queue
    }()

    private var snapshot = ObjectModeV2GuidanceSnapshot.idle
    private var recordingStartedAt: Date?
    private var lastYawSample: Double?
    private var lastAcceptedYaw: Double?
    private var lastAcceptedAt: Date?
    private var accumulatedYaw: Double = 0

    func startMonitoring() {
        guard motionManager.isDeviceMotionAvailable else {
            publish(.init(
                acceptedFrames: 0,
                orbitCompletion: 0,
                hintText: "设备不支持姿态监测，请开始后围绕物体缓慢拍一圈。",
                stabilityScore: 0.7
            ))
            return
        }

        motionManager.deviceMotionUpdateInterval = 1.0 / 15.0
        motionManager.startDeviceMotionUpdates(using: .xArbitraryCorrectedZVertical, to: motionQueue) { [weak self] motion, _ in
            guard let self, let motion else { return }
            Task { @MainActor in
                self.handle(motion)
            }
        }
    }

    func stopMonitoring() {
        motionManager.stopDeviceMotionUpdates()
    }

    func beginRecording() {
        recordingStartedAt = Date()
        lastYawSample = nil
        lastAcceptedYaw = nil
        lastAcceptedAt = nil
        accumulatedYaw = 0
        publish(.init(
            acceptedFrames: 0,
            orbitCompletion: 0,
            hintText: "很好，开始缓慢绕主体移动，系统会自动挑选有效帧。",
            stabilityScore: 1
        ))
    }

    func endRecording() {
        recordingStartedAt = nil
        publish(snapshot)
    }

    private func handle(_ motion: CMDeviceMotion) {
        guard recordingStartedAt != nil else { return }

        let yaw = motion.attitude.yaw
        let rotation = magnitude(of: motion.rotationRate)
        let acceleration = magnitude(of: motion.userAcceleration)

        if let lastYawSample {
            accumulatedYaw += abs(shortestAngle(from: lastYawSample, to: yaw))
        }
        self.lastYawSample = yaw

        let stability = max(0, min(1, 1 - min(rotation / 2.5, 1) * 0.7 - min(acceleration / 1.2, 1) * 0.5))
        let orbitCompletion = min(accumulatedYaw / (2 * .pi), 1)

        let now = Date()
        let enoughTimePassed = lastAcceptedAt.map { now.timeIntervalSince($0) > 0.24 } ?? true
        let enoughNovelty = abs(shortestAngle(from: lastAcceptedYaw ?? yaw, to: yaw)) > 0.10
        let stableEnough = stability > 0.45

        var acceptedFrames = snapshot.acceptedFrames
        if acceptedFrames < maxAcceptedFrames && stableEnough && enoughTimePassed && (acceptedFrames == 0 || enoughNovelty) {
            acceptedFrames += 1
            lastAcceptedYaw = yaw
            lastAcceptedAt = now
        }

        let hintText: String
        if acceptedFrames >= maxAcceptedFrames {
            hintText = "已达到当前模式的关键帧上限，可以结束生成。"
        } else if stability < 0.28 {
            hintText = "移动太快了，放慢一点并保持稳定。"
        } else if orbitCompletion < 0.25 {
            hintText = "先补正面和侧面，沿着主体缓慢移动。"
        } else if orbitCompletion < 0.7 {
            hintText = "很好，继续补背面角度，尽量绕满一圈。"
        } else if acceptedFrames < 20 {
            hintText = "快完成一圈了，再补一些新的角度。"
        } else if acceptedFrames < 40 {
            hintText = "已经够做预览，建议继续补顶部和边缘细节。"
        } else {
            hintText = "质量已经不错，可以结束，也可以继续补更细节的角度。"
        }

        publish(.init(
            acceptedFrames: acceptedFrames,
            orbitCompletion: orbitCompletion,
            hintText: hintText,
            stabilityScore: stability
        ))
    }

    private func publish(_ snapshot: ObjectModeV2GuidanceSnapshot) {
        self.snapshot = snapshot
        onUpdate?(snapshot)
    }

    private func shortestAngle(from lhs: Double, to rhs: Double) -> Double {
        var delta = rhs - lhs
        while delta > .pi { delta -= 2 * .pi }
        while delta < -.pi { delta += 2 * .pi }
        return delta
    }

    private func magnitude(of rotationRate: CMRotationRate) -> Double {
        sqrt(rotationRate.x * rotationRate.x + rotationRate.y * rotationRate.y + rotationRate.z * rotationRate.z)
    }

    private func magnitude(of acceleration: CMAcceleration) -> Double {
        sqrt(acceleration.x * acceleration.x + acceleration.y * acceleration.y + acceleration.z * acceleration.z)
    }
}

#endif
