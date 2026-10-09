/** Alle Versionen. */
import { useEffect, useState } from "react";
import { ChangelogList } from "../components/ChangelogList";
import { Link } from "react-router-dom";
import { Icon } from "../components/icons";
import { Callout, Card, PageHead } from "../components/ui";
import { GITHUB_ISSUES_URL } from "../lib/links";
import { useI18n } from "../i18n";
import { loadChangelog, type Changelog as Data } from "../lib/changelog";

export function Changelog() {
  const { t } = useI18n();
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { loadChangelog().then(setData).catch((e) => setError(String(e.message ?? e))); }, []);
  return (
    <div className="page">
      <PageHead title={t("changelog.title")} sub={data ? t("changelog.current", { version: data.version }) : undefined} />
      <Callout title={t("changelog.reportTitle")} action={
        <div className="btn-row">
          <Link className="btn ghost" to="/diagnose"><Icon name="pulse" size={16} /> {t("changelog.toDiagnose")}</Link>
          <a className="btn ghost" href={GITHUB_ISSUES_URL} target="_blank" rel="noopener noreferrer">
            <Icon name="github" size={16} /> {t("changelog.openIssue")}
          </a>
        </div>}>
        {t("changelog.reportText")}
      </Callout>
      <Card>
        {error && <p className="err-text">{error}</p>}
        {!data && !error && <div className="skeleton" style={{ height: 240 }} />}
        {data && <ChangelogList entries={data.entries} />}
      </Card>
    </div>
  );
}
