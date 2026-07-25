// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "LemmaVZ",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "lemma-vz", targets: ["LemmaVZ"]),
    ],
    targets: [
        .executableTarget(name: "LemmaVZ"),
    ]
)
