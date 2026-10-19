import AppKit
import HeliadKit
import SwiftUI

/// The files of one dataset, newest version of each observation, as a grid of previews.
struct FilesView: View {
    @EnvironmentObject var model: AppModel
    let dataset: Dataset

    private static let page = 200
    @State var files: [LibraryFile] = []
    @State var more = false
    @State var loading = false
    @State var start = ""
    @State var end = ""
    @State var selection: Set<Int> = []
    @State var deleting: [LibraryFile]?

    let columns = [GridItem(.adaptive(minimum: 150, maximum: 200), spacing: 12)]

    var body: some View {
        VStack(spacing: 0) {
            filterBar
            Divider()
            ScrollView {
                LazyVGrid(columns: columns, spacing: 12) {
                    ForEach(files) { file in
                        FileCell(file: file, selected: selection.contains(file.fileId))
                            .onTapGesture { select(file) }
                            .contextMenu { menu(for: file) }
                    }
                }
                .padding(12)
                if more {
                    Button("Show More") { Task { await load(reset: false) } }
                        .disabled(loading)
                        .padding(.bottom, 16)
                }
            }
            .overlay {
                if files.isEmpty && !loading {
                    Text("No files in this range.").foregroundStyle(.secondary)
                }
            }
        }
        .navigationTitle(dataset.label)
        .navigationSubtitle("\(dataset.observations) observations · \(Format.time(dataset.first)) to \(Format.time(dataset.last))")
        .toolbar {
            ToolbarItemGroup {
                Button { model.reveal(selectedFiles) } label: { Label("Show in Finder", systemImage: "folder") }
                    .disabled(selection.isEmpty)
                    .help("Show the selected files in Finder")
                Button { model.copyPaths(selectedFiles) } label: { Label("Copy Paths", systemImage: "doc.on.doc") }
                    .disabled(selection.isEmpty)
                    .help("Copy the selected files' paths")
                Button(role: .destructive) { deleting = selectedFiles } label: { Label("Delete", systemImage: "trash") }
                    .disabled(selection.isEmpty)
                    .help("Delete the selected files from the library")
            }
        }
        .sheet(isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })) {
            if let doomed = deleting {
                DeleteSheet(target: AppModel.DeleteTarget(fileIds: doomed.map(\.fileId)),
                            what: doomed.count == 1 ? doomed[0].name : "\(doomed.count) files") {
                    selection.subtract(doomed.map(\.fileId))
                }
            }
        }
        .task { await load(reset: true) }
        .onChange(of: model.libraryGeneration) { Task { await load(reset: true) } }
    }

    private var filterBar: some View {
        HStack {
            TextField("From (UTC)", text: $start, prompt: Text(Format.time(dataset.first)))
                .frame(maxWidth: 220)
            TextField("To (UTC)", text: $end, prompt: Text(Format.time(dataset.last)))
                .frame(maxWidth: 220)
            Button("Filter") { Task { await load(reset: true) } }
                .keyboardShortcut(.defaultAction)
            if !start.isEmpty || !end.isEmpty {
                Button("Clear") {
                    start = ""
                    end = ""
                    Task { await load(reset: true) }
                }
            }
            Spacer()
            Text(countText)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .textFieldStyle(.roundedBorder)
        .padding(10)
    }

    private var countText: String {
        let shown = "\(files.count)\(more ? "+" : "") shown"
        return selection.isEmpty ? shown : "\(selection.count) selected · \(shown)"
    }

    private var selectedFiles: [LibraryFile] { files.filter { selection.contains($0.fileId) } }

    @ViewBuilder private func menu(for file: LibraryFile) -> some View {
        let targets = selection.contains(file.fileId) ? selectedFiles : [file]
        Button("Show in Finder") { model.reveal(targets) }
        Button("Copy Path\(targets.count == 1 ? "" : "s")") { model.copyPaths(targets) }
        Divider()
        Button("Delete…", role: .destructive) { deleting = targets }
    }

    private func select(_ file: LibraryFile) {
        let flags = NSEvent.modifierFlags
        if flags.contains(.command) {
            if selection.contains(file.fileId) { selection.remove(file.fileId) } else { selection.insert(file.fileId) }
        } else if flags.contains(.shift), let anchor = files.firstIndex(where: { selection.contains($0.fileId) }),
                  let here = files.firstIndex(of: file) {
            let range = min(anchor, here)...max(anchor, here)
            selection.formUnion(files[range].map(\.fileId))
        } else {
            selection = [file.fileId]
        }
    }

    private func load(reset: Bool) async {
        loading = true
        defer { loading = false }
        let offset = reset ? 0 : files.count
        do {
            let page = try await model.files(dataset: dataset.datasetKey, start: iso(start), end: iso(end),
                                             offset: offset, limit: Self.page)
            if reset {
                files = page
                selection.formIntersection(Set(page.map(\.fileId)))
            } else {
                files += page
            }
            more = page.count == Self.page
        } catch {
            model.report(error)
        }
    }

    /// The filter fields accept "2025-09-21 16:00" as well as ISO 8601.
    private func iso(_ text: String) -> String? {
        let t = text.trimmingCharacters(in: .whitespaces)
        return t.isEmpty ? nil : t.replacingOccurrences(of: " ", with: "T")
    }
}

private struct FileCell: View {
    @EnvironmentObject var model: AppModel
    let file: LibraryFile
    let selected: Bool
    @State var image: NSImage?
    @State var tried = false

    var body: some View {
        VStack(spacing: 6) {
            ZStack {
                RoundedRectangle(cornerRadius: 6)
                    .fill(Color.black)
                if let image {
                    Image(nsImage: image)
                        .resizable()
                        .interpolation(.medium)
                        .aspectRatio(contentMode: .fit)
                } else if tried {
                    Image(systemName: "photo")
                        .foregroundStyle(.secondary)
                } else {
                    ProgressView().controlSize(.small)
                }
            }
            .frame(height: 140)
            .clipShape(RoundedRectangle(cornerRadius: 6))
            Text(Format.time(file.dateObs))
                .font(.caption.monospacedDigit())
                .lineLimit(1)
            Text([file.version.map { "v\($0)" }, Format.bytes(file.size)].compactMap { $0 }.joined(separator: " · "))
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(6)
        .background(RoundedRectangle(cornerRadius: 8)
            .fill(selected ? Color.accentColor.opacity(0.25) : Color.clear))
        .overlay(RoundedRectangle(cornerRadius: 8)
            .stroke(selected ? Color.accentColor : Color.clear, lineWidth: 2))
        .contentShape(Rectangle())
        .help(file.name)
        .task(id: file.fileId) {
            image = await model.thumbnail(for: file)
            tried = true
        }
    }
}
