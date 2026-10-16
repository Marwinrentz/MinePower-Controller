import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import Testing
@testable import MinePowerKit

/// Transport-Mock: beantwortet Anfragen ohne Netz und merkt sich die letzte.
final class MockURLProtocol: URLProtocol {
    struct Reply: Sendable {
        var status: Int
        var body: Data
        var error: URLError?
    }

    static let reply = Locked<Reply>(Reply(status: 200, body: Data("{}".utf8)))
    static let lastRequest = Locked<URLRequest?>(nil)
    static let lastBody = Locked<Data?>(nil)

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.lastRequest.withLock { $0 = request }
        Self.lastBody.withLock { $0 = request.httpBody ?? Self.readStream(request.httpBodyStream) }
        let reply = Self.reply.current
        if let error = reply.error {
            client?.urlProtocol(self, didFailWithError: error)
            return
        }
        let response = HTTPURLResponse(url: request.url!, statusCode: reply.status, httpVersion: "HTTP/1.1",
                                       headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: reply.body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}

    static func readStream(_ stream: InputStream?) -> Data? {
        guard let stream else { return nil }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let read = stream.read(&buffer, maxLength: buffer.count)
            if read <= 0 { break }
            data.append(buffer, count: read)
        }
        return data
    }

    static func session() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        return URLSession(configuration: config)
    }

    static func respond(_ status: Int, _ json: String) {
        reply.withLock { $0 = Reply(status: status, body: Data(json.utf8)) }
    }
}

@Suite("REST-Client", .serialized)
struct APIClientTests {
    let client = APIClient(session: MockURLProtocol.session(), baseURL: URL(string: "http://192.0.2.10:8080/mp"),
                           token: "tok")

    @Test func sendsAuthHeaderPathAndQuery() async throws {
        MockURLProtocol.respond(200, try String(decoding: Fixture.data("statistics-24h.json"), as: UTF8.self))
        _ = try await client.statistics(.day)
        let request = try #require(MockURLProtocol.lastRequest.current)
        #expect(request.url?.absoluteString == "http://192.0.2.10:8080/mp/api/statistics?range=24h")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer tok")
    }

    @Test func postsJSONBody() async throws {
        MockURLProtocol.respond(200, #"{"ok": true}"#)
        try await client.deviceAction(4, .setMode("pv_only"))
        let body = try #require(MockURLProtocol.lastBody.current)
        let json = try JSONDecoder().decode(JSONObject.self, from: body)
        #expect(json.string("action") == "set_mode")
        #expect(json.string("value") == "pv_only")
        #expect(MockURLProtocol.lastRequest.current?.httpMethod == "POST")
    }

    @Test func mapsUnauthorized() async {
        MockURLProtocol.respond(401, #"{"detail": "Ungültiges oder abgelaufenes Token"}"#)
        await #expect(throws: APIError.unauthorized) { try await client.me() }
    }

    @Test func mapsStructuredValidationErrors() async {
        MockURLProtocol.respond(422, #"{"detail": {"code": "invalid_config", "message": "Eingabe ungültig: Port: Port 1–65535", "fields": [{"key": "port", "label": "Port", "error": "Port 1–65535"}]}}"#)
        do {
            _ = try await client.createDevice(DeviceCreate(name: "WR", category: .inverter, driverId: "sunspec_inverter",
                                                           config: ["port": 99999]))
            Issue.record("Fehler erwartet")
        } catch let APIError.validation(message, fields) {
            #expect(message.contains("Port"))
            #expect(fields.first?.key == "port")
        } catch {
            Issue.record("Falscher Fehler: \(error)")
        }
    }

    @Test func mapsCategoryLimitConflict() async {
        MockURLProtocol.respond(409, #"{"detail": {"code": "category_limit", "message": "Netzzähler: höchstens 1 aktives Gerät"}}"#)
        await #expect(throws: APIError.conflict("Netzzähler: höchstens 1 aktives Gerät")) {
            _ = try await client.createDevice(DeviceCreate(name: "Z", category: .meter, driverId: "shelly_3em", config: [:]))
        }
    }

    @Test func pydanticErrorListBecomesMessage() async {
        MockURLProtocol.respond(422, #"{"detail": [{"loc": ["body", "email"], "msg": "value is not a valid email address"}]}"#)
        do {
            _ = try await client.createFirstAdmin(SetupRequest(email: "x", password: "12345678", name: "A"))
            Issue.record("Fehler erwartet")
        } catch let APIError.validation(message, fields) {
            #expect(message.contains("email"))
            #expect(fields.first?.key == "email")
        } catch {
            Issue.record("Falscher Fehler: \(error)")
        }
    }

    @Test func transportErrorsAreReadable() async {
        MockURLProtocol.reply.withLock { $0 = .init(status: 0, body: Data(), error: URLError(.timedOut)) }
        await #expect(throws: APIError.timedOut) { try await client.health() }
        MockURLProtocol.reply.withLock { $0 = .init(status: 0, body: Data(), error: URLError(.notConnectedToInternet)) }
        await #expect(throws: APIError.offline) { try await client.health() }
        #expect(APIError.offline.isTransient)
        #expect(!APIError.forbidden.isTransient)
    }

    @Test func malformedJSONIsADecodingError() async {
        MockURLProtocol.respond(200, "<html>Router-Anmeldung</html>")
        await #expect(throws: APIError.decoding("Health")) { try await client.health() }
    }

    @Test func csvExportComesBackAsText() async throws {
        MockURLProtocol.respond(200, try String(decoding: Fixture.data("sessions-sample.csv"), as: UTF8.self))
        let csv = try await client.sessionsCSV(.month)
        #expect(ChargeList(csv: csv).rows.count == 2)
    }

    @Test func probeRecognisesMinePowerAndRejectsOthers() async throws {
        let probe = ServerProbe(session: MockURLProtocol.session())
        MockURLProtocol.respond(200, try String(decoding: Fixture.data("health.json"), as: UTF8.self))
        let result = try await probe.check(URL(string: "http://192.0.2.10:8080")!)
        #expect(result.health.version != nil)
        MockURLProtocol.respond(200, "<html>Fritz!Box</html>")
        await #expect(throws: APIError.notMinePower) { _ = try await probe.check(URL(string: "http://192.0.2.1")!) }
    }
}
