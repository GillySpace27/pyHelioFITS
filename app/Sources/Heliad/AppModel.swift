import AppKit
import Foundation
import HeliadKit
import HelioFITSCore

enum SidebarItem: Hashable {
    case search
    case downloads
    case timeline
    case dataset(String)  // dataset_key
    case event(String)    // view name
}

/// A time range to open a dataset's files at, set by the Timeline before it selects the dataset.
struct FilesFilter: Equatable {
    let datasetKey: String
    let start: String
    let end: String
}

/// An exported file (or its .hfrec.json sidecar) whose processing record the inspector shows.
struct RecordFile: Identifiable, Equatable {
    let url: URL
    var id: String { url.path }
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
    @Published var events: [EventView] = []
    @Published var filesFilter: FilesFilter?
    @Published var selection: SidebarItem? = .search
    @Published var searchFields = QueryFields()
    /// Set when another program (Studio) sent a heliad://get link: the search is shown,
    /// and the download waits for a click, since it can be large.
    @Published var linkAskedToDownload = false
    @Published var alert: String?
    /// Bumped whenever files may have changed, so open file lists reload.
    @Published var libraryGeneration = 0
    /// The file whose processing record is open in the Record inspector.
    @Published var recordFile: RecordFile?

    let core = CoreClient()
    private(set) var launch: CoreClient.Launch?
    private var thumbnails: [Int: NSImage] = [:]
    private var failedThumbnails: Set<Int> = []
    /// The frame viewer's files, read by Peek's renderer, oldest dropped first so a long
    /// playback stays bounded (each holds its pages at previewSize).
    private var peeks: [Int: FITSPreviewModel] = [:]
    private var peekOrder: [Int] = []
    private var peeksInFlight: [Int: Task<FITSPreviewModel?, Never>] = [:]
    private var failedPeeks: Set<Int> = []
    static let previewSize = 1024
    static let peeksKept = 60
    static let thumbnailSize = 256
    /// Which renderer drew the last thumbnail: "peek", or "core" when Peek could not read the file.
    private(set) var lastThumbnailSource = ""
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
        peeks.removeAll()
        peekOrder.removeAll()
        failedPeeks.removeAll()
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
            events = try await core.call("view.list", as: [EventView].self)
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

    /// The file's thumbnail as HelioFITS Peek draws it in Finder; the core's greyscale one when
    /// Peek cannot read the file.
    func thumbnail(for file: LibraryFile) async -> NSImage? {
        if let image = thumbnails[file.fileId] { return image }
        if failedThumbnails.contains(file.fileId) { return nil }
        guard let root = libraryRoot else { return nil }
        let fits = file.url(libraryRoot: root).path
        if let drawn = await PeekRender.shared.thumbnail(path: fits, sha256: file.sha256, size: Self.thumbnailSize) {
            let image = NSImage(cgImage: drawn, size: .zero)
            thumbnails[file.fileId] = image
            lastThumbnailSource = "peek"
            return image
        }
        lastThumbnailSource = "core"
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

    /// The frame viewer's look at a file: Peek's model of it, with every page in its colormap,
    /// the solar WCS, colorbar, readout and filters. Asking twice while it loads waits for the
    /// one load. Nil when Peek cannot read the file.
    func peek(for file: LibraryFile) async -> FITSPreviewModel? {
        if let m = peeks[file.fileId] { return m }
        if failedPeeks.contains(file.fileId) { return nil }
        guard let root = libraryRoot else { return nil }
        let task: Task<FITSPreviewModel?, Never>
        if let running = peeksInFlight[file.fileId] {
            task = running
        } else {
            let path = file.url(libraryRoot: root).path
            task = Task { await PeekRender.shared.model(path: path, maxSide: Self.previewSize) }
            peeksInFlight[file.fileId] = task
        }
        let loaded = await task.value
        peeksInFlight[file.fileId] = nil
        if let m = peeks[file.fileId] { return m }  // the other waiter got there first
        guard let loaded else {
            failedPeeks.insert(file.fileId)
            return nil
        }
        peeks[file.fileId] = loaded
        peekOrder.append(file.fileId)
        if peekOrder.count > Self.peeksKept {
            peeks[peekOrder.removeFirst()] = nil
        }
        return loaded
    }

    /// The viewer's picture of a file as it first opens (the starting page, default stretch).
    func preview(for file: LibraryFile) async -> NSImage? {
        guard let image = await peek(for: file)?.image() else { return nil }
        return NSImage(cgImage: image, size: .zero)
    }

    func checks(for file: LibraryFile) async -> [FileCheck] {
        (try? await core.call("library.checks", ["file_id": .int(file.fileId)], as: [FileCheck].self)) ?? []
    }

    // MARK: - coverage and events

    /// Observations per bin for every dataset (or those a search names) over [start, end];
    /// nil start and end mean the library's own first to last observation.
    func coverage(start: String?, end: String?, bins: Int, instrument: String? = nil,
                  wavelength: Double? = nil) async throws -> Coverage {
        var params: [String: JSONValue] = ["bins": .int(bins)]
        if let start { params["start"] = .string(start) }
        if let end { params["end"] = .string(end) }
        if let instrument, !instrument.isEmpty { params["instrument"] = .string(instrument) }
        if let wavelength { params["wavelength"] = .double(wavelength) }
        return try await core.call("library.coverage", params, as: Coverage.self)
    }

    /// Open one dataset's files at a time range (from the Timeline).
    func showFiles(_ datasetKey: String, start: String, end: String) {
        filesFilter = FilesFilter(datasetKey: datasetKey, start: start, end: end)
        selection = .dataset(datasetKey)
    }

    /// Fill the search with a range (and a dataset's instrument and wavelength) and show it.
    func searchArchives(start: String, end: String, like row: Coverage.Row? = nil) {
        searchFields.start = IndexTime.wholeSeconds(start, up: false)
        searchFields.end = IndexTime.wholeSeconds(end, up: true)
        if let row {
            searchFields.instrument = row.instrument ?? ""
            searchFields.wavelength = row.wavelength.map { JSONValue.double($0).display } ?? ""
        }
        linkAskedToDownload = false
        selection = .search
    }

    /// Make an event: a view of these datasets' observations in [start, end], with its folder
    /// of links written beside the library (views/<name>). Adding to an existing event keeps
    /// what it already holds.
    func makeEvent(name: String, description: String, start: String, end: String, datasetKeys: [String]) async {
        do {
            var params: [String: JSONValue] = ["name": .string(name), "start": .string(start), "end": .string(end),
                                               "dataset_keys": .array(datasetKeys.map { .string($0) })]
            if !description.isEmpty { params["description"] = .string(description) }
            _ = try await core.callRaw("view.create", params)
            _ = try await core.callRaw("view.materialize", ["name": .string(name)])
            await refreshLibrary()
            selection = .event(name)
        } catch {
            report(error)
        }
    }

    /// Write an event's folder of links again from the index.
    func rebuildEvent(_ name: String) async {
        do {
            _ = try await core.callRaw("view.materialize", ["name": .string(name)])
            await refreshLibrary()
        } catch {
            report(error)
        }
    }

    func eventMembers(_ name: String) async throws -> [LibraryFile] {
        try await core.call("view.members", ["name": .string(name)], as: [LibraryFile].self)
    }

    func revealFolder(_ path: String) {
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
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

    // MARK: - the cache editor

    func datasetQuery(_ datasetKey: String) async throws -> DatasetQuery {
        try await core.call("dataset.query", ["dataset_key": .string(datasetKey)], as: DatasetQuery.self)
    }

    /// An archive search at full cadence over the dataset's range; downloads nothing.
    func cadences(_ datasetKey: String) async throws -> CadenceReport {
        try await core.call("dataset.cadences", ["dataset_key": .string(datasetKey)], as: CadenceReport.self)
    }

    func upgradePlan(_ datasetKey: String, sampleS: Double?) async throws -> UpgradePlan {
        var params: [String: JSONValue] = ["dataset_key": .string(datasetKey), "dry_run": .bool(true)]
        if let sampleS { params["sample_s"] = .double(sampleS) }
        return try await core.call("dataset.upgrade", params, as: UpgradePlan.self)
    }

    /// Queue a job that fetches only the frames the dataset lacks, then show Downloads.
    func densify(_ datasetKey: String, sampleS: Double?) async {
        var params: [String: JSONValue] = ["dataset_key": .string(datasetKey), "dry_run": .bool(false)]
        if let sampleS { params["sample_s"] = .double(sampleS) }
        do {
            _ = try await core.call("dataset.upgrade", params, as: Job.self)
            await refreshJobs()
            selection = .downloads
        } catch {
            report(error)
        }
    }

    func rejected(_ datasetKey: String) async throws -> [RejectedFrame] {
        try await core.call("library.rejected", ["dataset_key": .string(datasetKey)], as: [RejectedFrame].self)
    }

    /// Set frames aside as bad: readers (Studio's Heliad tab, views) stop seeing them, their
    /// files stay in the store, and an upgrade never fetches them again.
    func reject(_ files: [LibraryFile], reason: String) async {
        await editRejections("library.reject", files.map(\.observationId), reason: reason)
    }

    func unreject(_ frames: [RejectedFrame]) async {
        await editRejections("library.unreject", frames.map(\.observationId), reason: nil)
    }

    private func editRejections(_ method: String, _ ids: [Int], reason: String?) async {
        var params: [String: JSONValue] = ["observation_ids": .array(ids.map { .int($0) })]
        if let reason { params["reason"] = .string(reason) }
        do {
            _ = try await core.callRaw(method, params)
            await refreshLibrary()
        } catch {
            report(error)
        }
    }

    /// Ask the archive again for one frame; a newer version becomes current when it arrives.
    func refetch(_ file: LibraryFile) async {
        do {
            _ = try await core.call("library.refetch", ["observation_id": .int(file.observationId)], as: Job.self)
            await refreshJobs()
        } catch {
            report(error)
        }
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
            for id in target.fileIds {
                thumbnails[id] = nil
                peeks[id] = nil
            }
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

    // MARK: - processing records

    /// HelioFITS Studio's bundle id (release/deploy_release.sh in the Studio repository).
    static let studioBundleID = "space.gilly.hfstudio"

    /// Show the Record inspector for an exported file or a sidecar.
    func openRecord(_ url: URL) {
        recordFile = RecordFile(url: url)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// File > Open Processing Record: pick an export (PNG, EXR, movie, FITS) or a .hfrec.json.
    func chooseRecord() {
        let panel = NSOpenPanel()
        panel.title = "Open Processing Record"
        panel.message = "Choose an exported image or movie, or its .hfrec.json record."
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { openRecord(url) }
    }

    func readRecord(_ url: URL) async throws -> RecordRead {
        try await core.call("record.read", ["path": .string(url.path)], as: RecordRead.self)
    }

    func resolveRecord(_ record: JSONValue) async throws -> RecordResolution {
        try await core.call("record.resolve", ["record": record], as: RecordResolution.self)
    }

    /// Run the fetch record.resolve planned for one dataset's missing sources (dataset.upgrade or
    /// download.enqueue), then show Downloads.
    func fetchMissing(_ group: RecordResolution.MissingGroup) async {
        guard let fetch = group.fetch else { return }
        do {
            _ = try await core.callRaw(fetch.method, fetch.params)
            await refreshJobs()
            selection = .downloads
        } catch {
            report(error)
        }
    }

    /// Launch HelioFITS Studio on a layer's dataset and time span. The -dataset, -start and -end
    /// options are being added to Studio separately; a Studio without them ignores or refuses
    /// them. macOS passes arguments only when Studio is not already running.
    func openInStudio(_ launch: StudioLaunch) {
        guard let app = NSWorkspace.shared.urlForApplication(withBundleIdentifier: Self.studioBundleID) else {
            alert = "HelioFITS Studio is not installed (no application with bundle id \(Self.studioBundleID))."
            return
        }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.arguments = launch.args
        configuration.activates = true
        NSWorkspace.shared.openApplication(at: app, configuration: configuration) { [weak self] _, error in
            guard let error else { return }
            let text = "HelioFITS Studio did not open: \(error.localizedDescription)"
            Task { @MainActor in self?.alert = text }
        }
    }

    // MARK: - heliad:// links

    func open(_ url: URL) {
        if url.isFileURL {  // a file handed to the app: show its processing record
            openRecord(url)
            return
        }
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

