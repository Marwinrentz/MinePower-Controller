import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import Testing
@testable import MinePowerKit

/// Eigene UserDefaults je Test, damit sich Tests nicht gegenseitig Server unterschieben.
func isolatedDefaults() -> UserDefaults {
    let name = "test.\(UUID().uuidString)"
    let defaults = UserDefaults(suiteName: name)!
    defaults.removePersistentDomain(forName: name)
    return defaults
}

@MainActor
func makeDemoSession(secrets: InMemorySecretStore = InMemorySecretStore(),
                     defaults: UserDefaults = isolatedDefaults()) -> AppSession {
    AppSession(secrets: secrets, defaults: defaults, registry: TrustRegistry()) { _ in URLSession.demo() }
}

@Suite("Serverliste")
@MainActor
struct ServerDirectoryTests {
    @Test func addDeduplicatesByAddressAndKeepsID() {
        let directory = ServerDirectory(defaults: isolatedDefaults())
        let url = URL(string: "http://192.168.1.5:8080")!
        let first = directory.add(ServerProfile(name: "Zuhause", url: url))
        let second = directory.add(ServerProfile(name: "Neu", url: url, fingerprint: "AB"))
        #expect(directory.profiles.count == 1)
        #expect(first.id == second.id)
        #expect(directory.active?.name == "Neu")
        #expect(directory.pins["192.168.1.5"] == "AB")
    }

    @Test func persistsAcrossInstances() {
        let defaults = isolatedDefaults()
        let directory = ServerDirectory(defaults: defaults)
        let a = directory.add(ServerProfile(name: "A", url: URL(string: "http://10.0.0.2:8080")!))
        directory.add(ServerProfile(name: "B", url: URL(string: "https://e.example.de")!), activate: false)
        let reloaded = ServerDirectory(defaults: defaults)
        #expect(reloaded.profiles.map(\.name) == ["A", "B"])
        #expect(reloaded.activeID == a.id)
    }

    @Test func removingActiveFallsBackToNext() {
        let directory = ServerDirectory(defaults: isolatedDefaults())
        let a = directory.add(ServerProfile(name: "A", url: URL(string: "http://10.0.0.2:8080")!))
        let b = directory.add(ServerProfile(name: "B", url: URL(string: "http://10.0.0.3:8080")!))
        directory.remove(b.id)
        #expect(directory.activeID == a.id)
        directory.remove(a.id)
        #expect(directory.active == nil)
    }

    @Test func migratesLegacySingleAddress() {
        let defaults = isolatedDefaults()
        defaults.set("192.168.20.5:8080", forKey: "server-url")
        let directory = ServerDirectory(defaults: defaults)
        let migrated = directory.migrateLegacy()
        #expect(migrated?.url.absoluteString == "http://192.168.20.5:8080")
        #expect(defaults.string(forKey: "server-url") == nil)
    }

    @Test func displayAddressHidesDefaultPorts() {
        #expect(ServerProfile(name: "x", url: URL(string: "https://e.example.de/mp")!).displayAddress == "e.example.de/mp")
        #expect(ServerProfile(name: "x", url: URL(string: "http://10.0.0.2:8080")!).displayAddress == "10.0.0.2:8080")
    }
}

@Suite("Ressource")
@MainActor
struct ResourceTests {
    @Test func keepsOldValueWhenReloadFails() async {
        let counter = Locked(0)
        let resource = Resource<Int> {
            let n = counter.withLock { $0 += 1; return $0 }
            if n > 1 { throw APIError.offline }
            return n
        }
        #expect(resource.phase == .loading)
        await resource.load()
        #expect(resource.value == 1)
        await resource.reload()
        #expect(resource.value == 1)
        #expect(resource.error == .offline)
        #expect(resource.phase == .loaded)
    }

    @Test func failedWithoutValue() async {
        let resource = Resource<Int> { throw APIError.forbidden }
        await resource.load()
        #expect(resource.phase == .failed)
    }

    @Test func maxAgeSkipsFreshValue() async {
        let counter = Locked(0)
        let resource = Resource<Int> { counter.withLock { $0 += 1; return $0 } }
        await resource.load()
        await resource.load(maxAge: 60)
        #expect(resource.value == 1)
    }
}

/// Alles, was den gemeinsamen Zustand von `DemoBackend.shared` berührt, läuft nacheinander.
@Suite("Mit Demo-Anlage", .serialized)
struct DemoBacked {}

extension DemoBacked {
    @Suite("Sitzung")
    @MainActor
    struct AppSessionTests {
        init() { DemoBackend.shared.reset() }

        @Test func launchStartsOnlyOnceForSeveralWindows() {
            let session = makeDemoSession()
            #expect(session.beginLaunch())
            #expect(!session.beginLaunch())
        }

        @Test func foregroundKeepsRunningConnection() async throws {
            let session = makeDemoSession()
            await session.startDemo()
            try await session.live.refresh()
            #expect(session.live.connection == .live)
            // Kontrollzentrum: aktiv → inaktiv → aktiv, ohne Hintergrund.
            await session.enterForeground()
            #expect(session.live.connection == .live)
            session.enterBackground()
            #expect(session.live.connection == .idle)
            await session.enterForeground()
            #expect(session.live.connection != .idle)
            session.live.stop()
        }

        @Test func freshInstallNeedsServer() async {
            let session = makeDemoSession()
            await session.launch()
            #expect(session.phase == .needsServer)
        }

        @Test func demoSignsInAndStreamsLiveValues() async throws {
            let session = makeDemoSession()
            await session.startDemo()
            #expect(session.phase == .signedIn)
            #expect(session.isDemo)
            #expect(session.user?.role == .admin)
            try await session.live.refresh()
            let snapshot = try #require(session.live.snapshot)
            #expect(snapshot.pv > 1000)
            #expect(session.live.connection == .live)
            session.live.stop()
        }

        @Test func signedOutWhenTokenMissing() async {
            let defaults = isolatedDefaults()
            let session = makeDemoSession(defaults: defaults)
            await session.startDemo()
            await session.signOut()
            #expect(session.phase == .signedOut)
            let relaunched = makeDemoSession(defaults: defaults)
            await relaunched.launch()
            #expect(relaunched.phase == .signedOut)
        }

        @Test func lockedWhenFaceIDCancelled() async throws {
            let secrets = InMemorySecretStore()
            let defaults = isolatedDefaults()
            let session = makeDemoSession(secrets: secrets, defaults: defaults)
            await session.startDemo()
            try await session.setBiometricLock(true)
            let key = try #require(session.server?.tokenKey)
            #expect(await secrets.isProtected(key))
            await secrets.setDenyProtectedReads(true)
            let relaunched = makeDemoSession(secrets: secrets, defaults: defaults)
            await relaunched.launch()
            #expect(relaunched.phase == .locked)
            await secrets.setDenyProtectedReads(false)
            await relaunched.unlock()
            #expect(relaunched.phase == .signedIn)
            relaunched.live.stop()
        }

        @Test func expiredTokenSignsOutWithNotice() async {
            let secrets = InMemorySecretStore()
            let defaults = isolatedDefaults()
            let session = makeDemoSession(secrets: secrets, defaults: defaults)
            await session.startDemo()
            session.live.stop()
            // Token verfälschen: Demo-Backend antwortet mit 401.
            try? await secrets.setSecret("abgelaufen", for: session.server!.tokenKey, protected: false)
            let relaunched = makeDemoSession(secrets: secrets, defaults: defaults)
            await relaunched.launch()
            #expect(relaunched.phase == .signedOut)
            #expect(relaunched.notice != nil)
        }

        @Test func compatibilityFromServerVersion() async {
            let session = makeDemoSession()
            await session.startDemo()
            await session.refreshServerInfo()
            #expect(session.serverVersion == "3.1.1")
            #expect(session.compatibility == .ok)
            #expect(session.supports(.mcp))
            session.live.stop()
        }
    }

    @Suite("Live-Speicher")
    @MainActor
    struct LiveStoreTests {
        init() { DemoBackend.shared.reset() }

        func startedStore() async -> (LiveStore, APIClient) {
            let api = APIClient(session: .demo(), baseURL: DemoBackend.baseURL, token: DemoBackend.token)
            let store = LiveStore()
            store.start(api: api, socket: nil, baseURL: DemoBackend.baseURL, token: DemoBackend.token, pollInterval: .seconds(60))
            try? await store.refresh()
            return (store, api)
        }

        @Test func deviceActionMarksPendingAndRefreshes() async throws {
            let (store, _) = await startedStore()
            let wallbox = try #require(store.snapshot?.devices(.wallbox).first)
            #expect(wallbox.mode == "pv_only")
            try await store.perform(.setMode("min_pv"), device: wallbox.id)
            #expect(store.pending.isEmpty)
            #expect(store.snapshot?.devices(.wallbox).first?.mode == "min_pv")
            store.stop()
        }

        @Test func pauseStopsLoads() async throws {
            let (store, _) = await startedStore()
            try await store.setPaused(true)
            #expect(store.snapshot?.isPaused == true)
            #expect(store.snapshot?.wallboxPower == 0)
            try await store.setPaused(false)
            #expect(store.snapshot?.isPaused == false)
            store.stop()
        }

        @Test func ageAndStaleness() {
            let store = LiveStore()
            #expect(store.isStale)
            store.inject(Snapshot())
            #expect(!store.isStale)
            #expect((store.age() ?? 99) < 1)
        }

        @Test func snapshotCallbackFires() async throws {
            let (store, _) = await startedStore()
            var received = 0
            store.onSnapshot = { _ in received += 1 }
            try await store.refresh()
            #expect(received >= 1)
            store.stop()
        }
    }

    @Suite("Demo-Anlage")
    struct DemoBackendTests {
        let api = APIClient(session: .demo(), baseURL: DemoBackend.baseURL, token: DemoBackend.token)

        init() { DemoBackend.shared.reset() }

        @Test func answersEveryScreenEndpoint() async throws {
            _ = try await api.health()
            _ = try await api.me()
            _ = try await api.devices()
            _ = try await api.deviceLimits()
            #expect(try await api.drivers(category: .wallbox).allSatisfy { $0.category == .wallbox })
            _ = try await api.batteryStatus()
            _ = try await api.settings()
            _ = try await api.schedules()
            _ = try await api.vehicles()
            _ = try await api.statistics(.week)
            let history = try await api.history(.day, fields: ["pv_power", "grid_power"])
            #expect((history["pv_power"]?.count ?? 0) > 100)
            #expect(try await api.sessions().count == 12)
            #expect(ChargeList(csv: try await api.sessionsCSV(.month)).rows.count == 12)
            #expect(try await api.tariff().prices.count == 24)
            _ = try await api.forecast()
            #expect(!(try await api.events()).isEmpty)
            _ = try await api.findings()
            _ = try await api.changelog()
            _ = try await api.backupStatus()
            _ = try await api.mcpTools()
            _ = try await api.users()
        }

        @Test func rejectsWrongToken() async {
            let stranger = APIClient(session: .demo(), baseURL: DemoBackend.baseURL, token: "falsch")
            await #expect(throws: APIError.unauthorized) { try await stranger.me() }
        }

        @Test func scenariosChangeTheFlow() {
            let demo = DemoBackend()
            demo.scenario = .sunny
            let sunny = demo.snapshot()
            #expect(sunny.pv > 6000)
            #expect(sunny.isExporting || sunny.isBalanced)
            demo.scenario = .night
            let night = demo.snapshot()
            #expect(night.pv == 0)
            #expect((night.battery?.power ?? 0) < 0)
            #expect(night.devices(.wallbox).first?.presence == .away)
            demo.scenario = .attention
            #expect(!demo.snapshot().offlineDevices.isEmpty)
        }

        @Test func settingsRoundTripKeepsSecretsMasked() async throws {
            try await api.saveSettings(["tariff": .object(["cheap_limit_ct": 18, "api_token": .string(secretMask)])])
            let settings = try await api.settings()
            #expect(settings.section("tariff").double("cheap_limit_ct") == 18)
            #expect(settings.section("tariff").string("api_token") == nil)
        }

        @Test func crudForSchedulesVehiclesAndTokens() async throws {
            let schedule = try await api.createSchedule(ScheduleCreate(deviceId: 3, daysMask: 31, startTime: "06:00", endTime: "07:00"))
            #expect(try await api.schedules().contains { $0.id == schedule.id })
            try await api.deleteSchedule(schedule.id)
            #expect(!(try await api.schedules().contains { $0.id == schedule.id }))
            let token = try await api.createMcpToken(name: "Claude", role: .user)
            #expect(token.token?.hasPrefix("mp_demo_") == true)
            #expect(try await api.mcpTokens().first?.token == nil)
        }
    }
}
