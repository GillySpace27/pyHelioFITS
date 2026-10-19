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

    /// A length of time in the largest units that keep it short: "40 s", "12 min", "3 h 20 min", "6 d".
    static func duration(_ seconds: Double) -> String {
        let s = max(seconds, 0)
        if s < 1 { return String(format: "%.0f ms", s * 1000) }
        if s < 60 { return s.rounded() == s ? "\(Int(s)) s" : String(format: "%.1f s", s) }
        if s < 3600 {
            let m = Int(s / 60), rest = Int(s.truncatingRemainder(dividingBy: 60))
            return rest == 0 ? "\(m) min" : "\(m) min \(rest) s"
        }
        if s < 86400 {
            let h = Int(s / 3600), m = Int(s.truncatingRemainder(dividingBy: 3600) / 60)
            return m == 0 ? "\(h) h" : "\(h) h \(m) min"
        }
        let d = s / 86400
        return d < 10 ? String(format: "%.1f d", d) : "\(Int(d.rounded())) d"
    }

    static func wavelength(_ w: Double?) -> String {
        guard let w else { return "" }
        return w.rounded() == w ? "\(Int(w)) Å" : String(format: "%.1f Å", w)
    }
}
