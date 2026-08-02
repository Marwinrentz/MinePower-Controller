import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { I18nProvider } from "./i18n";
import "./theme.css";
// Danach geladen: die Handy-Oberfläche überschreibt gezielt (alles unter .m-app)
import "./mobile/mobile.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </React.StrictMode>,
);
