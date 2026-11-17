/** Fehlergrenze: Ein Fehler in einer Ansicht legt nicht die ganze Oberfläche
 *  lahm. Ohne sie zeigt React nach einer Ausnahme beim Zeichnen eine leere
 *  Seite – ohne Hinweis und ohne Weg zurück. */
import { Component, type ErrorInfo, type ReactNode } from "react";
import { tr } from "../i18n";

type Props = { children: ReactNode; /** Wechselt der Wert (z. B. die Adresse), wird der Fehler verworfen. */ resetKey?: string };
type State = { error: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Ansicht abgestürzt:", error, info.componentStack);
  }

  componentDidUpdate(prev: Props): void {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="page" role="alert">
        <div className="card load-error">
          <h2 className="card-title">{tr("error.viewTitle")}</h2>
          <p className="field-help">{tr("error.viewText")}</p>
          <p className="field-help err-text">{error.message}</p>
          <div className="form-actions start">
            <button type="button" className="btn primary" onClick={() => this.setState({ error: null })}>
              {tr("error.retry")}
            </button>
            <button type="button" className="btn" onClick={() => window.location.reload()}>
              {tr("error.reload")}
            </button>
          </div>
        </div>
      </div>
    );
  }
}
