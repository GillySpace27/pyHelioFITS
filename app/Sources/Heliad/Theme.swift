import AppKit
import SwiftUI

/// HelioFITS Studio's look, so Heliad reads as part of the same suite: Studio's two default
/// themes, Sunset Dark and Sunset Light (the built-in table in Studio's
/// src/org/helioviewer/jhv/app/Theme.java), and the colours of its range chips
/// (CacheDialog.java). Dark is the default, as it is in Studio; Settings picks Light or
/// Follow system. Images sit on HelioFITS Peek's canvas grey (Palette.canvas in HelioFITSCore).
enum StudioTheme {
    /// Window and panel background.
    static let background = dynamic(light: 0xF6EFE6, dark: 0x221E33)
    /// Text.
    static let foreground = dynamic(light: 0x2B2118, dark: 0xE6E1F2)
    /// Selection, the current frame, focus.
    static let accent = dynamic(light: 0xB5541C, dark: 0x8C79D9)
    /// Lists, tables and fields: Studio's component background.
    static let component = dynamic(light: 0xFFFBF5, dark: 0x2B2640)
    static let separator = dynamic(light: 0x8C7F66, dark: 0x7E749E)
    /// Section header bands and their text.
    static let headerFill = dynamic(light: 0xB5541C, dark: 0x7B6FA0)
    static let headerText = dynamic(light: 0xFFFFFF, dark: 0xFFFFFF)

    /// Coverage, as Studio's range chips say it: the library covers a range, covers part, or none.
    static let covers = dynamic(light: 0x1E6B3C, dark: 0x6FD096)
    static let partial = dynamic(light: 0x8A590C, dark: 0xE3B25C)
    static let none = dynamic(light: 0x6E665C, dark: 0xA197BC)

    /// Behind every image, in both modes: Peek's canvas (grey 0.07).
    static let canvas = Color(white: 0.07)

    private static func dynamic(light: UInt32, dark: UInt32) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            return rgb(isDark ? dark : light)
        })
    }

    private static func rgb(_ hex: UInt32) -> NSColor {
        NSColor(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
                blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
    }
}

/// Studio's appearance modes. Stored under "appearance"; Dark when nothing is stored.
enum Appearance: String, CaseIterable, Identifiable {
    case dark, light, system

    var id: String { rawValue }
    var label: String {
        switch self {
        case .dark: return "Dark (Sunset Dark)"
        case .light: return "Light (Sunset Light)"
        case .system: return "Follow system"
        }
    }
    var colorScheme: ColorScheme? {
        switch self {
        case .dark: return .dark
        case .light: return .light
        case .system: return nil
        }
    }
}

extension View {
    /// A list, table or grid on Studio's component background instead of the system's.
    func studioPanel() -> some View {
        scrollContentBackground(.hidden)
            .background(StudioTheme.component)
    }
}
