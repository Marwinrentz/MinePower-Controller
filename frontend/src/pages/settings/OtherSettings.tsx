/** Einstellungen → Reihenfolge, Benachrichtigungen, Benutzer, Darstellung, Sicherung. */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PriorityChain } from "../../components/PriorityChain";
import { Icon } from "../../components/icons";
import { ActionButton, Badge, Button, Card, CardHead, Field, Segment } from "../../components/ui";
import { APP_VERSION, useServerVersion } from "../../lib/useVersion";
import { useI18n } from "../../i18n";
import { del, get, post, put } from "../../lib/api";
import { fmtTime } from "../../lib/format";
import type { User } from "../../lib/types";
import { useDevices } from "../../lib/useDevices";
import { useAuth } from "../../store/auth";
import { useSection, useSettings } from "../../store/settings";
import { useTheme, type ThemeMode } from "../../store/theme";
import { toast } from "../../store/toast";

export function PrioritySettings() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { devices } = useDevices();
  const order = (useSettings((s) => s.values.priority?.order) as number[] | undefined) ?? [];
  const setVal = useSettings((s) => s.set);
  const save = async (next: number[]) => {
    setVal("priority", "order", next);
    try {
      await put("/api/settings/priority", { order: next });
      // Gespeicherten Stand nachziehen, sonst meldet die Speicherleiste
      // eine Änderung, die längst gespeichert ist.
      useSettings.setState((s) => ({ saved: { ...s.saved, priority: { order: next } } }));
      toast.ok(t("common.saved"));
    } catch (e) {
      toast.err(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <Card>
      <CardHead title={t("set.sec.priority")} sub={t("set.priority.help")}>
        <Button size="sm" variant="ghost" onClick={() => navigate("/einstellungen?tab=battery")}>
          <Icon name="battery" size={16} /> {t("set.priority.toBattery")}
        </Button>
      </CardHead>
      {devices ? <PriorityChain devices={devices} order={order.filter((id) => devices.some((d) => d.id === id && d.category !== "battery"))} onChange={save} />
        : <div className="skeleton" style={{ height: 160 }} />}
    </Card>
  );
}

export function NotificationSettings() {
  const { t } = useI18n();
  const n = useSection("notifications");
  return (
    <Card>
      <CardHead title={t("set.sec.notifications")} sub={t("set.notifications.help")} />
      <Field label={t("set.notifications.ntfy")} help={t("set.notifications.ntfyHelp")}>
        <input className="input" inputMode="url" value={n.str("ntfy_url", "")} placeholder="https://ntfy.sh/…"
               onChange={(e) => n.set("ntfy_url", e.target.value)} />
      </Field>
      <div className="form-grid">
        <Field label={t("set.notifications.telegramToken")}>
          <input className="input" type="password" autoComplete="off" value={n.str("telegram_token", "")}
                 onChange={(e) => n.set("telegram_token", e.target.value)} />
        </Field>
        <Field label={t("set.notifications.telegramChat")}>
          <input className="input" value={n.str("telegram_chat_id", "")} onChange={(e) => n.set("telegram_chat_id", e.target.value)} />
        </Field>
      </div>
    </Card>
  );
}

const ROLE_LABEL: Record<string, string> = { admin: "Admin", user: "Benutzer", readonly: "Nur lesen" };

export function UsersSettings() {
  const { t } = useI18n();
  const me = useAuth((s) => s.user);
  const [users, setUsers] = useState<User[]>([]);
  const [draft, setDraft] = useState({ email: "", name: "", password: "", role: "user" });
  const load = () => { get<User[]>("/api/auth/users").then(setUsers).catch(() => {}); };
  useEffect(load, []);

  const add = async () => {
    try {
      await post("/api/auth/users", { ...draft, rfid_tag: null });
      setDraft({ email: "", name: "", password: "", role: "user" });
      toast.ok(t("common.saved"));
      load();
    } catch (e) {
      toast.err(e instanceof Error ? e.message : String(e));
    }
  };
  const remove = async (u: User) => {
    if (!confirm(`${t("common.delete")}: ${u.email}?`)) return;
    try {
      await del(`/api/auth/users/${u.id}`);
      load();
    } catch (e) {
      toast.err(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Card>
      <CardHead title={t("set.sec.users")} sub={t("set.users.help")} />
      <ul className="user-list">
        {users.map((u) => (
          <li key={u.id} className="user-row">
            <Icon name="lock" size={18} />
            <div className="user-text">
              <span className="user-name">{u.name || u.email}</span>
              <span className="user-state">{u.email}</span>
            </div>
            <div className="row tight">
              <Badge tone={u.role === "admin" ? "accent" : undefined}>{ROLE_LABEL[u.role] ?? u.role}</Badge>
              {u.id !== me?.id && <ActionButton size="sm" variant="danger" onClick={() => remove(u)}>{t("common.delete")}</ActionButton>}
            </div>
          </li>
        ))}
      </ul>
      <h3 className="group-title">{t("common.add")}</h3>
      <div className="form-grid">
        <Field label={t("login.email")}><input className="input" type="email" value={draft.email} onChange={(e) => setDraft({ ...draft, email: e.target.value })} /></Field>
        <Field label={t("login.name")}><input className="input" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Field>
        <Field label={t("login.password")} hint="min. 8">
          <input className="input" type="password" autoComplete="new-password" value={draft.password}
                 onChange={(e) => setDraft({ ...draft, password: e.target.value })} />
        </Field>
        <Field label={t("set.users.role")}>
          <Segment size="lg" value={draft.role} onChange={(v) => setDraft({ ...draft, role: v })}
                   options={[{ value: "admin", label: t("set.users.roleAdmin") }, { value: "user", label: t("set.users.roleUser") }, { value: "readonly", label: t("set.users.roleReadonly") }]} />
        </Field>
      </div>
      <div className="form-actions">
        <ActionButton variant="primary" disabled={!draft.email || draft.password.length < 8} onClick={add}>{t("common.add")}</ActionButton>
      </div>
    </Card>
  );
}

/** Eigenes Konto: Passwort ändern. Danach gilt nur noch die Anmeldung
 *  dieses Geräts; alle anderen enden (der Server entwertet ältere Tokens). */
export function AccountSettings() {
  const { t } = useI18n();
  const me = useAuth((s) => s.user);
  const login = useAuth((s) => s.login);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const mismatch = repeat.length > 0 && next !== repeat;
  const ready = current.length > 0 && next.length >= 8 && next === repeat;

  const change = async () => {
    try {
      const body = await post<{ token: string; user: User }>("/api/auth/me/password",
        { current_password: current, new_password: next });
      login(body.token, body.user);
      setCurrent(""); setNext(""); setRepeat("");
      toast.ok(t("set.account.changed"));
    } catch (e) {
      toast.err(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Card>
      <CardHead title={t("set.account.password")} sub={me ? t("set.account.signedInAs", { email: me.email }) : undefined} />
      <div className="form-grid">
        <Field label={t("set.account.current")}>
          <input className="input" type="password" autoComplete="current-password" value={current}
                 onChange={(e) => setCurrent(e.target.value)} />
        </Field>
        <Field label={t("set.account.next")} hint="min. 8">
          <input className="input" type="password" autoComplete="new-password" value={next}
                 onChange={(e) => setNext(e.target.value)} />
        </Field>
        <Field label={t("set.account.repeat")} error={mismatch ? t("set.account.mismatch") : null}>
          <input className="input" type="password" autoComplete="new-password" value={repeat}
                 aria-invalid={mismatch || undefined} onChange={(e) => setRepeat(e.target.value)} />
        </Field>
      </div>
      <div className="form-actions">
        <ActionButton variant="primary" disabled={!ready} onClick={change}>{t("set.account.password")}</ActionButton>
      </div>
    </Card>
  );
}

export function AppearanceSettings() {
  const { t, lang, setLang } = useI18n();
  const { mode, setMode } = useTheme();
  const navigate = useNavigate();
  return (
    <Card>
      <CardHead title={t("set.sec.appearance")} />
      <Field label={t("set.appearance.theme")}>
        <Segment<ThemeMode> size="lg" value={mode} onChange={setMode} options={[
          { value: "light", label: t("shell.themeLight"), icon: "sun" },
          { value: "dark", label: t("shell.themeDark"), icon: "moon" },
          { value: "system", label: t("shell.themeSystem"), icon: "display" },
        ]} />
      </Field>
      <Field label={t("set.appearance.language")}>
        <Segment size="lg" value={lang} onChange={setLang} options={[{ value: "de", label: "Deutsch" }, { value: "en", label: "English" }]} />
      </Field>
      <Field label={t("nav.wall")} help={t("set.appearance.wallHelp")}>
        <Button onClick={() => navigate("/wall")}><Icon name="fullscreen" size={16} /> {t("set.appearance.wall")}</Button>
      </Field>
    </Card>
  );
}

type BackupFile = { name: string; kind: "config" | "data" | "pre-migration"; bytes: number; time: string };
type BackupOverview = {
  backups: BackupFile[];
  status: { count: number; latest: string | null; data_count: number; data_latest: string | null };
  storage: { volumes: { name: string; free_bytes: number; total_bytes: number }[]; database_bytes: number | null; warning: string | null };
  measurements: { spool_bytes: number; backlog_rows: number; failures: number } | null;
};

function fmtBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} kB`;
}

export function BackupSettings() {
  const { t } = useI18n();
  const fileRef = useRef<HTMLInputElement>(null);
  const [overview, setOverview] = useState<BackupOverview | null>(null);
  const [busy, setBusy] = useState(false);
  const reloadSettings = useSettings((s) => s.load);
  const load = () => { get<BackupOverview>("/api/system/backups").then(setOverview).catch(() => {}); };
  useEffect(load, []);
  const fail = (e: unknown) => toast.err(e instanceof Error ? e.message : String(e));

  const exportConfig = async () => {
    try {
      const data = await get<unknown>("/api/system/export");
      saveBlob(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }), "minepower-config.json");
    } catch (e) { fail(e); }
  };
  const importConfig = async (file: File) => {
    if (!confirm(t("set.backup.importWarn"))) return;
    try {
      await post("/api/system/import", JSON.parse(await file.text()));
      toast.ok(t("common.saved"));
      void reloadSettings();
    } catch (e) { fail(e); }
  };
  const backupNow = async () => {
    setBusy(true);
    try {
      await post("/api/system/backups");
      toast.ok(t("set.backup.created"));
      load();
    } catch (e) { fail(e); } finally { setBusy(false); }
  };
  const restore = async (file: BackupFile) => {
    const warn = file.kind === "data" ? "set.backup.restoreDataWarn" : "set.backup.restoreConfigWarn";
    if (!confirm(t(warn, { time: fmtTime(file.time) }))) return;
    setBusy(true);
    try {
      const r = await post<{ kind: string; devices?: number; measurements?: number; sessions?: number }>(
        `/api/system/backups/${encodeURIComponent(file.name)}/restore`);
      toast.ok(r.kind === "data"
        ? t("set.backup.restoredData", { m: r.measurements ?? 0, s: r.sessions ?? 0 })
        : t("set.backup.restoredConfig", { n: r.devices ?? 0 }));
      void reloadSettings();
      load();
    } catch (e) { fail(e); } finally { setBusy(false); }
  };
  const download = async (file: BackupFile) => {
    try {
      // Mit Anmeldung laden (ein einfacher Link trüge kein Token)
      const token = useAuth.getState().token;
      const resp = await fetch(`/api/system/backups/${encodeURIComponent(file.name)}/download`,
        { headers: token ? { Authorization: `Bearer ${token}` } : {} });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      saveBlob(await resp.blob(), file.name);
    } catch (e) { fail(e); }
  };

  const kindLabel = (kind: BackupFile["kind"]) =>
    kind === "data" ? t("set.backup.kindData") : kind === "config" ? t("set.backup.kindConfig") : t("set.backup.kindMigration");
  const volume = overview?.storage.volumes[0];
  const spool = overview?.measurements?.spool_bytes ?? 0;

  return (
    <Card>
      <CardHead title={t("set.sec.backup")} sub={t("set.backup.help")} />
      <div className="row wrap">
        <ActionButton onClick={exportConfig}><Icon name="download" size={16} /> {t("set.backup.export")}</ActionButton>
        <Button onClick={() => fileRef.current?.click()}><Icon name="export" size={16} /> {t("set.backup.import")}</Button>
        <input ref={fileRef} type="file" accept=".json,application/json" hidden
               onChange={(e) => e.target.files?.[0] && void importConfig(e.target.files[0])} />
      </div>

      <h3 className="group-title">{t("set.backup.auto")}</h3>
      <p className="field-help">{t("set.backup.autoHelp")}</p>
      {overview && (
        <p className="field-help strong">
          {overview.status.latest ? t("set.backup.last", { time: fmtTime(overview.status.latest) }) : t("set.backup.empty")}
        </p>
      )}
      <div className="form-actions start">
        <ActionButton disabled={busy} onClick={backupNow}>{t("set.backup.now")}</ActionButton>
      </div>

      {overview && overview.backups.length > 0 && (
        <>
          <h3 className="group-title">{t("set.backup.list")}</h3>
          <ul className="user-list">
            {overview.backups.slice(0, 12).map((file) => (
              <li key={file.name} className="user-row">
                <Icon name="download" size={18} />
                <div className="user-text">
                  <span className="user-name">{fmtTime(file.time)}</span>
                  <span className="user-state">{kindLabel(file.kind)} · {fmtBytes(file.bytes)}</span>
                </div>
                <div className="row tight">
                  {file.kind === "data" && <Button size="sm" onClick={() => download(file)}>{t("set.backup.download")}</Button>}
                  {file.kind !== "pre-migration" && (
                    <ActionButton size="sm" disabled={busy} onClick={() => restore(file)}>{t("set.backup.restore")}</ActionButton>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      {overview && (volume || overview.storage.database_bytes !== null) && (
        <>
          <h3 className="group-title">{t("set.backup.storage")}</h3>
          {overview.storage.warning && <p className="field-help strong" role="alert">{overview.storage.warning}</p>}
          <p className="field-help">
            {volume && t("set.backup.free", { free: fmtBytes(volume.free_bytes), total: fmtBytes(volume.total_bytes) })}
            {volume && overview.storage.database_bytes !== null && " · "}
            {overview.storage.database_bytes !== null && t("set.backup.database", { size: fmtBytes(overview.storage.database_bytes) })}
          </p>
          {spool > 0 && <p className="field-help strong">{t("set.backup.spool", { size: fmtBytes(spool) })}</p>}
        </>
      )}
    </Card>
  );
}

function saveBlob(blob: Blob, name: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

export function InfoSettings() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { server } = useServerVersion();
  return (
    <Card>
      <CardHead title={t("set.sec.info")} />
      <dl className="info-list">
        <dt>{t("set.info.version")}</dt><dd className="num">{server ?? APP_VERSION}</dd>
        <dt>{t("set.info.ui")}</dt><dd className="num">{APP_VERSION}</dd>
      </dl>
      <Button onClick={() => navigate("/changelog")}><Icon name="events" size={16} /> {t("changelog.title")}</Button>
    </Card>
  );
}
