import "./lib/storage"; // Speicherschlüssel übernehmen, bevor Stores lesen
import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { I18nProvider } from "./i18n";
import "./theme.css";
import "./legacy.css";
import "./mobile.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </React.StrictMode>,
);

// Service Worker nur im fertigen Build: schneller Start der Handy-App und
// gewohnte Oberfläche, wenn der Server kurz weg ist (siehe public/sw.js).
if (import.meta.env.PROD && "serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  });
}
