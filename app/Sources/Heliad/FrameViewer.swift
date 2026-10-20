import AppKit
import HeliadKit
import HelioFITSCore
import SwiftUI

/// A larger look at the files of a dataset, one frame at a time, for checking what the library
/// keeps: step or play through them in time order, see where the cadence has gaps, read the
/// header checks, and show, copy or delete a frame. It is hidden unless asked for (View > Show
/// Frame Viewer), and it composes and exports nothing: that is HelioFITS Studio's work.
///
/// The picture is HelioFITS Peek's: its renderer and colormaps, its colorbar, solar limb,
/// pixel and helioprojective readout, and its RHEF, MGN and WOW filters (HelioFITSCore's
/// FITSPreviewModel), so a frame looks here as it does in Quick Look.
struct FrameViewer: View {
    @EnvironmentObject var model: AppModel
    let frames: [LibraryFile]          // time order
    @Binding var current: Int?         // the file id on screen
    let more: Bool                     // whether the list has more files than are loaded
    let onDelete: ([LibraryFile]) -> Void

    @State var peek: FITSPreviewModel?
    @State var picture: CGImage?
    @State var bar: Colorbar?
    @State var caption = ""
    @State var readout: String?
    @State var failed = false
    @State var loading = false
    @State var playing = false
    @State var checks: [FileCheck] = []
    @AppStorage("frameViewerRate") var rate = 5.0
    @AppStorage("frameViewerFilter") var filterRaw = 0   // FITSPreviewModel.Filter, kept across frames
    @AppStorage("frameViewerLimb") var limb = false

    private var index: Int? { frames.firstIndex { $0.fileId == current } }
    private var file: LibraryFile? { index.map { frames[$0] } }
    private var filter: FITSPreviewModel.Filter { FITSPreviewModel.Filter(rawValue: filterRaw) ?? .none }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            PeekPicture(picture: picture, caption: caption, readout: readout, model: peek,
                        failed: failed, loading: loading) { u, v in
                readout = u.flatMap { u in v.flatMap { peek?.readout(u: u, v: $0) } }
            }
            if let bar { ColorbarStrip(bar: bar) }
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
            look
            checkList
            Text("Drawn by HelioFITS Peek's renderer at \(AppModel.previewSize) px in the instrument's colormap. "
                 + "Filters are a visualization, not a calibrated radiance. "
                 + (more ? "It plays the files listed; Show More adds the next page." : ""))
                .font(.caption2)
                .foregroundStyle(.secondary)
            Spacer(minLength: 0)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .task(id: current) { await show() }
        .task(id: playing) { await play() }
        .onChange(of: filterRaw) { refresh() }
        .onChange(of: limb) { refresh() }
        .onAppear { if current == nil { current = frames.first?.fileId } }
        .onChange(of: frames) { if index == nil { current = frames.first?.fileId } }
    }

    /// Playback and the file's own actions on one row when the pane is wide enough, else two.
    private var controls: some View {
        ViewThatFits(in: .horizontal) {
            HStack {
                playback
                Spacer(minLength: 8)
                actions
            }
            VStack(alignment: .leading, spacing: 6) {
                HStack { playback }
                HStack { actions }
            }
        }
        .disabled(frames.isEmpty)
    }

    /// Peek's ways of looking: an enhancement filter, the solar limb, and the file's pages.
    private var look: some View {
        HStack(spacing: 10) {
            Picker("Filter", selection: $filterRaw) {
                ForEach(FITSPreviewModel.Filter.allCases, id: \.rawValue) { Text($0.label).tag($0.rawValue) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(maxWidth: 240)
            .help("RHEF, MGN or WOW, as in Quick Look; they run once the full-resolution pixels are read")
            Toggle("Limb", isOn: $limb)
                .toggleStyle(.button)
                .disabled(!(peek?.hasLimb ?? false))
                .help("Draw the solar limb from the file's WCS")
            if let peek, peek.count > 1 {
                Button { page(-1) } label: { Image(systemName: "chevron.left") }
                    .help("Previous page (HDU or cube plane) of this file")
                Text("page \(peek.cur + 1) of \(peek.count)")
                    .font(.caption.monospacedDigit())
                Button { page(1) } label: { Image(systemName: "chevron.right") }
                    .help("Next page (HDU or cube plane) of this file")
            }
            Spacer(minLength: 0)
        }
        .disabled(peek == nil)
    }

    @ViewBuilder private var playback: some View {
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
    }

    @ViewBuilder private var actions: some View {
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

    private func page(_ by: Int) {
        guard let peek else { return }
        peek.select(page: peek.cur + by)
        refresh()
    }

    private func show() async {
        guard let file else {
            peek = nil
            refresh()
            checks = []
            return
        }
        loading = true
        let shown = await model.peek(for: file)
        guard file.fileId == current else { return }  // a newer frame was asked for meanwhile
        loading = false
        shown?.onFullRes = { refresh() }  // the full pixels, or a filter, arrived: draw again
        peek = shown
        failed = shown == nil
        refresh()
        checks = await model.checks(for: file)
        prefetch(after: file)
    }

    /// Draw the current file with the chosen filter and limb. Peek reads the full-resolution
    /// pixels in the background (for the readout and the filters) and calls back when done.
    private func refresh() {
        guard let m = peek else {
            picture = nil
            bar = nil
            caption = ""
            readout = nil
            return
        }
        m.filter = filter
        m.limbOn = limb
        m.prefetchFullRes()
        picture = m.image()
        bar = m.colorbar()
        caption = m.caption()
    }

    /// The next few files, one at a time, so stepping and playing find them ready.
    private func prefetch(after file: LibraryFile) {
        guard let i = frames.firstIndex(of: file), frames.count > 1 else { return }
        let upcoming = (1...min(4, frames.count - 1)).map { frames[(i + $0) % frames.count] }
        Task {
            for next in upcoming { _ = await model.peek(for: next) }
        }
    }

    /// Advance at the chosen rate, but never past a frame that is not read yet.
    private func play() async {
        while playing, !Task.isCancelled, !frames.isEmpty {
            let started = Date()
            let next = frames[((index ?? -1) + 1) % frames.count]
            _ = await model.peek(for: next)
            let wait = max(0, 1 / max(rate, 0.5) - Date().timeIntervalSince(started))
            try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
            guard playing, !Task.isCancelled else { return }
            current = next.fileId
        }
    }
}

/// The frame itself on Peek's canvas grey, with its caption, the limb when asked for, and the
/// readout under the pointer, drawn in Peek's caption colours.
struct PeekPicture: View {
    let picture: CGImage?
    let caption: String
    let readout: String?
    let model: FITSPreviewModel?
    let failed: Bool
    let loading: Bool
    /// The pointer's place on the image, 0 to 1 from the top left; nil when it left the image.
    let onHover: (Double?, Double?) -> Void

    static let ink = Color(red: 0.91, green: 0.86, blue: 0.72)  // Peek's captionInk

    var body: some View {
        GeometryReader { geo in
            let box = fitted(in: geo.size)
            ZStack(alignment: .topLeading) {
                StudioTheme.canvas
                if let picture {
                    Image(decorative: picture, scale: 1)
                        .resizable()
                        .interpolation(.medium)
                        .frame(width: box.width, height: box.height)
                        .offset(x: box.minX, y: box.minY)
                    limbCircle(in: box)
                } else if failed {
                    Label("Peek cannot read this file", systemImage: "photo")
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                if loading && picture == nil {
                    ProgressView().controlSize(.small)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                if !caption.isEmpty {
                    label(caption).padding(6)
                }
                if let readout {
                    label(readout)
                        .padding(6)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
                }
            }
            .contentShape(Rectangle())
            .onContinuousHover { phase in
                switch phase {
                case .active(let at) where box.contains(at) && box.width > 0:
                    onHover(Double((at.x - box.minX) / box.width), Double((at.y - box.minY) / box.height))
                default:
                    onHover(nil, nil)
                }
            }
        }
        .aspectRatio(1, contentMode: .fit)
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private func label(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 11, design: .monospaced))
            .foregroundStyle(Self.ink)
            .padding(.horizontal, 5)
            .padding(.vertical, 3)
            .background(Color.black.opacity(0.6), in: RoundedRectangle(cornerRadius: 3))
            .lineLimit(3)
    }

    /// Where the image sits in the square, aspect kept.
    private func fitted(in size: CGSize) -> CGRect {
        guard let picture, picture.width > 0, picture.height > 0 else { return .zero }
        let s = min(size.width / CGFloat(picture.width), size.height / CGFloat(picture.height))
        let w = CGFloat(picture.width) * s, h = CGFloat(picture.height) * s
        return CGRect(x: (size.width - w) / 2, y: (size.height - h) / 2, width: w, height: h)
    }

    /// The limb as Peek draws it: a thick dark line under a dashed white one, so it reads over
    /// the corona and the sky alike. Its centre is in native pixels from the top left.
    @ViewBuilder private func limbCircle(in box: CGRect) -> some View {
        if let model, let l = model.limbCircle(), let p = model.page, p.res.natW > 0 {
            let s = box.width / CGFloat(p.res.natW)
            let r = CGFloat(l.r) * s
            let circle = Path(ellipseIn: CGRect(x: box.minX + CGFloat(l.cx) * s - r,
                                                y: box.minY + CGFloat(l.cy) * s - r,
                                                width: 2 * r, height: 2 * r))
            ZStack {
                circle.stroke(Color.black.opacity(0.7), lineWidth: 3.5)
                circle.stroke(Color.white, style: StrokeStyle(lineWidth: 1.2, dash: [6, 5]))
            }
            .allowsHitTesting(false)
        }
    }
}

/// Peek's colorbar laid along the bottom: the page's colormap from dark to bright, the values
/// at its ticks, and what the numbers are (BUNIT, or a filter's rank).
struct ColorbarStrip: View {
    let bar: Colorbar

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Canvas { context, size in
                let steps = 256
                let w = size.width / CGFloat(steps)
                for i in 0..<steps {
                    context.fill(Path(CGRect(x: CGFloat(i) * w, y: 0, width: w + 0.5, height: size.height)),
                                 with: .color(color(i)))
                }
            }
            .frame(height: 10)
            .clipShape(RoundedRectangle(cornerRadius: 2))
            GeometryReader { geo in
                ForEach(bar.ticks, id: \.t) { tick in
                    Text(tick.label)
                        .font(.caption2.monospacedDigit())
                        .fixedSize()
                        .position(x: min(max(CGFloat(tick.t) * geo.size.width, 14), geo.size.width - 14), y: 6)
                }
            }
            .frame(height: 12)
            Text([bar.heading, bar.note].filter { !$0.isEmpty }.joined(separator: " · "))
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    private func color(_ i: Int) -> Color {
        guard let lut = bar.lut, lut.count >= 768 else { return Color(white: Double(i) / 255) }
        return Color(red: Double(lut[3 * i]) / 255, green: Double(lut[3 * i + 1]) / 255, blue: Double(lut[3 * i + 2]) / 255)
    }
}

/// One mark per frame along the frames' own time span, the current one highlighted; empty
/// stretches are gaps in the cadence. A click picks the nearest frame.
struct FrameTimes: View {
    let frames: [LibraryFile]
    @Binding var current: Int?

    private var times: [Date?] { frames.map { IndexTime.date($0.dateObs) } }
    static let inset: CGFloat = 4  // so the first and last marks are not on the edge

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
                    let x = CGFloat(t.timeIntervalSince(first) / span) * (size.width - 2 * Self.inset) + Self.inset
                    let on = frames[i].fileId == current
                    context.fill(Path(CGRect(x: x - (on ? 1 : 0.5), y: on ? 0 : 5, width: on ? 3 : 1,
                                             height: on ? size.height : size.height - 10)),
                                 with: .color(on ? StudioTheme.accent : .primary.opacity(0.5)))
                }
            }
            .contentShape(Rectangle())
            .onTapGesture { point in
                let at = first.addingTimeInterval(Double((point.x - Self.inset) / max(geo.size.width - 2 * Self.inset, 1)) * span)
                let nearest = times.enumerated().compactMap { i, t in t.map { (i, abs($0.timeIntervalSince(at))) } }
                    .min { $0.1 < $1.1 }
                if let nearest { current = frames[nearest.0].fileId }
            }
        }
    }
}
