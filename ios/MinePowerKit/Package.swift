// swift-tools-version: 6.0
import PackageDescription

/// MinePowerKit: alles, was nicht SwiftUI ist.
///
/// Modelle und Dekodierung, REST-Client, WebSocket, Formatierung, Kompatibilität
/// App ↔ Server und die Logik des Einrichtungsassistenten. Ohne SwiftUI, damit
/// es unter Linux baut und getestet wird (`swift test`) – genau die Schicht, in
/// der Fehler sonst als "App zeigt nichts an" auffallen.
let package = Package(
    name: "MinePowerKit",
    defaultLocalization: "de",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "MinePowerKit", targets: ["MinePowerKit"]),
    ],
    targets: [
        .target(
            name: "MinePowerKit",
            // Beispielanlage für Demo-Modus, App-Prüfung und UI-Tests.
            resources: [.copy("Resources/Demo")],
            swiftSettings: [.enableUpcomingFeature("ExistentialAny")]
        ),
        .testTarget(
            name: "MinePowerKitTests",
            dependencies: ["MinePowerKit"],
            resources: [.copy("Fixtures")]
        ),
    ],
    swiftLanguageModes: [.v6]
)
