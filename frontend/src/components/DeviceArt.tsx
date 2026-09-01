/** Gerätegrafiken für die Detailseiten.
 *
 *  Eigene Vektorformen, keine Herstellerbilder: Ein Foto oder die
 *  nachgezeichnete Silhouette eines bestimmten Modells wäre fremdes Material,
 *  und MinePower steuert ohnehin ein Dutzend Fahrzeuge und Heizstäbe.
 *  Gezeichnet ist die Gattung, nicht das Modell.
 *
 *  **Der Füllstand ist die Illustration.** Beide Objekte zeigen ihren Zustand
 *  in der eigenen Form: Das Fahrzeug füllt sich von unten mit dem Ladestand,
 *  der Speicher mit der Wärme. Ein Balken daneben bräuchte eine zweite
 *  Blickbewegung und eine Beschriftung, um dasselbe zu sagen.
 *
 *  Vier Entscheidungen, die den Unterschied machen:
 *
 *  * Radläufe sind **echte Kreisbögen um die Radmitte**, kein Parabelbauch.
 *    Vorher waren die Bögen quadratische Kurven mit frei gewähltem
 *    Kontrollpunkt – ihr Scheitel lag über dem Reifen, und dazwischen klaffte
 *    eine Lücke. Das Rad steckte nicht im Kotflügel, es lag daneben.
 *  * Alle Zierteile **folgen der Karosseriekante**, statt als freie Polygone
 *    darauf zu liegen. Scheinwerfer und Rückleuchte waren vorher graue
 *    Vierecke im Nichts und sahen aus wie aufgeklebt.
 *  * Der Ladestand endet am **Fenstersims**, nicht am Dach. Der Akku sitzt im
 *    Fahrzeugboden – lief die Füllung bis oben, schwappte sie über die
 *    Scheiben und der Innenraum färbte sich mit.
 *  * Die Wasseroberfläche ist eine **Ellipse**, keine Linie. Sie folgt der
 *    Wölbung des Zylinders; erst dadurch wirkt es wie ein Gefäß, in dem etwas
 *    steht, und nicht wie ein farbiges Rechteck.
 *
 *  Alle Verläufe und Masken bekommen über `useId()` eindeutige Namen. Vorher
 *  standen dort feste Zeichenketten – sobald zwei Geräte gleichzeitig auf dem
 *  Schirm waren, griffen beide auf dieselbe Maske zu und die Füllung des
 *  zweiten brach zusammen.
 */
import { useId } from "react";

/* ------------------------------------------------------------------ Fahrzeug */

/** Maße des Fahrzeugs. Alles Weitere leitet sich daraus ab, damit Rad,
 *  Radlauf und Schweller nicht auseinanderlaufen, wenn eines davon wandert. */
const C = {
  GROUND: 194,
  SILL: 172,     // Unterkante der Karosserie
  BELT: 104,     // Fenstersims – hier endet die Füllung
  ROOF: 46,
  WHEEL_Y: 162,
  TYRE: 32,
  ARCH: 46,      // Radlaufradius: Reifen plus Luft
  FRONT_X: 128,
  REAR_X: 352,
};

/** Karosserie – einmal definiert, dreimal gebraucht: Fläche, Maske, Kontur.
 *
 *  Die beiden `A`-Befehle sind die Radläufe: Halbkreise mit Radius 46 um die
 *  jeweilige Radmitte, exakt auf der Schwellerlinie angesetzt. Ein Halbkreis,
 *  weil Anfang und Ende genau einen Durchmesser auseinanderliegen – dadurch
 *  ist der Scheitel rechnerisch bei `SILL − ARCH` und damit garantiert über
 *  dem Reifen, egal wie die Maße später verschoben werden. */
const CAR_BODY = `
  M 24 162
  C 24 140 32 126 52 118
  L 114 102
  C 126 99 134 94 142 86
  L 178 56
  C 187 49 197 46 210 46
  L 288 46
  C 313 46 329 52 341 64
  L 381 100
  L 429 110
  C 449 115 456 128 456 146
  L 456 164
  C 456 169 452 172 447 172
  L 398 172
  A 46 46 0 0 0 306 172
  L 174 172
  A 46 46 0 0 0 82 172
  L 33 172
  C 27 172 24 169 24 163 Z`;

type CarProps = {
  /** Ladestand in Prozent, null wenn das Fahrzeug ihn nicht meldet. */
  soc: number | null;
  charging: boolean;
  /** Kabel gesteckt (auch ohne Ladung). */
  plugged: boolean;
};

export function CarArt({ soc, charging, plugged }: CarProps) {
  const uid = useId().replace(/:/g, "");
  const clip = `car-clip-${uid}`;
  const grad = `car-grad-${uid}`;
  const glass = `car-glass-${uid}`;

  const pct = soc == null ? 0 : Math.max(0, Math.min(100, soc)) / 100;
  const level = C.SILL - (C.SILL - C.BELT) * pct;

  return (
    <svg viewBox="0 0 480 240" className="art art-car" role="img"
         aria-label={soc == null ? "Fahrzeug" : `Fahrzeug, Ladestand ${Math.round(soc)} Prozent`}>
      <defs>
        <clipPath id={clip}><path d={CAR_BODY} /></clipPath>
        <linearGradient id={grad} x1="0" y1="1" x2="0" y2="0">
          <stop offset="0" stopColor="var(--c-car)" stopOpacity="0.9" />
          <stop offset="1" stopColor="var(--c-car)" stopOpacity="0.45" />
        </linearGradient>
        <linearGradient id={glass} x1="0" y1="0" x2="0.25" y2="1">
          <stop offset="0" stopColor="var(--art-glass-a)" />
          <stop offset="1" stopColor="var(--art-glass-b)" />
        </linearGradient>
      </defs>

      {/* Standschatten auf Bodenhöhe: verankert das Fahrzeug, statt es
          schweben zu lassen. Schmaler als die Karosserie, damit er wie
          Schatten wirkt und nicht wie ein Sockel. */}
      <ellipse className="art-shadow" cx="240" cy={C.GROUND + 4} rx="196" ry="7" />

      <path className="art-body" d={CAR_BODY} />

      {/* Ladestand füllt den unteren Wagenkörper bis zum Fenstersims */}
      <g clipPath={`url(#${clip})`}>
        <rect x="16" y={level} width="450" height={C.SILL - level + 4}
              fill={`url(#${grad})`} className="art-level" />
        {pct > 0.02 && <rect x="16" y={level} width="450" height="2" className="art-surface" />}
      </g>

      {/* Kontur zuletzt, damit die Füllung sie nicht anknabbert */}
      <path className="art-body-line" d={CAR_BODY} />

      {/* Fensterband: zwei Scheiben, dazwischen bleibt die Karosseriefarbe
          als B-Säule stehen. */}
      <path className="art-glass" fill={`url(#${glass})`}
            d="M 152 100 L 182 62 C 190 55 199 52 210 52 L 246 52 L 246 100 Z" />
      <path className="art-glass" fill={`url(#${glass})`}
            d="M 256 52 L 288 52 C 308 52 322 57 332 68 L 358 100 L 256 100 Z" />

      {/* Türfuge und Griffe – zwischen den Radläufen, wo wirklich Türen sind */}
      <path className="art-detail" d="M 250 106 L 250 168" />
      <rect className="art-trim" x="224" y="118" width="18" height="5" rx="2.5" />
      <rect className="art-trim" x="290" y="118" width="18" height="5" rx="2.5" />

      {/* Leuchten folgen der Karosseriekante, statt als Vierecke darauf zu
          liegen: vorn ein schmaler Keil, der der Bugrundung folgt, hinten ein
          schlankes Band knapp unter der Heckkante. Die Rückleuchte sitzt
          bewusst höher als die Ladeklappe – vorher lagen beide auf einer Höhe
          direkt nebeneinander und verschmolzen zu einem grauen Klecks. */}
      <path className="art-lamp" d="M 26 134 C 33 129 43 126 55 125 L 55 135 C 42 136 32 137 26 138 Z" />
      <path className="art-lamp" d="M 450 124 C 453 129 455 135 455 141 L 432 141 L 432 122 Z" />

      {/* Räder: Reifen, Felge, Nabe – drei Ringe statt einer flachen Scheibe */}
      {[C.FRONT_X, C.REAR_X].map((cx) => (
        <g key={cx}>
          <circle className="art-tyre" cx={cx} cy={C.WHEEL_Y} r={C.TYRE} />
          <circle className="art-rim" cx={cx} cy={C.WHEEL_Y} r="18" />
          <circle className="art-hub" cx={cx} cy={C.WHEEL_Y} r="5.5" />
        </g>
      ))}

      {/* Ladeklappe im hinteren Kotflügel, klein und bündig */}
      <rect className={`art-port ${plugged ? "on" : ""}`} x="400" y="128" width="15" height="19" rx="5" />

      {plugged && (
        <g className={charging ? "art-cable live" : "art-cable"}>
          <path d="M 407 149 C 405 172 420 182 438 190 C 447 194 452 200 452 210" />
          <circle className="art-plug" cx="452" cy="214" r="6" />
        </g>
      )}
    </svg>
  );
}

/* --------------------------------------------------------------- Speicher */

/** Schlanker Zylinder statt Fass: Ein Warmwasserspeicher ist deutlich höher
 *  als breit. Erst mit diesem Verhältnis liest sich die Form als Speicher. */
const T = { X: 100, W: 88, TOP: 34, BOTTOM: 190, RY: 12, ROD_Y: 164 };

type TankProps = {
  /** Aktuelle Temperatur, null wenn das Gerät keine meldet. */
  temp: number | null;
  target: number;
  heating: boolean;
};

export function TankArt({ temp, target, heating }: TankProps) {
  const uid = useId().replace(/:/g, "");
  const clip = `tank-clip-${uid}`;
  const grad = `tank-grad-${uid}`;
  const glow = `tank-glow-${uid}`;

  // Bezug ist die Spanne, die den Nutzer interessiert: kaltes Leitungswasser
  // bis Zieltemperatur. Eine Skala ab 0 °C hielte den Speicher dauerhaft halb
  // gefüllt und sagte damit nichts aus.
  const COLD = 15;
  const pct = temp == null ? 0
    : Math.max(0, Math.min(1, (temp - COLD) / Math.max(target - COLD, 1)));

  const { X, W, TOP, BOTTOM, RY, ROD_Y } = T;
  const rx = W / 2;
  const cx = X + rx;
  const level = BOTTOM - (BOTTOM - TOP) * pct;
  const shell = `M ${X} ${TOP} L ${X} ${BOTTOM} A ${rx} ${RY} 0 0 0 ${X + W} ${BOTTOM}
                 L ${X + W} ${TOP} A ${rx} ${RY} 0 0 0 ${X} ${TOP} Z`;

  return (
    <svg viewBox="0 0 300 240" className="art art-tank" role="img"
         aria-label={temp == null ? "Warmwasserspeicher" : `Warmwasserspeicher, ${Math.round(temp)} Grad`}>
      <defs>
        <clipPath id={clip}>
          <path d={`M ${X} ${TOP} A ${rx} ${RY} 0 0 1 ${X + W} ${TOP}
                    L ${X + W} ${BOTTOM} A ${rx} ${RY} 0 0 1 ${X} ${BOTTOM} Z`} />
        </clipPath>
        <linearGradient id={grad} x1="0" y1="1" x2="0" y2="0">
          <stop offset="0" stopColor="var(--c-water)" stopOpacity="0.30" />
          <stop offset="1" stopColor="var(--c-water)" stopOpacity="0.66" />
        </linearGradient>
        <radialGradient id={glow} cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor="var(--c-waste)" stopOpacity="0.5" />
          <stop offset="1" stopColor="var(--c-waste)" stopOpacity="0" />
        </radialGradient>
      </defs>

      <ellipse className="art-shadow" cx={cx} cy={BOTTOM + RY + 6} rx="66" ry="6" />

      {/* Zwei Anschlüsse: warm oben heraus, kalt unten heraus, beide nach
          rechts und aus dem Bild heraus. Sie enden nicht mehr frei in der
          Luft – ein Rohr, das im Nichts aufhört, liest sich als Fehler in der
          Zeichnung; eines, das den Rand verlässt, führt weiter in die Wand.
          Beide liegen hinter dem Mantel, damit sie daran ansetzen statt davor
          zu schweben, und sind schlanker als der Speicher, den sie versorgen. */}
      <path className="art-pipe" d={`M ${cx} ${TOP} L ${cx} 22 L 300 22`} />
      <path className="art-pipe" d={`M ${X + W - 12} ${BOTTOM} L 300 ${BOTTOM}`} />

      <path className="art-body" d={shell} />

      <g clipPath={`url(#${clip})`}>
        <rect x={X - 4} y={level} width={W + 8} height={BOTTOM + RY - level}
              fill={`url(#${grad})`} className="art-level" />
        {pct > 0.015 && (
          <ellipse className="art-surface-e" cx={cx} cy={level} rx={rx} ry={RY} />
        )}
        {heating && <ellipse cx={cx} cy={ROD_Y} rx={rx + 10} ry="32" fill={`url(#${glow})`} />}
        {/* Der Heizstab liegt IM Speicher – deshalb innerhalb der Maske. */}
        <g className={`art-rod ${heating ? "on" : ""}`}>
          <rect x={cx - 26} y={ROD_Y} width="54" height="8" rx="4" />
        </g>
        {/* Die Wärmefahnen steigen vom Stab auf und liegen deshalb ebenfalls
            in der Maske. Vorher standen sie darüber und blieben auch dann
            sichtbar, wenn der Speicher an dieser Stelle längst leer war –
            Hitzeschlieren in der Luft über dem Wasserspiegel. */}
        {heating && (
          <g className="art-heat">
            <path d={`M ${cx - 20} ${ROD_Y - 8} C ${cx - 25} ${ROD_Y - 20} ${cx - 15} ${ROD_Y - 28} ${cx - 20} ${ROD_Y - 42}`} />
            <path d={`M ${cx + 2} ${ROD_Y - 10} C ${cx - 3} ${ROD_Y - 22} ${cx + 7} ${ROD_Y - 30} ${cx + 2} ${ROD_Y - 46}`} />
            <path d={`M ${cx + 22} ${ROD_Y - 8} C ${cx + 17} ${ROD_Y - 20} ${cx + 27} ${ROD_Y - 28} ${cx + 22} ${ROD_Y - 42}`} />
          </g>
        )}
      </g>

      {/* Flansch sitzt in der Wand, nicht daneben */}
      <rect className={`art-rod-cap ${heating ? "on" : ""}`}
            x={X + W - 9} y={ROD_Y - 6} width="16" height="20" rx="4" />

      <ellipse className="art-cap" cx={cx} cy={TOP} rx={rx} ry={RY} />
      <path className="art-body-line" d={shell} />

      {/* Isolierbänder, dezent */}
      <path className="art-detail" d={`M ${X} 86 A ${rx} ${RY} 0 0 0 ${X + W} 86`} />
      <path className="art-detail" d={`M ${X} 134 A ${rx} ${RY} 0 0 0 ${X + W} 134`} />
    </svg>
  );
}
