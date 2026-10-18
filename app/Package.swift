// swift-tools-version:5.10
// The Heliad Mac app. HeliadKit talks to the Python core (heliad serve) and holds
// the models; Heliad is the SwiftUI app. Build with `swift build`, test with
// `swift test`; tools/make_app.sh wraps the binary as Heliad.app.
import PackageDescription

let package = Package(
    name: "Heliad",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "Heliad", targets: ["Heliad"]),
        .library(name: "HeliadKit", targets: ["HeliadKit"]),
    ],
    targets: [
        .target(name: "HeliadKit"),
        .executableTarget(name: "Heliad", dependencies: ["HeliadKit"]),
        .testTarget(name: "HeliadKitTests", dependencies: ["HeliadKit"]),
    ]
)
