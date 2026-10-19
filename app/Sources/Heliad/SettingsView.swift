import HeliadKit
import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var model: AppModel
    @AppStorage("corePath") var corePath = ""
    @AppStorage("libraryPath") var libraryPath = ""
    @State var jsocEmail = ""
    @State var vsoMirrors = ""
    @State var loaded = false

    var body: some View {
        Form {
            Section("Archives") {
                TextField("JSOC email", text: $jsocEmail, prompt: Text("registered at jsoc.stanford.edu"))
                    .onSubmit { Task { await model.setSetting("jsoc_email", jsocEmail) } }
                Text("JSOC exports need an address registered at jsoc.stanford.edu/ajax/register_email.html.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                TextField("VSO mirrors", text: $vsoMirrors, prompt: Text("NSO, SAO, ROB"))
                    .onSubmit { Task { await model.setSetting("vso_mirrors", vsoMirrors) } }
                Text("Tried in order for files the first VSO mirror cannot serve. \"none\" turns this off.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Button("Save") {
                    Task {
                        await model.setSetting("jsoc_email", jsocEmail)
                        await model.setSetting("vso_mirrors", vsoMirrors)
                    }
                }
                .disabled(model.coreState != .ready)
            }
            Section("Core") {
                LabeledContent("Library") {
                    Text(model.libraryRoot ?? "not open").textSelection(.enabled)
                }
                TextField("Library folder", text: $libraryPath, prompt: Text("the default library"))
                TextField("Core", text: $corePath, prompt: Text("found automatically"))
                Text("The heliad command, or a Python with heliad installed. Leave empty to use the bundled one or the development checkout.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let launch = model.launch {
                    Text(launch.commandLine)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                }
                Button("Restart Core") { model.restartCore() }
            }
        }
        .formStyle(.grouped)
        .frame(width: 520)
        .task(id: model.coreState) { await load() }
    }

    private func load() async {
        guard model.coreState == .ready, !loaded else { return }
        let values = await model.settings()
        jsocEmail = values["jsoc_email"] ?? ""
        vsoMirrors = values["vso_mirrors"] ?? ""
        loaded = true
    }
}
