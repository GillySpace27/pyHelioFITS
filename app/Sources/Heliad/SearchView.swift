import HeliadKit
import SwiftUI

/// Search the archives and queue the results for download.
struct SearchView: View {
    @EnvironmentObject var model: AppModel
    @AppStorage("searchRoute") var route = "fido"
    @State var result: SearchResult?
    @State var searching = false
    @State var problem: String?
    @State var showMore = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if model.linkAskedToDownload {
                linkBanner
            }
            Form {
                Section {
                    LabeledContent("Start (UTC)") {
                        TextField("", text: $model.searchFields.start, prompt: Text("2025-09-21T16:00:00"))
                    }
                    LabeledContent("End (UTC)") {
                        TextField("", text: $model.searchFields.end, prompt: Text("2025-09-21T17:00:00"))
                    }
                    LabeledContent("Instrument") {
                        TextField("", text: $model.searchFields.instrument, prompt: Text("AIA, EUI, LASCO…"))
                    }
                    LabeledContent("Wavelength (Å)") {
                        TextField("", text: $model.searchFields.wavelength, prompt: Text("193"))
                    }
                    LabeledContent("Cadence (s)") {
                        TextField("", text: $model.searchFields.sample, prompt: Text("one file per this many seconds"))
                    }
                }
                Section {
                    Picker("Route", selection: $route) {
                        Text("Fido (VSO, SOAR and other archives)").tag("fido")
                        Text("JSOC export").tag("jsoc")
                    }
                    if route == "jsoc" {
                        LabeledContent("JSOC series") {
                            TextField("", text: $model.searchFields.jsocSeries, prompt: Text("aia.lev1_euv_12s"))
                        }
                    }
                    DisclosureGroup("More fields", isExpanded: $showMore) {
                        LabeledContent("Mission (Source)") {
                            TextField("", text: $model.searchFields.source, prompt: Text("SDO, SOHO, PUNCH…"))
                        }
                        LabeledContent("Provider") {
                            TextField("", text: $model.searchFields.provider, prompt: Text("JSOC, SDAC…"))
                        }
                        LabeledContent("Detector") {
                            TextField("", text: $model.searchFields.detector)
                        }
                        LabeledContent("Level") {
                            TextField("", text: $model.searchFields.level)
                        }
                        LabeledContent("Physical observable") {
                            TextField("", text: $model.searchFields.physobs, prompt: Text("intensity, LOS_magnetic_field…"))
                        }
                    }
                }
            }
            .formStyle(.grouped)
            .frame(maxHeight: showMore ? 520 : 380)

            HStack {
                Button("Search") { Task { await search() } }
                    .keyboardShortcut(.defaultAction)
                    .disabled(searching)
                Button("Download") { Task { await model.download(model.searchFields, route: route) } }
                    .disabled(searching)
                    .help("Queue everything this search finds; it downloads in the background")
                if searching {
                    ProgressView().controlSize(.small)
                    Text("Searching the archives…").foregroundStyle(.secondary)
                }
                Spacer()
                if let problem {
                    Text(problem).foregroundStyle(.red).lineLimit(2)
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 10)
            Divider()
            results
        }
        .navigationTitle("Search Archives")
    }

    private var linkBanner: some View {
        HStack {
            Image(systemName: "link")
            Text("Another program asked Heliad to download this search. Check it, then download.")
            Spacer()
            Button("Download") { Task { await model.download(model.searchFields, route: route) } }
            Button("Dismiss") { model.linkAskedToDownload = false }
        }
        .padding(10)
        .background(Color.accentColor.opacity(0.15))
    }

    @ViewBuilder private var results: some View {
        if let result {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text(verbatim: result.total == 1 ? "1 file found" : "\(result.total) files found")
                        .font(.headline)
                    if let rs = result.recordset {
                        Text(rs).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                    }
                    ForEach(result.providers) { provider in
                        ProviderTable(provider: provider)
                    }
                }
                .padding(20)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        } else {
            Spacer()
        }
    }

    private func search() async {
        problem = nil
        searching = true
        defer { searching = false }
        do {
            result = try await model.search(model.searchFields, route: route)
        } catch {
            problem = error.localizedDescription
        }
    }
}

/// One archive client's results: a few telling columns, the first rows.
private struct ProviderTable: View {
    let provider: SearchResult.Provider
    static let preferred = ["Start Time", "Instrument", "Wavelength", "Provider", "Source", "Level",
                            "Detector", "Physobs", "Size", "T_REC", "WAVELNTH", "QUALITY", "fileid"]
    static let shown = 200

    var columns: [String] {
        let picked = Self.preferred.filter { provider.columns.contains($0) }
        return Array((picked.isEmpty ? provider.columns : picked).prefix(6))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("\(provider.client): \(provider.count)")
                .font(.subheadline.bold())
            ScrollView(.horizontal) {
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 4) {
                    GridRow {
                        ForEach(columns, id: \.self) { Text($0).font(.caption.bold()) }
                    }
                    Divider()
                    ForEach(Array(provider.rows.prefix(Self.shown).enumerated()), id: \.offset) { _, row in
                        GridRow {
                            ForEach(columns, id: \.self) { column in
                                Text(row[column]?.display ?? "")
                                    .font(.caption.monospacedDigit())
                                    .lineLimit(1)
                            }
                        }
                    }
                }
                .textSelection(.enabled)
            }
            if provider.rows.count > Self.shown {
                Text("Showing the first \(Self.shown) of \(provider.rows.count).")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}
