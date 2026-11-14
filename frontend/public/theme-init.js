// Farbschema vor dem ersten Bild setzen (verhindert helles Aufblitzen im
// Dunkelmodus). Eigene Datei statt Inline-Skript: Die Content-Security-Policy
// erlaubt nur Skripte vom eigenen Ursprung.
(function () {
  try {
    var m = localStorage.getItem("mp_theme") || localStorage.getItem("sc_theme") || "system";
    var dark = m === "dark" || (m === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.querySelector('meta[name="theme-color"]').content = dark ? "#0d1015" : "#f3f5f8";
    document.querySelector('meta[name="apple-mobile-web-app-status-bar-style"]').content = dark ? "black-translucent" : "default";
  } catch (e) { /* Speicher gesperrt: Standard hell */ }
})();
