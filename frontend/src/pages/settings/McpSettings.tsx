/** KI-Assistenten (MCP): Zugangsschlüssel, Anleitungen je Anwendung, Funktionsliste. */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Icon } from "../../components/icons";
import { ActionButton, Badge, Button, Callout, Card, CardHead, Field, Segment } from "../../components/ui";
import { useI18n } from "../../i18n";
import { del, get, post } from "../../lib/api";
import { fmtAgo } from "../../lib/format";
import { mcpClients } from "../../lib/mcpClients";
import { useAuth } from "../../store/auth";
import { toast } from "../../store/toast";

type Role = "readonly" | "user" | "admin";
type Token = { id: number; name: string; prefix: string; role: Role; created_at: string; last_used_at: string | null };
type ToolInfo = { name: string; title: string; role: Role; description: string };

const RANK: Record<Role, number> = { readonly: 0, user: 1, admin: 2 };

function CopyBlock({ text, label }: { text: string; label: string }) {
  const { t } = useI18n();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast.ok(t("mcp.copied"));
    } catch {
      toast.err(t("mcp.copyFailed"));
    }
  };
  return (
    <div className="copy-block">
      <pre aria-label={label}><code>{text}</code></pre>
      <button type="button" className="btn ghost btn-sm copy-btn" onClick={() => void copy()}>
        <Icon name="check" size={14} /> {t("mcp.copy")}
      </button>
    </div>
  );
}

export function McpSettings() {
  const { t } = useI18n();
  const userRole = (useAuth((s) => s.user?.role) ?? "readonly") as Role;
  const [tokens, setTokens] = useState<Token[] | null>(null);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("readonly");
  const [fresh, setFresh] = useState<string | null>(null);
  const [client, setClient] = useState("claude_desktop");
  const [publicBase, setPublicBase] = useState(() => {
    try {
      return localStorage.getItem("mp_mcp_public") ?? "";
    } catch {
      return "";
    }
  });
  const savePublic = (value: string) => {
    setPublicBase(value);
    try {
      localStorage.setItem("mp_mcp_public", value);
    } catch {
      /* nur für diese Sitzung */
    }
  };
  const url = `${window.location.origin}/mcp`;
  const insecure = window.location.protocol === "http:" && !/^(localhost|127\.0\.0\.1)$/.test(window.location.hostname);

  const load = useCallback(async () => {
    try {
      setTokens(await get<Token[]>("/api/mcp/tokens"));
    } catch (e) {
      toast.err((e as Error).message);
      setTokens([]);
    }
  }, []);
  useEffect(() => {
    void load();
    get<ToolInfo[]>("/api/mcp/tools").then(setTools).catch(() => {});
  }, [load]);

  const create = async () => {
    const res = await post<Token & { token: string }>("/api/mcp/tokens", { name: name.trim(), role });
    setFresh(res.token);
    setName("");
    await load();
  };
  const revoke = async (id: number) => {
    await del(`/api/mcp/tokens/${id}`);
    await load();
  };

  const roles = (["readonly", "user", "admin"] as Role[]).filter((r) => RANK[r] <= RANK[userRole]);
  const clients = useMemo(
    () => mcpClients(url, fresh ?? t("mcp.tokenPlaceholder"), publicBase.trim() || t("mcp.publicPlaceholder")),
    [url, fresh, t, publicBase],
  );
  const active = clients.find((c) => c.id === client) ?? clients[0];
  const groups: { role: Role; key: string }[] = [
    { role: "readonly", key: "mcp.groupRead" }, { role: "user", key: "mcp.groupControl" },
    { role: "admin", key: "mcp.groupSetup" },
  ];

  return (
    <>
      <Card>
        <CardHead title={t("mcp.title")} sub={t("mcp.sub")} />
        <Field label={t("mcp.url")}>
          <CopyBlock text={url} label={t("mcp.url")} />
        </Field>
        {insecure && <Callout tone="warn">{t("mcp.insecure")}</Callout>}

        <h3 className="group-title">{t("mcp.keys")}</h3>
        {tokens && tokens.length === 0 && <p className="dim">{t("mcp.noKeys")}</p>}
        {tokens && tokens.length > 0 && (
          <ul className="token-list">
            {tokens.map((k) => (
              <li key={k.id}>
                <div className="token-main">
                  <strong>{k.name}</strong>
                  <span className="dim num">{k.prefix}…</span>
                </div>
                <Badge tone={k.role === "admin" ? "warn" : k.role === "user" ? "accent" : undefined}>
                  {t(`mcp.role.${k.role}`)}
                </Badge>
                <span className="dim token-used">
                  {k.last_used_at ? t("mcp.used", { ago: fmtAgo(k.last_used_at) }) : t("mcp.unused")}
                </span>
                <ActionButton variant="ghost" size="sm" onClick={() => revoke(k.id)} title={t("mcp.revoke")}>
                  <Icon name="close" size={16} /> {t("mcp.revoke")}
                </ActionButton>
              </li>
            ))}
          </ul>
        )}

        <div className="form-grid">
          <Field label={t("mcp.keyName")}>
            <input className="input" value={name} maxLength={80} placeholder="Claude Desktop"
                   onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label={t("mcp.keyRole")} help={t(`mcp.roleHelp.${role}`)}>
            <Segment<Role> size="lg" value={role} onChange={setRole}
                           options={roles.map((r) => ({ value: r, label: t(`mcp.role.${r}`) }))} />
          </Field>
        </div>
        <div className="form-actions">
          <ActionButton variant="primary" disabled={!name.trim()} onClick={create}>
            <Icon name="plus" size={16} /> {t("mcp.create")}
          </ActionButton>
        </div>
        {fresh && (
          <Callout tone="ok" title={t("mcp.freshTitle")}>
            <p className="field-help">{t("mcp.freshText")}</p>
            <CopyBlock text={fresh} label={t("mcp.freshTitle")} />
          </Callout>
        )}
      </Card>

      <Card>
        <CardHead title={t("mcp.guide")} sub={t("mcp.guideSub")} />
        <div className="client-picker" role="tablist" aria-label={t("mcp.guide")}>
          {clients.map((c) => (
            <button key={c.id} type="button" role="tab" aria-selected={c.id === active.id}
                    className={`chip ${c.id === active.id ? "on" : ""}`} onClick={() => setClient(c.id)}>
              {c.name}
            </button>
          ))}
        </div>
        {active.urlOnly && (
          <Field label={t("mcp.publicUrl")} help={t("mcp.publicHelp")}>
            <input className="input" type="url" inputMode="url" value={publicBase} placeholder="https://minepower.example.com"
                   onChange={(e) => savePublic(e.target.value)} />
          </Field>
        )}
        <ol className="client-steps">
          {t(`mcp.c.${active.id}`).split("\n").map((line) => <li key={line}>{line}</li>)}
        </ol>
        {active.code && <CopyBlock text={active.code} label={active.name} />}
        {!fresh && <p className="field-help">{t("mcp.placeholderHint")}</p>}
        {active.urlOnly && <Callout tone="warn">{t("mcp.urlKeyWarn")}</Callout>}
      </Card>

      <Card>
        <CardHead title={t("mcp.tools")} sub={t("mcp.toolsSub")} />
        {groups.map((g) => (
          <div key={g.role} className="tool-group">
            <h3 className="group-title">{t(g.key)}</h3>
            <ul className="tool-list">
              {tools.filter((x) => x.role === g.role).map((x) => (
                <li key={x.name}><span className="num">{x.name}</span><span>{x.title}</span></li>
              ))}
            </ul>
          </div>
        ))}
        <Button onClick={() => window.open("https://modelcontextprotocol.io", "_blank", "noopener")}>
          <Icon name="info" size={16} /> modelcontextprotocol.io
        </Button>
      </Card>
    </>
  );
}
