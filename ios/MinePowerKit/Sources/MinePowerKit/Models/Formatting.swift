import Foundation

/// Zahlen- und Zeitformate, gleich wie in der Weboberfläche (`lib/format.ts`).
///
/// Deutsch ist die Leitsprache: Dezimalkomma, Einheit mit Leerzeichen,
/// 24-h-Zeit. Alles über `FormatStyle` (Wertetypen, `Sendable`), damit die
/// Formate gefahrlos geteilt werden. `locale` ist injizierbar für Tests.
public struct Fmt: Sendable {
    public var locale: Locale

    public init(locale: Locale = .autoupdatingCurrent) { self.locale = locale }

    public static let de = Fmt(locale: Locale(identifier: "de_DE"))

    public func number(_ value: Double, digits: Int = 0) -> String {
        value.formatted(.number.precision(.fractionLength(digits)).locale(locale).grouping(.automatic))
    }

    /// Leistung: unter 1 kW in Watt, darüber in kW mit sinnvoller Stellenzahl.
    public func watts(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "–" }
        let abs = Swift.abs(value)
        if abs >= 10_000 { return "\(number(value / 1000, digits: 1)) kW" }
        if abs >= 1000 { return "\(number(value / 1000, digits: 2)) kW" }
        return "\(number(value.rounded())) W"
    }

    /// Nur die Zahl einer Leistung (für große Ziffern mit separater Einheit).
    public func wattsValue(_ value: Double) -> String {
        let abs = Swift.abs(value)
        if abs >= 10_000 { return number(value / 1000, digits: 1) }
        if abs >= 1000 { return number(value / 1000, digits: 2) }
        return number(value.rounded())
    }

    public func wattsUnit(_ value: Double) -> String { Swift.abs(value) >= 1000 ? "kW" : "W" }

    public func kwh(_ value: Double?, digits: Int = 1) -> String {
        guard let value, value.isFinite else { return "–" }
        return "\(number(value, digits: digits)) kWh"
    }

    public func cents(_ value: Double?, digits: Int = 1) -> String {
        guard let value, value.isFinite else { return "–" }
        return "\(number(value, digits: digits)) ct"
    }

    public func percent(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "–" }
        return "\(number(value.rounded())) %"
    }

    public func celsius(_ value: Double?, digits: Int = 0) -> String {
        guard let value, value.isFinite else { return "–" }
        return "\(number(value, digits: digits)) °C"
    }

    public func euro(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "–" }
        return value.formatted(.currency(code: "EUR").locale(locale))
    }

    public func kilograms(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "–" }
        return "\(number(value, digits: value < 10 ? 1 : 0)) kg"
    }

    /// Dauer aus Sekunden: '12 min", '1 h 05 min".
    public func duration(_ seconds: Double?) -> String {
        guard let seconds, seconds.isFinite else { return "–" }
        let minutes = max(0, Int((seconds / 60).rounded()))
        if minutes < 60 { return "\(minutes) min" }
        return "\(minutes / 60) h \(String(format: "%02d", minutes % 60)) min"
    }

    /// 'vor 40 s", 'vor 3 min", 'vor 2 h".
    public func ago(_ date: Date?, now: Date = Date()) -> String {
        guard let date else { return "–" }
        let s = max(0, Int(now.timeIntervalSince(date).rounded()))
        let de = locale.language.languageCode?.identifier != "en"
        if s < 60 { return de ? "vor \(s) s" : "\(s) s ago" }
        if s < 3600 { return de ? "vor \(s / 60) min" : "\(s / 60) min ago" }
        if s < 86_400 { return de ? "vor \(s / 3600) h" : "\(s / 3600) h ago" }
        return de ? "vor \(s / 86_400) d" : "\(s / 86_400) d ago"
    }

    public func clock(_ date: Date, timeZone: TimeZone = .autoupdatingCurrent) -> String {
        var style = Date.FormatStyle(date: .omitted, time: .shortened, locale: locale, timeZone: timeZone)
        style.calendar = Calendar(identifier: .gregorian)
        return date.formatted(style)
    }

    public func dateTime(_ date: Date, timeZone: TimeZone = .autoupdatingCurrent) -> String {
        date.formatted(Date.FormatStyle(date: .numeric, time: .shortened, locale: locale, timeZone: timeZone))
    }

    /// Kalenderdatum aus 'YYYY-MM-DD" (Changelog), ohne Zeitzonenverschiebung.
    public func calendarDate(_ iso: String) -> String {
        let parts = iso.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return iso }
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC") ?? .gmt
        guard let date = cal.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2])) else { return iso }
        return date.formatted(Date.FormatStyle(date: .numeric, time: .omitted, locale: locale, timeZone: cal.timeZone))
    }
}

/// Zeitstempel des Backends lesen.
///
/// Drei Formen kommen vor: `isoformat()` mit Mikrosekunden und Zeitzone (Live),
/// glatte Sekunden mit Zeitzone (Verlauf) und **ohne Zeitzone** (Ereignisse,
/// UTC gemeint). Ein Parser für nur eine Form lässt jeweils einen Teil der App
/// leer.
public enum BackendDate {
    public static func parse(_ raw: String) -> Date? {
        var text = raw.trimmingCharacters(in: .whitespaces)
        if text.hasSuffix("Z") == false, !hasZone(text) { text += "Z" }
        // Mikrosekunden (6 Stellen) auf Millisekunden kürzen: ISO8601FormatStyle
        // verarbeitet auf allen Plattformen sicher nur drei Nachkommastellen.
        if let dot = text.firstIndex(of: ".") {
            let afterDot = text.index(after: dot)
            let digitsEnd = text[afterDot...].firstIndex(where: { !$0.isNumber }) ?? text.endIndex
            let digits = text[afterDot..<digitsEnd]
            let ms = String(digits.prefix(3)).padding(toLength: 3, withPad: "0", startingAt: 0)
            text = String(text[..<dot]) + "." + ms + String(text[digitsEnd...])
        }
        if let date = try? Date(text, strategy: Date.ISO8601FormatStyle(includingFractionalSeconds: true)) {
            return date
        }
        return try? Date(text, strategy: Date.ISO8601FormatStyle())
    }

    private static func hasZone(_ text: String) -> Bool {
        // '+02:00" oder '-05:00" hinter der Uhrzeit (nicht das Minus im Datum)
        guard let tIndex = text.firstIndex(of: "T") else { return true }
        let time = text[tIndex...]
        return time.contains("+") || time.contains("-")
    }

    public static func format(_ date: Date) -> String {
        date.formatted(Date.ISO8601FormatStyle(includingFractionalSeconds: true))
    }
}

public extension JSONDecoder {
    /// Decoder, der alle Zeitformate des Backends versteht.
    static func minePower() -> JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let raw = try container.decode(String.self)
            guard let date = BackendDate.parse(raw) else {
                throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unlesbares Datum: \(raw)")
            }
            return date
        }
        return d
    }
}

public extension JSONEncoder {
    static func minePower() -> JSONEncoder {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .custom { date, encoder in
            var container = encoder.singleValueContainer()
            try container.encode(BackendDate.format(date))
        }
        e.outputFormatting = [.sortedKeys]
        return e
    }
}
