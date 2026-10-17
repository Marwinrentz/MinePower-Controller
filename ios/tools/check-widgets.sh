#!/usr/bin/env bash
# Widget-Erweiterung ohne Mac übersetzen (nur Typprüfung, kein Linken der App).
#
# Baut ein Hilfspaket aus Widgets/*.swift und den mitgenutzten App-Dateien
# (siehe Ziel MinePowerWidgets in project.yml) gegen das iOS-SDK von xtool.
#
#   ios/tools/check-widgets.sh
set -euo pipefail
IOS="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/Sources/WidgetCheck"
cat > "$WORK/Package.swift" <<PKG
// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "WidgetCheck", platforms: [.iOS(.v17)],
    products: [.library(name: "WidgetCheck", targets: ["WidgetCheck"])],
    dependencies: [.package(path: "$IOS/MinePowerKit")],
    targets: [.target(name: "WidgetCheck", dependencies: [.product(name: "MinePowerKit", package: "MinePowerKit")],
                      path: "Sources/WidgetCheck", resources: [.copy("Fonts")],
                      swiftSettings: [.enableUpcomingFeature("ExistentialAny")])],
    swiftLanguageModes: [.v6])
PKG
for f in "$IOS"/Widgets/*.swift "$IOS"/Sources/MinePower/Shared/*.swift \
         "$IOS"/Sources/MinePower/Design/{Theme,Typography,WebIcon,WebIconPaths,BrandMark}.swift \
         "$IOS"/Sources/MinePower/Core/KeychainStore.swift; do
  ln -s "$f" "$WORK/Sources/WidgetCheck/"
done
ln -s "$IOS/Sources/MinePower/Resources/Fonts" "$WORK/Sources/WidgetCheck/Fonts"
export DEVELOPER_DIR="${DEVELOPER_DIR:-$HOME/.swiftpm/swift-sdks/darwin.artifactbundle/Developer}"
cd "$WORK"
swift build --swift-sdk darwin --triple arm64-apple-ios --build-system native 2>&1 \
  | grep -v "build-system native' has been deprecated" | tail -20
