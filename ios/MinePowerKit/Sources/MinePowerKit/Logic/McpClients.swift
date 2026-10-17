import Foundation

/// Einrichtung des MinePower-MCP-Servers in KI-Anwendungen (Spiegel von
/// `frontend/src/lib/mcpClients.ts`).
///
/// Transport: Streamable HTTP mit `Authorization: Bearer <Schlüssel>`.
/// Anwendungen ohne eigenen HTTP-Transport nutzen die Brücke `mcp-remote`;
/// Konnektoren, die nur eine Adresse annehmen (Grok, Claude, ChatGPT,
/// Le Chat), bekommen die Adresse mit Schlüssel im Pfad (`/mcp/k/<Schlüssel>`).
public struct McpClient: Sendable, Identifiable, Equatable {
    public var id: String
    public var name: String
    public var code: String
    public var urlOnly: Bool
    public var steps: [String]

    public static func all(url: String, token: String, publicBase: String) -> [McpClient] {
        let keyURL = publicBase.replacing(/\/+$/, with: "") + "/mcp/k/" + token
        let insecure = url.hasPrefix("http://") && url.firstMatch(of: /\/\/(localhost|127\.0\.0\.1)[:\/]/) == nil
        let bridge = ["-y", "mcp-remote", url, "--header", "Authorization:${AUTH}"] + (insecure ? ["--allow-http"] : [])
        let auth = "Bearer \(token)"
        func json(_ value: Any) -> String { prettyJSON(value) }
        func quoted(_ s: String) -> String { "\"\(s.replacingOccurrences(of: "\"", with: "\\\""))\"" }
        func connector(_ id: String, _ name: String) -> McpClient {
            McpClient(id: id, name: name, code: keyURL, urlOnly: true, steps: steps(id))
        }
        return [
            connector("grok", "Grok"),
            connector("claude_connector", Loc.t("Claude (Konnektor)", "Claude (connector)")),
            connector("chatgpt", Loc.t("ChatGPT (Konnektor)", "ChatGPT (connector)")),
            connector("lechat", "Mistral Le Chat"),
            McpClient(id: "claude_desktop", name: "Claude Desktop",
                      code: json(["mcpServers": ["minepower": ["command": "npx", "args": bridge, "env": ["AUTH": auth]]]]),
                      urlOnly: false, steps: steps("claude_desktop")),
            McpClient(id: "claude_code", name: "Claude Code",
                      code: "claude mcp add --transport http minepower \(url) --header \"Authorization: \(auth)\"",
                      urlOnly: false, steps: steps("claude_code")),
            McpClient(id: "codex", name: "OpenAI Codex", code: [
                "[mcp_servers.minepower]", "command = \"npx\"", "args = [\(bridge.map(quoted).joined(separator: ", "))]",
                "", "[mcp_servers.minepower.env]", "AUTH = \(quoted(auth))",
            ].joined(separator: "\n"), urlOnly: false, steps: steps("codex")),
            McpClient(id: "cursor", name: "Cursor",
                      code: json(["mcpServers": ["minepower": ["url": url, "headers": ["Authorization": auth]]]]),
                      urlOnly: false, steps: steps("cursor")),
            McpClient(id: "vscode", name: "VS Code (GitHub Copilot)",
                      code: json(["servers": ["minepower": ["type": "http", "url": url, "headers": ["Authorization": auth]]]]),
                      urlOnly: false, steps: steps("vscode")),
            McpClient(id: "windsurf", name: "Windsurf",
                      code: json(["mcpServers": ["minepower": ["serverUrl": url, "headers": ["Authorization": auth]]]]),
                      urlOnly: false, steps: steps("windsurf")),
            McpClient(id: "gemini", name: "Gemini CLI",
                      code: json(["mcpServers": ["minepower": ["httpUrl": url, "headers": ["Authorization": auth]]]]),
                      urlOnly: false, steps: steps("gemini")),
            McpClient(id: "lmstudio", name: "LM Studio",
                      code: json(["mcpServers": ["minepower": ["url": url, "headers": ["Authorization": auth]]]]),
                      urlOnly: false, steps: steps("lmstudio")),
            McpClient(id: "openwebui", name: "Open WebUI / Ollama",
                      code: ["URL:  \(url)", "Typ:  MCP (Streamable HTTP)", "Auth: Bearer", "Key:  \(token)"].joined(separator: "\n"),
                      urlOnly: false, steps: steps("openwebui")),
            McpClient(id: "continue", name: "Continue", code: [
                "mcpServers:", "  - name: MinePower", "    type: streamable-http", "    url: \(url)", "    requestOptions:",
                "      headers:", "        Authorization: \(auth)",
            ].joined(separator: "\n"), urlOnly: false, steps: steps("continue")),
            McpClient(id: "manual", name: Loc.t("Manuell", "Manual"), code: [
                "curl -s \(url) \\", "  -H \"Authorization: \(auth)\" \\", "  -H \"Content-Type: application/json\" \\",
                "  -H \"Accept: application/json, text/event-stream\" \\",
                "  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}'",
            ].joined(separator: "\n"), urlOnly: false, steps: steps("manual")),
        ]
    }

    /// JSON mit zwei Leerzeichen Einzug und sortierten Schlüsseln, auf allen
    /// Plattformen gleich (JSONSerialization rückt je nach System anders ein).
    static func prettyJSON(_ value: Any, indent: Int = 0) -> String {
        let pad = String(repeating: "  ", count: indent + 1), end = String(repeating: "  ", count: indent)
        switch value {
        case let object as [String: Any]:
            guard !object.isEmpty else { return "{}" }
            let items = object.keys.sorted().map { "\(pad)\(jsonString($0)): \(prettyJSON(object[$0]!, indent: indent + 1))" }
            return "{\n" + items.joined(separator: ",\n") + "\n\(end)}"
        case let array as [Any]:
            guard !array.isEmpty else { return "[]" }
            return "[\n" + array.map { pad + prettyJSON($0, indent: indent + 1) }.joined(separator: ",\n") + "\n\(end)]"
        case let text as String:
            return jsonString(text)
        case let flag as Bool:
            return flag ? "true" : "false"
        case let number as Int:
            return String(number)
        case let number as Double:
            return number.rounded() == number ? String(Int(number)) : String(number)
        default:
            return "null"
        }
    }

    static func jsonString(_ text: String) -> String {
        var out = "\""
        for scalar in text.unicodeScalars {
            switch scalar {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            case let c where c.value < 0x20: out += String(format: "\\u%04x", c.value)
            default: out.unicodeScalars.append(scalar)
            }
        }
        return out + "\""
    }

    static func steps(_ id: String) -> [String] {
        let https = Loc.t("Voraussetzung: MinePower ist per HTTPS aus dem Internet erreichbar",
                          "Requirement: MinePower is reachable from the internet via HTTPS")
        let node = Loc.t("Voraussetzung: Node.js (für mcp-remote)", "Requirement: Node.js (for mcp-remote)")
        switch id {
        case "grok":
            return [Loc.t("Grok → Einstellungen → Konnektoren → Eigenen MCP-Konnektor hinzufügen",
                          "Grok → Settings → Connectors → Add custom MCP connector"),
                    Loc.t("Adresse unten einfügen, Authentifizierung: keine", "Paste the address below, authentication: none"), https]
        case "claude_connector":
            return [Loc.t("claude.ai oder Claude-App → Einstellungen → Konnektoren → Eigenen Konnektor hinzufügen",
                          "claude.ai or Claude app → Settings → Connectors → Add custom connector"),
                    Loc.t("Adresse unten einfügen, OAuth-Felder leer lassen", "Paste the address below, leave OAuth fields empty"), https]
        case "chatgpt":
            return [Loc.t("ChatGPT → Einstellungen → Apps und Konnektoren → Entwicklermodus → Erstellen",
                          "ChatGPT → Settings → Apps & Connectors → Developer mode → Create"),
                    Loc.t("Adresse unten einfügen, Authentifizierung: keine", "Paste the address below, authentication: none"), https]
        case "lechat":
            return [Loc.t("Le Chat → Intelligenz → Konnektoren → Eigener MCP-Konnektor",
                          "Le Chat → Intelligence → Connectors → Custom MCP connector"),
                    Loc.t("Adresse unten einfügen, Authentifizierung: keine", "Paste the address below, authentication: none"), https]
        case "claude_desktop":
            return [Loc.t("Einstellungen → Entwickler → Konfiguration bearbeiten", "Settings → Developer → Edit config"),
                    Loc.t("Block in claude_desktop_config.json einfügen, Claude neu starten",
                          "Paste the block into claude_desktop_config.json, restart Claude"), node]
        case "claude_code":
            return [Loc.t("Befehl im Terminal ausführen", "Run the command in a terminal"),
                    Loc.t("Prüfen mit: claude mcp list", "Check with: claude mcp list")]
        case "codex":
            return [Loc.t("Datei ~/.codex/config.toml ergänzen (Codex CLI und Codex-App)",
                          "Add to ~/.codex/config.toml (Codex CLI and app)"), node]
        case "cursor":
            return [Loc.t("Datei ~/.cursor/mcp.json oder .cursor/mcp.json im Projekt",
                          "File ~/.cursor/mcp.json or .cursor/mcp.json in the project"),
                    Loc.t("Cursor → Einstellungen → MCP: Server aktivieren", "Cursor → Settings → MCP: enable the server")]
        case "vscode":
            return [Loc.t("Datei .vscode/mcp.json im Projekt oder Befehl 'MCP: Add Server'",
                          "File .vscode/mcp.json in the project or command 'MCP: Add Server'"),
                    Loc.t("Copilot-Chat im Agent-Modus verwenden", "Use Copilot Chat in agent mode")]
        case "windsurf":
            return [Loc.t("Datei ~/.codeium/windsurf/mcp_config.json", "File ~/.codeium/windsurf/mcp_config.json"),
                    Loc.t("Windsurf → Cascade → MCP-Server aktualisieren", "Windsurf → Cascade → refresh MCP servers")]
        case "gemini":
            return [Loc.t("Datei ~/.gemini/settings.json ergänzen", "Add to ~/.gemini/settings.json"),
                    Loc.t("Prüfen mit: /mcp", "Check with: /mcp")]
        case "lmstudio":
            return [Loc.t("LM Studio ab 0.3.17: Programm → Install → Edit mcp.json", "LM Studio 0.3.17+: Program → Install → Edit mcp.json"),
                    Loc.t("Modell mit Tool-Unterstützung wählen", "Pick a model with tool support")]
        case "openwebui":
            return [Loc.t("Admin-Einstellungen → Externe Tools → Hinzufügen", "Admin settings → External tools → Add"),
                    Loc.t("Typ 'MCP (Streamable HTTP)', Werte unten eintragen", "Type 'MCP (Streamable HTTP)', enter the values below"),
                    Loc.t("Funktioniert mit Ollama-Modellen mit Tool-Unterstützung", "Works with Ollama models with tool support")]
        case "continue":
            return [Loc.t("Datei .continue/mcpServers/minepower.yaml oder config.yaml",
                          "File .continue/mcpServers/minepower.yaml or config.yaml"),
                    Loc.t("Im Agent-Modus verwenden", "Use in agent mode")]
        default:
            return [Loc.t("Transport: Streamable HTTP, JSON-RPC 2.0 per POST an die Server-Adresse",
                          "Transport: Streamable HTTP, JSON-RPC 2.0 via POST to the server address"),
                    Loc.t("Anmeldung: Kopfzeile Authorization: Bearer <SCHLÜSSEL>", "Auth: header Authorization: Bearer <KEY>"),
                    Loc.t("Methoden: initialize, tools/list, tools/call, prompts/list, prompts/get",
                          "Methods: initialize, tools/list, tools/call, prompts/list, prompts/get")]
        }
    }
}
