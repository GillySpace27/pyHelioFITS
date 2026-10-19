import Foundation

/// Times as the index writes them (ISO 8601 UTC, milliseconds, no zone letter) and the
/// arithmetic a time axis needs: reading and writing that text, and choosing tick marks.
public enum IndexTime {
    private static func formatter(_ format: String) -> DateFormatter {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = format
        return f
    }

    private static let millis = formatter("yyyy-MM-dd'T'HH:mm:ss.SSS")
    private static let seconds = formatter("yyyy-MM-dd'T'HH:mm:ss")
    private static let minutes = formatter("yyyy-MM-dd'T'HH:mm")
    private static let day = formatter("yyyy-MM-dd")
    private static let lock = NSLock()

    /// The time in index text, or nil when it is not one. Accepts a trailing Z and a space for T.
    public static func date(_ text: String?) -> Date? {
        guard var t = text?.trimmingCharacters(in: .whitespaces), !t.isEmpty else { return nil }
        if t.hasSuffix("Z") { t.removeLast() }
        t = t.replacingOccurrences(of: " ", with: "T")
        if let dot = t.firstIndex(of: ".") {  // DateFormatter wants exactly three digits
            let digits = t[t.index(after: dot)...]
            guard !digits.isEmpty, digits.allSatisfy(\.isNumber) else { return nil }
            t = String(t[...dot]) + String((digits + "000").prefix(3))
        }
        lock.lock()
        defer { lock.unlock() }
        for f in [millis, seconds, minutes, day] {
            if let d = f.date(from: t) { return d }
        }
        return nil
    }

    /// Index text for a time, to the millisecond.
    public static func text(_ date: Date) -> String {
        lock.lock()
        defer { lock.unlock() }
        return millis.string(from: date)
    }

    /// Whole seconds, for a search form: rounded down for a start, up for an end, so the
    /// range asked of the archives covers the one selected.
    public static func wholeSeconds(_ text: String, up: Bool) -> String {
        guard let d = date(text) else { return text }
        let t = d.timeIntervalSince1970
        let whole = up ? t.rounded(.up) : t.rounded(.down)
        lock.lock()
        defer { lock.unlock() }
        return seconds.string(from: Date(timeIntervalSince1970: whole))
    }

    /// Steps a time axis can use, smallest first: whole seconds up to a year.
    public static let steps: [TimeInterval] = [
        1, 2, 5, 10, 15, 30,
        60, 120, 300, 600, 900, 1800,
        3600, 7200, 10800, 21600, 43200,
        86400, 2 * 86400, 7 * 86400, 14 * 86400, 30 * 86400, 91 * 86400, 182 * 86400, 365 * 86400,
    ]

    /// The smallest step that puts at most `count` ticks across [start, end].
    public static func step(from start: Date, to end: Date, count: Int) -> TimeInterval {
        let span = max(end.timeIntervalSince(start), 0.001)
        return steps.first { span / $0 <= Double(max(count, 1)) } ?? steps[steps.count - 1]
    }

    /// Tick times inside [start, end] at whole multiples of the step, counted from the epoch,
    /// which lands them on whole minutes, hours and UTC days. (Steps of a week or more are
    /// multiples of a day, not calendar weeks or months.)
    public static func ticks(from start: Date, to end: Date, count: Int) -> [Date] {
        let step = step(from: start, to: end, count: count)
        var t = (start.timeIntervalSince1970 / step).rounded(.up) * step
        var out: [Date] = []
        while t <= end.timeIntervalSince1970, out.count <= count {
            out.append(Date(timeIntervalSince1970: t))
            t += step
        }
        return out
    }

    /// A tick's label: the date where the day changes or the step is a day or more, else the clock.
    public static func label(_ date: Date, step: TimeInterval, showDate: Bool) -> String {
        let format: String
        if step >= 86400 {
            format = "yyyy-MM-dd"
        } else if step >= 60 {
            format = showDate ? "MM-dd HH:mm" : "HH:mm"
        } else {
            format = showDate ? "MM-dd HH:mm:ss" : "HH:mm:ss"
        }
        return formatter(format).string(from: date)
    }
}
