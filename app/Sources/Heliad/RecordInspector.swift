import AppKit
import HeliadKit
import SwiftUI

/// The Record inspector: what made an exported image or movie, read from its processing record
/// (record.read), with each source looked up in the library (record.resolve). Summary first,
/// then the layers top to bottom with their steps in order, then citations and provenance.
/// Fetch Missing Sources runs the fetch the core planned; Open in Studio launches Studio on a
/// layer's dataset and time span. Reading a record changes nothing.
struct RecordInspector: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    let file: RecordFile

    @State var read: RecordRead?
    @State var resolution: RecordResolution?
    @State var problem: String?
    @State var loading = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Processing Record").font(.headline)
                Text(file.url.lastPathComponent)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(file.url.path)
            }
            content
            HStack {
                if let launch = read?.view?.studio {
                    Button("Open in Studio") { model.openInStudio(launch) }
                        .help("Open \(launch.dataset) in HelioFITS Studio, \(Format.span(launch.start, launch.end)) UTC")
                }
                Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([file.url]) }
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }
        }
        .padding(20)
        .frame(width: 640, height: 720)
        .task(id: model.coreState == .ready) { await load() }
        .onChange(of: model.libraryGeneration) { Task { await resolve() } }
    }

    @ViewBuilder private var content: some View {
        if let problem {
            Text(problem).foregroundStyle(.red).font(.callout)
            Spacer()
        } else if let read, !read.found {
            Text("This file carries no processing record: no sidecar beside it (\(file.url.lastPathComponent).hfrec.json), and nothing embedded that Heliad reads.")
                .foregroundStyle(.secondary)
            skippedNotes(read.skipped ?? [])
            Spacer()
        } else if let read, let view = read.view {
            Form {
                Section("Summary") { summaryRows(read, view) }
                Section("Sources") { sourceRows }
                Section("Layers, top to bottom") {
                    ForEach(view.layers, id: \.drawn) { layer in layerRow(layer) }
                }
                if !view.citations.isEmpty || !view.acknowledgements.isEmpty {
                    Section("Cite") { citeRows(view) }
                }
                Section("Provenance") { provenanceRows(read, view) }
            }
            .formStyle(.grouped)
        } else {
            HStack {
                ProgressView().controlSize(.small)
                Text(model.coreState == .ready ? "Reading the record…" : "Waiting for the Heliad core…")
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
    }

    // MARK: summary

    @ViewBuilder private func summaryRows(_ read: RecordRead, _ view: RecordView) -> some View {
        Text(view.summary)
            .font(.system(.callout, design: .monospaced))
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
        if read.legacy == true {
            Label("An older Studio export: Heliad read this from Studio's hfstudio block. Its sources carry no checksums, and the steps are Heliad's reading of the saved scene.",
                  systemImage: "clock.arrow.circlepath")
                .font(.caption)
                .foregroundStyle(StudioTheme.partial)
        }
        if read.valid == false {
            DisclosureGroup("The record does not match the hfrecord schema (\(read.errors?.count ?? 0) problem\(read.errors?.count == 1 ? "" : "s"))") {
                ForEach(read.errors ?? [], id: \.self) { Text($0).font(.caption.monospaced()).textSelection(.enabled) }
            }
            .font(.caption)
            .foregroundStyle(StudioTheme.partial)
        }
        if let check = read.outputCheck {
            switch check.matches {
            case .some(true):
                Label("The export's checksum matches the record.", systemImage: "checkmark.seal")
                    .font(.caption).foregroundStyle(StudioTheme.covers)
            case .some(false):
                Label("The export has changed since the record was written (checksum differs).", systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(.red)
            case .none:
                Text("The export the record describes is not beside it, so its checksum was not checked.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        ForEach(view.caveats, id: \.self) { caveat in
            Label(caveat, systemImage: "exclamationmark.circle").font(.caption).foregroundStyle(StudioTheme.partial)
        }
        ForEach(view.notGuaranteedToRebuild, id: \.self) { why in
            Label("Not guaranteed to rebuild: \(why).", systemImage: "hammer").font(.caption).foregroundStyle(.secondary)
        }
    }

    // MARK: sources

    @ViewBuilder private var sourceRows: some View {
        if let resolution {
            let c = resolution.counts
            HStack(spacing: 14) {
                mark(StudioTheme.covers, "\(c["in_library"] ?? 0) in the library")
                if let n = c["superseded"], n > 0 { mark(StudioTheme.partial, "\(n) superseded by a newer version") }
                if let n = c["missing"], n > 0 { mark(.red, "\(n) missing") }
                if let n = c["no_hash"], n > 0 { mark(StudioTheme.none, "\(n) without a checksum") }
            }
            .font(.callout)
            if resolution.missingByDataset.isEmpty {
                Text(resolution.complete ? "Every source with a checksum is in the library." : "")
                    .font(.caption).foregroundStyle(.secondary)
            }
            ForEach(resolution.missingByDataset) { group in missingRow(group) }
        } else if read?.record != nil {
            HStack {
                ProgressView().controlSize(.small)
                Text("Looking the sources up in the library…").foregroundStyle(.secondary)
            }
        }
    }

    private func missingRow(_ group: RecordResolution.MissingGroup) -> some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 2) {
                Text("\(group.label ?? group.dataset): \(group.count) missing")
                if let start = group.start, let end = group.end {
                    Text("\(Format.span(start, end)) UTC").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
                if let fetch = group.fetch {
                    Text(fetch.inferred ? "Inferred: \(fetch.note)." : "\(fetch.note.prefix(1).uppercased())\(fetch.note.dropFirst()).")
                        .font(.caption)
                        .foregroundStyle(fetch.inferred ? StudioTheme.partial : .secondary)
                } else if let reason = group.reason {
                    Text("Cannot fetch: \(reason).").font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
            Button("Fetch Missing Sources") {
                Task {
                    await model.fetchMissing(group)
                    dismiss()
                }
            }
            .disabled(group.fetch == nil)
        }
    }

    private func mark(_ color: Color, _ text: String) -> some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(text)
        }
    }

    /// Green when every source is kept as current, amber when some are superseded or have no
    /// checksum, red when any is missing; grey before the lookup.
    private func layerColor(_ layer: RecordLayer) -> Color {
        guard let resolved = resolution?.layer(drawn: layer.drawn) else { return StudioTheme.none }
        if resolved.missing > 0 { return .red }
        if resolved.partial > 0 { return StudioTheme.partial }
        return StudioTheme.covers
    }

    // MARK: layers and steps

    private func layerRow(_ layer: RecordLayer) -> some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 8) {
                if let key = layer.datasetKey {
                    Text(layer.datasetUid.map { "\(key) (\($0))" } ?? key)
                        .font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                }
                if let spec = layer.sourceSpec {
                    Text("Asked for as: \(spec.replacingOccurrences(of: "_", with: " "))")
                        .font(.caption).foregroundStyle(.secondary)
                }
                ForEach(layer.steps, id: \.index) { step in stepRow(step) }
                if let ack = layer.acknowledgement {
                    Text(ack).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                }
                if let launch = layer.studio {
                    Button("Open This Layer in Studio") { model.openInStudio(launch) }
                        .controlSize(.small)
                }
            }
            .padding(.vertical, 4)
        } label: {
            HStack {
                Circle().fill(layerColor(layer)).frame(width: 8, height: 8)
                VStack(alignment: .leading, spacing: 2) {
                    Text(layer.title).strikethrough(!layer.enabled)
                    Text(layerLine(layer)).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private func layerLine(_ layer: RecordLayer) -> String {
        var parts = ["\(layer.frames) frame\(layer.frames == 1 ? "" : "s")\(layer.shortened ? " (list shortened)" : "")",
                     layer.filter]
        if let lut = layer.lut { parts.append("LUT \(lut)") }
        if layer.automaticSteps > 0 { parts.append("\(layer.automaticSteps) automatic") }
        if layer.varies { parts.append("changes during the movie") }
        if !layer.enabled { parts.append("hidden") }
        return parts.joined(separator: " · ")
    }

    private func stepRow(_ step: RecordStep) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text("\(step.index).").monospacedDigit().foregroundStyle(.secondary)
                Text(stepTitle(step)).fontWeight(.medium)
                if step.automatic { badge("automatic") }
                if step.varies { badge(step.automation.isEmpty ? "varies" : "automated") }
                if !step.knownOp { badge("unknown step") }
                Spacer()
                if let stage = step.stage { Text(stage).font(.caption2).foregroundStyle(.secondary) }
            }
            if !step.params.isEmpty {
                Text(step.params.map { "\($0.name) = \($0.value)" }.joined(separator: ", "))
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
            }
            if !step.resolved.isEmpty {
                Text("Used: " + step.resolved.map { "\($0.name) = \($0.value)" }.joined(separator: ", "))
                    .font(.caption.monospaced()).foregroundStyle(.secondary)
            }
            if step.varies, !step.variesParams.isEmpty {
                Text("Changes during the movie: \(step.variesParams.joined(separator: ", "))"
                     + (step.automation.isEmpty ? "" : " (automation \(step.automation.joined(separator: ", ")))"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let reference = step.reference {
                Text("Reference: \(reference)").font(.caption).foregroundStyle(.secondary)
            }
            if let caveat = step.caveat {
                Text(caveat).font(.caption).foregroundStyle(StudioTheme.partial)
            }
            if let stamp = step.stamp {
                Text(stamp).font(.caption2.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
            }
            if let citation = step.citation {
                Text(citation).font(.caption2).foregroundStyle(.secondary).textSelection(.enabled)
            }
        }
        .padding(.leading, 16)
    }

    /// The op as words ("image filter"), with its algorithm when it names one beyond the op.
    private func stepTitle(_ step: RecordStep) -> String {
        let op = step.op.replacingOccurrences(of: "_", with: " ")
        guard let algorithm = step.algorithm, !algorithm.isEmpty else { return op }
        return "\(op): \(algorithm)"
    }

    private func badge(_ text: String) -> some View {
        Text(text)
            .font(.caption2)
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .background(Capsule().fill(StudioTheme.accent.opacity(0.25)))
    }

    // MARK: citations and provenance

    @ViewBuilder private func citeRows(_ view: RecordView) -> some View {
        ForEach(view.citations, id: \.self) { Text($0).textSelection(.enabled) }
        ForEach(view.acknowledgements, id: \.self) { ack in
            Text(ack.layer.map { "\($0): \(ack.text)" } ?? ack.text).font(.callout).textSelection(.enabled)
        }
        HStack {
            Spacer()
            Button("Copy Citations") {
                let lines = view.citations + view.acknowledgements.map(\.text)
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(lines.joined(separator: "\n"), forType: .string)
            }
        }
    }

    @ViewBuilder private func provenanceRows(_ read: RecordRead, _ view: RecordView) -> some View {
        LabeledContent("Made by") { Text(view.producer).textSelection(.enabled) }
        if let created = view.created { LabeledContent("Created") { Text(created).monospacedDigit() } }
        if let stamp = view.producerStamp {
            LabeledContent("Stamp") { Text(stamp).font(.caption.monospaced()).textSelection(.enabled) }
        }
        if view.dirty {
            Text("Built with uncommitted changes.").font(.caption).foregroundStyle(StudioTheme.partial)
        }
        ForEach(view.output + view.scene + view.provenance + view.snapshot, id: \.self) { pair in
            LabeledContent(pair.name) { Text(pair.value).textSelection(.enabled) }
        }
        if let format = view.format { LabeledContent("Format") { Text("hfrecord \(format)") } }
        if let recordId = view.recordId {
            LabeledContent("Record") { Text(recordId).font(.caption.monospaced()).textSelection(.enabled) }
        }
        if let found = read.foundIn {
            LabeledContent("Read from") {
                Text("\(found.container): \(found.path)")
                    .font(.caption).lineLimit(1).truncationMode(.middle).help(found.path)
            }
        }
        skippedNotes(read.skipped ?? [])
    }

    @ViewBuilder private func skippedNotes(_ skipped: [RecordRead.Skipped]) -> some View {
        ForEach(skipped, id: \.self) { note in
            Text("\(note.location): \(note.problem)").font(.caption).foregroundStyle(.secondary)
        }
    }

    // MARK: loading

    private func load() async {
        guard model.coreState == .ready, read == nil, !loading else { return }
        loading = true
        defer { loading = false }
        do {
            read = try await model.readRecord(file.url)
            problem = nil
        } catch {
            problem = error.localizedDescription
            return
        }
        await resolve()
    }

    private func resolve() async {
        guard let record = read?.record else { return }
        do {
            resolution = try await model.resolveRecord(record)
        } catch {
            problem = "The sources could not be looked up: \(error.localizedDescription)"
        }
    }
}
