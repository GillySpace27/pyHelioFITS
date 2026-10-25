import AppKit
import CacheManagerKit
import SwiftUI

/// A run through the app against its real core, for checking a build without anyone at
/// the screen: `CacheManager -selfTest DIR [-selfTestQuit YES]`. It visits each screen, saves
/// a picture of the window for each into DIR, and writes DIR/report.json.
@MainActor
enum SelfTest {
    static var directory: URL? {
        guard let path = UserDefaults.standard.string(forKey: "selfTest"), !path.isEmpty else { return nil }
        return URL(fileURLWithPath: (path as NSString).expandingTildeInPath)
    }

    static func run(_ model: AppModel) async {
        guard let dir = directory else { return }
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        var report: [String: String] = [:]
        func note(_ key: String, _ value: String) {
            report[key] = value
            print("selftest \(key): \(value)")
        }

        for _ in 0..<600 where model.coreState == .starting {
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        note("core", model.launch?.commandLine ?? "none")
        switch model.coreState {
        case .ready: note("state", "ready")
        case .starting: note("state", "still starting after 60 s")
        case .failed(let why): note("state", "failed: \(why)")
        }
        if case .ready = model.coreState {
            await model.refreshLibrary()  // the sidebar's load may not have landed yet
        }
        if let hello = model.hello {
            note("hello", "hfcore \(hello.version), schema \(hello.schemaVersion), library \(hello.libraryRoot)")
        }
        if let stats = model.stats {
            note("stats", "\(stats.datasets) datasets, \(stats.files) files, \(Format.bytes(stats.bytes))")
        }
        if let first = model.datasets.first {
            note("dataset", "\(first.label) (\(first.observations) observations)")
            if let q = try? await model.datasetQuery(first.datasetKey) {
                note("upgrade", "\(q.inferred ? "inferred" : "stored") query through \(q.source)")
            } else {
                note("upgrade", "no query")
            }
            note("rejected", "\((try? await model.rejected(first.datasetKey))?.count ?? -1) set aside")
            model.selection = .dataset(first.datasetKey)
            do {
                let files = try await model.files(dataset: first.datasetKey, start: nil, end: nil, offset: 0, limit: 5)
                note("files", "\(files.count) of \(first.observations) listed, first \(files.first?.name ?? "none")")
                if let file = files.first {
                    let started = Date()
                    let image = await model.thumbnail(for: file)
                    note("thumbnail", image.map { "\(Int($0.size.width))x\(Int($0.size.height)) in \(String(format: "%.1f", Date().timeIntervalSince(started))) s" } ?? "failed")
                    note("renderer", model.lastThumbnailSource)
                }
            } catch {
                note("files", "error: \(error.localizedDescription)")
            }
            if UserDefaults.standard.bool(forKey: "showFrameViewer"), let file = try? await model.files(
                dataset: first.datasetKey, start: nil, end: nil, offset: 0, limit: 1).first {
                let started = Date()  // the viewer is asking for the same preview; this waits on that one
                let preview = await model.preview(for: file)
                note("preview", preview.map { "\(Int($0.size.width))x\(Int($0.size.height)) in \(String(format: "%.1f", Date().timeIntervalSince(started))) s" } ?? "failed")
            } else {
                note("preview", "viewer hidden")
            }
            try? await Task.sleep(nanoseconds: 4_000_000_000)  // let the grid's previews arrive
            snapshot("library.png", dir, note)
        }
        model.selection = .timeline
        do {
            let cov = try await model.coverage(start: nil, end: nil, bins: TimelineView.bins)
            let rows = cov.datasets.filter { $0.frames > 0 }.count
            note("timeline", "\(Format.time(cov.start)) to \(Format.time(cov.end)), \(rows) of \(cov.datasets.count) datasets drawn, \(cov.total.reduce(0, +)) frames")
        } catch {
            note("timeline", "error: \(error.localizedDescription)")
        }
        try? await Task.sleep(nanoseconds: 2_000_000_000)
        snapshot("timeline.png", dir, note)
        note("events", "\(model.events.count) listed" + (model.events.first.map { ", first \($0.name) (\($0.observations) observations, folder made: \($0.materialized))" } ?? ""))
        if let event = model.events.first {
            model.selection = .event(event.name)
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            snapshot("event.png", dir, note)
        }
        model.selection = .downloads
        await model.refreshJobs()
        note("jobs", "\(model.jobs.count) listed" + (model.jobs.first.map { ", newest \($0.status): \($0.title)" } ?? ""))
        try? await Task.sleep(nanoseconds: 1_000_000_000)
        snapshot("downloads.png", dir, note)

        model.open(URL(string: "hfcore://get?start=2025-09-21T16:00:00&end=2025-09-21T16:05:00&instrument=AIA&wavelength=193&sample_s=60")!)
        try? await Task.sleep(nanoseconds: 2_000_000_000)
        let f = model.searchFields
        note("link", "\(f.instrument) \(f.wavelength) \(f.start) to \(f.end), cadence \(f.sample), asks to download: \(model.linkAskedToDownload)")
        snapshot("search.png", dir, note)

        if let data = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: dir.appendingPathComponent("report.json"))
        }
        print("selftest done: \(dir.path)")
        if UserDefaults.standard.bool(forKey: "selfTestQuit") { NSApp.terminate(nil) }
    }

    /// The window as the app draws it (no screen-recording permission needed).
    private static func snapshot(_ name: String, _ dir: URL, _ note: (String, String) -> Void) {
        guard let window = NSApp.windows.first(where: { $0.isVisible && $0.isMainWindow })
                ?? NSApp.windows.first(where: { $0.isVisible }),
              let view = window.contentView?.superview ?? window.contentView,
              let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else {
            note(name, "no window to draw")
            return
        }
        view.cacheDisplay(in: view.bounds, to: rep)
        guard let png = rep.representation(using: .png, properties: [:]) else {
            note(name, "could not encode")
            return
        }
        do {
            try png.write(to: dir.appendingPathComponent(name))
            note(name, "\(rep.pixelsWide)x\(rep.pixelsHigh)")
        } catch {
            note(name, "error: \(error.localizedDescription)")
        }
    }
}
