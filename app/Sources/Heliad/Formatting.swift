import Foundation

enum Format {
    static func bytes(_ n: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: n, countStyle: .file)
    }

    /// An index time (ISO 8601 UTC text) shortened for a list: no T, at most milliseconds.
    static func time(_ iso: String?) -> String {
        guard var s = iso else { return "" }
        s = s.replacingOccurrences(of: "T", with: " ")
        if let dot = s.firstIndex(of: "."), s.distance(from: dot, to: s.endIndex) > 4 {
            s = String(s[..<s.index(dot, offsetBy: 4)])
        }
        return s
    }

    static func wavelength(_ w: Double?) -> String {
        guard let w else { return "" }
        return w.rounded() == w ? "\(Int(w)) Å" : String(format: "%.1f Å", w)
    }
}
