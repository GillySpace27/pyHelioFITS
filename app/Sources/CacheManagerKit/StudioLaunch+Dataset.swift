import Foundation

/// Studio launched on a library dataset (the dataset view's Open in Studio), and the
/// hfcore://dataset/ link to the same thing.
extension StudioLaunch {
    public init(datasetUid: String, start: String, end: String) {
        dataset = datasetUid
        self.start = start
        self.end = end
        args = ["-dataset", datasetUid, "-start", start, "-end", end]
    }

    /// Studio on a dataset over [start, end], each time as index text (ISO 8601 UTC,
    /// YYYY-MM-DDTHH:MM:SS.sss, no zone letter); an empty or missing bound is the dataset's
    /// first or last observation. Nil when the dataset has no uid (a core before schema 5)
    /// or a time cannot be read.
    public static func forDataset(_ d: Dataset, start: String?, end: String?) -> StudioLaunch? {
        guard let uid = d.datasetUid else { return nil }
        func pick(_ typed: String?, _ fallback: String?) -> String? {
            let t = typed?.trimmingCharacters(in: .whitespaces) ?? ""
            return IndexTime.date(t.isEmpty ? fallback : t).map(IndexTime.text)
        }
        guard let s = pick(start, d.first), let e = pick(end, d.last) else { return nil }
        return StudioLaunch(datasetUid: uid, start: s, end: e)
    }

    /// hfcore://dataset/<uid>?start=<iso>&end=<iso>, the form the core's dataset.from_url reads.
    public static func link(datasetUid: String, start: String?, end: String?) -> URL? {
        var parts = URLComponents()
        parts.scheme = "hfcore"
        parts.host = "dataset"
        parts.path = "/" + datasetUid
        let items = [("start", start), ("end", end)].compactMap { name, value in
            value.flatMap { $0.isEmpty ? nil : URLQueryItem(name: name, value: $0) }
        }
        if !items.isEmpty { parts.queryItems = items }
        return parts.url
    }
}
