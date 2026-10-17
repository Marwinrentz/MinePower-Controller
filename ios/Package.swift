// swift-tools-version: 6.0
import PackageDescription

/// MinePower für iPhone und iPad.
///
/// Zwei Wege zum Bauen, eine Quelle:
/// - `xtool dev build` (Linux/macOS, SwiftPM): schneller Übersetzungstest und
///   Installation aufs eigene Gerät.
/// - Xcode über `project.yml` (XcodeGen, CI): App, Widget, Tests, Archiv für
///   TestFlight. Dort wird `Sources/MinePower` direkt ins App-Ziel übersetzt.
///
/// Alles ohne SwiftUI liegt in `MinePowerKit` und wird unter Linux getestet.
let package = Package(
    name: "MinePower",
    defaultLocalization: "de",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        // xtool erwartet genau ein Bibliotheksprodukt: die App.
        .library(name: "MinePower", targets: ["MinePower"]),
    ],
    dependencies: [
        .package(path: "MinePowerKit"),
    ],
    targets: [
        .target(
            name: "MinePower",
            dependencies: [.product(name: "MinePowerKit", package: "MinePowerKit")],
            path: "Sources/MinePower",
            // String-Kataloge übersetzt nur Xcode (xcstringstool gibt es unter
            // Linux nicht): xtool-Builds sind deutsch, Xcode/CI zweisprachig.
            exclude: ["Resources/Localizable.xcstrings", "Resources/InfoPlist.xcstrings", "Resources/AppShortcuts.xcstrings"],
            resources: [.copy("Resources/Fonts")],
            swiftSettings: [.enableUpcomingFeature("ExistentialAny")]
        ),
    ],
    swiftLanguageModes: [.v6]
)
