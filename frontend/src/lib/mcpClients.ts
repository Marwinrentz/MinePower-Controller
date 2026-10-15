/** Einrichtung des MinePower-MCP-Servers in verbreiteten KI-Anwendungen.
 *
 *  Transport: Streamable HTTP, Anmeldung per Kopfzeile
 *  'Authorization: Bearer <Schlüssel>'. Anwendungen ohne eigenen
 *  HTTP-Transport binden den Server über die Brücke 'mcp-remote' (Node.js)
 *  als lokalen Prozess ein. Anwendungen, die nur eine Adresse annehmen
 *  (Konnektoren in Grok, Claude, ChatGPT, Le Chat), bekommen die Adresse mit
 *  Schlüssel im Pfad (/mcp/k/<Schlüssel>). Schritte in i18n unter mcp.c.<id>.
 */
export type McpClient = {
  id: string; name: string; lang: "json" | "bash" | "toml" | "yaml" | "text"; code?: string; urlOnly?: boolean;
};

const json = (value: unknown) => JSON.stringify(value, null, 2);

export function mcpClients(url: string, token: string, publicBase: string): McpClient[] {
  const keyUrl = `${publicBase.replace(/\/+$/, "")}/mcp/k/${token}`;
  const http = url.startsWith("http://") && !/\/\/(localhost|127\.0\.0\.1)[:/]/.test(url);
  const bridge = ["-y", "mcp-remote", url, "--header", "Authorization:${AUTH}", ...(http ? ["--allow-http"] : [])];
  const auth = `Bearer ${token}`;
  const connector = (id: string, name: string): McpClient => ({ id, name, lang: "text", code: keyUrl, urlOnly: true });
  return [
    connector("grok", "Grok"),
    connector("claude_connector", "Claude (Konnektor)"),
    connector("chatgpt", "ChatGPT (Konnektor)"),
    connector("lechat", "Mistral Le Chat"),
    {
      id: "claude_desktop", name: "Claude Desktop", lang: "json",
      code: json({ mcpServers: { minepower: { command: "npx", args: bridge, env: { AUTH: auth } } } }),
    },
    {
      id: "claude_code", name: "Claude Code", lang: "bash",
      code: `claude mcp add --transport http minepower ${url} --header "Authorization: ${auth}"`,
    },
    {
      id: "codex", name: "OpenAI Codex", lang: "toml",
      code: [
        "[mcp_servers.minepower]",
        'command = "npx"',
        `args = [${bridge.map((a) => JSON.stringify(a)).join(", ")}]`,
        "",
        "[mcp_servers.minepower.env]",
        `AUTH = ${JSON.stringify(auth)}`,
      ].join("\n"),
    },
    {
      id: "cursor", name: "Cursor", lang: "json",
      code: json({ mcpServers: { minepower: { url, headers: { Authorization: auth } } } }),
    },
    {
      id: "vscode", name: "VS Code (GitHub Copilot)", lang: "json",
      code: json({ servers: { minepower: { type: "http", url, headers: { Authorization: auth } } } }),
    },
    {
      id: "windsurf", name: "Windsurf", lang: "json",
      code: json({ mcpServers: { minepower: { serverUrl: url, headers: { Authorization: auth } } } }),
    },
    {
      id: "gemini", name: "Gemini CLI", lang: "json",
      code: json({ mcpServers: { minepower: { httpUrl: url, headers: { Authorization: auth } } } }),
    },
    {
      id: "lmstudio", name: "LM Studio", lang: "json",
      code: json({ mcpServers: { minepower: { url, headers: { Authorization: auth } } } }),
    },
    {
      id: "openwebui", name: "Open WebUI / Ollama", lang: "text",
      code: [`URL:  ${url}`, "Typ:  MCP (Streamable HTTP)", "Auth: Bearer", `Key:  ${token}`].join("\n"),
    },
    {
      id: "continue", name: "Continue", lang: "yaml",
      code: [
        "mcpServers:",
        "  - name: MinePower",
        "    type: streamable-http",
        `    url: ${url}`,
        "    requestOptions:",
        "      headers:",
        `        Authorization: ${auth}`,
      ].join("\n"),
    },
    {
      id: "manual", name: "Manuell", lang: "bash",
      code: [
        `curl -s ${url} \\`,
        `  -H "Authorization: ${auth}" \\`,
        '  -H "Content-Type: application/json" \\',
        '  -H "Accept: application/json, text/event-stream" \\',
        `  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`,
      ].join("\n"),
    },
  ];
}
