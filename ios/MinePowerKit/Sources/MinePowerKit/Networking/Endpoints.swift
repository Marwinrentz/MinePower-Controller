import Foundation

/// Alle Endpunkte der MinePower-REST-API an einer Stelle.
///
/// Spiegel von `backend/app/api/*.py`. Rollen prüft das Backend; die App
/// blendet Bedienelemente zusätzlich nach `User.Role` aus.
public extension APIClient {
    // MARK: System (ohne Anmeldung)

    func health() async throws -> Health { try await get("api/system/health") }
    func setupState() async throws -> SetupState { try await get("api/system/setup-state") }

    // MARK: Anmeldung

    func login(email: String, password: String) async throws -> TokenResponse {
        try await send(.post, "api/auth/login", body: LoginRequest(email: email, password: password))
    }

    func createFirstAdmin(_ request: SetupRequest) async throws -> TokenResponse {
        try await send(.post, "api/auth/setup", body: request)
    }

    func me() async throws -> User { try await get("api/auth/me") }
    func refreshToken() async throws -> TokenResponse { try await send(.post, "api/auth/refresh", body: Optional<Never>.none) }
    func markChangelogSeen() async throws -> User { try await send(.post, "api/auth/me/seen", body: Optional<Never>.none) }

    func users() async throws -> [User] { try await get("api/auth/users") }
    func createUser(_ user: UserCreate) async throws -> User { try await send(.post, "api/auth/users", body: user) }
    func updateUser(_ id: Int, _ update: UserUpdate) async throws -> User {
        try await send(.patch, "api/auth/users/\(id)", body: update)
    }
    func deleteUser(_ id: Int) async throws { try await perform(.delete, "api/auth/users/\(id)") }

    // MARK: Live

    func liveStatus() async throws -> Snapshot { try await get("api/status/live") }
    func pause() async throws { try await perform(.post, "api/status/pause") }
    func resume() async throws { try await perform(.post, "api/status/resume") }

    // MARK: Geräte

    func devices() async throws -> [Device] { try await get("api/devices") }
    func deviceLimits() async throws -> DeviceLimits { try await get("api/devices/limits") }
    func drivers(category: DeviceCategory? = nil) async throws -> [DriverMeta] {
        try await get("api/drivers", query: category.map { ["category": $0.rawValue] } ?? [:])
    }
    func createDevice(_ device: DeviceCreate) async throws -> Device { try await send(.post, "api/devices", body: device) }
    func updateDevice(_ id: Int, _ update: DeviceUpdate) async throws -> Device {
        try await send(.patch, "api/devices/\(id)", body: update)
    }
    func deleteDevice(_ id: Int) async throws { try await perform(.delete, "api/devices/\(id)") }
    func testConnection(_ request: TestConnectionRequest) async throws -> TestResult {
        try await send(.post, "api/devices/test", body: request)
    }
    func deviceAction(_ id: Int, _ action: DeviceAction) async throws {
        try await perform(.post, "api/devices/\(id)/action", body: action)
    }

    // MARK: Batterie

    func batteryStatus() async throws -> BatteryStatus { try await get("api/battery/status") }
    func batteryCommand(_ command: BatteryCommand) async throws -> BatteryStatus {
        try await send(.post, "api/battery/manual", body: command)
    }

    // MARK: Einstellungen

    func settings() async throws -> SettingsEnvelope { try await get("api/settings") }

    /// Einen Abschnitt speichern. Nicht genannte Schlüssel des Abschnitts bleiben
    /// erhalten, weil der vollständige Abschnitt gesendet wird; Geheimnisse als
    /// "•••" bleiben serverseitig unverändert.
    func saveSettings(_ sections: [String: JSONValue]) async throws {
        try await perform(.put, "api/settings", body: SettingsEnvelope(values: sections))
    }

    func schedules() async throws -> [Schedule] { try await get("api/settings/schedules") }
    func createSchedule(_ schedule: ScheduleCreate) async throws -> Schedule {
        try await send(.post, "api/settings/schedules", body: schedule)
    }
    func deleteSchedule(_ id: Int) async throws { try await perform(.delete, "api/settings/schedules/\(id)") }
    func setPriority(_ order: [Int]) async throws { try await perform(.put, "api/settings/priority", body: PriorityUpdate(order: order)) }

    // MARK: Fahrzeuge

    func vehicles() async throws -> [Vehicle] { try await get("api/vehicles") }
    func createVehicle(_ vehicle: VehicleCreate) async throws -> Vehicle { try await send(.post, "api/vehicles", body: vehicle) }
    func updateVehicle(_ id: Int, _ vehicle: VehicleCreate) async throws -> Vehicle {
        try await send(.patch, "api/vehicles/\(id)", body: vehicle)
    }
    func deleteVehicle(_ id: Int) async throws { try await perform(.delete, "api/vehicles/\(id)") }

    // MARK: Auswertung

    func statistics(_ range: StatsRange) async throws -> Statistics {
        try await get("api/statistics", query: ["range": range.rawValue])
    }

    /// Verlauf; `source` z. B. "device:4" für Werte eines einzelnen Geräts.
    func history(_ range: HistoryRange, fields: [String], source: String? = nil) async throws -> HistorySeries {
        var query = ["range": range.rawValue, "fields": fields.joined(separator: ",")]
        if let source { query["source"] = source }
        return try await get("api/history", query: query)
    }

    func sessions(limit: Int = 100) async throws -> [ChargeSession] {
        try await get("api/sessions", query: ["limit": String(limit)])
    }

    func sessionsCSV(_ range: StatsRange) async throws -> String {
        try await text("api/statistics/export.csv", query: ["range": range.rawValue])
    }

    func tariff() async throws -> TariffInfo { try await get("api/statistics/tariff") }
    func forecast() async throws -> Forecast { try await get("api/statistics/forecast") }

    // MARK: Diagnose

    func events(limit: Int = 100, category: String? = nil) async throws -> [AppEvent] {
        var query = ["limit": String(limit)]
        if let category { query["category"] = category }
        return try await get("api/events", query: query)
    }

    func findings(range: String = "7d") async throws -> FindingsReport {
        try await get("api/system/findings", query: ["range": range])
    }

    func diagnosticReport(range: String = "7d") async throws -> Data {
        try await rawData("api/system/diagnostic-report", query: ["range": range])
    }

    // MARK: System

    func changelog() async throws -> Changelog { try await get("api/system/changelog") }
    func backupStatus() async throws -> BackupStatus { try await get("api/system/backup-status") }
    func exportConfiguration() async throws -> Data { try await rawData("api/system/export") }
    func importConfiguration(_ json: Data) async throws { _ = try await sendRaw(.post, "api/system/import", json: json) }
    func enableDemo() async throws { try await perform(.post, "api/system/demo") }

    // MARK: KI-Assistent (MCP)

    func mcpTokens() async throws -> [McpToken] { try await get("api/mcp/tokens") }
    func createMcpToken(name: String, role: User.Role) async throws -> McpToken {
        struct Body: Encodable, Sendable {
            var name: String
            var role: String
        }
        return try await send(.post, "api/mcp/tokens", body: Body(name: name, role: role.rawValue))
    }
    func revokeMcpToken(_ id: Int) async throws { try await perform(.delete, "api/mcp/tokens/\(id)") }
    func mcpTools() async throws -> [McpTool] { try await get("api/mcp/tools") }
}
