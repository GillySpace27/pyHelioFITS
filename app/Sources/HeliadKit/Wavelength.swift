import Foundation

/// Wavelengths as the core stores them (angstrom) and as people write them: EUV bands in Å
/// (193 Å), visible light in nm (530 nm). The rule matches `_wavelength_text` in
/// src/heliad/fitsmeta.py, so a dataset label and an app column agree.
public enum Wavelength {
    /// "193 Å", "171.5 Å", "530 nm" (5300 Å), "530.3 nm" (5303 Å).
    public static func text(_ angstrom: Double) -> String {
        if angstrom >= 1000 { return "\(g(angstrom / 10)) nm" }
        return "\(g(angstrom)) Å"
    }

    /// A typed wavelength in angstrom: "193", "193 Å", "193 A", "5300", "530 nm" or "530nm".
    /// A bare number is angstrom, the unit the core searches in. Nil when it is not a number.
    public static func angstrom(from typed: String) -> Double? {
        var t = typed.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        var scale = 1.0
        for (suffix, factor) in [("nm", 10.0), ("å", 1.0), ("angstrom", 1.0), ("a", 1.0)] where t.hasSuffix(suffix) {
            t = String(t.dropLast(suffix.count)).trimmingCharacters(in: .whitespaces)
            scale = factor
            break
        }
        guard let n = Double(t) else { return nil }
        return n * scale
    }

    /// Python's `{:g}`: six significant digits, no trailing zeros.
    private static func g(_ x: Double) -> String { String(format: "%g", x) }
}
