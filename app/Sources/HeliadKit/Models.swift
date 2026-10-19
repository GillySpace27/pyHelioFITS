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
        if let wave = query["wavelength"]?.doubleValue { parts.append("\(JSONValue.double(wave).display) Å") }
        if let series = query["jsoc_series"]?.stringValue { parts.append(series) }
        let start = query["start"]?.stringValue ?? "?"
        let end = query["end"]?.stringValue ?? "?"
        parts.append("\(start) to \(end)")
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
    }

    public let total: Int
    public let recordset: String?
    public let providers: [Provider]

    private enum Keys: String, CodingKey { case total, recordset, providers, rows }

    private struct RawProvider: Decodable {
        let client: String?
        let count: Int
        let columns: [String]
        let rows: [[String: JSONValue]]
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        total = try c.decode(Int.self, forKey: .total)
        recordset = try c.decodeIfPresent(String.self, forKey: .recordset)
        if let raw = try c.decodeIfPresent([RawProvider].self, forKey: .providers) {
            providers = raw.enumerated().map { i, p in
                Provider(id: i, client: p.client ?? "archive", count: p.count, columns: p.columns, rows: p.rows)
            }
        } else {
            let rows = try c.decodeIfPresent([[String: JSONValue]].self, forKey: .rows) ?? []
            let columns = rows.first.map { Array($0.keys).sorted() } ?? []
            providers = [Provider(id: 0, client: "JSOC export", count: total, columns: columns, rows: rows)]
        }
    }
}

/// query.from_url
public struct LinkRequest: Decodable, Sendable {
    public let action: String   // search | get
    public let query: [String: JSONValue]
}
