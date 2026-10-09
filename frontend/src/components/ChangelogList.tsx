/** Versionen mit Neu / Geändert / Behoben. */
import { useI18n } from "../i18n";
import { fmtDate } from "../lib/format";
import { SECTIONS, type ChangelogEntry } from "../lib/changelog";

export function ChangelogList({ entries }: { entries: ChangelogEntry[] }) {
  const { t } = useI18n();
  return (
    <div className="changelog">
      {entries.map((e) => (
        <section key={e.version} className="changelog-version">
          <h3>
            <span className="num">{e.version}</span>
            <small>{fmtDate(e.date)}</small>
          </h3>
          {SECTIONS.map(([key, label]) => e[key].length > 0 && (
            <div key={key} className="changelog-group">
              <h4>{t(label)}</h4>
              <ul>{e[key].map((line) => <li key={line}>{line}</li>)}</ul>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
