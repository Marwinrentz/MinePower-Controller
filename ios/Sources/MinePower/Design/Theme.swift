import MinePowerKit
import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

/// Farben exakt wie die Weboberfläche (`frontend/src/theme.css`), Hell und Dunkel.
///
/// Jede Energiefarbe steht für genau einen Pfad: Türkis ist immer das Auto,
/// Violett immer das Warmwasser. `fill` ist der Webwert (Flächen, Linien,
/// Symbole); `ink` ist für kleinen farbigen Text im Hellen etwas dunkler
/// gezogen, damit er 4,5:1 erreicht; `soft` entspricht `color-mix(… 15 %)`.
struct DomainColor: Sendable {
    let fill: Color
    let ink: Color
    let soft: Color

    init(light: UInt32, dark: UInt32, inkLight: UInt32? = nil) {
        fill = Color(light: light, dark: dark)
        ink = Color(light: inkLight ?? light, dark: dark)
        soft = Color(light: light, dark: dark, opacity: 0.15)
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

    /// `--accent`: Knöpfe, Auswahl, Verweise.
    static let accent = Color(light: 0x0F7A4D, dark: 0x3DDC97)

    static func category(_ category: DeviceCategory) -> DomainColor {
        switch category {
        case .wallbox: car
        case .waterHeater: water
        case .battery: battery
        case .inverter: pv
        case .meter: gridImport
        case .unknown: house
        }
    }
}

/// Neutrale Flächen und Grafikfarben der Weboberfläche.
enum Web {
    static let bg = Color(light: 0xF3F5F8, dark: 0x0D1015)
    static let surface = Color(light: 0xFFFFFF, dark: 0x161B23)
    static let surface2 = Color(light: 0xF7F9FB, dark: 0x1B212A)
    static let surface3 = Color(light: 0xEDF1F5, dark: 0x232A35)
    static let line = Color(light: 0xE1E7EE, dark: 0x262E3A)
    static let lineStrong = Color(light: 0xC9D3DE, dark: 0x364152)
    static let text = Color(light: 0x3D4A5A, dark: 0xB9C4D3)
    static let textStrong = Color(light: 0x0F172A, dark: 0xF2F5F9)
    static let textDim = Color(light: 0x64748B, dark: 0x8592A5)
    static let accentSoft = Color(light: 0xE4F3EA, dark: 0x3DDC97, darkOpacity: 0.14)
    static let accentText = Color(light: 0xFFFFFF, dark: 0x05261A)

    // Grafiken (Garage, Boiler)
    static let artGlassA = Color(light: 0xCFDCEC, dark: 0x2F3D4E)
    static let artGlassB = Color(light: 0xB3C6DD, dark: 0x1F2935)
    static let artMetal = Color(light: 0xE8EDF3, dark: 0x394454)
    static let artTyre = Color(light: 0x3B4655, dark: 0x070A0E)
    static let artLine = Color(light: 0x7D8EA3, dark: 0x6B7B8F)
    static let carBody = Color(light: 0xDFE6EE, dark: 0x2A3441)
    static let carBodyHi = Color(light: 0xF6F8FB, dark: 0x3A4656)
    static let garageBg = Color(light: 0xEEF2F6, dark: 0x141A22)
    static let garageLight = Color(light: 0xFFECAA, dark: 0xFFE28C, lightOpacity: 0.35, darkOpacity: 0.10)
    static let waterCold = Color(light: 0x3B82F6, dark: 0x60A5FA)
    static let waterWarm = Color(light: 0xA855F7, dark: 0xC084FC)
    static let waterHot = Color(light: 0xE5484D, dark: 0xF87171)
    static let boilerHi = Color(light: 0xFFFFFF, dark: 0x3A4656)
    static let boilerShade = Color(light: 0xD9E1EA, dark: 0x1C232D)
    static let boilerEmpty = Color(light: 0xEEF2F6, dark: 0x10151C)

    /// `--bg-grad`: zwei weiche Farbwolken oben (grünlich rechts, bläulich links).
    static let gradGreen = Color(light: 0xE5F2EA, dark: 0x13241B)
    static let gradBlue = Color(light: 0xE8EEF7, dark: 0x131A25)

    /// `--shadow-sm` / `--shadow-md`
    static let shadow = Color(light: 0x0F172A, dark: 0x000000, lightOpacity: 0.07, darkOpacity: 0.4)
}

/// Marke: Verlauf Grün → Cyan aus dem Logo.
enum Brand {
    static let green = Color(rgb: 0x22C55E)
    static let cyan = Color(rgb: 0x0EA5B7)
    static let gradient = LinearGradient(colors: [green, cyan], startPoint: .topLeading, endPoint: .bottomTrailing)
    static let claim = String(localized: "Dein E-Auto lädt, wenn die Sonne scheint.")
}

/// Abstände wie im Web (`--s1` … `--s10`).
enum Space {
    static let xs: CGFloat = 4
    static let s: CGFloat = 8
    static let m: CGFloat = 12
    static let l: CGFloat = 16
    static let xl: CGFloat = 20
    static let xxl: CGFloat = 24
    static let section: CGFloat = 32
}

/// Radien wie im Web (`--r-sm` … `--r-xl`).
enum Radius {
    static let small: CGFloat = 8
    static let medium: CGFloat = 12
    static let large: CGFloat = 16
    static let card: CGFloat = 20
}

extension Color {
    /// Farbe mit eigenem Wert für Hell und Dunkel.
    init(light: UInt32, dark: UInt32, opacity: Double = 1) {
        self.init(light: light, dark: dark, lightOpacity: opacity, darkOpacity: opacity)
    }

    init(light: UInt32, dark: UInt32, lightOpacity: Double = 1, darkOpacity: Double) {
        #if canImport(UIKit)
        self.init(uiColor: UIColor { traits in
            traits.userInterfaceStyle == .dark
                ? UIColor(rgb: dark, alpha: darkOpacity) : UIColor(rgb: light, alpha: lightOpacity)
        })
        #else
        self.init(rgb: light, opacity: lightOpacity)
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

/// Seitenhintergrund wie `body` im Web: Grundfarbe plus zwei Farbwolken oben.
struct AppBackground: View {
    var body: some View {
        GeometryReader { geo in
            ZStack {
                Web.bg
                RadialGradient(colors: [Web.gradGreen, Web.gradGreen.opacity(0)], center: UnitPoint(x: 0.85, y: -0.1),
                               startRadius: 0, endRadius: max(geo.size.width, 600) * 0.75)
                RadialGradient(colors: [Web.gradBlue, Web.gradBlue.opacity(0)], center: .topLeading,
                               startRadius: 0, endRadius: max(geo.size.width, 500) * 0.6)
            }
        }
        .ignoresSafeArea()
    }
}
