import AppKit
import Foundation
import HeliadKit

enum SidebarItem: Hashable {
    case search
    case downloads
    case dataset(String)  // dataset_key
}

/// What the window shows, kept in step with the core.
@MainActor
final class AppModel: ObservableObject {
    enum CoreState: Equatable {
        case starting
        case ready
        case failed(String)
    }

    @Published var coreState: CoreState = .starting
    @Published var hello: Hello?
    @Published var stats: Stats?
    @Published var datasets: [Dataset] = []
    @Published var jobs: [Job] = []
    @Published var selection: SidebarItem? = .search
    @Published var searchFields = QueryFields()
    /// Set when another program (Studio) sent a heliad://get link: the search is shown,
    /// and the download waits for a click, since it can be large.
    @Published var linkAskedToDownload = false
    @Published var alert: String?
    /// Bumped whenever files may have changed, so open file lists reload.
    @Published var libraryGeneration = 0

    let core = CoreClient()
    private(set) var launch: CoreClient.Launch?
    private var thumbnails: [Int: NSImage] = [:]
    private var failedThumbnails: Set<Int> = []
    private var pendingLinks: [URL] = []

    var libraryRoot: String? { hello?.libraryRoot }
    var activeJobs: Int { jobs.filter(\.isActive).count }

    init() {
        core.onNotification = { [weak self] method, params in
            Task { @MainActor in self?.notified(method, params) }
        }
        core.onExit = { [weak self] status, stderr in
            Task { @MainActor in self?.coreExited(status, stderr) }
        }
    }

    // MARK: - the core

    func startCore() {
        coreState = .starting
        hello = nil
        let defaults = UserDefaults.standard
        do {
            let launch = try CoreLocator.launch(library: defaults.string(forKey: "libraryPath"),
                                                corePath: defaults.string(forKey: "corePath"))
            self.launch = launch
            try core.start(launch)
        } catch {
            coreState = .failed(error.localizedDescription)
            return
        }
        Task { await connect() }
    }

    func restartCore() {
        coreState = .starting
        thumbnails.removeAll()
        failedThumbnails.removeAll()
        Task {
            await core.stop(grace: 8)  // it holds the library lock until it exits
            startCore()
        }
    }

    private func connect() async {
        do {
            let hello = try await core.call("heliad.hello", as: Hello.self)
            self.hello = hello
            coreState = .ready
            await refreshAll()
            for url in pendingLinks { await openLink(url) }
            pendingLinks.removeAll()
        } catch {
            // coreExited reports a core that stopped; this is one that answered badly.
            if core.isRunning { coreState = .failed(error.localizedDescription) }
        }
    }

    private func coreExited(_ status: Int32, _ stderr: String) {
        let tail = stderr.split(separator: "\n").suffix(12).joined(separator: "\n")
        coreState = .failed("The Heliad core stopped (exit status \(status)).\n\(tail)")
    }

    private func notified(_ method: String, _ params: Data) {
        switch method {
        case "download.progress":
            Task { await refreshJobs() }
        case "download.finished":
            Task {
                await refreshJobs()
                await refreshLibrary()
            }
        default:
            break
        }
    }

    // MARK: - reading

    func refreshAll() async {
        await refreshLibrary()
        await refreshJobs()
    }

    func refreshLibrary() async {
        do {
            stats = try await core.call("library.stats", as: Stats.self)
            datasets = try await core.call("library.datasets", as: [Dataset].self)
            libraryGeneration += 1
        } catch {
            report(error)
        }
    }

    func refreshJobs() async {
        do {
            jobs = try await core.call("download.list", ["limit": .int(100)], as: [Job].self)
        } catch {
            report(error)
        }
    }

    func files(dataset: String, start: String?, end: String?, offset: Int, limit: Int) async throws -> [LibraryFile] {
        var params: [String: JSONValue] = ["dataset_key": .string(dataset), "limit": .int(limit), "offset": .int(offset)]
        if let start, !start.isEmpty { params["start"] = .string(start) }
        if let end, !end.isEmpty { params["end"] = .string(end) }
        return try await core.call("library.files", params, as: [LibraryFile].self)
    }

    /// The file's preview: from the core's cache when it is there, else rendered by the core.
    func thumbnail(for file: LibraryFile) async -> NSImage? {
        if let image = thumbnails[file.fileId] { return image }
        if failedThumbnails.contains(file.fileId) { return nil }
        guard let root = libraryRoot else { return nil }
        let cached = file.thumbnailURL(libraryRoot: root)
        var path = cached.path
        if !FileManager.default.fileExists(atPath: path) {
            struct Rendered: Decodable { let path: String }
            do {
                path = try await core.call("library.thumbnail", ["file_id": .int(file.fileId)], as: Rendered.self).path
            } catch {
                failedThumbnails.insert(file.fileId)
                return nil
            }
        }
        guard let image = NSImage(contentsOfFile: path) else {
            failedThumbnails.insert(file.fileId)
            return nil
        }
        thumbnails[file.fileId] = image
        return image
    }

    // MARK: - searching and downloading

    func search(_ fields: QueryFields, route: String) async throws -> SearchResult {
        let query = try fields.json()
        return try await core.call("search", ["query": .object(query), "source": .string(route)], as: SearchResult.self)
    }

    func download(_ fields: QueryFields, route: String) async {
        do {
            let query = try fields.json()
            _ = try await core.call("download.enqueue", ["query": .object(query), "source": .string(route)], as: Job.self)
            linkAskedToDownload = false
            await refreshJobs()
            selection = .downloads
        } catch {
            report(error)
        }
    }

    func cancel(_ job: Job) async {
        do {
            _ = try await core.callRaw("download.cancel", ["job_id": .int(job.jobId)])
            await refreshJobs()
        } catch {
            report(error)
        }
    }

    func retry(_ job: Job) async {
        do {
            _ = try await core.callRaw("download.retry", ["job_id": .int(job.jobId)])
            await refreshJobs()
        } catch {
            report(error)
        }
    }

    func repeatSearch(of job: Job) {
        searchFields = QueryFields(query: job.query)
        selection = .search
    }

    // MARK: - deleting

    struct DeleteTarget {
        var fileIds: [Int] = []
        var datasetKeys: [String] = []
        var params: [String: JSONValue] {
            ["file_ids": .array(fileIds.map { .int($0) }),
             "dataset_keys": .array(datasetKeys.map { .string($0) })]
        }
    }

    /// What a delete would remove, without removing anything.
    func previewDelete(_ target: DeleteTarget) async throws -> DeleteSummary {
        var params = target.params
        params["dry_run"] = .bool(true)
        return try await core.call("library.delete", params, as: DeleteSummary.self)
    }

    func delete(_ target: DeleteTarget) async {
        var params = target.params
        params["dry_run"] = .bool(false)
        do {
            _ = try await core.call("library.delete", params, as: DeleteSummary.self)
            for id in target.fileIds { thumbnails[id] = nil }
            if case .dataset(let key) = selection, target.datasetKeys.contains(key) { selection = .search }
            await refreshLibrary()
        } catch {
            report(error)
        }
    }

    // MARK: - files in Finder

    func reveal(_ files: [LibraryFile]) {
        guard let root = libraryRoot else { return }
        NSWorkspace.shared.activateFileViewerSelecting(files.map { $0.url(libraryRoot: root) })
    }

    func copyPaths(_ files: [LibraryFile]) {
        guard let root = libraryRoot else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(files.map { $0.url(libraryRoot: root).path }.joined(separator: "\n"),
                                       forType: .string)
    }

    // MARK: - heliad:// links

    func open(_ url: URL) {
        guard coreState == .ready else {
            pendingLinks.append(url)
            return
        }
        Task { await openLink(url) }
    }

    private func openLink(_ url: URL) async {
        do {
            let link = try await core.call("query.from_url", ["url": .string(url.absoluteString)], as: LinkRequest.self)
            searchFields = QueryFields(query: link.query)
            linkAskedToDownload = link.action == "get"
            selection = .search
            NSApp.activate(ignoringOtherApps: true)
        } catch {
            report(error)
        }
    }

    // MARK: - settings

    func settings() async -> [String: String] {
        guard let values = try? await core.call("settings.get", as: [String: JSONValue].self) else { return [:] }
        return values.mapValues(\.display)
    }

    func setSetting(_ key: String, _ value: String) async {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            _ = try await core.callRaw("settings.set", ["key": .string(key),
                                                        "value": trimmed.isEmpty ? .null : .string(trimmed)])
        } catch {
            report(error)
        }
    }

    func report(_ error: Error) {
        if case .failed = coreState { return }  // the core stopping explains everything after it
        alert = error.localizedDescription
    }
}
