import HeliadKit
import SwiftUI

struct ContentView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        NavigationSplitView {
            Sidebar()
                .navigationSplitViewColumnWidth(min: 220, ideal: 260)
        } detail: {
            switch model.coreState {
            case .starting:
                ProgressView("Starting the Heliad core…")
            case .failed(let why):
                CoreFailedView(message: why)
            case .ready:
                detail
            }
        }
        .dropDestination(for: URL.self) { urls, _ in
            // An export dropped on the window: show its processing record.
            guard model.coreState == .ready, let url = urls.first(where: \.isFileURL) else { return false }
            model.openRecord(url)
            return true
        }
        .sheet(item: $model.recordFile) { file in
            RecordInspector(file: file)
        }
        .alert("Heliad", isPresented: Binding(get: { model.alert != nil }, set: { if !$0 { model.alert = nil } })) {
            Button("OK", role: .cancel) { model.alert = nil }
        } message: {
            Text(model.alert ?? "")
        }
    }

    @ViewBuilder private var detail: some View {
        switch model.selection {
        case .search, .none:
            SearchView()
        case .downloads:
            DownloadsView()
        case .timeline:
            TimelineView()
        case .event(let name):
            if let event = model.events.first(where: { $0.name == name }) {
                EventPane(event: event)
                    .id(name)
            } else {
                Text("This event is no longer in the library.")
                    .foregroundStyle(.secondary)
            }
        case .dataset(let key):
            if let dataset = model.datasets.first(where: { $0.datasetKey == key }) {
                FilesView(dataset: dataset)
                    .id(key)
            } else {
                Text("This dataset is no longer in the library.")
                    .foregroundStyle(.secondary)
            }
        }
    }
}

private struct Sidebar: View {
    @EnvironmentObject var model: AppModel
    @State var pendingDelete: Dataset?

    var body: some View {
        List(selection: $model.selection) {
            Section("Archives") {
                Label("Search", systemImage: "magnifyingglass")
                    .tag(SidebarItem.search)
                Label {
                    HStack {
                        Text("Downloads")
                        Spacer()
                        if model.activeJobs > 0 {
                            Text("\(model.activeJobs)")
                                .font(.caption.monospacedDigit())
                                .padding(.horizontal, 6)
                                .background(Capsule().fill(StudioTheme.accent.opacity(0.25)))
                        }
                    }
                } icon: {
                    Image(systemName: "arrow.down.circle")
                }
                .tag(SidebarItem.downloads)
            }
            Section("Library") {
                Label("Timeline", systemImage: "chart.bar.xaxis")
                    .tag(SidebarItem.timeline)
                if model.datasets.isEmpty, model.coreState == .ready {
                    Text("Nothing kept yet")
                        .foregroundStyle(.secondary)
                }
                ForEach(model.datasets) { dataset in
                    DatasetRow(dataset: dataset)
                        .tag(SidebarItem.dataset(dataset.datasetKey))
                        .contextMenu {
                            Button("Delete Dataset…", role: .destructive) { pendingDelete = dataset }
                        }
                }
            }
            // No placeholder row here: one that appeared as the core became ready, while the list
            // was updating, made AppKit warn of a reentrant table operation (traced on the Mac,
            // 2026-10-19). The Timeline says how to make an event instead.
            Section("Events") {
                ForEach(model.events) { event in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(event.name)
                            .lineLimit(1)
                        Text("\(event.observations) · \(event.datasets) dataset\(event.datasets == 1 ? "" : "s")")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .tag(SidebarItem.event(event.name))
                    .help(event.description ?? event.name)
                }
            }
        }
        .listStyle(.sidebar)
        .safeAreaInset(edge: .bottom) { LibraryFooter() }
        .sheet(item: $pendingDelete) { dataset in
            DeleteSheet(target: AppModel.DeleteTarget(datasetKeys: [dataset.datasetKey]),
                        what: "every file of \(dataset.label)")
        }
    }
}

private struct DatasetRow: View {
    let dataset: Dataset

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(dataset.label)
                .lineLimit(1)
            Text("\(dataset.observations) · \(Format.bytes(dataset.bytes))")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .help(dataset.datasetKey)
    }
}

private struct LibraryFooter: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Divider()
            if let stats = model.stats {
                Text("\(stats.files) files · \(Format.bytes(stats.bytes))")
                    .font(.caption)
                if stats.reindexDue {
                    Label("The library needs a reindex (heliad reindex).", systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
            }
            if let root = model.libraryRoot {
                Text(root)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(root)
            }
        }
        .padding(.horizontal, 12)
        .padding(.bottom, 8)
    }
}

private struct CoreFailedView: View {
    @EnvironmentObject var model: AppModel
    let message: String

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Heliad could not reach its core", systemImage: "exclamationmark.octagon")
                .font(.title3)
            ScrollView {
                Text(message)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 260)
            if let launch = model.launch {
                Text("Command: \(launch.commandLine)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            HStack {
                Button("Try Again") { model.restartCore() }
                SettingsLink { Text("Settings…") }
            }
        }
        .padding(24)
        .frame(maxWidth: 640)
    }
}

/// Shows what a delete would remove (a dry run in the core), then removes it on confirmation.
struct DeleteSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    let target: AppModel.DeleteTarget
    let what: String
    var onDeleted: () -> Void = {}
    @State var summary: DeleteSummary?
    @State var problem: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Delete \(what)?")
                .font(.headline)
            if let summary {
                Text(describe(summary))
                Text("The files are removed from the library folder. This cannot be undone.")
                    .foregroundStyle(.secondary)
            } else if let problem {
                Text(problem).foregroundStyle(.red)
            } else {
                ProgressView().controlSize(.small)
            }
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Delete", role: .destructive) {
                    Task {
                        await model.delete(target)
                        onDeleted()
                        dismiss()
                    }
                }
                .disabled(summary == nil || summary?.files == 0)
            }
        }
        .padding(20)
        .frame(width: 420)
        .task {
            do {
                summary = try await model.previewDelete(target)
            } catch {
                problem = error.localizedDescription
            }
        }
    }

    private func describe(_ s: DeleteSummary) -> String {
        var parts = ["\(s.files) file\(s.files == 1 ? "" : "s") (\(Format.bytes(s.bytesReleased)))"]
        if s.observations > 0 { parts.append("\(s.observations) observation\(s.observations == 1 ? "" : "s")") }
        if s.datasets > 0 { parts.append("\(s.datasets) dataset\(s.datasets == 1 ? "" : "s")") }
        var text = "This removes " + parts.joined(separator: ", ") + "."
        if s.repointed > 0 {
            text += " \(s.repointed) observation\(s.repointed == 1 ? "" : "s") fall back to an older version."
        }
        return text
    }
}
