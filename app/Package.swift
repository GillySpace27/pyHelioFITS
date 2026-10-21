// swift-tools-version:5.10
// The Heliad Mac app. HeliadKit talks to the Python core (heliad serve) and holds
// the models; Heliad is the SwiftUI app. Build with `swift build`, test with
// `swift test`; tools/make_app.sh wraps the binary as Heliad.app.
import PackageDescription

let package = Package(
    name: "Heliad",
    platforms: [.macOS("14.5")],  // HelioFITSCore's floor
    products: [
        .executable(name: "Heliad", targets: ["Heliad"]),
        .library(name: "HeliadKit", targets: ["HeliadKit"]),
    ],
    dependencies: [
        // HelioFITS Peek's renderer (thumbnails, colormaps, colorbar, filters, limb), so library
        // files look in Heliad exactly as they do in Finder and Quick Look. Follows a branch;
        // Package.resolved pins the exact commit, and .github/workflows/follow-peek.yml opens a
        // PR when Peek moves. The branch becomes "main" once Peek PR #82 (the root package) merges.
        .package(url: "https://github.com/GillySpace27/HelioFITS-Peek.git",
                 branch: "claude/shared-core-root-package"),
    ],
    targets: [
        .target(name: "HeliadKit"),
        .executableTarget(name: "Heliad", dependencies: [
            "HeliadKit",
            .product(name: "HelioFITSCore", package: "HelioFITS-Peek"),
        ]),
        .testTarget(name: "HeliadKitTests", dependencies: ["HeliadKit"]),
    ]
)
