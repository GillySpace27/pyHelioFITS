import AppKit
import SwiftUI

@main
struct HeliadApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup("Heliad", id: "main") {
            ContentView()
                .environmentObject(model)
                .frame(minWidth: 900, minHeight: 560)
                .onAppear {
                    appDelegate.model = model
                    if model.hello == nil, case .starting = model.coreState { model.startCore() }
                }
                .task { await SelfTest.run(model) }
                .onOpenURL { url in model.open(url) }
                .handlesExternalEvents(preferring: ["*"], allowing: ["*"])
        }
        .handlesExternalEvents(matching: ["*"])
        .commands {
            CommandGroup(after: .newItem) {
                Button("Search Archives") { model.selection = .search }
                    .keyboardShortcut("f", modifiers: [.command, .shift])
                Button("Downloads") { model.selection = .downloads }
                    .keyboardShortcut("d", modifiers: [.command, .shift])
            }
            CommandGroup(after: .toolbar) {
                Button("Reload Library") { Task { await model.refreshAll() } }
                    .keyboardShortcut("r", modifiers: .command)
            }
        }

        Settings {
            SettingsView()
                .environmentObject(model)
        }
    }
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
