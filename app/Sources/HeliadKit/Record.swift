import Foundation

// The same file name and StudioLaunch as PR #18 (claude/processing-record-reader), so the two
// branches share one launcher: whichever merges second meets an add/add conflict here, and
// keeping #18's Record.swift resolves it (its StudioLaunch is this one).

/// The Studio command line for one layer: -dataset ID -start ISO -end ISO.
public struct StudioLaunch: Decodable, Sendable, Hashable {
    public let dataset: String
    public let start: String
    public let end: String
    public let args: [String]
}
