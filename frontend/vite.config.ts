import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev-Proxy: API und WebSocket zum lokalen Backend (uvicorn auf :8000)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api/ws": { target: "ws://localhost:8000", ws: true },
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
