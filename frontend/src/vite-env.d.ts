/// <reference types="vite/client" />

/** Versionsnummer aus package.json, beim Build eingesetzt (vite.config.ts). */
declare const __APP_VERSION__: string;

// ECharts liefert für die Sprachdateien unter lib/i18n keine Typen mit.
declare module "echarts/lib/i18n/langDE.js" {
  const locale: Parameters<typeof import("echarts").registerLocale>[1];
  export default locale;
}
