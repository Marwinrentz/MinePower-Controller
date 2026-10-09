/** Schlüssel im Browser-Speicher.
 *
 *  Bis 2.17 hießen sie `sc_*` (Altname 'SolarCharge"). Beim ersten Start
 *  werden die alten Einträge übernommen und gelöscht – niemand muss sich
 *  nach dem Update neu anmelden. Muss vor allen anderen Modulen laufen,
 *  deshalb in main.tsx als erster Import.
 */
const KEYS = ["token", "user", "theme", "lang"] as const;

export const STORAGE = {
  token: "mp_token",
  user: "mp_user",
  theme: "mp_theme",
  lang: "mp_lang",
} as const;

export function migrateStorage(): void {
  try {
    for (const k of KEYS) {
      const old = localStorage.getItem(`sc_${k}`);
      if (old !== null && localStorage.getItem(`mp_${k}`) === null) localStorage.setItem(`mp_${k}`, old);
      if (old !== null) localStorage.removeItem(`sc_${k}`);
    }
  } catch {
    /* Speicher gesperrt (privates Fenster) */
  }
}

migrateStorage();
