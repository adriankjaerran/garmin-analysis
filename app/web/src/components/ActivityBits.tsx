// Small shared pieces for activities: sport dot, speed/pace cell, table column set, summary kv.
import type { Activity } from "../lib/api";
import { SPORT_LABEL, dur, km, num, pace, kmh, sportColor, stypeLabel, typeLabel, time } from "../lib/format";
import type { Col } from "./Table";

export const SportDot = ({ sport }: { sport: string }) => <span class="dot" style={{ background: sportColor(sport) }} title={SPORT_LABEL[sport]} />;

/** pace for runs/hikes on foot, km/h otherwise */
export function speedText(a: Activity): string {
  const s = a.dist_km && (a.moving_s || a.dur_s) ? (a.dist_km * 1000) / (a.moving_s || a.dur_s)! : null;
  if (!s) return "—";
  return a.sport === "run" ? pace(s) + " /km" : kmh(s) + " km/h";
}

export const what = (a: Activity) => [stypeLabel(a.stype) || typeLabel(a.type), a.sig].filter(Boolean).join(" · ");

export function activityCols(opts: { date?: boolean } = {}): Col<Activity>[] {
  const cols: Col<Activity>[] = [];
  if (opts.date !== false) cols.push({ key: "start", label: "Date", render: (a) => <span>{a.date} <span class="muted">{time(a.start)}</span></span> });
  else cols.push({ key: "start", label: "Start", render: (a) => time(a.start) });
  cols.push(
    { key: "type", label: "Type", render: (a) => <span><SportDot sport={a.sport} />{what(a)}</span>, sort: (a) => a.sport + (a.stype || a.type) },
    { key: "dist_km", label: "km", num: true, render: (a) => km(a.dist_km) },
    { key: "moving_s", label: "Moving", num: true, render: (a) => dur(a.moving_s ?? a.dur_s) },
    { key: "speed", label: "Pace / speed", num: true, render: speedText, sort: (a) => (a.dist_km && a.moving_s ? a.dist_km / a.moving_s : null) },
    { key: "hr", label: "HR", num: true, render: (a) => num(a.hr) },
    { key: "elev", label: "Climb", num: true, render: (a) => num(a.elev) },
    { key: "place", label: "Place", render: (a) => a.place ?? a.location ?? "—" },
    { key: "track", label: "GPS", render: (a) => <span><span class={"dot " + (a.track ? "good" : a.fit ? "partial" : "none")} />{a.track ? "track" : a.fit ? "no GPS" : "no file"}</span> },
  );
  return cols;
}

export function ActivityKV({ a }: { a: Activity }) {
  return (
    <dl class="kv two">
      <dt>Date</dt><dd><a href={`/day/${a.date}`}>{a.date}</a> {time(a.start)}</dd>
      <dt>Type</dt><dd><SportDot sport={a.sport} />{what(a)}{a.type_detail ? ` (${a.type_detail})` : ""}</dd>
      <dt>Distance</dt><dd>{a.dist_km ? `${a.dist_km.toFixed(2)} km` : "—"}</dd>
      <dt>Time</dt><dd>{dur(a.dur_s)} <span class="muted">moving {dur(a.moving_s)}</span></dd>
      <dt>Pace / speed</dt><dd>{speedText(a)}</dd>
      <dt>Heart rate</dt><dd>{num(a.hr)} <span class="muted">max {num(a.maxhr)}</span></dd>
      <dt>Climb</dt><dd>{a.elev !== null ? `${a.elev} m` : "—"}</dd>
      <dt>Place</dt><dd>{a.place ? <a href={`/places?place=${encodeURIComponent(a.place)}`}>{a.place}</a> : a.location ?? "—"}</dd>
      <dt>Route</dt><dd>{a.route ? <a href={`/places?route=${a.route}`}>{a.route}</a> : "—"}</dd>
      <dt>Load / TE</dt><dd>{num(a.load)} / {a.te ?? "—"}</dd>
    </dl>
  );
}
