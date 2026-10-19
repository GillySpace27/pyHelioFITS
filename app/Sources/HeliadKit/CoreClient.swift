import Foundation

/// An error the core returned for a request (JSON-RPC error object).
public struct RPCError: Error, LocalizedError, Sendable {
    public let code: Int
    public let message: String
    public var errorDescription: String? { message }
}

public enum CoreError: Error, LocalizedError, Sendable {
    case notRunning(String)
    case notFound(String)

    public var errorDescription: String? {
        switch self {
        case .notRunning(let why), .notFound(let why): return why
        }
    }
}

/// The Python core as a child process: `heliad serve`, JSON-RPC 2.0 with one JSON
/// object per line on its stdin and stdout.
///
/// Replies can arrive out of order (the core answers searches and thumbnails from a
/// pool), so each is matched to its request by id. Lines with a method and no id are
/// notifications (download.progress, download.finished, ingest.progress). Closing the
/// core's stdin asks it to finish what it was asked and exit; a download it was
/// running is queued again the next time a core starts.
public final class CoreClient: @unchecked Sendable {
    public struct Launch: Sendable, Equatable {
        public var executable: URL
        public var arguments: [String]
        public var environment: [String: String]

        public init(executable: URL, arguments: [String], environment: [String: String] = [:]) {
            self.executable = executable
            self.arguments = arguments
            self.environment = environment
        }

        /// The command line, for showing where the core came from.
        public var commandLine: String {
            ([executable.path] + arguments).joined(separator: " ")
        }
    }

    /// Called on a background queue with the method and its params as JSON data.
    public var onNotification: (@Sendable (String, Data) -> Void)?
    /// Called on a background queue when a core stops without being asked to:
    /// its exit status and the end of its stderr.
    public var onExit: (@Sendable (Int32, String) -> Void)?

    private let lock = NSLock()
    private var current: Connection?

    public init() {}

    public var isRunning: Bool { locked { current?.process.isRunning ?? false } }

    public func start(_ launch: Launch) throws {
        signal(SIGPIPE, SIG_IGN)  // a write to a core that died throws instead of killing the app
        let connection = Connection(launch: launch)
        connection.onLine = { [weak self] line in self?.dispatch(line, from: connection) }
        connection.onExit = { [weak self] status, stderr in
            guard let self else { return }
            let wasCurrent = self.locked { () -> Bool in
                guard self.current === connection else { return false }
                self.current = nil
                return true
            }
            if wasCurrent && !connection.stopping { self.onExit?(status, stderr) }
        }
        try connection.run()
        locked { current = connection }
    }

    /// Ask the core to finish and exit without waiting for it (when the app quits).
    public func stopSoon() {
        guard let connection = locked({ () -> Connection? in
            let c = current
            current = nil
            return c
        }) else { return }
        connection.stopping = true
        connection.closeInput()
    }

    /// Ask the core to finish and exit, and wait until it has; it is terminated if it
    /// has not exited after `grace` seconds.
    public func stop(grace: TimeInterval = 8) async {
        guard let connection = locked({ () -> Connection? in
            let c = current
            current = nil
            return c
        }) else { return }
        connection.stopping = true
        connection.closeInput()
        let deadline = Date().addingTimeInterval(grace)
        while connection.process.isRunning && Date() < deadline {
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        if connection.process.isRunning {
            connection.process.terminate()
            connection.process.waitUntilExit()
        }
    }

    /// Send a request and wait for its result, as raw JSON.
    public func callRaw(_ method: String, _ params: [String: JSONValue] = [:]) async throws -> Data {
        guard let connection = locked({ current }) else {
            throw CoreError.notRunning("The Heliad core is not running.")
        }
        return try await connection.send(method, params)
    }

    /// Send a request and decode its result.
    public func call<T: Decodable>(_ method: String, _ params: [String: JSONValue] = [:],
                                   as type: T.Type) async throws -> T {
        let data = try await callRaw(method, params)
        return try HeliadJSON.decoder().decode(T.self, from: data)
    }

    private func dispatch(_ line: Data, from connection: Connection) {
        guard let object = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any] else {
            return  // not a JSON-RPC message; the core writes nothing else on stdout
        }
        if let method = object["method"] as? String, object["id"] == nil {
            let params = (try? JSONSerialization.data(withJSONObject: object["params"] ?? [String: Any](),
                                                      options: [.fragmentsAllowed])) ?? Data("{}".utf8)
            onNotification?(method, params)
            return
        }
        guard let id = (object["id"] as? NSNumber)?.intValue else { return }
        if let error = object["error"] as? [String: Any] {
            connection.finish(id, .failure(RPCError(code: (error["code"] as? NSNumber)?.intValue ?? 0,
                                                    message: error["message"] as? String ?? "unknown error")))
            return
        }
        do {
            let result = try JSONSerialization.data(withJSONObject: object["result"] ?? NSNull(),
                                                    options: [.fragmentsAllowed])
            connection.finish(id, .success(result))
        } catch {
            connection.finish(id, .failure(error))
        }
    }

    private func locked<T>(_ body: () throws -> T) rethrows -> T {
        lock.lock()
        defer { lock.unlock() }
        return try body()
    }
}

/// One run of the core: its process, pipes and the requests waiting on it.
private final class Connection: @unchecked Sendable {
    let process = Process()
    var onLine: ((Data) -> Void)?
    var onExit: ((Int32, String) -> Void)?
    /// Set when the app asked it to stop, so its exit is not reported as a failure.
    var stopping = false

    private let lock = NSLock()
    private var input: FileHandle?
    private var nextID = 1
    private var pending: [Int: CheckedContinuation<Data, Error>] = [:]
    private var buffer = Data()
    private var stderrTail = Data()
    private static let stderrKeep = 8192

    init(launch: CoreClient.Launch) {
        process.executableURL = launch.executable
        process.arguments = launch.arguments
        var env = ProcessInfo.processInfo.environment
        for (key, value) in launch.environment { env[key] = value }
        process.environment = env
        // python -m puts the current folder on sys.path; start somewhere private and empty of code.
        process.currentDirectoryURL = FileManager.default.temporaryDirectory
    }

    func run() throws {
        let inPipe = Pipe(), outPipe = Pipe(), errPipe = Pipe()
        process.standardInput = inPipe
        process.standardOutput = outPipe
        process.standardError = errPipe
        outPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
            } else {
                self?.received(data)
            }
        }
        errPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
            } else {
                self?.receivedError(data)
            }
        }
        process.terminationHandler = { [weak self] proc in
            self?.exited(proc.terminationStatus)
        }
        try process.run()
        locked { input = inPipe.fileHandleForWriting }
    }

    func closeInput() {
        let handle = locked { () -> FileHandle? in
            let h = input
            input = nil
            return h
        }
        try? handle?.close()
    }

    func send(_ method: String, _ params: [String: JSONValue]) async throws -> Data {
        let (id, handle) = try locked { () throws -> (Int, FileHandle) in
            guard let handle = input else {
                throw CoreError.notRunning("The Heliad core is not running.")
            }
            let id = nextID
            nextID += 1
            return (id, handle)
        }
        var message: [String: JSONValue] = [
            "jsonrpc": .string("2.0"), "id": .int(id), "method": .string(method),
        ]
        if !params.isEmpty { message["params"] = .object(params) }
        var line = try JSONEncoder().encode(JSONValue.object(message))
        line.append(0x0A)
        return try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Data, Error>) in
            locked { pending[id] = cont }
            do {
                try handle.write(contentsOf: line)
            } catch {
                finish(id, .failure(CoreError.notRunning(
                    "The Heliad core is not running (\(error.localizedDescription)).")))
            }
        }
    }

    func finish(_ id: Int, _ result: Result<Data, Error>) {
        guard let waiting = locked({ pending.removeValue(forKey: id) }) else { return }
        waiting.resume(with: result)
    }

    private func received(_ data: Data) {
        var lines: [Data] = []
        locked {
            buffer.append(data)
            while let newline = buffer.firstIndex(of: 0x0A) {
                lines.append(Data(buffer[buffer.startIndex..<newline]))
                buffer.removeSubrange(buffer.startIndex...newline)
            }
        }
        for line in lines where !line.isEmpty {
            onLine?(line)
        }
    }

    private func receivedError(_ data: Data) {
        locked {
            stderrTail.append(data)
            if stderrTail.count > Self.stderrKeep {
                stderrTail = Data(stderrTail.suffix(Self.stderrKeep))
            }
        }
    }

    private func exited(_ status: Int32) {
        let waiting = locked { () -> [CheckedContinuation<Data, Error>] in
            let all = Array(pending.values)
            pending.removeAll()
            input = nil
            return all
        }
        for cont in waiting {
            cont.resume(throwing: CoreError.notRunning("The Heliad core stopped (exit status \(status))."))
        }
        // The last of stderr (a traceback) can arrive just after the exit is reported.
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.3) { [self] in
            let tail = locked { String(decoding: stderrTail, as: UTF8.self) }
            onExit?(status, tail)
            onLine = nil  // the handlers hold this connection; let it go
            onExit = nil
        }
    }

    private func locked<T>(_ body: () throws -> T) rethrows -> T {
        lock.lock()
        defer { lock.unlock() }
        return try body()
    }
}
