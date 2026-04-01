import Foundation

public protocol ControlPlaneUploadCoordinatorDelegate: AnyObject {
    func uploadCoordinatorDidFinishBackgroundEvents(_ identifier: String)
}

public final class ControlPlaneUploadCoordinator: NSObject, URLSessionTaskDelegate, URLSessionDataDelegate {
    public static let shared = ControlPlaneUploadCoordinator()

    private var backgroundCompletionHandlers: [String: () -> Void] = [:]
    private var sessions: [String: URLSession] = [:]

    private override init() {}

    public func backgroundSession(identifier: String) -> URLSession {
        if let session = sessions[identifier] {
            return session
        }
        let configuration = URLSessionConfiguration.background(withIdentifier: identifier)
        configuration.isDiscretionary = false
        configuration.sessionSendsLaunchEvents = true
        configuration.waitsForConnectivity = true
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        sessions[identifier] = session
        return session
    }

    @discardableResult
    public func enqueueUpload(
        sessionIdentifier: String,
        uploadSpec: ControlPlaneUploadSpec,
        localFileURL: URL
    ) -> URLSessionUploadTask {
        var request = URLRequest(url: uploadSpec.url)
        request.httpMethod = uploadSpec.method
        for (header, value) in uploadSpec.headers {
            request.setValue(value, forHTTPHeaderField: header)
        }
        let task = backgroundSession(identifier: sessionIdentifier).uploadTask(with: request, fromFile: localFileURL)
        task.resume()
        return task
    }

    public func setCompletionHandler(_ handler: @escaping () -> Void, for identifier: String) {
        backgroundCompletionHandlers[identifier] = handler
    }

    public func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        guard let identifier = session.configuration.identifier else { return }
        let handler = backgroundCompletionHandlers.removeValue(forKey: identifier)
        handler?()
    }
}
