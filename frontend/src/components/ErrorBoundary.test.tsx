import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

afterEach(cleanup);

function Broken({ fail }: { fail: boolean }) {
  if (fail) throw new Error("Wert fehlt");
  return <p>Inhalt</p>;
}

describe("Fehlergrenze", () => {
  it("zeigt statt einer leeren Seite die Ursache und einen Weg zurück", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<ErrorBoundary><Broken fail /></ErrorBoundary>);
    expect(screen.getByRole("alert").textContent).toContain("Wert fehlt");
    expect(screen.getAllByRole("button").length).toBe(2);
  });

  it("verwirft den Fehler, wenn die Ansicht wechselt", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { rerender } = render(<ErrorBoundary resetKey="/a"><Broken fail /></ErrorBoundary>);
    expect(screen.queryByText("Inhalt")).toBeNull();
    rerender(<ErrorBoundary resetKey="/b"><Broken fail={false} /></ErrorBoundary>);
    expect(screen.getByText("Inhalt")).toBeTruthy();
  });

  it("lässt sich mit 'Erneut versuchen' zurücksetzen", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    let fail = true;
    function Flaky() {
      if (fail) throw new Error("kurz gestört");
      return <p>Inhalt</p>;
    }
    render(<ErrorBoundary><Flaky /></ErrorBoundary>);
    fail = false;
    fireEvent.click(screen.getAllByRole("button")[0]);
    expect(screen.getByText("Inhalt")).toBeTruthy();
  });
});
