/** Welche Darstellung ist aktiv? Die BETA-Oberfläche ist dieselbe Anwendung
 *  mit neuem Anstrich und neuen Grafiken; die Wahl steht je Konto im Server. */
import { useAuth } from "../store/auth";

export function useBetaUi(): boolean {
  return useAuth((s) => s.user?.prefs?.beta_ui === true);
}
