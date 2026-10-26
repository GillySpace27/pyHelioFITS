import CacheManagerKit
import SwiftUI

/// Search the archives and queue the results for download.
struct SearchView: View {
    @EnvironmentObject var model: AppModel
    @AppStorage("searchRoute") var route = "fido"
    @State var result: SearchResult?
    @State var local: Coverage?   // the library over the same bins, for the coverage rows
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
                    LabeledContent("Wavelength") {
                        TextField("", text: $model.searchFields.wavelength, prompt: Text("193 Å or 530 nm"))
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
            .frame(minHeight: showMore ? 420 : 320, maxHeight: showMore ? 520 : 380)
            .layoutPriority(1)  // the results list gives up height first, not the fields

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
            Text("Another program asked HelioFITS Cache Manager to download this search. Check it, then download.")
            Spacer()
            Button("Download") { Task { await model.download(model.searchFields, route: route) } }
            Button("Dismiss") { model.linkAskedToDownload = false }
        }
        .padding(10)
        .background(StudioTheme.accent.opacity(0.15))
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
                    if result.providers.contains(where: { $0.coverage != nil }) {
                        CoverageCompare(providers: result.providers.filter { $0.coverage != nil }, local: local)
                    }
                    ForEach(result.providers) { provider in
                        ProviderTable(provider: provider)
                    }
                }
                .padding(20)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        } else {
            VStack(spacing: 6) {
                Text("Search shows what the archives hold for these fields.")
                Text("Download queues everything the search finds; it runs in the background.")
            }
            .font(.callout)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func search() async {
        problem = nil
        searching = true
        defer { searching = false }
        do {
            let found = try await model.search(model.searchFields, route: route)
            result = found
            local = nil
            if let bins = found.providers.compactMap(\.coverage).first {
                let fields = model.searchFields
                local = try? await model.coverage(
                    start: bins.start, end: bins.end, bins: bins.bins,
                    instrument: fields.instrument.trimmingCharacters(in: .whitespaces),
                    wavelength: Wavelength.angstrom(from: fields.wavelength))
            }
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

/// What the archives list over the search range, row by row, beside what the library already
/// holds of the matching datasets; the last row marks where the archives have data and the
/// library has none.
private struct CoverageCompare: View {
    let providers: [SearchResult.Provider]
    let local: Coverage?
    static let labelWidth: CGFloat = 160

    private var offered: [Int] {
        guard let first = providers.first?.coverage else { return [] }
        return providers.compactMap(\.coverage).reduce(into: Array(repeating: 0, count: first.bins)) { sum, bins in
            for (i, n) in bins.counts.enumerated() where i < sum.count { sum[i] += n }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Coverage").font(.subheadline.bold())
            if let bins = providers.first?.coverage, let start = IndexTime.date(bins.start),
               let end = IndexTime.date(bins.end), end > start {
                row("") { Axis(span: start...end) }
                    .frame(height: 22)
            }
            ForEach(providers) { provider in
                if let bins = provider.coverage {
                    row(provider.client) { Strip(counts: bins.counts) }
                        .help(note(bins))
                }
            }
            if let local {
                row("In the library") { Strip(counts: local.total) }
                    .help(local.datasets.map { "\($0.label): \($0.frames)" }.joined(separator: "\n"))
                row("Not kept yet") { Gaps(offered: offered, kept: local.total) }
                Text(summary(local))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: 900, alignment: .leading)
    }

    private func row<Content: View>(_ label: String, @ViewBuilder _ strip: () -> Content) -> some View {
        HStack(spacing: 0) {
            Text(label)
                .font(.caption)
                .lineLimit(1)
                .frame(width: Self.labelWidth, alignment: .leading)
            strip()
        }
        .frame(height: 20)
    }

    private func note(_ bins: TimeBins) -> String {
        var parts = ["one column is \(Format.duration(bins.binSeconds))"]
        if bins.outside > 0 { parts.append("\(bins.outside) records outside the range") }
        if bins.undated > 0 { parts.append("\(bins.undated) without a readable time") }
        return parts.joined(separator: "; ")
    }

    private func summary(_ local: Coverage) -> String {
        let kept = local.datasets.reduce(0) { $0 + $1.frames }
        let missing = zip(offered, local.total).filter { $0.0 > 0 && $0.1 == 0 }.count
        let names = local.datasets.filter { $0.frames > 0 }.map(\.label)
        var text = "The library holds \(kept) frame\(kept == 1 ? "" : "s") here"
        text += names.isEmpty ? "." : " (\(names.joined(separator: ", ")))."
        if missing > 0 {
            text += " \(missing) of \(offered.count) columns have archive records and nothing kept."
        }
        return text
    }
}

/// Orange where the archives have records and the library has none; faint where both have some.
private struct Gaps: View {
    let offered: [Int]
    let kept: [Int]

    var body: some View {
        Canvas { context, size in
            context.fill(Path(CGRect(x: 0, y: 4, width: size.width, height: size.height - 8)),
                         with: .color(.secondary.opacity(0.08)))
            guard !offered.isEmpty else { return }
            let width = size.width / CGFloat(offered.count)
            for i in offered.indices where offered[i] > 0 {
                let missing = i >= kept.count || kept[i] == 0
                let rect = CGRect(x: CGFloat(i) * width, y: 4, width: max(width, 1), height: size.height - 8)
                context.fill(Path(rect), with: .color(missing ? .orange : StudioTheme.accent.opacity(0.25)))
            }
        }
    }
}
