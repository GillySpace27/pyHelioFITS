import AppKit
import SwiftUI

@main
struct CacheManagerApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()
    @AppStorage("showFrameViewer") private var showViewer = false
    @AppStorage("appearance") private var appearance = Appearance.dark.rawValue

    var body: some Scene {
        WindowGroup("HelioFITS Cache Manager", id: "main") {
            ContentView()
                .environmentObject(model)
                .frame(minWidth: 900, minHeight: 560)
                .background(StudioTheme.background)
                .tint(StudioTheme.accent)
                .preferredColorScheme(scheme)
                .onAppear {
                    appDelegate.model = model
                    if model.hello == nil, case .starting = model.coreState { model.startCore() }
                }
                .task { await SelfTest.run(model) }
                .onOpenURL { url in model.open(url) }
                .handlesExternalEvents(preferring: ["*"], allowing: ["*"])
        }
        .defaultSize(width: 1240, height: 780)  // room for the grid beside the frame viewer
        .handlesExternalEvents(matching: ["*"])
        .commands {
            CommandGroup(after: .newItem) {
                Button("Search Archives") { model.selection = .search }
                    .keyboardShortcut("f", modifiers: [.command, .shift])
                Button("Downloads") { model.selection = .downloads }
                    .keyboardShortcut("d", modifiers: [.command, .shift])
                Button("Timeline") { model.selection = .timeline }
                    .keyboardShortcut("t", modifiers: [.command, .shift])
                Divider()
                Button("Open Processing Record…") { model.chooseRecord() }
                    .keyboardShortcut("o", modifiers: .command)
                    .disabled(model.coreState != .ready)
            }
            CommandGroup(after: .sidebar) {
                // Hidden by default: the viewer is for checking what is kept, not for making images.
                Toggle("Show Frame Viewer", isOn: $showViewer)
                    .keyboardShortcut("p", modifiers: [.command, .option])
            }
            CommandGroup(after: .toolbar) {
                Button("Reload Library") { Task { await model.refreshAll() } }
                    .keyboardShortcut("r", modifiers: .command)
            }
        }

        Settings {
            SettingsView()
                .environmentObject(model)
                .tint(StudioTheme.accent)
                .preferredColorScheme(scheme)
        }
    }

    private var scheme: ColorScheme? { (Appearance(rawValue: appearance) ?? .dark).colorScheme }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var model: AppModel?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Run from `swift run` there is no bundle telling macOS this is a regular app.
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationWillTerminate(_ notification: Notification) {
        // The core finishes what it was asked and exits; a running download resumes next time.
        MainActor.assumeIsolated { model?.core.stopSoon() }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}
