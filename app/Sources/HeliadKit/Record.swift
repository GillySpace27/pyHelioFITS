import Foundation

// MARK: - processing records (record.read, record.resolve)
//
// The core turns a record into display rows (docs/api.md, "Processing record methods"), so
// these models are plain text and flags; nothing here interprets the hfrecord format.

/// A name and its value as text (JSON for anything but a string), as the core writes them.
public struct RecordPair: Decodable, Sendable, Hashable {
    public let name: String
    public let value: String
}

/// The Studio command line for one layer: -dataset ID -start ISO -end ISO.
public struct StudioLaunch: Decodable, Sendable, Hashable {
    public let dataset: String
    public let start: String
    public let end: String
    public let args: [String]
}

/// One step of a layer, in the order the producer ran them.
public struct RecordStep: Decodable, Sendable, Hashable {
    public let index: Int
    public let op: String
    public let knownOp: Bool
    public let stage: String?
    public let algorithm: String?
    public let implementation: String?
    public let automatic: Bool
    public let params: [RecordPair]
    public let resolved: [RecordPair]
    public let units: [RecordPair]
    public let varies: Bool
    public let variesParams: [String]
    public let automation: [String]
    public let changedByUser: Bool?
    public let reference: String?
    public let caveat: String?
    public let citation: String?
    public let stamp: String?
}

/// One layer of the record, top layer first in RecordView.layers; `drawn` is its index in the
/// record (drawing order, 0 at the bottom).
public struct RecordLayer: Decodable, Sendable, Hashable {
    public let drawn: Int
    public let id: String?
    public let name: String?
    public let enabled: Bool
    public let datasetKey: String?
    public let datasetUid: String?
    public let datasetLabel: String?
    public let frames: Int
    public let shortened: Bool
    public let sourceSpec: String?
    public let filter: String
    public let lut: String?
    public let automaticSteps: Int
    public let varies: Bool
    public let steps: [RecordStep]
    public let caveats: [String]
    public let acknowledgement: String?
    public let studio: StudioLaunch?

    public var title: String { datasetLabel ?? name ?? "Layer \(drawn + 1)" }
}

public struct RecordAcknowledgement: Decodable, Sendable, Hashable {
    public let layer: String?
    public let text: String
}

/// record.read's `view`: everything the inspector shows.
public struct RecordView: Decodable, Sendable {
    public let summary: String
    public let producer: String
    public let producerStamp: String?
    public let dirty: Bool
    public let created: String?
    public let recordId: String?
    public let format: String?
    public let output: [RecordPair]
    public let scene: [RecordPair]
    public let layers: [RecordLayer]
    public let caveats: [String]
    public let citations: [String]
    public let acknowledgements: [RecordAcknowledgement]
    public let provenance: [RecordPair]
    public let snapshot: [RecordPair]
    public let notGuaranteedToRebuild: [String]
    public let studio: StudioLaunch?
}

/// record.read: the record found for a file, or found == false.
public struct RecordRead: Decodable, Sendable {
    public struct FoundIn: Decodable, Sendable {
        public let kind: String
        public let container: String
        public let path: String
    }

    public struct Skipped: Decodable, Sendable, Hashable {
        public let location: String
        public let problem: String
    }

    /// Sidecars only: the export's SHA-256 against the record's output.sha256.
    public struct OutputCheck: Decodable, Sendable {
        public let sha256: String
        public let file: String?
        public let actual: String?
        public let matches: Bool?
    }

    public let found: Bool
    public let path: String
    /// The record as written, kept whole to hand back to record.resolve.
    public let record: JSONValue?
    public let foundIn: FoundIn?
    public let legacy: Bool?
    public let valid: Bool?
    public let errors: [String]?
    public let skipped: [Skipped]?
    public let outputCheck: OutputCheck?
    public let view: RecordView?
}

/// record.resolve: each source looked up in the library by SHA-256.
public struct RecordResolution: Decodable, Sendable {
    public struct Source: Decodable, Sendable, Hashable {
        public let layer: Int
        public let source: Int
        public let sha256: String?
        public let dateObs: String?
        public let originalName: String?
        /// in_library | superseded | missing | no_hash
        public let status: String
        public let inLibrary: Bool
        public let datasetKey: String?
        public let relpath: String?
        public let fileId: Int?
        public let observationId: Int?
        public let rejected: String?
    }

    public struct Layer: Decodable, Sendable {
        public let layer: Int
        public let name: String?
        public let datasetKey: String?
        public let datasetUid: String?
        public let datasetInLibrary: String?
        public let sources: [Source]

        public var missing: Int { sources.filter { $0.status == "missing" }.count }
        public var partial: Int { sources.filter { $0.status == "superseded" || $0.status == "no_hash" }.count }
    }

    /// The core call that fetches a group of missing sources.
    public struct Fetch: Decodable, Sendable {
        public let method: String
        public let params: [String: JSONValue]
        public let inferred: Bool
        public let note: String
    }

    public struct MissingGroup: Decodable, Sendable, Identifiable {
        public let dataset: String
        public let datasetKey: String?
        public let label: String?
        public let inLibrary: Bool
        public let count: Int
        public let start: String?
        public let end: String?
        public let fetch: Fetch?
        public let reason: String?

        public var id: String { dataset }
    }

    public let counts: [String: Int]
    public let complete: Bool
    public let layers: [Layer]
    public let missingByDataset: [MissingGroup]

    public func layer(drawn: Int) -> Layer? { layers.first { $0.layer == drawn } }
}
