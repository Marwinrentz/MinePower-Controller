import Foundation

/// Ladeliste aus dem CSV-Export (`GET /api/statistics/export.csv`).
///
/// Format des Backends: Semikolon getrennt, Dezimalkomma, Kopfzeile
/// `ID;Start;Ende;Energie (kWh);Solar (kWh);Solaranteil (%);Kosten (EUR);Geraet;Nutzer;RFID`.
public struct ChargeList: Sendable, Equatable {
    public struct Row: Sendable, Equatable, Identifiable {
        public var id: Int
        public var start: Date?
        public var end: Date?
        public var energyKwh: Double
        public var solarKwh: Double
        public var costEur: Double
        public var device: String
        public var user: String
    }

    public var rows: [Row]

    public var totalEnergy: Double { rows.reduce(0) { $0 + $1.energyKwh } }
    public var totalSolar: Double { rows.reduce(0) { $0 + $1.solarKwh } }
    public var totalCost: Double { rows.reduce(0) { $0 + $1.costEur } }
    public var solarShare: Double { totalEnergy > 0 ? totalSolar / totalEnergy : 0 }

    public init(rows: [Row]) { self.rows = rows }

    public init(csv: String) {
        var rows: [Row] = []
        let lines = csv.split(whereSeparator: \.isNewline).map(String.init)
        for line in lines.dropFirst() where !line.trimmingCharacters(in: .whitespaces).isEmpty {
            let cols = Self.split(line)
            guard cols.count >= 7, let id = Int(cols[0]) else { continue }
            rows.append(Row(
                id: id,
                start: BackendDate.parse(cols[1]),
                end: cols[2].isEmpty ? nil : BackendDate.parse(cols[2]),
                energyKwh: Self.number(cols[3]),
                solarKwh: Self.number(cols[4]),
                costEur: Self.number(cols[6]),
                device: cols.count > 7 ? cols[7] : "",
                user: cols.count > 8 ? cols[8] : ""
            ))
        }
        self.rows = rows
    }

    static func number(_ text: String) -> Double {
        Double(text.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: ",", with: ".")) ?? 0
    }

    /// Semikolon-getrennt mit Anführungszeichen-Feldern (Python `csv`).
    static func split(_ line: String) -> [String] {
        var out: [String] = []
        var current = ""
        var quoted = false
        var iterator = line.makeIterator()
        while let ch = iterator.next() {
            if ch == "\"" {
                quoted.toggle()
            } else if ch == ";" && !quoted {
                out.append(current)
                current = ""
            } else {
                current.append(ch)
            }
        }
        out.append(current)
        return out
    }
}
