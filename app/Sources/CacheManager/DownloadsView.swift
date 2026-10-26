import CacheManagerKit
import SwiftUI

/// Download jobs, newest first, with their progress and what they stored.
struct DownloadsView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        Group {
            if model.jobs.isEmpty {
                Text("No downloads yet. Search the archives and press Download.")
                    .foregroundStyle(.secondary)
            } else {
                List(model.jobs) { job in
                    JobRow(job: job)
                }
                .studioPanel()
            }
        }
        .navigationTitle("Downloads")
        .toolbar {
            Button { Task { await model.refreshJobs() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
        }
        .task { await model.refreshJobs() }
    }
}

private struct JobRow: View {
    @EnvironmentObject var model: AppModel
    let job: Job

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .foregroundStyle(color)
                .font(.title3)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 4) {
                Text(job.title)
                    .lineLimit(1)
                (Text(statusText).foregroundStyle(color)
                    + Text(verbatim: " · \(job.source == "jsoc" ? "JSOC export" : "Fido")"
                        + " · queued \(Format.localTime(job.createdAt))"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if job.status == "active", let total = job.filesTotal, total > 0 {
                    ProgressView(value: Double(min(job.filesDone, total)), total: Double(total))
                        .frame(maxWidth: 360)
                }
                if let error = job.error {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(job.status == "partial" ? Color.orange : Color.red)
                        .lineLimit(3)
                        .textSelection(.enabled)
                }
            }
            Spacer()
            if job.isActive {
                Button("Cancel") { Task { await model.cancel(job) } }
            }
            if job.canRetry {
                Button("Retry") { Task { await model.retry(job) } }
                    .help("Fetch what is still missing; files already stored are not downloaded again")
            }
            Menu {
                Button("Search Again") { model.repeatSearch(of: job) }
            } label: {
                Image(systemName: "ellipsis.circle")
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
        }
        .padding(.vertical, 4)
    }

    var statusText: String {
        switch job.status {
        case "queued": return "Waiting"
        case "active":
            let phase = job.phase.map { $0.prefix(1).uppercased() + $0.dropFirst() } ?? "Working"
            if let total = job.filesTotal, total > 0 { return "\(phase) \(job.filesDone) of \(total)" }
            return phase
        case "done" where job.fellShort: return job.summary.map { "Ended short, \($0)" } ?? "Ended short"
        case "done": return job.summary.map { "Done, \($0)" } ?? "Done"
        case "partial": return job.summary.map { "Partly done, \($0)" } ?? "Partly done"
        case "failed": return "Failed"
        case "cancelled": return "Cancelled"
        default: return job.status
        }
    }

    var icon: String {
        switch job.status {
        case "queued": return "clock"
        case "active": return "arrow.down.circle"
        case "done" where job.fellShort: return "exclamationmark.circle.fill"
        case "done": return "checkmark.circle.fill"
        case "partial": return "exclamationmark.circle.fill"
        case "failed": return "xmark.octagon.fill"
        default: return "minus.circle"
        }
    }

    var color: Color {
        switch job.status {
        case "done" where job.fellShort: return .orange
        case "done": return .green
        case "partial": return .orange
        case "failed": return .red
        case "active": return StudioTheme.accent
        default: return .secondary
        }
    }
}
