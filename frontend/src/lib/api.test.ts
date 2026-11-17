import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "./api";
import { useAuth } from "../store/auth";

function respond(status: number, body: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  useAuth.setState({ token: null, user: null });
});

describe("REST-Client", () => {
  it("schickt das Token mit und liefert JSON", async () => {
    useAuth.setState({ token: "abc" });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(200, { ok: true }));
    await expect(api<{ ok: boolean }>("/api/x")).resolves.toEqual({ ok: true });
    const headers = fetchMock.mock.calls[0][1]?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer abc");
  });

  it("macht aus Feldfehlern eine lesbare Meldung mit Code", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(422, {
      detail: { code: "invalid_config", message: "Eingabe ungültig: Port: Port 1–65535",
                fields: [{ key: "port", label: "Port", error: "Port 1–65535" }] },
    }));
    const error = await api("/api/devices", { method: "POST" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(422);
    expect(apiError.code).toBe("invalid_config");
    expect(apiError.fields?.[0].key).toBe("port");
  });

  it("nennt bei Pydantic-Fehlern das Feld", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(422, {
      detail: [{ loc: ["body", "password"], msg: "String should have at least 8 characters" }],
    }));
    const error = (await api("/api/auth/users", { method: "POST" }).catch((e: unknown) => e)) as ApiError;
    expect(error.message).toContain("password");
  });

  it("meldet bei 401 nicht sofort ab, sondern prüft erst gegen", async () => {
    useAuth.setState({ token: "abc" });
    const verify = vi.fn().mockResolvedValue(true);
    useAuth.setState({ verify });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(401, { detail: "abgelaufen" }));
    await expect(api("/api/x")).rejects.toMatchObject({ status: 401 });
    expect(verify).toHaveBeenCalledOnce();
    expect(useAuth.getState().token).toBe("abc");
  });

  it("gibt bei 204 nichts zurück", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api("/api/x", { method: "DELETE" })).resolves.toBeUndefined();
  });
});
