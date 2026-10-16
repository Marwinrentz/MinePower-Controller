import Foundation

/// Energiefluss als Stern um das Haus: Jede Quelle und jede Last hat genau
/// eine Leitung zur Mitte, Richtung und Stärke ergeben sich aus der Leistung.
///
/// Bewusst keine Aufteilung "wie viel PV geht ins Auto": Die wäre geschätzt.
/// Gezeigt wird nur, was gemessen ist – und das ist auf einen Blick lesbar.
public struct EnergyFlow: Sendable, Equatable {
    public enum Node: String, Sendable, CaseIterable, Identifiable {
        case pv, grid, battery, car, water
        public var id: String { rawValue }
    }

    public enum Direction: Sendable, Equatable {
        /// Leistung fließt zum Haus (Erzeugung, Netzbezug, Batterie entlädt).
        case toHouse
        /// Leistung fließt vom Haus weg (Last, Einspeisung, Batterie lädt).
        case fromHouse
        case idle
    }

    public struct Branch: Sendable, Equatable, Identifiable {
        public var node: Node
        public var watts: Double
        public var direction: Direction
        public var id: Node { node }

        /// 0…1 für Linienstärke und Tempo (logarithmisch: 100 W sichtbar, 11 kW nicht übermächtig).
        public var intensity: Double {
            guard direction != .idle else { return 0 }
            let w = max(watts, 1)
            return min(1, max(0.12, log10(w / 30) / log10(12_000 / 30)))
        }
    }

    /// Unter dieser Leistung gilt eine Leitung als ruhig (Messrauschen).
    public static let threshold: Double = 30

    public var branches: [Branch]
    /// Verbrauch des Hauses ohne Auto, Warmwasser und Batterie.
    public var houseWatts: Double

    public init(_ s: Snapshot) {
        houseWatts = max(0, s.house)
        var list: [Branch] = []
        let has = { (category: DeviceCategory) in s.enabledDevices.contains { $0.category == category } }

        if has(.inverter) || s.pv > Self.threshold || s.allDevices.isEmpty {
            list.append(Self.branch(.pv, s.pv, positive: .toHouse))
        }
        let grid = s.gridPower ?? 0
        list.append(Branch(node: .grid, watts: abs(grid),
                           direction: abs(grid) < Self.threshold ? .idle : grid > 0 ? .toHouse : .fromHouse))
        if let battery = s.battery, battery.soc != nil || battery.power != nil || has(.battery) {
            let power = battery.power ?? 0
            list.append(Branch(node: .battery, watts: abs(power),
                               direction: abs(power) < Self.threshold ? .idle : power > 0 ? .fromHouse : .toHouse))
        }
        if has(.wallbox) { list.append(Self.branch(.car, s.wallboxPower ?? 0, positive: .fromHouse)) }
        if has(.waterHeater) { list.append(Self.branch(.water, s.waterPower ?? 0, positive: .fromHouse)) }
        branches = list
    }

    public func branch(_ node: Node) -> Branch? { branches.first { $0.node == node } }

    private static func branch(_ node: Node, _ watts: Double, positive: Direction) -> Branch {
        Branch(node: node, watts: max(0, watts), direction: watts < threshold ? .idle : positive)
    }

    /// Kurzer Satz für VoiceOver: "Sonne 7,2 kW zum Haus, Auto 3,45 kW, …".
    public func accessibilitySummary(fmt: Fmt = Fmt()) -> String {
        var parts = [Loc.t("Haus verbraucht \(fmt.watts(houseWatts))", "House uses \(fmt.watts(houseWatts))")]
        for b in branches where b.direction != .idle {
            let w = fmt.watts(b.watts)
            switch (b.node, b.direction) {
            case (.pv, _): parts.append(Loc.t("Sonne liefert \(w)", "Solar provides \(w)"))
            case (.grid, .toHouse): parts.append(Loc.t("Netzbezug \(w)", "Grid import \(w)"))
            case (.grid, _): parts.append(Loc.t("Einspeisung \(w)", "Grid export \(w)"))
            case (.battery, .toHouse): parts.append(Loc.t("Batterie entlädt \(w)", "Battery discharging \(w)"))
            case (.battery, _): parts.append(Loc.t("Batterie lädt \(w)", "Battery charging \(w)"))
            case (.car, _): parts.append(Loc.t("Auto lädt \(w)", "Car charging \(w)"))
            case (.water, _): parts.append(Loc.t("Warmwasser heizt \(w)", "Hot water heating \(w)"))
            }
        }
        return parts.joined(separator: ", ")
    }
}
