import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useFetch } from "./useFetch";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function View() {
  const q = useFetch<{ value: number }>("/api/x");
  return (
    <div>
      <span data-testid="state">{q.loading ? "lädt" : q.error ? `Fehler: ${q.error}` : `Wert ${q.data?.value}`}</span>
      <span data-testid="data">{q.data ? q.data.value : "-"}</span>
      <button type="button" onClick={q.reload}>neu</button>
    </div>
  );
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("Daten laden", () => {
  it("zeigt den Fehler statt ewig zu laden und behält nach einem Fehlschlag den letzten Stand", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(json({ detail: "Datenbank weg" }, 500));
    render(<View />);
    expect(screen.getByTestId("state").textContent).toBe("lädt");
    await waitFor(() => expect(screen.getByTestId("state").textContent).toBe("Fehler: Datenbank weg"));

    fetchMock.mockResolvedValueOnce(json({ value: 7 }));
    await act(async () => { screen.getByRole("button").click(); });
    await waitFor(() => expect(screen.getByTestId("state").textContent).toBe("Wert 7"));

    fetchMock.mockResolvedValueOnce(json({ detail: "wieder weg" }, 500));
    await act(async () => { screen.getByRole("button").click(); });
    await waitFor(() => expect(screen.getByTestId("state").textContent).toContain("Fehler"));
    expect(screen.getByTestId("data").textContent).toBe("7");
  });
});
