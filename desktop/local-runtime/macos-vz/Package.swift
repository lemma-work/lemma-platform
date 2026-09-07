// swift-tools-version: 5.9
import Foundation
import PackageDescription

let identity = URL(fileURLWithPath: #filePath)
    .deletingLastPathComponent().appendingPathComponent("Info.plist").path

let package = Package(
    name: "LemmaVZ",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "lemma-vz", targets: ["LemmaVZ"]),
    ],
    targets: [
        .executableTarget(
            name: "LemmaVZ",
            linkerSettings: [.unsafeFlags([
                "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT",
                "-Xlinker", "__info_plist", "-Xlinker", identity,
            ])]
        ),
    ]
)
