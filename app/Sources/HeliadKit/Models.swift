import Foundation

/// heliad.hello: who the core is and which library it opened.
public struct Hello: Decodable, Sendable, Equatable {
    public let name: String
    public let version: String
    public let schemaVersion: Int
    public let readerCompat: Int
    public let rulesVersion: Int
    public let reindexDue: Bool
    public let readonly: Bool
    public let libraryRoot: String
    public let dbPath: String
    public let methods: [String]
}

/// library.stats
public struct Stats: Decodable, Sendable, Equatable {
    public let libraryRoot: String
    public let datasets: Int
    public let observations: Int
    public let files: Int
    public let bytes: Int64
    public let warnings: Int
    public let collisions: Int
    public let reindexDue: Bool
}

/// One row of library.datasets: a kind of data (instrument, channel, level) and what is kept of it.
public struct Dataset: Decodable, Sendable, Identifiable, Hashable {
    public let id: Int
    public let datasetKey: String
    public let label: String
    public let observatory: String?
    public let instrument: String?
    public let detector: String?
    public let product: String?
    public let level: String?
    public let wavelength: Double?
    public let observations: Int
    public let first: String?
    public let last: String?
    public let bytes: Int64
    /// Stable across reindexing (schema 5); nil from a core older than that.
    public let datasetUid: String?
}

/// One row of library.files (the current_files view): the newest version of an observation.
public struct LibraryFile: Decodable, Sendable, Identifiable, Hashable {
    public let fileId: Int
    public let relpath: String
    public let sha256: String
    public let size: Int64
    public let version: String?
    public let observationId: Int
    public let dateObs: String
    public let datasetKey: String
    public let label: String

    public var id: Int { fileId }
    public var name: String { (relpath as NSString).lastPathComponent }
    /// The version for sorting a table by, "" when the file has none.
    public var versionText: String { version ?? "" }

    /// Where the core caches this file's thumbnail (thumbs.thumb_path in the core).
    public func thumbnailURL(libraryRoot: String) -> URL {
        URL(fileURLWithPath: libraryRoot)
            .appendingPathComponent(".thumbs")
            .appendingPathComponent(String(sha256.prefix(2)))
            .appendingPathComponent("\(sha256).png")
    }

    public func url(libraryRoot: String) -> URL {
        URL(fileURLWithPath: libraryRoot).appendingPathComponent(relpath)
    }
}

/// A download job (download.list, download.get).
public struct Job: Decodable, Sendable, Identifiable, Equatable {
    public let jobId: Int
    public let source: String
    public let status: String       // queued | active | done | partial | failed | cancelled
    public let phase: String?
    public let filesTotal: Int?
    public let filesDone: Int
    public let error: String?
    public let createdAt: String
    public let updatedAt: String
    public let query: [String: JSONValue]
    public let result: JSONValue?

    public var id: Int { jobId }
    public var isActive: Bool { status == "queued" || status == "active" }
    public var canRetry: Bool { status == "partial" || status == "failed" }

    /// A short name for the job: what it asked for and over which times.
    public var title: String {
        var parts: [String] = []
        if let instrument = query["instrument"]?.stringValue { parts.append(instrument) }
        if let wave = query["wavelength"]?.doubleValue { parts.append(Wavelength.text(wave)) }
        if let series = query["jsoc_series"]?.stringValue { parts.append(series) }
        let start = query["start"]?.stringValue ?? "?"
        let end = query["end"]?.stringValue ?? "?"
        parts.append("\(start) to \(end) UTC")
        return parts.joined(separator: " · ")
    }

    /// "stored 5 of 5", from the result once the job has ended.
    public var summary: String? {
        guard let result, let stored = result["stored"]?.intValue ?? result["fetched"]?.intValue else {
            return nil
        }
        if let found = result["found"]?.intValue { return "stored \(stored) of \(found)" }
        return "stored \(stored)"
    }

    /// Ended "done" but stored fewer files than it found: jobs from before partial existed
    /// (schema 3) were marked done this way, so the list shows them as partial.
    public var fellShort: Bool {
        guard status == "done", let result, let found = result["found"]?.intValue,
              let stored = result["stored"]?.intValue ?? result["fetched"]?.intValue else { return false }
        return stored < found
    }
}

/// library.delete, as a dry run or done.
public struct DeleteSummary: Decodable, Sendable, Equatable {
    public let files: Int
    public let observations: Int
    public let datasets: Int
    public let bytesReleased: Int64
    public let repointed: Int
    public let dryRun: Bool
}

/// The result of a search: Fido's per-client tables, or one JSOC record set.
public struct SearchResult: Decodable, Sendable {
    public struct Provider: Sendable, Identifiable {
        public let id: Int
        public let client: String
        public let count: Int
        public let columns: [String]
        public let rows: [[String: JSONValue]]
        /// Every record's time binned over the search range, when the archive reports times.
        public let coverage: TimeBins?
    }

    public let total: Int
    public let recordset: String?
    public let providers: [Provider]

    private enum Keys: String, CodingKey { case total, recordset, providers, rows, coverage }

    private struct RawProvider: Decodable {
        let client: String?
        let count: Int
        let columns: [String]
        let rows: [[String: JSONValue]]
        let coverage: TimeBins?
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        total = try c.decode(Int.self, forKey: .total)
        recordset = try c.decodeIfPresent(String.self, forKey: .recordset)
        if let raw = try c.decodeIfPresent([RawProvider].self, forKey: .providers) {
            providers = raw.enumerated().map { i, p in
                Provider(id: i, client: p.client ?? "archive", count: p.count, columns: p.columns, rows: p.rows,
                         coverage: p.coverage)
            }
        } else {
            let rows = try c.decodeIfPresent([[String: JSONValue]].self, forKey: .rows) ?? []
            let columns = rows.first.map { Array($0.keys).sorted() } ?? []
            providers = [Provider(id: 0, client: "JSOC export", count: total, columns: columns, rows: rows,
                                  coverage: try c.decodeIfPresent(TimeBins.self, forKey: .coverage))]
        }
    }
}

/// library.coverage: per dataset, how many observations fall in each equal slice of a range.
/// The bins split [start, end]; an observation exactly at end is in the last bin.
public struct Coverage: Decodable, Sendable, Equatable {
    public struct Row: Decodable, Sendable, Identifiable, Equatable {
        public let datasetKey: String
        public let label: String
        public let instrument: String?
        public let wavelength: Double?
        public let counts: [Int]
        public let frames: Int        // observations in the range
        public let first: String?     // first and last of those
        public let last: String?
        public let undated: Int       // observations whose time the index cannot read

        public var id: String { datasetKey }
    }

    public let start: String?         // nil when nothing in the library has a time
    public let end: String?
    public let bins: Int
    public let binSeconds: Double?
    public let datasets: [Row]

    /// Every row's counts added bin by bin.
    public var total: [Int] {
        datasets.reduce(into: Array(repeating: 0, count: bins)) { sum, row in
            for (i, n) in row.counts.enumerated() where i < sum.count { sum[i] += n }
        }
    }
}

/// An archive search's record times binned the way library.coverage bins the index.
public struct TimeBins: Decodable, Sendable, Equatable {
    public let start: String
    public let end: String
    public let bins: Int
    public let binSeconds: Double
    public let counts: [Int]
    public let outside: Int           // records the archive gave with a time outside the range
    public let undated: Int           // records with no time Heliad could read
}

/// One row of view.list: a named selection of observations (an event) and its folder of links.
public struct EventView: Decodable, Sendable, Identifiable, Hashable {
    public let viewId: Int
    public let name: String
    public let kind: String
    public let description: String?
    public let createdAt: String
    public let observations: Int
    public let datasets: Int
    public let bytes: Int64
    public let first: String?
    public let last: String?
    public let path: String           // where its folder of links is, or would be
    public let materialized: Bool     // whether that folder has been made

    public var id: String { name }
}

/// library.checks: what the header and WCS checks found in one file.
public struct FileCheck: Decodable, Sendable, Hashable {
    public let code: String
    public let severity: String       // info | warning | error
    public let message: String
}

/// query.from_url
public struct LinkRequest: Decodable, Sendable {
    public let action: String   // search | get
    public let query: [String: JSONValue]
}

/// dataset.from_url: a heliad://dataset/<uid>?start=&end= link, and the dataset it names
/// (nil when this library has no dataset with that uid).
public struct DatasetLink: Decodable, Sendable {
    public let datasetUid: String
    public let start: String?
    public let end: String?
    public let dataset: Dataset?
}

// MARK: - the cache editor (dataset.query, dataset.cadences, dataset.upgrade, library.rejected)

/// The query that asks the archive again for a dataset (dataset.query): the newest one stored
/// with its files, or one inferred from its headers, which `inferred` and `basis` say.
public struct DatasetQuery: Decodable, Sendable {
    public let query: [String: JSONValue]
    public let source: String
    public let inferred: Bool
    public let basis: String
}

/// What the archive holds for a dataset against what the library holds (dataset.cadences).
public struct CadenceReport: Decodable, Sendable {
    public struct Side: Decodable, Sendable {
        public let count: Int
        public let medianSpacingS: Double?
        public let rejected: Int?       // the library side only
    }

    public let datasetKey: String
    public let query: [String: JSONValue]
    public let source: String
    public let toleranceS: Double
    public let library: Side
    public let archive: Side
    public let missing: [String]        // archive record times the library has no frame for
}

/// A dry run of dataset.upgrade: the query a densify job would run and its options.
public struct UpgradePlan: Decodable, Sendable {
    public let dryRun: Bool
    public let query: [String: JSONValue]
    public let source: String
    public let options: [String: JSONValue]
    public let inferred: Bool
    public let basis: String
}

/// One frame set aside as bad (library.rejected); its file stays in the store.
public struct RejectedFrame: Decodable, Sendable, Identifiable, Hashable {
    public let observationId: Int
    public let datasetKey: String
    public let label: String
    public let dateObs: String
    public let reason: String
    public let rejectedAt: String
    public let fileId: Int?
    public let relpath: String?

    public var id: Int { observationId }
}
