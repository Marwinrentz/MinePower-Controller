/** Gerätegrafiken für die Detailseite.
 *
 *  Eigene Vektorformen, keine Herstellerbilder: Ein Foto oder eine
 *  nachgezeichnete Silhouette eines bestimmten Modells wäre fremdes Material,
 *  und MinePower steuert ohnehin ein Dutzend verschiedener Fahrzeuge und
 *  Heizstäbe. Gezeichnet ist deshalb die Gattung, nicht das Modell.
 *
 *  Beide Grafiken zeigen ihren Zustand in der Form selbst — Ladestand als
 *  gefüllter Anteil, laufender Betrieb am fließenden Kabel bzw. an steigender
 *  Wärme. Wer hinschaut, weiß Bescheid, bevor er eine Zahl gelesen hat.
 */

type CarProps = {
  /** Ladestand in Prozent, null wenn das Fahrzeug ihn nicht meldet. */
  soc: number | null;
  charging: boolean;
  /** Kabel gesteckt (auch ohne Ladung). */
  plugged: boolean;
  color?: string;
};

export function CarArt({ soc, charging, plugged, color = "var(--c-car)" }: CarProps) {
  const fill = soc == null ? 0 : Math.max(0, Math.min(100, soc)) / 100;

  return (
    <svg viewBox="0 0 420 200" className="art art-car" role="img"
         aria-label={soc == null ? "Fahrzeug" : `Fahrzeug, Ladestand ${Math.round(soc)} Prozent`}>
      <defs>
        <clipPath id="car-body-clip">
          {/* Deckungsgleich mit der Karosserie unten – der Ladestand füllt
              den Wagen von unten, statt daneben als Balken zu stehen. */}
          <path d="M46 128 C46 112 58 104 74 100 L118 74 C130 66 146 62 162 62
                   L258 62 C278 62 296 68 310 80 L338 100 C362 104 378 112 378 128
                   L378 140 C378 146 373 150 367 150 L57 150 C51 150 46 146 46 140 Z" />
        </clipPath>
        <linearGradient id="car-fill" x1="0" y1="1" x2="0" y2="0">
          <stop offset="0" stopColor={color} stopOpacity="0.55" />
          <stop offset="1" stopColor={color} stopOpacity="0.28" />
        </linearGradient>
      </defs>

      {/* Karosserie */}
      <path className="art-shell"
            d="M46 128 C46 112 58 104 74 100 L118 74 C130 66 146 62 162 62
               L258 62 C278 62 296 68 310 80 L338 100 C362 104 378 112 378 128
               L378 140 C378 146 373 150 367 150 L57 150 C51 150 46 146 46 140 Z" />

      {/* Ladestand als Füllung der Karosserie */}
      <g clipPath="url(#car-body-clip)">
        <rect x="46" y={150 - 88 * fill} width="332" height={88 * fill}
              fill="url(#car-fill)" className="art-level" />
      </g>

      {/* Fenster */}
      <path className="art-glass"
            d="M132 96 L166 74 C172 71 180 70 188 70 L206 70 L206 96 Z" />
      <path className="art-glass"
            d="M218 70 L252 70 C266 70 278 74 288 82 L306 96 L218 96 Z" />

      {/* Radkästen und Räder */}
      <circle className="art-tyre" cx="126" cy="150" r="27" />
      <circle className="art-rim" cx="126" cy="150" r="12" />
      <circle className="art-tyre" cx="300" cy="150" r="27" />
      <circle className="art-rim" cx="300" cy="150" r="12" />

      {/* Ladeklappe hinten links – dort, wo sie bei den meisten E-Autos sitzt */}
      <rect className={`art-port ${plugged ? "on" : ""}`} x="60" y="106" width="15" height="19" rx="4" />

      {plugged && (
        <g className={charging ? "art-cable live" : "art-cable"}>
          <path d="M60 116 C34 116 22 134 22 156 C22 176 30 186 30 186" />
          <circle cx="30" cy="188" r="4.5" className="art-plug" />
        </g>
      )}
    </svg>
  );
}

type TankProps = {
  /** Aktuelle Temperatur, null wenn das Gerät keine meldet. */
  temp: number | null;
  target: number;
  heating: boolean;
  color?: string;
};

export function TankArt({ temp, target, heating, color = "var(--c-water)" }: TankProps) {
  // Bezug ist die Spanne, die den Nutzer interessiert: kaltes Leitungswasser
  // bis Zieltemperatur. Eine Skala ab 0 °C würde den halben Speicher
  // dauerhaft gefüllt zeigen und damit nichts aussagen.
  const COLD = 15;
  const fill = temp == null ? 0
    : Math.max(0, Math.min(1, (temp - COLD) / Math.max(target - COLD, 1)));

  return (
    <svg viewBox="0 0 260 200" className="art art-tank" role="img"
         aria-label={temp == null ? "Warmwasserspeicher" : `Warmwasserspeicher, ${Math.round(temp)} Grad`}>
      <defs>
        <clipPath id="tank-clip">
          <rect x="86" y="24" width="88" height="152" rx="30" />
        </clipPath>
        <linearGradient id="tank-fill" x1="0" y1="1" x2="0" y2="0">
          <stop offset="0" stopColor={color} stopOpacity="0.62" />
          <stop offset="1" stopColor={color} stopOpacity="0.30" />
        </linearGradient>
      </defs>

      <rect className="art-shell" x="86" y="24" width="88" height="152" rx="30" />
      <g clipPath="url(#tank-clip)">
        <rect x="86" y={176 - 152 * fill} width="88" height={152 * fill}
              fill="url(#tank-fill)" className="art-level" />
      </g>

      {/* Heizstab: sitzt unten im Speicher, glüht beim Heizen */}
      <rect className={`art-rod ${heating ? "on" : ""}`} x="124" y="118" width="12" height="46" rx="6" />

      {/* Anschlüsse */}
      <path className="art-pipe" d="M130 24 L130 6" />
      <path className="art-pipe" d="M174 148 L206 148 L206 190" />
      <path className="art-pipe" d="M86 60 L54 60 L54 10" />

      {heating && (
        <g className="art-heat">
          <path d="M108 104 C104 96 110 92 108 84" />
          <path d="M130 100 C126 92 132 88 130 80" />
          <path d="M152 104 C148 96 154 92 152 84" />
        </g>
      )}
    </svg>
  );
}
