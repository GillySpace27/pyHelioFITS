import Foundation

/// Where the app finds the Python core, first match wins:
///
/// 1. `HFCORE_CORE` in the environment: the `hfcore` command, or a Python that has HelioFITS Core
///    installed. `HELIAD_CORE`, its name from before the rename, is read when it is not set, and a
///    Python named there is run as `-m heliad`, which cores from before the rename understand too.
/// 2. The `corePath` default (Settings, or `-corePath PATH` on the command line), the same two kinds.
/// 3. The Python bundled in the app (`Contents/Resources/python/bin/python3`).
/// 4. A development checkout's virtual environment, `~/Documents/NWRA/heliad/.venv/bin/heliad`
///    (Gilly's checkout keeps the folder name the repository was cloned under).
///
/// The library is the core's default (its config.json) unless `libraryPath` is set
/// (Settings, or `-libraryPath PATH` on the command line).
public enum CoreLocator {
    public static let devCheckout = "Documents/NWRA/heliad/.venv/bin/heliad"
    /// The core's Python package, and its name from before the rename (still importable).
    public static let module = "heliofits_core"
    public static let legacyModule = "heliad"

    public static func launch(library: String?,
                              corePath: String?,
                              bundle: Bundle = .main,
                              environment: [String: String] = ProcessInfo.processInfo.environment,
                              home: URL = FileManager.default.homeDirectoryForCurrentUser,
                              exists: (String) -> Bool = { FileManager.default.isExecutableFile(atPath: $0) })
        throws -> CoreClient.Launch {
        var tried: [String] = []
        var candidates: [(String, Bool, String)] = []  // path, bundled, the module a Python runs
        if let env = environment["HFCORE_CORE"], !env.isEmpty {
            candidates.append((expand(env, home), false, module))
        } else if let env = environment["HELIAD_CORE"], !env.isEmpty {
            candidates.append((expand(env, home), false, legacyModule))
        }
        if let corePath, !corePath.isEmpty { candidates.append((expand(corePath, home), false, module)) }
        if let resources = bundle.resourceURL {
            candidates.append((resources.appendingPathComponent("python/bin/python3").path, true, module))
        }
        candidates.append((home.appendingPathComponent(devCheckout).path, false, module))

        for (path, bundled, module) in candidates {
            tried.append(path)
            guard exists(path) else { continue }
            var arguments: [String] = []
            let name = (path as NSString).lastPathComponent
            if bundled {
                // -B: writing bytecode into a signed app would break its signature.
                // -s, -P: no user site-packages, no current folder on sys.path.
                arguments += ["-B", "-s", "-P", "-m", module]
            } else if name.hasPrefix("python") {
                // Isolated too: a named Python runs its own packages, never ones in ~/.local (an
                // astropy there whose asdf_astropy lacked asdf broke every download, 2026-10-25).
                // A core installed with pip --user is named by its hfcore command instead.
                arguments += ["-s", "-P", "-m", module]
            }
            if let library, !library.isEmpty { arguments += ["--library", expand(library, home)] }
            arguments.append("serve")
            return CoreClient.Launch(executable: URL(fileURLWithPath: path), arguments: arguments,
                                     environment: ["PYTHONUNBUFFERED": "1"])
        }
        throw CoreError.notFound("No HelioFITS Core found. Looked for:\n" + tried.joined(separator: "\n")
            + "\nSet its path in Settings, or HFCORE_CORE.")
    }

    static func expand(_ path: String, _ home: URL) -> String {
        if path == "~" { return home.path }
        if path.hasPrefix("~/") { return home.appendingPathComponent(String(path.dropFirst(2))).path }
        return path
    }
}
