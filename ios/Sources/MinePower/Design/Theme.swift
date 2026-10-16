import MinePowerKit
import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

/// Farben der Weboberfläche (`frontend/src/theme.css`), Hell und Dunkel.
///
/// Jede Energiefarbe steht für genau einen Pfad: Türkis ist immer das Auto,
/// Violett immer das Warmwasser. Wer das im Flussbild gelernt hat, findet es
/// in Statistik, Widget und Wandanzeige wieder.
///
/// `fill` ist für Flächen, Linien und Symbole (Kontrast ≥ 3:1 genügt dort).
/// Für farbigen **Text** gibt es `ink`, im Hellen dunkler gezogen, damit auch
/// kleine Schrift 4,5:1 erreicht.
struct DomainColor: Sendable {
    let fill: Color
    let ink: Color
    let soft: Color

    init(light: UInt32, dark: UInt32, inkLight: UInt32) {
        fill = Color(light: light, dark: dark)
        ink = Color(light: inkLight, dark: dark)
        soft = Color(light: light, dark: dark, opacity: 0.14)
    }
}

enum Palette {
    static let pv = DomainColor(light: 0x16A34A, dark: 0x34D399, inkLight: 0x15803D)
    static let gridImport = DomainColor(light: 0xDC4A47, dark: 0xF87171, inkLight: 0xB91C1C)
    static let gridExport = DomainColor(light: 0x2F76E8, dark: 0x60A5FA, inkLight: 0x1D4ED8)
    static let battery = DomainColor(light: 0xD39222, dark: 0xFBBF24, inkLight: 0xA15C07)
    static let car = DomainColor(light: 0x0B9DB0, dark: 0x22D3EE, inkLight: 0x0E7490)
    static let water = DomainColor(light: 0x8B5CF6, dark: 0xA78BFA, inkLight: 0x6D28D9)
    static let house = DomainColor(light: 0x748196, dark: 0x94A3B8, inkLight: 0x475569)
    static let waste = DomainColor(light: 0xD97706, dark: 0xFB923C, inkLight: 0xB45309)

    static let ok = DomainColor(light: 0x16A34A, dark: 0x34D399, inkLight: 0x15803D)
    static let warn = DomainColor(light: 0xC26D00, dark: 0xFBBF24, inkLight: 0xA15C07)
    static let error = DomainColor(light: 0xD33232, dark: 0xF87171, inkLight: 0xB91C1C)
    static let info = DomainColor(light: 0x2F76E8, dark: 0x60A5FA, inkLight: 0x1D4ED8)

    /// Markenakzent (Knöpfe, Auswahl). Im Hellen dunkelgrün wie im Web (#0f7a4d).
    static let accent = Color(light: 0x0F7A4D, dark: 0x3DDC97)

    static func category(_ category: DeviceCategory) -> DomainColor {
        switch category {
        case .wallbox: car
        case .waterHeater: water
        case .battery: battery
        case .inverter: pv
        case .meter: gridExport
        case .unknown: house
        }
    }
}

/// Marke: Verlauf Grün → Cyan aus dem Logo.
enum Brand {
    static let green = Color(rgb: 0x22C55E)
    static let cyan = Color(rgb: 0x0EA5B7)
    static let gradient = LinearGradient(colors: [green, cyan], startPoint: .topLeading, endPoint: .bottomTrailing)
    static let claim = String(localized: "Dein E-Auto lädt, wenn die Sonne scheint.")
}

/// Abstände in 4er-Schritten wie im Web (`--s1` … `--s10`).
enum Space {
    static let xs: CGFloat = 4
    static let s: CGFloat = 8
    static let m: CGFloat = 12
    static let l: CGFloat = 16
    static let xl: CGFloat = 20
    static let xxl: CGFloat = 24
    static let section: CGFloat = 32
}

enum Radius {
    static let small: CGFloat = 10
    static let card: CGFloat = 22
    static let large: CGFloat = 28
}

extension Color {
    /// Farbe mit eigenem Wert für Hell und Dunkel.
    init(light: UInt32, dark: UInt32, opacity: Double = 1) {
        #if canImport(UIKit)
        self.init(uiColor: UIColor { traits in
            UIColor(rgb: traits.userInterfaceStyle == .dark ? dark : light, alpha: opacity)
        })
        #else
        self.init(rgb: light, opacity: opacity)
        #endif
    }

    init(rgb: UInt32, opacity: Double = 1) {
        self.init(.sRGB, red: Double((rgb >> 16) & 0xFF) / 255, green: Double((rgb >> 8) & 0xFF) / 255,
                  blue: Double(rgb & 0xFF) / 255, opacity: opacity)
    }
}

#if canImport(UIKit)
extension UIColor {
    convenience init(rgb: UInt32, alpha: Double = 1) {
        self.init(red: CGFloat((rgb >> 16) & 0xFF) / 255, green: CGFloat((rgb >> 8) & 0xFF) / 255,
                  blue: CGFloat(rgb & 0xFF) / 255, alpha: alpha)
    }
}
#endif
