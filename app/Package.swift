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
        // files look in Heliad exactly as they do in Finder and Quick Look.
        .package(url: "https://github.com/GillySpace27/HelioFITS-Peek.git",
                 revision: "24531c0ec0c821b8f2105eac47a3e55f1bb920a7"),
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
