/** Zeitfenster für den Modus 'Zeitplan' (Warmwasser).
 *
 *  Liste der Fenster eines Geräts, darunter eine Zeile zum Anlegen:
 *  Wochentage als Chips, Beginn und Ende als Uhrzeit. Fenster über
 *  Mitternacht (22:00–06:00) sind erlaubt; der Tag zählt ab Beginn.
 */
import { useCallback, useEffect, useState } from "react";
import { tr, useI18n } from "../i18n";
import { del, get, post } from "../lib/api";
import { toast } from "../store/toast";
import { Icon } from "./icons";
import { Button } from "./ui";

type Schedule = { id: number; device_id: number; days_mask: number; start_time: string; end_time: string; enabled: boolean };

/** Kurznamen Mo–So aus den Texten. */
const dayNames = () => tr("schedule.dayNames").split(",");
const ALL = 127;
const WORKDAYS = 31;
const WEEKEND = 96;

/** 'Mo–Fr', 'Sa, So', 'täglich' */
export function daysLabel(mask: number): string {
  if (mask === ALL) return tr("schedule.daily");
  if (mask === WORKDAYS) return tr("schedule.workdays");
  if (mask === WEEKEND) return tr("schedule.weekend");
  return dayNames().filter((_, i) => mask & (1 << i)).join(", ");
}

export function ScheduleEditor({ deviceId }: { deviceId: number }) {
  const { t } = useI18n();
  const [items, setItems] = useState<Schedule[] | null>(null);
  const [mask, setMask] = useState(ALL);
  const [start, setStart] = useState("06:00");
  const [end, setEnd] = useState("08:00");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const all = await get<Schedule[]>("/api/settings/schedules");
      setItems(all.filter((s) => s.device_id === deviceId));
    } catch (e) {
      toast.err((e as Error).message);
      setItems([]);
    }
  }, [deviceId]);

  useEffect(() => { void load(); }, [load]);

  const add = async () => {
    if (!mask) return toast.err(t("schedule.noDay"));
    if (start === end) return toast.err(t("schedule.sameTime"));
    setBusy(true);
    try {
      await post("/api/settings/schedules", { device_id: deviceId, days_mask: mask, start_time: start, end_time: end });
      toast.ok(t("schedule.saved"));
      await load();
    } catch (e) {
      toast.err((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: number) => {
    try {
      await del(`/api/settings/schedules/${id}`);
      await load();
    } catch (e) {
      toast.err((e as Error).message);
    }
  };

  return (
    <div className="sched">
      {items && items.length === 0 && (
        <p className="sched-empty">{t("schedule.empty")}</p>
      )}
      {items && items.length > 0 && (
        <ul className="sched-list">
          {items.map((s) => (
            <li key={s.id}>
              <Icon name="clock" size={18} />
              <span className="num">{s.start_time}–{s.end_time}</span>
              <span className="sched-days">{daysLabel(s.days_mask)}</span>
              <button type="button" className="sched-del" aria-label={t("schedule.delete", { range: `${s.start_time}–${s.end_time}` })}
                      onClick={() => void remove(s.id)}>
                <Icon name="close" size={18} />
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="sched-add">
        <span className="sched-new">{t("schedule.new")}</span>
        <div className="sched-days-pick" role="group" aria-label={t("schedule.days")}>
          {dayNames().map((d, i) => {
            const on = (mask & (1 << i)) !== 0;
            return (
              <button key={d} type="button" className={`day-chip ${on ? "on" : ""}`} aria-pressed={on}
                      onClick={() => setMask(mask ^ (1 << i))}>{d}</button>
            );
          })}
        </div>
        <div className="sched-times">
          <label>
            <span>{t("common.from")}</span>
            <input type="time" value={start} onChange={(e) => setStart(e.target.value)} required />
          </label>
          <label>
            <span>{t("common.to")}</span>
            <input type="time" value={end} onChange={(e) => setEnd(e.target.value)} required />
          </label>
          <Button variant="primary" loading={busy} onClick={() => void add()}>
            <Icon name="check" size={18} /> {t("common.add")}
          </Button>
        </div>
      </div>
    </div>
  );
}
