import AppKit
import HeliadKit
import SwiftUI

/// A larger look at the files of a dataset, one frame at a time, for checking what the library
/// keeps: step or play through them in time order, see where the cadence has gaps, read the
/// header checks, and show, copy or delete a frame. It is hidden unless asked for (View > Show
/// Frame Viewer), and it composes and exports nothing: that is HelioFITS Studio's work.
struct FrameViewer: View {
    @EnvironmentObject var model: AppModel
    let frames: [LibraryFile]          // time order
    @Binding var current: Int?         // the file id on screen
    let more: Bool                     // whether the list has more files than are loaded
    let onDelete: ([LibraryFile]) -> Void

    @State var image: NSImage?
    @State var failed = false
    @State var loading = false
    @State var playing = false
    @State var checks: [FileCheck] = []
    @AppStorage("frameViewerRate") var rate = 5.0

    private var index: Int? { frames.firstIndex { $0.fileId == current } }
    private var file: LibraryFile? { index.map { frames[$0] } }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            picture
            if let file, let index {
                HStack {
                    Text(Format.time(file.dateObs))
                        .font(.callout.monospacedDigit())
                    Spacer()
                    Text("\(index + 1) of \(frames.count)\(more ? "+" : "")")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                Text([file.name, file.version.map { "v\($0)" }, Format.bytes(file.size)]
                        .compactMap { $0 }.joined(separator: " · "))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(file.name)
            }
            FrameTimes(frames: frames, current: $current)
                .frame(height: 22)
                .help("Each mark is a frame; a gap in the marks is a gap in the cadence. Click to jump.")
            controls
            checkList
            Text("Preview: \(AppModel.previewSize) px, each frame stretched on its own, not calibrated. "
                 + (more ? "It plays the files listed; Show More adds the next page." : ""))
                .font(.caption2)
                .foregroundStyle(.secondary)
            Spacer(minLength: 0)
        }
        .padding(10)
        .task(id: current) { await show() }
        .task(id: playing) { await play() }
        .onAppear { if current == nil { current = frames.first?.fileId } }
        .onChange(of: frames) { if index == nil { current = frames.first?.fileId } }
    }

    private var picture: some View {
        ZStack {
            Color.black
            if let image {
                Image(nsImage: image)
                    .resizable()
                    .interpolation(.medium)
                    .aspectRatio(contentMode: .fit)
            } else if failed {
                Label("No preview for this file", systemImage: "photo")
                    .foregroundStyle(.secondary)
            }
            if loading && image == nil {
                ProgressView().controlSize(.small)
            }
        }
        .aspectRatio(1, contentMode: .fit)
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private var controls: some View {
        HStack {
            Button { step(-1) } label: { Image(systemName: "backward.frame") }
                .help("Previous frame")
            Button { playing.toggle() } label: { Image(systemName: playing ? "pause.fill" : "play.fill") }
                .help(playing ? "Pause" : "Play through the frames, looping")
            Button { step(1) } label: { Image(systemName: "forward.frame") }
                .help("Next frame")
            Picker("Rate", selection: $rate) {
                ForEach([1.0, 2, 5, 10, 20], id: \.self) { Text("\(Int($0)) per s").tag($0) }
            }
            .labelsHidden()
            .frame(width: 90)
            .help("Frames a second, when the previews are ready; playback waits for each one")
            Spacer()
            if let file {
                Button { model.reveal([file]) } label: { Image(systemName: "folder") }
                    .help("Show in Finder")
                Button { model.copyPaths([file]) } label: { Image(systemName: "doc.on.doc") }
                    .help("Copy the file's path")
                Button(role: .destructive) {
                    playing = false
                    onDelete([file])
                } label: { Image(systemName: "trash") }
                    .help("Delete this frame from the library")
            }
        }
        .disabled(frames.isEmpty)
    }

    @ViewBuilder private var checkList: some View {
        let shown = checks.filter { $0.severity != "info" }
        if !shown.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(shown, id: \.self) { check in
                    Label(check.message, systemImage: check.severity == "error" ? "xmark.octagon" : "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(check.severity == "error" ? Color.red : Color.orange)
                        .help(check.code)
                }
            }
        }
    }

    private func step(_ by: Int) {
        playing = false
        guard !frames.isEmpty else { return }
        let i = ((index ?? 0) + by + frames.count) % frames.count
        current = frames[i].fileId
    }

    private func show() async {
        guard let file else {
            image = nil
            checks = []
            return
        }
        loading = true
        let shown = await model.preview(for: file)
        guard file.fileId == current else { return }  // a newer frame was asked for meanwhile
        loading = false
        image = shown
        failed = shown == nil
        checks = await model.checks(for: file)
        prefetch(after: file)
    }

    /// The next few previews, one at a time, so stepping and playing find them ready.
    private func prefetch(after file: LibraryFile) {
        guard let i = frames.firstIndex(of: file), frames.count > 1 else { return }
        let upcoming = (1...min(4, frames.count - 1)).map { frames[(i + $0) % frames.count] }
        Task {
            for next in upcoming { _ = await model.preview(for: next) }
        }
    }

    /// Advance at the chosen rate, but never past a frame whose preview is not ready yet.
    private func play() async {
        while playing, !Task.isCancelled, !frames.isEmpty {
            let started = Date()
            let next = frames[((index ?? -1) + 1) % frames.count]
            _ = await model.preview(for: next)
            let wait = max(0, 1 / max(rate, 0.5) - Date().timeIntervalSince(started))
            try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
            guard playing, !Task.isCancelled else { return }
            current = next.fileId
        }
    }
}

/// One mark per frame along the frames' own time span, the current one highlighted; empty
/// stretches are gaps in the cadence. A click picks the nearest frame.
struct FrameTimes: View {
    let frames: [LibraryFile]
    @Binding var current: Int?

    private var times: [Date?] { frames.map { IndexTime.date($0.dateObs) } }

    var body: some View {
        GeometryReader { geo in
            let times = self.times
            let known = times.compactMap { $0 }
            let first = known.min() ?? Date()
            let span = max((known.max() ?? first).timeIntervalSince(first), 0.001)
            Canvas { context, size in
                context.fill(Path(CGRect(x: 0, y: 4, width: size.width, height: size.height - 8)),
                             with: .color(.secondary.opacity(0.08)))
                for (i, t) in times.enumerated() {
                    guard let t else { continue }
                    let x = CGFloat(t.timeIntervalSince(first) / span) * (size.width - 2) + 1
                    let on = frames[i].fileId == current
                    context.fill(Path(CGRect(x: x - (on ? 1 : 0.5), y: on ? 0 : 5, width: on ? 3 : 1,
                                             height: on ? size.height : size.height - 10)),
                                 with: .color(on ? .accentColor : .primary.opacity(0.5)))
                }
            }
            .contentShape(Rectangle())
            .onTapGesture { point in
                let at = first.addingTimeInterval(Double((point.x - 1) / max(geo.size.width - 2, 1)) * span)
                let nearest = times.enumerated().compactMap { i, t in t.map { (i, abs($0.timeIntervalSince(at))) } }
                    .min { $0.1 < $1.1 }
                if let nearest { current = frames[nearest.0].fileId }
            }
        }
    }
}
