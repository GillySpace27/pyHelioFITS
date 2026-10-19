import HeliadKit
import SwiftUI

/// One event: the observations it names, by dataset, and its folder of links. The index is
/// the event of record; the folder is a projection for Finder and scripts, rebuilt on demand.
struct EventPane: View {
    @EnvironmentObject var model: AppModel
    let event: EventView
    @State var members: [LibraryFile] = []
    @State var problem: String?

    private struct DatasetGroup: Identifiable {
        let label: String
        let files: [LibraryFile]
        var id: String { label }
    }

    private var groups: [DatasetGroup] {
        Dictionary(grouping: members) { $0.label }
            .map { DatasetGroup(label: $0.key, files: $0.value.sorted { $0.dateObs < $1.dateObs }) }
            .sorted { $0.label < $1.label }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            List {
                ForEach(groups) { group in
                    Section {
                        ForEach(group.files) { file in
                            HStack {
                                Text(Format.time(file.dateObs))
                                    .font(.callout.monospacedDigit())
                                Text(file.name)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                                Spacer()
                                Text(Format.bytes(file.size))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            .contextMenu {
                                Button("Show in Finder") { model.reveal([file]) }
                                Button("Copy Path") { model.copyPaths([file]) }
                                Button("Show in Dataset") {
                                    model.showFiles(file.datasetKey, start: file.dateObs, end: file.dateObs)
                                }
                            }
                        }
                    } header: {
                        Text("\(group.label) (\(group.files.count))")
                    }
                }
            }
            .overlay {
                if let problem {
                    Text(problem).foregroundStyle(.red)
                } else if members.isEmpty {
                    Text("This event holds no observations that are still in the library.")
                        .foregroundStyle(.secondary)
                }
            }
        }
        .navigationTitle(event.name)
        .navigationSubtitle("\(event.observations) observation\(event.observations == 1 ? "" : "s") · "
                            + "\(event.datasets) dataset\(event.datasets == 1 ? "" : "s") · \(Format.bytes(event.bytes))")
        .toolbar {
            ToolbarItemGroup {
                Button { model.revealFolder(event.path) } label: { Label("Show Folder", systemImage: "folder") }
                    .disabled(!event.materialized)
                    .help("Show the event's folder of links in Finder")
                Button { Task { await model.rebuildEvent(event.name) } } label: {
                    Label("Rebuild Folder", systemImage: "arrow.triangle.2.circlepath")
                }
                .help("Write the folder of links again from the index")
            }
        }
        .task { await load() }
        .onChange(of: model.libraryGeneration) { Task { await load() } }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let description = event.description, !description.isEmpty {
                Text(description)
            }
            if let first = event.first, let last = event.last {
                Text("\(Format.time(first)) to \(Format.time(last)) UTC")
                    .font(.callout.monospacedDigit())
            }
            Text(event.materialized ? event.path : "No folder yet: Rebuild Folder writes it to \(event.path)")
                .font(.caption)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                .lineLimit(1)
                .truncationMode(.middle)
                .help(event.path)
        }
        .padding(12)
    }

    private func load() async {
        do {
            members = try await model.eventMembers(event.name)
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }
}
