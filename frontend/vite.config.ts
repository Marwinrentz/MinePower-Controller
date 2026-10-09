import { readFileSync } from "node:fs";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Eine Quelle für die Version: Datei VERSION im Repository (im Image: Build-Kontext)
const VERSION = (() => {
  for (const p of ["../VERSION", "./VERSION"]) {
    try { return readFileSync(new URL(p, import.meta.url), "utf-8").trim(); } catch { /* weiter */ }
  }
  return process.env.npm_package_version ?? "dev";
})();

// Dev-Proxy: API und WebSocket zum lokalen Backend (uvicorn auf :8000)
export default defineConfig({
  plugins: [react()],
  // Versionsnummer für den Abgleich mit dem Backend (src/lib/useVersion.ts).
  define: { __APP_VERSION__: JSON.stringify(VERSION) },
  server: {
    port: 5173,
    proxy: {
      "/api/ws": { target: "ws://localhost:8000", ws: true },
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
