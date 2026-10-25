import AppKit
import CacheManagerKit
import SwiftUI

/// The files of one dataset, newest version of each observation, as a grid of previews or a
/// sortable table, with the frame viewer beside them when it is shown.
struct FilesView: View {
    @EnvironmentObject var model: AppModel
    let dataset: Dataset

    private static let page = 200
    @State var files: [LibraryFile] = []       // time order, as the core lists them
    @State var more = false
    @State var loading = false
    @State var start = ""
    @State var end = ""
    @State var selection: Set<Int> = []
    @State var deleting: [LibraryFile]?
    @State var rejecting: [LibraryFile]?
    @State var upgrading = false
    @State var sortOrder = [KeyPathComparator(\LibraryFile.dateObs)]
    @AppStorage("filesLayout") var layout = "grid"
    @AppStorage("showFrameViewer") var showViewer = false

    let columns = [GridItem(.adaptive(minimum: 150, maximum: 200), spacing: 12)]

    var body: some View {
        HSplitView {
            VStack(spacing: 0) {
                filterBar
                Divider()
                if layout == "table" { table } else { grid }
            }
            .frame(minWidth: 300)
            if showViewer {
                FrameViewer(frames: files, current: current, more: more) { deleting = $0 }
                    .frame(minWidth: 300, idealWidth: 440)
            }
        }
        .navigationTitle(dataset.label)
        .navigationSubtitle("\(dataset.observations) observations · \(Format.span(dataset.first, dataset.last)) UTC")
        .toolbar {
            ToolbarItemGroup {
                Button {
                    if let launch = StudioLaunch.forDataset(dataset, start: start, end: end) {
                        model.openInStudio(launch)
                    } else {
                        model.alert = dataset.datasetUid == nil
                            ? "This core is too old to give datasets a stable id; update HelioFITS Cache Manager."
                            : "From and To must be UTC times like 2025-09-21T16:00:00."
                    }
                } label: { Label("Open in Studio", systemImage: "sun.max") }
                    .disabled(dataset.datasetUid == nil)
                    .help("Open this dataset in HelioFITS Studio over the range shown")
                Button { upgrading = true } label: { Label("Upgrade", systemImage: "arrow.up.circle") }
                    .help("Check the archive's cadence, fetch missing frames, and review frames set aside")
                Toggle(isOn: $showViewer) { Label("Frame Viewer", systemImage: "play.rectangle") }
                    .help("Show or hide the frame viewer (View > Show Frame Viewer)")
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
        .sheet(isPresented: $upgrading, onDismiss: { Task { await load(reset: true) } }) {
            UpgradePanel(dataset: dataset)
        }
        .sheet(isPresented: Binding(get: { rejecting != nil }, set: { if !$0 { rejecting = nil } })) {
            if let frames = rejecting {
                RejectSheet(files: frames) {
                    selection.subtract(frames.map(\.fileId))
                    Task { await load(reset: true) }
                }
            }
        }
        .task {
            if let filter = model.filesFilter, filter.datasetKey == dataset.datasetKey {  // from the Timeline
                start = filter.start
                end = filter.end
                model.filesFilter = nil
            }
            await load(reset: true)
        }
        .onChange(of: model.libraryGeneration) { Task { await load(reset: true) } }
    }

    private var grid: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVGrid(columns: columns, spacing: 12) {
                    ForEach(files) { file in
                        FileCell(file: file, selected: selection.contains(file.fileId))
                            .id(file.fileId)
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
            .background(StudioTheme.component)
            .overlay { emptyNote }
            .onChange(of: selection) {  // keep the viewer's frame in sight as it plays
                if showViewer, selection.count == 1, let id = selection.first { proxy.scrollTo(id) }
            }
        }
    }

    private var table: some View {
        VStack(spacing: 0) {
            Table(files.sorted(using: sortOrder), selection: $selection, sortOrder: $sortOrder) {
                TableColumn("Observed (UTC)", value: \.dateObs) { file in
                    Text(Format.time(file.dateObs)).monospacedDigit()
                }
                TableColumn("Version", value: \.versionText)
                    .width(min: 50, ideal: 60, max: 90)
                TableColumn("Size", value: \.size) { file in
                    Text(Format.bytes(file.size)).monospacedDigit()
                }
                .width(min: 60, ideal: 80, max: 110)
                TableColumn("File", value: \.name)
            }
            .contextMenu(forSelectionType: Int.self) { ids in
                let targets = files.filter { ids.contains($0.fileId) }
                if !targets.isEmpty { menuItems(targets) }
            } primaryAction: { _ in
                showViewer = true  // a double-click opens the frame viewer on that file
            }
            .studioPanel()
            .overlay { emptyNote }
            if more {
                Button("Show More") { Task { await load(reset: false) } }
                    .disabled(loading)
                    .padding(8)
            }
        }
    }

    @ViewBuilder private var emptyNote: some View {
        if files.isEmpty && !loading {
            Text("No files in this range.").foregroundStyle(.secondary)
        }
    }

    /// One row when there is room; narrower (the frame viewer open), two rows, then four.
    private var filterBar: some View {
        ViewThatFits(in: .horizontal) {
            HStack {
                fromField
                toField
                filterButtons
                Spacer(minLength: 8)
                status
            }
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    fromField
                    toField
                    filterButtons
                }
                HStack {
                    Spacer()
                    status
                }
            }
            VStack(alignment: .leading, spacing: 6) {
                fromField
                toField
                HStack {
                    filterButtons
                    Spacer()
                    status
                }
            }
        }
        .textFieldStyle(.roundedBorder)
        .padding(10)
    }

    private var fromField: some View {
        TextField("From (UTC)", text: $start, prompt: Text(Format.time(dataset.first)))
            .frame(minWidth: 170, maxWidth: 220)
    }

    private var toField: some View {
        TextField("To (UTC)", text: $end, prompt: Text(Format.time(dataset.last)))
            .frame(minWidth: 170, maxWidth: 220)
    }

    @ViewBuilder private var filterButtons: some View {
        Button("Filter") { Task { await load(reset: true) } }
            .keyboardShortcut(.defaultAction)
        if !start.isEmpty || !end.isEmpty {
            Button("Clear") {
                start = ""
                end = ""
                Task { await load(reset: true) }
            }
        }
    }

    @ViewBuilder private var status: some View {
        Text(countText)
            .font(.caption)
            .foregroundStyle(.secondary)
            .lineLimit(1)
        Picker("Layout", selection: $layout) {
            Image(systemName: "square.grid.2x2").tag("grid")
            Image(systemName: "list.bullet").tag("table")
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .frame(width: 80)
        .help("Show the files as previews or as a table")
    }

    private var countText: String {
        let shown = "\(files.count)\(more ? "+" : "") shown"
        return selection.isEmpty ? shown : "\(selection.count) selected · \(shown)"
    }

    private var selectedFiles: [LibraryFile] { files.filter { selection.contains($0.fileId) } }

    /// The viewer shows the first selected file, and picking a frame there selects it here.
    private var current: Binding<Int?> {
        Binding(get: { files.first { selection.contains($0.fileId) }?.fileId },
                set: { id in selection = id.map { [$0] } ?? [] })
    }

    @ViewBuilder private func menu(for file: LibraryFile) -> some View {
        menuItems(selection.contains(file.fileId) ? selectedFiles : [file])
    }

    @ViewBuilder private func menuItems(_ targets: [LibraryFile]) -> some View {
        Button("Show in Finder") { model.reveal(targets) }
        Button("Copy Path\(targets.count == 1 ? "" : "s")") { model.copyPaths(targets) }
        if targets.count == 1 {
            Button("Show in Frame Viewer") {
                selection = [targets[0].fileId]
                showViewer = true
            }
        }
        Divider()
        Button("Set Aside as Bad…") { rejecting = targets }
        if targets.count == 1 {
            Button("Ask the Archive Again") { Task { await model.refetch(targets[0]) } }
        }
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
                    .fill(StudioTheme.canvas)
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
            .aspectRatio(1, contentMode: .fit)  // square, like most full-disk and coronagraph frames
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
            .fill(selected ? StudioTheme.accent.opacity(0.25) : Color.clear))
        .overlay(RoundedRectangle(cornerRadius: 8)
            .stroke(selected ? StudioTheme.accent : Color.clear, lineWidth: 2))
        .contentShape(Rectangle())
        .help(file.name)
        .task(id: file.fileId) {
            image = await model.thumbnail(for: file)
            tried = true
        }
    }
}
