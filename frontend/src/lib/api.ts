/** REST-Client mit JWT aus dem Auth-Store. */
import { useAuth } from "../store/auth";

export class ApiError extends Error {
  status: number;
  /** Fehlercode des Backends (z. B. "category_limit", "invalid_config") */
  code?: string;
  /** Feldfehler bei "invalid_config" */
  fields?: { key: string; label: string; error: string }[];
  constructor(status: number, message: string, code?: string, fields?: ApiError["fields"]) {
    super(message);
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}

export async function api<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const token = useAuth.getState().token;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 401) {
    // Gegenprüfen statt blind abmelden: Eine einzelne 401 kann auch aus einem
    // Wettlauf beim Token-Wechsel stammen. Lehnt /api/auth/me ebenfalls ab,
    // meldet verify() ab; sonst bleibt die Anmeldung bestehen und der Aufrufer
    // sieht nur diesen einen Fehlschlag.
    void useAuth.getState().verify();
    throw new ApiError(401, "Sitzung abgelaufen");
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    let code: string | undefined;
    let fields: ApiError["fields"];
    try {
      const body = await resp.json();
      const d = body.detail;
      if (d && typeof d === "object" && !Array.isArray(d)) {
        detail = d.message ?? JSON.stringify(d);
        code = d.code;
        fields = d.fields;
      } else if (Array.isArray(d)) {
        // Pydantic-Validierung: erstes Feld nennen
        detail = d.map((e: { loc?: string[]; msg?: string }) => `${(e.loc ?? []).slice(-1)[0] ?? ""}: ${e.msg ?? ""}`).join("; ");
      } else {
        detail = d ?? JSON.stringify(body);
      }
    } catch {
      /* Klartext */
    }
    throw new ApiError(resp.status, detail, code, fields);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

export const get = <T,>(path: string) => api<T>(path);
export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined });
export const patch = <T,>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const put = <T,>(path: string, body: unknown) =>
  api<T>(path, { method: "PUT", body: JSON.stringify(body) });
export const del = (path: string) => api(path, { method: "DELETE" });
