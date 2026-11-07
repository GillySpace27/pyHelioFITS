// swift-tools-version:5.10
// HelioFITS Cache Manager, the Mac app. CacheManagerKit talks to HelioFITS
// Core, the Python core (hfcore serve), and holds the models; CacheManager is the SwiftUI app.
// Build with `swift build`, test with `swift test`; tools/make_app.sh wraps the binary as
// HelioFITS Cache Manager.app.
import PackageDescription

let package = Package(
    name: "CacheManager",
    platforms: [.macOS("14.5")],  // HelioFITSCore's floor
    products: [
        .executable(name: "CacheManager", targets: ["CacheManager"]),
        .library(name: "CacheManagerKit", targets: ["CacheManagerKit"]),
    ],
    dependencies: [
        // HelioFITS Peek's renderer (thumbnails, colormaps, colorbar, filters, limb), so library
        // files look in the Cache Manager exactly as they do in Finder and Quick Look. Follows a branch;
        // Package.resolved pins the exact commit, and .github/workflows/follow-peek.yml opens a
        // PR when Peek moves. Peek PR #82 put the root package on main (2026-10-21).
        .package(url: "https://github.com/GillySpace27/HelioFITS-Peek.git",
                 branch: "main"),
    ],
    targets: [
        .target(name: "CacheManagerKit"),
        .executableTarget(name: "CacheManager", dependencies: [
            "CacheManagerKit",
            .product(name: "HelioFITSCore", package: "HelioFITS-Peek"),
        ]),
        .testTarget(name: "CacheManagerKitTests", dependencies: ["CacheManagerKit"]),
    ]
)
