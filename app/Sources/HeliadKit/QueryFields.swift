import Foundation

/// The search form: a Query (heliad.query.Query in the core) as editable text.
public struct QueryFields: Equatable, Sendable {
    public var start = ""
    public var end = ""
    public var instrument = ""
    public var wavelength = ""
    public var sample = ""
    public var source = ""        // sunpy attrs.Source, e.g. SDO; not the archive route
    public var provider = ""
    public var detector = ""
    public var level = ""
    public var physobs = ""
    public var jsocSeries = ""

    public init() {}

    public enum Problem: Error, LocalizedError, Equatable {
        case missing(String)
        case badTime(String)
        case badNumber(String)
        case endBeforeStart

        public var errorDescription: String? {
            switch self {
            case .missing(let f): return "\(f) is required."
            case .badTime(let f): return "\(f) must be a UTC time like 2025-09-21T16:00:00."
            case .badNumber(let f): return "\(f) must be a number."
            case .endBeforeStart: return "End must be after start."
            }
        }
    }

    private static let timePattern = #"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?Z?$"#

    /// The query as the core takes it, or the first problem with the form.
    public func json() throws -> [String: JSONValue] {
        func trimmed(_ s: String) -> String { s.trimmingCharacters(in: .whitespacesAndNewlines) }
        var out: [String: JSONValue] = [:]
        for (name, value) in [("Start", start), ("End", end)] {
            let t = trimmed(value)
            if t.isEmpty { throw Problem.missing(name) }
            if t.range(of: Self.timePattern, options: .regularExpression) == nil { throw Problem.badTime(name) }
            out[name.lowercased()] = .string(t.replacingOccurrences(of: " ", with: "T"))
        }
        if let s = out["start"]?.stringValue, let e = out["end"]?.stringValue,
           Self.sortable(e) <= Self.sortable(s) {
            throw Problem.endBeforeStart
        }
        if !trimmed(wavelength).isEmpty {
            // Typed as "193", "193 Å" or "530 nm"; the core takes angstrom.
            guard let number = Wavelength.angstrom(from: wavelength) else { throw Problem.badNumber("Wavelength") }
            out["wavelength"] = .double(number)
        }
        if !trimmed(sample).isEmpty {
            guard let number = Double(trimmed(sample)) else { throw Problem.badNumber("Cadence") }
            out["sample_s"] = .double(number)
        }
        let texts = [("instrument", instrument), ("source", source), ("provider", provider),
                     ("detector", detector), ("level", level), ("physobs", physobs),
                     ("jsoc_series", jsocSeries)]
        for (key, value) in texts {
            let t = trimmed(value)
            if !t.isEmpty { out[key] = .string(t) }
        }
        return out
    }

    /// Fill the form from a query the core sent (a heliad:// link, or a job to repeat).
    public init(query: [String: JSONValue]) {
        func text(_ key: String) -> String { query[key]?.display ?? "" }
        start = text("start")
        end = text("end")
        instrument = text("instrument")
        wavelength = text("wavelength")
        sample = text("sample_s")
        source = text("source")
        provider = text("provider")
        detector = text("detector")
        level = text("level")
        physobs = text("physobs")
        jsocSeries = text("jsoc_series")
    }

    /// Times compare as text once padded to full seconds and stripped of a trailing Z.
    private static func sortable(_ t: String) -> String {
        var s = t.hasSuffix("Z") ? String(t.dropLast()) : t
        let full = "0000-00-00T00:00:00"
        if s.count < full.count { s += full.suffix(full.count - s.count) }
        return s
    }
}
