import CacheManagerKit
import SwiftUI

/// The cache editor for one dataset: what the archive holds against what is kept, a densify
/// job that fetches only the missing frames, and the frames set aside as bad. Studio stays the
/// place to download new datasets; this upgrades one that is already kept.
struct UpgradePanel: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    let dataset: Dataset

    @State var basis: DatasetQuery?
    @State var report: CadenceReport?
    @State var checking = false
    @State var step = Step.double
    @State var customStep = ""
    @State var plan: UpgradePlan?
    @State var rejected: [RejectedFrame] = []
    @State var problem: String?

    /// The cadence to densify to, against the dataset's own spacing.
    enum Step: String, CaseIterable, Identifiable {
        case double, quadruple, archive, custom
        var id: String { rawValue }
        var label: String {
            switch self {
            case .double: return "Twice as many frames"
            case .quadruple: return "Four times as many"
            case .archive: return "Everything the archive has"
            case .custom: return "Every … seconds"
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Upgrade \(dataset.label)").font(.headline)
            Form {
                Section("Asked of the archive") { queryRows }
                Section("Cadence") { cadenceRows }
                Section("Fetch the missing frames") { densifyRows }
                Section("Set aside as bad (\(rejected.count))") { rejectedRows }
            }
            .formStyle(.grouped)
            if let problem {
                Text(problem).foregroundStyle(.red).font(.callout)
            }
            HStack {
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }
        }
        .padding(20)
        .frame(width: 560, height: 620)
        .task { await load() }
        .onChange(of: step) { Task { await replan() } }
    }

    // MARK: sections

    @ViewBuilder private var queryRows: some View {
        if let basis {
            Text(summary(basis.query) + " through \(basis.source)")
                .textSelection(.enabled)
            Text(basis.inferred ? "Inferred: \(basis.basis)." : "From the download that brought it: \(basis.basis).")
                .font(.caption)
                .foregroundStyle(basis.inferred ? StudioTheme.partial : .secondary)
        } else {
            ProgressView().controlSize(.small)
        }
    }

    @ViewBuilder private var cadenceRows: some View {
        LabeledContent("Kept") {
            Text(side(dataset.observations, spacing: libraryStep, rejected: report?.library.rejected))
        }
        if let report {
            LabeledContent("Archive") { Text(side(report.archive.count, spacing: report.archive.medianSpacingS)) }
            LabeledContent("Missing") {
                Text("\(report.missing.count) frame\(report.missing.count == 1 ? "" : "s")")
                    .foregroundStyle(report.missing.isEmpty ? StudioTheme.covers : StudioTheme.partial)
            }
            Text("Two times within \(Format.duration(report.toleranceS)) count as one frame. Frames set aside count as kept.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        HStack {
            Button(report == nil ? "Check the Archive" : "Check Again") { Task { await check() } }
                .disabled(checking || basis == nil)
            if checking { ProgressView().controlSize(.small) }
            Spacer()
            Text("Searches only; downloads nothing.").font(.caption).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private var densifyRows: some View {
        Picker("Cadence", selection: $step) {
            ForEach(Step.allCases) { Text($0.label).tag($0) }
        }
        if step == .custom {
            TextField("Seconds between frames", text: $customStep, prompt: Text("e.g. 12"))
                .onSubmit { Task { await replan() } }
        }
        if let plan {
            Text("Asks for " + summary(plan.query) + ", and fetches only frames the library lacks.")
                .font(.callout)
        }
        if let estimate {
            Text(estimate).font(.caption).foregroundStyle(.secondary)
        }
        HStack {
            Spacer()
            Button("Fetch Missing Frames") {
                Task {
                    await model.densify(dataset.datasetKey, sampleS: targetStep)
                    dismiss()
                }
            }
            .disabled(plan == nil || (step == .custom && targetStep == nil))
        }
    }

    @ViewBuilder private var rejectedRows: some View {
        if rejected.isEmpty {
            Text("None. Right-click a frame and choose Set Aside as Bad.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        ForEach(rejected) { frame in
            HStack {
                VStack(alignment: .leading) {
                    Text(Format.time(frame.dateObs)).monospacedDigit()
                    Text(frame.reason).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Restore") {
                    Task {
                        await model.unreject([frame])
                        await loadRejected()
                    }
                }
            }
        }
    }

    // MARK: numbers

    /// The dataset's own spacing: the core's number when the archive was checked, else the span
    /// over the count (an estimate, and labelled so).
    private var libraryStep: Double? {
        if let s = report?.library.medianSpacingS { return s }
        guard dataset.observations > 1, let a = IndexTime.date(dataset.first)?.timeIntervalSince1970,
              let b = IndexTime.date(dataset.last)?.timeIntervalSince1970
        else { return nil }
        return (b - a) / Double(dataset.observations - 1)
    }

    private var targetStep: Double? {
        switch step {
        case .double: return libraryStep.map { $0 / 2 }
        case .quadruple: return libraryStep.map { $0 / 4 }
        case .archive: return nil  // no sample: everything the archive holds
        case .custom: return Double(customStep).flatMap { $0 > 0 ? $0 : nil }
        }
    }

    /// Roughly how many frames the job adds, when the span and the step are known.
    private var estimate: String? {
        if step == .archive, let report {
            return "About \(report.missing.count) frames, from the last archive check."
        }
        guard let target = targetStep, let a = IndexTime.date(dataset.first)?.timeIntervalSince1970,
              let b = IndexTime.date(dataset.last)?.timeIntervalSince1970,
              b > a else { return nil }
        let wanted = Int(((b - a) / target).rounded()) + 1
        let added = max(0, wanted - dataset.observations)
        return "Estimated: about \(added) more frames at one every \(Format.duration(target)), if the archive has them."
    }

    private func side(_ count: Int, spacing: Double?, rejected: Int? = nil) -> String {
        var text = "\(count) frame\(count == 1 ? "" : "s")"
        if let spacing { text += ", one every \(Format.duration(spacing))" }
        if let rejected, rejected > 0 { text += " (\(rejected) set aside)" }
        return text
    }

    private func summary(_ q: [String: JSONValue]) -> String {
        var parts: [String] = []
        if let i = q["instrument"]?.stringValue { parts.append(i) }
        if let w = q["wavelength"]?.doubleValue { parts.append(Wavelength.text(w)) }
        if let l = q["level"]?.stringValue { parts.append("level \(l)") }
        parts.append("\(Format.time(q["start"]?.stringValue)) to \(Format.time(q["end"]?.stringValue)) UTC")
        if let s = q["sample_s"]?.doubleValue { parts.append("every \(Format.duration(s))") }
        return parts.joined(separator: " · ")
    }

    // MARK: loading

    private func load() async {
        do {
            basis = try await model.datasetQuery(dataset.datasetKey)
        } catch {
            problem = "HelioFITS Core cannot build a query for this dataset: \(error.localizedDescription)"
        }
        await loadRejected()
        await replan()
    }

    private func loadRejected() async {
        rejected = (try? await model.rejected(dataset.datasetKey)) ?? []
    }

    private func check() async {
        checking = true
        defer { checking = false }
        do {
            report = try await model.cadences(dataset.datasetKey)
            problem = nil
        } catch {
            problem = "The archive check failed: \(error.localizedDescription)"
        }
    }

    private func replan() async {
        guard basis != nil, step != .custom || targetStep != nil else { plan = nil; return }
        plan = try? await model.upgradePlan(dataset.datasetKey, sampleS: targetStep)
    }
}

/// Asks why a frame is being set aside, then sets it aside. Nothing is deleted.
struct RejectSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    let files: [LibraryFile]
    var onDone: () -> Void = {}
    @State var reason = "bad frame"

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Set aside \(files.count == 1 ? files[0].name : "\(files.count) frames") as bad?")
                .font(.headline)
            TextField("Reason", text: $reason)
                .textFieldStyle(.roundedBorder)
            Text("Studio and event folders stop showing \(files.count == 1 ? "it" : "them"), and upgrades never fetch \(files.count == 1 ? "it" : "them") again. The file stays in the library; Restore brings it back.")
                .font(.callout)
                .foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Set Aside") {
                    Task {
                        await model.reject(files, reason: reason.trimmingCharacters(in: .whitespaces))
                        onDone()
                        dismiss()
                    }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(reason.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 420)
    }
}
