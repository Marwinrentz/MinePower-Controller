import XCTest

/// Screenshots aller Bereiche mit der eingebauten Demo-Anlage.
///
/// Läuft in der CI auf iPhone und iPad, je Hell/Dunkel, große Schrift und
/// Querformat. Jedes Bild wird als Anhang gespeichert (`keepAlways`); die CI
/// exportiert sie aus dem Ergebnisbündel als Artefakt `screenshots`
/// (`ios/tools/preview.sh` holt sie ab).
///
/// Namensschema: `<gerät>_<variante>_<bereich>.png`, z. B. `iphone_dark_car`.
@MainActor
final class ScreenshotTests: XCTestCase {
    enum Variant: String, CaseIterable {
        case light, dark, largeText = "large", landscape
    }

    static let sections = ["dashboard", "car", "water", "battery", "tariff", "statistics", "diagnose", "settings", "changelog"]

    override func setUp() async throws {
        continueAfterFailure = true
    }

    override func tearDown() async throws {
        XCUIDevice.shared.orientation = .portrait
    }

    func testLight() throws { try capture(.light) }
    func testDark() throws { try capture(.dark) }
    func testLargeText() throws { try capture(.largeText, sections: ["dashboard", "car", "settings"]) }
    func testLandscape() throws { try capture(.landscape, sections: ["dashboard", "statistics"]) }

    func testWallMode() throws {
        let app = launch(variant: .landscape, extra: ["-UITestWall"])
        XCUIDevice.shared.orientation = .landscapeLeft
        XCTAssertTrue(app.otherElements["wall"].waitForExistence(timeout: 10))
        sleep(2)
        attach(name: "wall")
    }

    func testNightAndAttentionScenarios() throws {
        for scenario in ["night", "attention"] {
            let app = launch(variant: .light, scenario: scenario)
            XCTAssertTrue(app.wait(for: .runningForeground, timeout: 10))
            sleep(2)
            attach(name: "light_dashboard_\(scenario)")
            app.terminate()
        }
    }

    func testConnectScreen() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-UITest"]
        app.launch()
        XCTAssertTrue(app.buttons["demo"].waitForExistence(timeout: 10))
        attach(name: "light_connect")
    }

    // MARK: - Hilfen

    private func capture(_ variant: Variant, sections: [String] = sections) throws {
        for section in sections {
            let app = launch(variant: variant, extra: ["-UITestSection", section])
            if variant == .landscape { XCUIDevice.shared.orientation = .landscapeLeft }
            XCTAssertTrue(app.wait(for: .runningForeground, timeout: 10))
            // Live-Werte und Diagramme haben ihre erste Animation hinter sich.
            sleep(2)
            attach(name: "\(variant.rawValue)_\(section)")
            app.terminate()
        }
    }

    private func launch(variant: Variant, scenario: String = "sunny", extra: [String] = []) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["-UITestDemo", scenario] + extra
        if variant == .dark {
            app.launchArguments += ["-UITestAppearance", "dark"]
        }
        if variant == .largeText {
            app.launchArguments += ["-UITestTextSize", "large"]
        }
        app.launchArguments += ["-AppleLanguages", "(de)", "-AppleLocale", "de_DE"]
        app.launch()
        return app
    }

    private func attach(name: String) {
        let device = UIDevice.current.userInterfaceIdiom == .pad ? "ipad" : "iphone"
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = "\(device)_\(name)"
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
