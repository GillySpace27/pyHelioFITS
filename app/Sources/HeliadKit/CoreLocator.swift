import Foundation

/// Where the app finds the Python core, first match wins:
///
/// 1. `HELIAD_CORE` in the environment: the `heliad` command, or a Python that has heliad installed.
/// 2. The `corePath` default (Settings, or `-corePath PATH` on the command line), the same two kinds.
/// 3. The Python bundled in the app (`Contents/Resources/python/bin/python3`).
/// 4. A development checkout's virtual environment, `~/Documents/NWRA/heliad/.venv/bin/heliad`.
///
/// The library is the core's default (its config.json) unless `libraryPath` is set
/// (Settings, or `-libraryPath PATH` on the command line).
public enum CoreLocator {
    public static let devCheckout = "Documents/NWRA/heliad/.venv/bin/heliad"

    public static func launch(library: String?,
                              corePath: String?,
                              bundle: Bundle = .main,
                              environment: [String: String] = ProcessInfo.processInfo.environment,
                              home: URL = FileManager.default.homeDirectoryForCurrentUser,
                              exists: (String) -> Bool = { FileManager.default.isExecutableFile(atPath: $0) })
        throws -> CoreClient.Launch {
        var tried: [String] = []
        var candidates: [(String, Bool)] = []  // path, bundled
        if let env = environment["HELIAD_CORE"], !env.isEmpty { candidates.append((expand(env, home), false)) }
        if let corePath, !corePath.isEmpty { candidates.append((expand(corePath, home), false)) }
        if let resources = bundle.resourceURL {
            candidates.append((resources.appendingPathComponent("python/bin/python3").path, true))
        }
        candidates.append((home.appendingPathComponent(devCheckout).path, false))

        for (path, bundled) in candidates {
            tried.append(path)
            guard exists(path) else { continue }
            var arguments: [String] = []
            let name = (path as NSString).lastPathComponent
            if bundled {
                // -B: writing bytecode into a signed app would break its signature.
                // -s, -P: no user site-packages, no current folder on sys.path.
                arguments += ["-B", "-s", "-P", "-m", "heliad"]
            } else if name.hasPrefix("python") {
                arguments += ["-m", "heliad"]
            }
            if let library, !library.isEmpty { arguments += ["--library", expand(library, home)] }
            arguments.append("serve")
            return CoreClient.Launch(executable: URL(fileURLWithPath: path), arguments: arguments,
                                     environment: ["PYTHONUNBUFFERED": "1"])
        }
        throw CoreError.notFound("No Heliad core found. Looked for:\n" + tried.joined(separator: "\n")
            + "\nSet its path in Settings, or HELIAD_CORE.")
    }

    static func expand(_ path: String, _ home: URL) -> String {
        if path == "~" { return home.path }
        if path.hasPrefix("~/") { return home.appendingPathComponent(String(path.dropFirst(2))).path }
        return path
    }
}
