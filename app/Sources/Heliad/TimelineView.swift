import HeliadKit
import SwiftUI

/// Every dataset in the library on one UTC time axis: where frames are kept and where the
/// gaps are. Drag across the strips to select a range, then zoom to it, open its files,
/// search the archives for it, or keep it as an event.
struct TimelineView: View {
    @EnvironmentObject var model: AppModel
    @State var range: ClosedRange<Date>?       // nil: the library's first to last observation
    @State var coverage: Coverage?
    @State var extent: (start: String, end: String)?  // the library's first and last observation
    @State var problem: String?
    @State var selected: ClosedRange<Date>?
    @State var inSelection: Coverage?          // one bin per dataset, over the selection
    @State var dragFrom: Date?
    @State var hover: Date?
    @State var makingEvent = false

    static let bins = 300
    static let labelWidth: CGFloat = 220
    static let rowHeight: CGFloat = 26
    static let axisHeight: CGFloat = 24

    var body: some View {
        VStack(spacing: 0) {
            topBar
            Divider()
            content
            if selected != nil {
                Divider()
                selectionBar
            }
        }
        .navigationTitle("Timeline")
        .task(id: rangeKey) { await load() }
        .onChange(of: model.libraryGeneration) { Task { await load() } }
        .sheet(isPresented: $makingEvent) {
            if let selected, let inSelection {
                MakeEventSheet(start: IndexTime.text(selected.lowerBound), end: IndexTime.text(selected.upperBound),
                               rows: inSelection.datasets.filter { $0.frames > 0 })
            }
        }
    }

    private var rangeKey: String {
        range.map { "\($0.lowerBound.timeIntervalSince1970) \($0.upperBound.timeIntervalSince1970)" } ?? "all"
    }

    // MARK: - the parts

    private var topBar: some View {
        HStack(spacing: 10) {
            if let coverage, let start = range == nil ? extent?.start : coverage.start,
               let end = range == nil ? extent?.end : coverage.end {
                Text("\(Format.span(start, end)) UTC")
                    .font(.callout.monospacedDigit())
                    .lineLimit(1)
                    .help("\(Format.time(start)) to \(Format.time(end)) UTC")
                if let seconds = coverage.binSeconds {
                    Text("one column is \(Format.duration(seconds))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            Spacer()
            if let hover {
                Text(Format.time(IndexTime.text(hover)))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            Button("Zoom Out") { zoomOut() }
                .disabled(range == nil)
                .help("Show three times as much time around the middle")
            Button("Whole Library") { show(nil) }
                .disabled(range == nil)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    @ViewBuilder private var content: some View {
        if let coverage, let span = span(of: coverage) {
            ScrollView(.vertical) {
                VStack(spacing: 0) {
                    HStack(spacing: 0) {
                        Color.clear.frame(width: Self.labelWidth)
                        Axis(span: span)
                    }
                    .frame(height: Self.axisHeight)
                    ForEach(coverage.datasets) { row in
                        HStack(spacing: 0) {
                            rowLabel(row)
                            Strip(counts: row.counts)
                        }
                        .frame(height: Self.rowHeight)
                        Divider().opacity(0.4)
                    }
                }
                .overlay(alignment: .topLeading) { dragLayer(span) }
            }
        } else if coverage != nil {
            Text("Nothing in the library has an observation time yet.")
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let problem {
            Text(problem)
                .foregroundStyle(.red)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            ProgressView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func rowLabel(_ row: Coverage.Row) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(row.label)
                .font(.callout)
                .lineLimit(1)
            Text(row.frames == 0 ? "none here" : "\(row.frames) here")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 10)
        .frame(width: Self.labelWidth, alignment: .leading)
        .contentShape(Rectangle())
        .onTapGesture { openFiles(row) }
        .help(helpText(row))
        .contextMenu {
            Button("Show Files") { openFiles(row) }
                .disabled(row.frames == 0)
            Button("Search Archives for This Dataset") { search(like: row) }
        }
    }

    private func helpText(_ row: Coverage.Row) -> String {
        var lines = [row.label]
        if let first = row.first, let last = row.last {
            lines.append("\(Format.time(first)) to \(Format.time(last))")
        }
        if row.undated > 0 { lines.append("\(row.undated) without a readable time (not drawn)") }
        lines.append("Click to show these files")
        return lines.joined(separator: "\n")
    }

    /// Drag to select a range over the strips; a click without a drag clears it.
    private func dragLayer(_ span: ClosedRange<Date>) -> some View {
        GeometryReader { geo in
            let width = max(geo.size.width - Self.labelWidth, 1)
            ZStack(alignment: .topLeading) {
                if let selected {
                    let x0 = x(selected.lowerBound, span, width)
                    let x1 = x(selected.upperBound, span, width)
                    Rectangle()
                        .fill(Color.accentColor.opacity(0.18))
                        .overlay(Rectangle().stroke(Color.accentColor, lineWidth: 1))
                        .frame(width: max(x1 - x0, 1), height: geo.size.height)
                        .offset(x: x0)
                        .allowsHitTesting(false)
                }
                Color.clear
                    .contentShape(Rectangle())
                    .gesture(DragGesture(minimumDistance: 0)
                        .onChanged { drag in
                            let from = dragFrom ?? time(drag.startLocation.x, span, width)
                            dragFrom = from
                            let to = time(drag.location.x, span, width)
                            selected = min(from, to)...max(from, to)
                        }
                        .onEnded { drag in
                            dragFrom = nil
                            if abs(drag.translation.width) < 3 {
                                selected = nil
                                inSelection = nil
                            } else {
                                Task { await loadSelection() }
                            }
                        })
                    .onContinuousHover { phase in
                        if case .active(let point) = phase {
                            hover = time(point.x, span, width)
                        } else {
                            hover = nil
                        }
                    }
            }
            .frame(width: width, height: geo.size.height)
            .offset(x: Self.labelWidth)
        }
    }

    private var selectionBar: some View {
        HStack(spacing: 10) {
            if let selected {
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(Format.time(IndexTime.text(selected.lowerBound))) to \(Format.time(IndexTime.text(selected.upperBound))) UTC")
                        .font(.callout.monospacedDigit())
                    Text(selectionSummary)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
            Button("Zoom In") { if let selected { show(selected) } }
            Button("Search Archives") { search(like: nil) }
                .help("Fill in the search with this range")
            Button("Make Event…") { makingEvent = true }
                .disabled((inSelection?.datasets.allSatisfy { $0.frames == 0 }) ?? true)
                .help("Keep this range of these datasets as a named event with a folder of links")
            Button("Clear") {
                selected = nil
                inSelection = nil
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private var selectionSummary: String {
        guard let selected else { return "" }
        let length = Format.duration(selected.upperBound.timeIntervalSince(selected.lowerBound))
        guard let inSelection else { return length }
        let rows = inSelection.datasets.filter { $0.frames > 0 }
        let frames = rows.reduce(0) { $0 + $1.frames }
        return "\(length) · \(frames) frame\(frames == 1 ? "" : "s") in \(rows.count) dataset\(rows.count == 1 ? "" : "s")"
    }

    // MARK: - acting

    private func span(of coverage: Coverage) -> ClosedRange<Date>? {
        guard let start = IndexTime.date(coverage.start), let end = IndexTime.date(coverage.end), end > start else {
            return nil
        }
        return start...end
    }

    private func x(_ t: Date, _ span: ClosedRange<Date>, _ width: CGFloat) -> CGFloat {
        let total = span.upperBound.timeIntervalSince(span.lowerBound)
        return CGFloat(t.timeIntervalSince(span.lowerBound) / total) * width
    }

    private func time(_ x: CGFloat, _ span: ClosedRange<Date>, _ width: CGFloat) -> Date {
        let f = min(max(Double(x / width), 0), 1)
        return span.lowerBound.addingTimeInterval(f * span.upperBound.timeIntervalSince(span.lowerBound))
    }

    private func show(_ newRange: ClosedRange<Date>?) {
        selected = nil
        inSelection = nil
        range = newRange
    }

    private func zoomOut() {
        guard let range else { return }
        let length = range.upperBound.timeIntervalSince(range.lowerBound)
        let middle = range.lowerBound.addingTimeInterval(length / 2)
        show(middle.addingTimeInterval(-1.5 * length)...middle.addingTimeInterval(1.5 * length))
    }

    /// The selection, else what is on screen.
    private var focus: (start: String, end: String)? {
        if let selected { return (IndexTime.text(selected.lowerBound), IndexTime.text(selected.upperBound)) }
        guard let start = coverage?.start, let end = coverage?.end else { return nil }
        return (start, end)
    }

    private func openFiles(_ row: Coverage.Row) {
        guard let focus else { return }
        model.showFiles(row.datasetKey, start: focus.start, end: focus.end)
    }

    private func search(like row: Coverage.Row?) {
        guard let focus else { return }
        model.searchArchives(start: focus.start, end: focus.end, like: row)
    }

    private func load() async {
        do {
            var shown = range
            if shown == nil {  // the whole library, with a margin so its first and last frames are not on the edges
                let whole = try await model.coverage(start: nil, end: nil, bins: 1)
                extent = whole.start.flatMap { s in whole.end.map { (start: s, end: $0) } }
                if let start = IndexTime.date(whole.start), let end = IndexTime.date(whole.end) {
                    let margin = max(end.timeIntervalSince(start) * 0.02, 1)
                    shown = start.addingTimeInterval(-margin)...end.addingTimeInterval(margin)
                }
            }
            coverage = try await model.coverage(start: shown.map { IndexTime.text($0.lowerBound) },
                                                end: shown.map { IndexTime.text($0.upperBound) }, bins: Self.bins)
            problem = nil
            if selected != nil { await loadSelection() }
        } catch {
            problem = error.localizedDescription
        }
    }

    private func loadSelection() async {
        guard let selected else { return }
        inSelection = try? await model.coverage(start: IndexTime.text(selected.lowerBound),
                                                end: IndexTime.text(selected.upperBound), bins: 1)
    }
}

/// Tick marks and labels for a time span, on whole minutes, hours or days (UTC).
struct Axis: View {
    let span: ClosedRange<Date>

    var body: some View {
        Canvas { context, size in
            let total = span.upperBound.timeIntervalSince(span.lowerBound)
            guard total > 0 else { return }
            let count = max(Int(size.width / 110), 2)
            let step = IndexTime.step(from: span.lowerBound, to: span.upperBound, count: count)
            let crossesDays = Int(span.lowerBound.timeIntervalSince1970 / 86400)
                != Int(span.upperBound.timeIntervalSince1970 / 86400)
            for tick in IndexTime.ticks(from: span.lowerBound, to: span.upperBound, count: count) {
                let x = CGFloat(tick.timeIntervalSince(span.lowerBound) / total) * size.width
                context.fill(Path(CGRect(x: x, y: size.height - 6, width: 1, height: 6)), with: .color(.secondary))
                let label = IndexTime.label(tick, step: step, showDate: crossesDays)
                // Near an edge the label hangs inward, so it is not cut off.
                let anchor: UnitPoint = x < 40 ? .bottomLeading : (x > size.width - 40 ? .bottomTrailing : .bottom)
                context.draw(Text(label).font(.caption2.monospacedDigit()).foregroundStyle(.secondary),
                             at: CGPoint(x: x, y: size.height - 8), anchor: anchor)
            }
        }
    }
}

/// One dataset's observations per bin: darker where more frames are kept, blank where none are.
struct Strip: View {
    let counts: [Int]

    var body: some View {
        Canvas { context, size in
            context.fill(Path(CGRect(x: 0, y: 4, width: size.width, height: size.height - 8)),
                         with: .color(.secondary.opacity(0.08)))
            guard !counts.isEmpty else { return }
            let width = size.width / CGFloat(counts.count)
            let peak = log(Double(max(counts.max() ?? 1, 1)) + 1)
            for (i, n) in counts.enumerated() where n > 0 {
                let level = 0.35 + 0.65 * log(Double(n) + 1) / peak
                let rect = CGRect(x: CGFloat(i) * width, y: 4, width: max(width, 1), height: size.height - 8)
                context.fill(Path(rect), with: .color(.accentColor.opacity(level)))
            }
        }
    }
}

/// Name the selected range and choose its datasets; Heliad keeps them as an event with a
/// folder of links (views/<name> beside the library).
struct MakeEventSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    let start: String
    let end: String
    let rows: [Coverage.Row]
    @State var name = ""
    @State var note = ""
    @State var chosen: Set<String> = []
    @State var working = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Make an Event")
                .font(.headline)
            Text("\(Format.time(start)) to \(Format.time(end)) UTC")
                .font(.callout.monospacedDigit())
            TextField("Name", text: $name, prompt: Text("2025-09-21 CME"))
            TextField("Description", text: $note, prompt: Text("optional"))
            if model.events.contains(where: { $0.name == name }) {
                Text("An event with this name exists; these observations are added to it.")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
            Text("Datasets").font(.subheadline.bold())
            ScrollView {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(rows) { row in
                        Toggle(isOn: Binding(get: { chosen.contains(row.datasetKey) },
                                             set: { on in
                                                 if on { chosen.insert(row.datasetKey) } else { chosen.remove(row.datasetKey) }
                                             })) {
                            Text("\(row.label) (\(row.frames))")
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 220)
            Text("The event lists observations; the files stay where they are in the library, and its folder holds links to them.")
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Make Event") {
                    working = true
                    Task {
                        await model.makeEvent(name: name.trimmingCharacters(in: .whitespaces), description: note,
                                              start: start, end: end, datasetKeys: Array(chosen))
                        dismiss()
                    }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(working || name.trimmingCharacters(in: .whitespaces).isEmpty || chosen.isEmpty)
            }
        }
        .textFieldStyle(.roundedBorder)
        .padding(20)
        .frame(width: 440)
        .onAppear { chosen = Set(rows.map(\.datasetKey)) }
    }
}
