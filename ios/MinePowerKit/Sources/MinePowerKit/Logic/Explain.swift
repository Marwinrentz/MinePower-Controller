import Foundation

/// "Warum passiert gerade was?" – Spiegel von `frontend/src/lib/explain.ts`.
///
/// Aus den knappen technischen Begründungen des Regelkreises ("warte auf
/// Überschuss (0 W)") werden eine Überschrift und kurze Sätze in
/// Alltagssprache, mit den Zahlen, auf die es ankommt.
public struct Explanation: Sendable, Equatable {
    public enum Tone: String, Sendable {
        case pv, gridImport, gridExport, battery, car, water, warn, idle, error
    }

    public enum Symbol: String, Sendable {
        case pause, warning, battery, price, car, water, export
    }

    public struct Reason: Sendable, Equatable, Identifiable {
        public var id: String
        public var symbol: Symbol
        public var text: String
        public var tone: Tone
    }

    public var headline: String
    public var tone: Tone
    public var reasons: [Reason]

    public init(_ s: Snapshot, fmt: Fmt = Fmt()) {
        let car = s.allDevices.first { $0.category == .wallbox && $0.enabled }
        let water = s.allDevices.first { $0.category == .waterHeater && $0.enabled }
        let grid = s.gridPower ?? 0
        let bat = s.battery?.power ?? 0
        let carW = s.wallboxPower ?? 0
        let waterW = s.waterPower ?? 0
        let ownProgram = water.map(Self.ownProgram) ?? false

        // Überschrift
        var headline = Loc.t("Alles ruhig", "All quiet")
        var tone = Tone.idle
        if s.isPaused {
            (headline, tone) = (Loc.t("Regelung angehalten", "Control paused"), .warn)
        } else if s.safety != nil {
            (headline, tone) = (Loc.t("Sicherheitsstopp", "Safety stop"), .error)
        } else if carW > 50, waterW > 50 {
            headline = Loc.t("Auto und Warmwasser nutzen den Strom", "Car and hot water use the power")
            tone = grid > 300 ? .gridImport : .pv
        } else if carW > 50 {
            let fromGrid = grid > carW * 0.5
            headline = fromGrid ? Loc.t("Auto lädt aus dem Netz", "Car charging from the grid")
                : Loc.t("Auto lädt mit Sonnenstrom", "Car charging with solar")
            tone = fromGrid ? .gridImport : .car
        } else if waterW > 50 {
            let fromGrid = grid > waterW * 0.5
            headline = ownProgram ? Loc.t("Warmwasser heizt per Geräteprogramm", "Hot water heating by its own program")
                : fromGrid ? Loc.t("Warmwasser heizt aus dem Netz", "Hot water heating from the grid")
                : Loc.t("Warmwasser heizt mit Sonnenstrom", "Hot water heating with solar")
            tone = ownProgram ? .warn : fromGrid ? .gridImport : .water
        } else if s.batteryGridCharging == true {
            (headline, tone) = (Loc.t("Batterie lädt günstig aus dem Netz", "Battery charging cheaply from the grid"), .gridImport)
        } else if bat > 100 {
            (headline, tone) = (Loc.t("Sonne lädt die Batterie", "Sun is charging the battery"), .battery)
        } else if (s.wasteW ?? 0) > 150 {
            (headline, tone) = (Loc.t("Überschuss geht ins Netz", "Surplus goes to the grid"), .gridExport)
        } else if bat < -100 {
            (headline, tone) = (Loc.t("Haus läuft über die Batterie", "House runs on the battery"), .battery)
        } else if grid > 100 {
            (headline, tone) = (Loc.t("Haus bezieht Strom aus dem Netz", "House draws power from the grid"), .gridImport)
        } else if s.pv > 50 {
            (headline, tone) = (Loc.t("Sonne versorgt das Haus", "Sun powers the house"), .pv)
        }

        // Gründe
        var reasons: [Reason] = []
        if s.isPaused {
            reasons.append(.init(id: "paused", symbol: .pause, text: Loc.t("Regelung angehalten. Auto und Warmwasser aus.",
                                                                          "Control paused. Car and hot water off."), tone: .warn))
        }
        if let safety = s.safety {
            reasons.append(.init(id: "safety", symbol: .warning, text: safety, tone: .error))
        }
        if let manual = s.batteryManual {
            let what = Self.batteryAction(manual.intent ?? (manual.mode == "force_charge" ? "charge" : "discharge"))
            reasons.append(.init(id: "manual", symbol: .battery, text: Loc.t(
                "Batterie von Hand: \(what) – noch \(fmt.duration(manual.remainingS)).",
                "Battery manual: \(what) – \(fmt.duration(manual.remainingS)) left."), tone: .battery))
        } else if let plan = s.batteryPlan, plan.intent != "auto" {
            reasons.append(.init(id: "plan", symbol: .battery, text: Loc.t("Batterie", "Battery") + ": " + plan.reason,
                                 tone: plan.intent == "charge" ? .gridImport : .battery))
        }
        if let price = s.priceCt {
            let cheap = s.priceCheap == true
            let limit = fmt.cents(s.priceLimitCt)
            reasons.append(.init(id: "price", symbol: .price, text: cheap
                ? Loc.t("Strompreis \(fmt.cents(price)) unter Grenze \(limit)", "Price \(fmt.cents(price)) below limit \(limit)")
                : Loc.t("Strompreis \(fmt.cents(price)), Grenze \(limit)", "Price \(fmt.cents(price)), limit \(limit)"),
                tone: cheap ? .pv : .idle))
        }
        if let car {
            let sentence = Self.carSentence(car, fmt: fmt)
            reasons.append(.init(id: "car", symbol: .car, text: sentence.text, tone: sentence.tone))
        }
        if let water {
            let sentence = Self.waterSentence(water, fmt: fmt)
            reasons.append(.init(id: "water", symbol: .water, text: sentence.text, tone: sentence.tone))
        }
        // Gezeigt wird die aktuelle Einspeisung; `waste_w` (geglättet) ist nur der Auslöser.
        let exportW = max(0, -grid)
        if (s.wasteW ?? 0) > 150, exportW > 150 {
            let shown = [car?.name, water?.name].compactMap { $0 }
            let causes = s.wasteReason.map { Self.wasteCauses($0, skip: shown) } ?? []
            let power = fmt.watts(exportW)
            let list = causes.joined(separator: ", ")
            let text = causes.isEmpty
                ? Loc.t("\(power) gehen ins Netz.", "\(power) going to the grid.")
                : Loc.t("\(power) gehen ins Netz: \(list).", "\(power) going to the grid: \(list).")
            reasons.append(.init(id: "waste", symbol: .export, text: text, tone: .gridExport))
        }
        self.headline = headline
        self.tone = tone
        self.reasons = reasons
    }

    // MARK: - Auto

    public static func carSentence(_ d: SnapshotDevice, fmt: Fmt = Fmt()) -> (text: String, tone: Tone) {
        let state = CarState(d)
        let reason = d.reason ?? ""
        let power = d.values.double("power") ?? 0
        switch state {
        case .proxy: return (Loc.t("Auto: Gateway offline", "Car: gateway offline"), .error)
        case .offline: return (Loc.t("Auto: keine Verbindung.", "Car: no connection."), .error)
        case .unknown: return (Loc.t("Auto: keine Antwort seit Start", "Car: no response since start"), .idle)
        case .asleep: return (Loc.t("Auto schläft angesteckt", "Car asleep, plugged in"), .idle)
        case .away: return (Loc.t("Auto unterwegs.", "Car is away."), .idle)
        case .unplugged: return (Loc.t("Auto ist nicht angesteckt.", "Car is not plugged in."), .idle)
        case .complete: return (Loc.t("Auto: Ladegrenze erreicht", "Car: charge limit reached"), .idle)
        case .none: return ("", .idle)
        case .charging:
            if d.override == "fast" {
                return (Loc.t("Auto lädt sofort mit voller Leistung (\(fmt.watts(power))).",
                              "Car fast charging at full power (\(fmt.watts(power)))."), .car)
            }
            if reason.contains("Netzladen") {
                return (Loc.t("Auto lädt günstig aus dem Netz (\(fmt.watts(power))).",
                              "Car charging cheaply from the grid (\(fmt.watts(power)))."), .gridImport)
            }
            return (Loc.t("Auto lädt mit \(fmt.watts(power)) Sonnenstrom.", "Car charging with \(fmt.watts(power)) of solar."), .pv)
        case .waiting:
            if d.override == "stop" { return (Loc.t("Auto: Laden aus", "Car: charging off"), .idle) }
            if reason.contains("Vorrang") {
                return (Loc.t("Auto wartet: Batterie hat Vorrang", "Car waits: battery has priority"), .battery)
            }
            if let seconds = startIn(reason) {
                return (Loc.t("Auto startet in \(seconds) s.", "Car starts in \(seconds) s."), .pv)
            }
            if reason.contains("warte auf Überschuss") || reason.contains("Überschuss zu gering") {
                let minimum = fmt.watts(d.startThresholdW ?? d.minPowerW ?? 1400)
                let free = fmt.watts(max(0, wattsIn(reason) ?? 0))
                return (Loc.t("Auto wartet auf \(minimum) Überschuss (frei \(free))",
                              "Car waits for \(minimum) surplus (available \(free))"), .idle)
            }
            if reason.contains("nicht ladebereit") || reason.contains("keine Leistung") || reason.hasPrefix("state=") {
                return (Loc.t("Auto nimmt keinen Strom an", "Car does not accept power"), .warn)
            }
            if let common = commonReason(reason, car: true) { return common }
            return (reason.isEmpty ? Loc.t("Auto bereit", "Car ready") : Loc.t("Auto: \(reason)", "Car: \(reason)"), .idle)
        }
    }

    // MARK: - Warmwasser

    public static func waterSentence(_ d: SnapshotDevice, fmt: Fmt = Fmt()) -> (text: String, tone: Tone) {
        let reason = d.reason ?? ""
        let power = d.values.double("power") ?? 0
        let temp = d.temperatureC
        if !d.online { return (Loc.t("Warmwasser: keine Verbindung", "Hot water: no connection"), .error) }
        if ownProgram(d) {
            return (Loc.t("Warmwasser heizt per Geräteprogramm (\(fmt.watts(power))).",
                          "Hot water heating by its own program (\(fmt.watts(power)))."), .warn)
        }
        if d.boost == true {
            if let remaining = d.boostState?.remainingS, d.boostState?.endMode != "temp" {
                return (Loc.t("Warmwasser-Boost läuft noch \(fmt.duration(remaining)).",
                              "Hot water boost: \(fmt.duration(remaining)) left."), .water)
            }
            let target = fmt.celsius(d.boostState?.targetTempC ?? d.boostTempC)
            return (Loc.t("Warmwasser-Boost läuft bis \(target).", "Hot water boost until \(target)."), .water)
        }
        if reason.contains("Wärmepuffer voll") {
            return (Loc.t("Warmwasser: Wärmepuffer voll (\(fmt.celsius(temp)))", "Hot water: heat buffer full (\(fmt.celsius(temp)))"), .idle)
        }
        if reason.contains("Wärmepuffer") {
            return (Loc.t("Warmwasser: Wärmepuffer \(fmt.watts(power))", "Hot water: heat buffer \(fmt.watts(power))"), .pv)
        }
        if reason.contains("Zieltemperatur") {
            return (Loc.t("Warmwasser \(fmt.celsius(temp)), Ziel \(fmt.celsius(d.targetTempC))",
                          "Hot water \(fmt.celsius(temp)), target \(fmt.celsius(d.targetTempC))"), .idle)
        }
        if power > 50, reason.contains("Netzladen") {
            return (Loc.t("Warmwasser heizt günstig aus dem Netz (\(fmt.watts(power))).",
                          "Hot water heating cheaply from the grid (\(fmt.watts(power)))."), .gridImport)
        }
        if power > 50 {
            return (Loc.t("Warmwasser heizt mit \(fmt.watts(power)) Überschuss.",
                          "Hot water heating with \(fmt.watts(power)) of surplus."), .pv)
        }
        if reason.contains("Vorrang") {
            return (Loc.t("Warmwasser wartet: Batterie hat Vorrang", "Hot water waits: battery has priority"), .battery)
        }
        if let seconds = startIn(reason) {
            return (Loc.t("Warmwasser: Start in \(seconds) s", "Hot water: starts in \(seconds) s"), .pv)
        }
        if reason.contains("warte auf Überschuss") {
            return (Loc.t("Warmwasser wartet auf Überschuss", "Hot water waits for surplus"), .idle)
        }
        if d.mode == "off" { return (Loc.t("Warmwasser aus", "Hot water off"), .idle) }
        if let common = commonReason(reason, car: false) { return common }
        return (reason.isEmpty ? Loc.t("Warmwasser bereit", "Hot water ready")
                : Loc.t("Warmwasser: \(reason)", "Hot water: \(reason)"), .idle)
    }

    // MARK: - Hilfen

    static func ownProgram(_ d: SnapshotDevice) -> Bool {
        guard let mode = d.values.string("device_mode") else { return false }
        return !mode.isEmpty
    }

    public static func batteryAction(_ intent: String) -> String {
        switch intent {
        case "charge": Loc.t("Laden", "Charge")
        case "discharge": Loc.t("Entladen", "Discharge")
        case "no_discharge": Loc.t("Entladen sperren", "Block discharge")
        case "hold": Loc.t("Komplett sperren", "Block completely")
        default: Loc.t("Automatik", "Automatic")
        }
    }

    /// "warte auf Überschuss (1234 W)" → 1234
    static func wattsIn(_ reason: String) -> Double? {
        guard let match = reason.firstMatch(of: /\((-?\d+) W\)/) else { return nil }
        return Double(match.1)
    }

    /// "Überschuss reicht (4067 W) – Start in 40 s" → 40
    static func startIn(_ reason: String) -> Int? {
        guard let match = reason.firstMatch(of: /Start in (\d+) s/) else { return nil }
        return Int(match.1)
    }

    /// Begründungen, die Auto und Warmwasser teilen.
    static func commonReason(_ reason: String, car: Bool) -> (text: String, tone: Tone)? {
        let who = car ? Loc.t("Auto", "Car") : Loc.t("Warmwasser", "Hot water")
        if let m = reason.firstMatch(of: /^PV-Überschuss (\d+) W/) {
            let power = Fmt().watts(Double(m.1))
            return (Loc.t("\(who) schaltet ein (\(power) Überschuss).", "\(who) switching on (\(power) surplus)."), .pv)
        }
        if reason.hasPrefix("Netzladen") { return (Loc.t("\(who) startet mit günstigem Netzstrom.", "\(who) starting on cheap grid power."), .gridImport) }
        if let m = reason.firstMatch(of: /Phasenumschaltung → (\d)p/) {
            return (Loc.t("Auto: Phasen werden umgeschaltet (\(m.1)-phasig).", "Car: switching phases (\(m.1)-phase)."), .car)
        }
        if reason.contains("Stopp-Verzögerung") { return (Loc.t("\(who) stoppt gleich (zu wenig Überschuss).", "\(who) stops soon (not enough surplus)."), .idle) }
        if reason.contains("Wolkendurchzug") { return (Loc.t("\(who) überbrückt eine Wolke.", "\(who) is riding out a cloud."), .pv) }
        if reason.contains("außerhalb Zeitfenster") { return (Loc.t("\(who): außerhalb des Zeitfensters", "\(who): outside the time window"), .idle) }
        if reason.hasPrefix("Zeitfenster aktiv") { return (Loc.t("\(who): Zeitfenster aktiv", "\(who): time window active"), .water) }
        if reason.hasPrefix("deaktiviert") { return (Loc.t("\(who) deaktiviert", "\(who) disabled"), .idle) }
        if reason.hasPrefix("manuell gestoppt") { return (Loc.t("Auto: Laden aus", "Car: charging off"), .idle) }
        return nil
    }

    /// "Batterie lädt … Vorrang; Wallbox: warte auf Überschuss (4067 W)" → kurze Teilsätze.
    static func wasteCauses(_ raw: String, skip: [String]) -> [String] {
        var out: [String] = []
        for part in raw.split(separator: ";").map({ $0.trimmingCharacters(in: .whitespaces) }) where !part.isEmpty {
            if skip.contains(where: { part.hasPrefix("\($0):") || part == "\($0) offline" }) { continue }
            if part.hasPrefix("Batterie lädt"), part.contains("Vorrang") {
                out.append(Loc.t("Batterie hat Vorrang, nimmt nichts mehr auf", "battery has priority, takes no more"))
            } else if part.hasPrefix("Batterie entlädt") {
                out.append(Loc.t("Batterie entlädt (kein Überschuss)", "battery discharging (no surplus)"))
            } else if let m = part.firstMatch(of: /^(.+?): .*Start in (\d+) s/) {
                out.append(Loc.t("\(m.1) startet in \(m.2) s", "\(m.1) starts in \(m.2) s"))
            } else if let m = part.firstMatch(of: /^(.+?): warte auf Überschuss/) {
                out.append(Loc.t("\(m.1) wartet", "\(m.1) waiting"))
            } else if let m = part.firstMatch(of: /^(.+?): .*Zieltemperatur/) {
                out.append(Loc.t("\(m.1) am Ziel", "\(m.1) at target"))
            } else if let m = part.firstMatch(of: /^(.+?): .*(nicht ladebereit|keine Leistung|nicht angesteckt|unplugged)/.ignoresCase()) {
                out.append(Loc.t("\(m.1) nimmt nichts an", "\(m.1) takes nothing"))
            } else if let m = part.firstMatch(of: /^(.+?) offline$/) {
                out.append(Loc.t("\(m.1) offline", "\(m.1) offline"))
            } else if part.contains("Keine steuerbare Last") {
                out.append(Loc.t("kein steuerbares Gerät", "no controllable device"))
            } else if part.contains("Alle steuerbaren Lasten sind offline") {
                out.append(Loc.t("alle steuerbaren Geräte offline", "all controllable devices offline"))
            } else if part.contains("am Maximum") {
                out.append(Loc.t("alle Geräte am Maximum", "all devices at maximum"))
            } else {
                out.append(part.replacing(/\s*\(-?\d+ W\)/, with: ""))
            }
        }
        return out
    }
}

/// Zustand des Autos an der Wallbox (Spiegel von `carState()` im Web).
public enum CarState: String, Sendable {
    case none, offline, proxy, unknown, asleep, away, unplugged, charging, waiting, complete

    public init(_ d: SnapshotDevice?) {
        guard let d else { self = .none; return }
        switch d.presence {
        case .proxyOffline: self = .proxy
        case .offline: self = .offline
        case .unknown: self = .unknown
        case .asleepPlugged: self = .asleep
        case .away: self = .away
        case .unplugged: self = .unplugged
        case .charging: self = .charging
        case .complete: self = .complete
        case .plugged: self = .waiting
        case nil:
            // Ältere Backends ohne `presence`.
            if !d.online {
                let error = d.lastError?.lowercased() ?? ""
                self = error.contains("proxy") || error.contains("8080") ? .proxy : .offline
            } else if d.values.bool("vehicle_reachable") == false {
                self = .asleep
            } else {
                let raw = d.values.string("state") ?? "idle"
                let power = d.values.double("power") ?? 0
                if raw == "idle" || raw == "error" {
                    self = .unplugged
                } else if raw == "charging", power > 100 {
                    self = .charging
                } else if raw == "complete" {
                    self = .complete
                } else {
                    self = .waiting
                }
            }
        }
    }

    /// Kurzform für Karten und Widgets.
    public var label: String {
        switch self {
        case .asleep: Loc.t("Schläft angesteckt", "Asleep, plugged in")
        case .away: Loc.t("Unterwegs", "Away")
        case .charging: Loc.t("Lädt", "Charging")
        case .complete: Loc.t("Voll", "Full")
        case .none: "–"
        case .offline: Loc.t("Keine Verbindung", "No connection")
        case .proxy: Loc.t("Gateway offline", "Gateway offline")
        case .unknown: Loc.t("Unbekannt", "Unknown")
        case .unplugged: Loc.t("Nicht angesteckt", "Not plugged in")
        case .waiting: Loc.t("Angesteckt", "Plugged in")
        }
    }

    /// Ist ein Auto angesteckt (Sofortladen sinnvoll)?
    public var isPlugged: Bool { [.charging, .waiting, .asleep, .complete].contains(self) }
}
