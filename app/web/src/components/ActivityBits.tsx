// Small shared pieces for activities: sport dot, speed/pace cell, table column set, summary kv.
import type { Activity } from "../lib/api";
import { SPORT_LABEL, dur, km, num, pace, pace100, kmh, sportColor, stypeLabel, typeLabel, time } from "../lib/format";
import type { Col } from "./Table";

export const SportDot = ({ sport }: { sport: string }) => <span class="dot" style={{ background: sportColor(sport) }} title={SPORT_LABEL[sport]} />;

/** pace /km for runs, /100 m for pool swims (swimming time only), km/h otherwise */
export function speedText(a: Activity): string {
  if (a.swim?.pace_100m_s) return dur(a.swim.pace_100m_s) + " /100 m";
  const s = a.dist_km && (a.moving_s || a.dur_s) ? (a.dist_km * 1000) / (a.moving_s || a.dur_s)! : null;
  if (!s) return "—";
  return a.sport === "run" ? pace(s) + " /km" : a.sport === "swim" ? pace100(s) + " /100 m" : kmh(s) + " km/h";
}
/** sort key for the pace / speed column (m/s) */
export const speedSort = (a: Activity) => (a.swim?.pace_100m_s ? 100 / a.swim.pace_100m_s : a.dist_km && a.moving_s ? (a.dist_km * 1000) / a.moving_s : null);

/** "Track run" (Garmin type, humanised server-side) */
export const typeText = (a: Activity) => a.type_label || stypeLabel(a.stype) || typeLabel(a.type);
/** what the session was: workout label ("6×1000 · rest ~2:30", "Easy run") or the type */
export const what = (a: Activity) => a.workout_label || [stypeLabel(a.stype) || typeLabel(a.type), a.sig].filter(Boolean).join(" · ");
/** workout label without the "· R3 Holmenkollen climb…" route suffix (for lists that already are one route / place) */
export const whatShort = (a: Activity) => what(a).replace(/ · R\d+ .*$/, "");
/** place display name (id stays in links) */
export const placeText = (a: Activity) => a.place_name ?? a.place ?? a.location ?? "—";
export const placeHref = (id: string) => `/places?place=${encodeURIComponent(id)}`;

export function activityCols(opts: { date?: boolean } = {}): Col<Activity>[] {
  const cols: Col<Activity>[] = [];
  if (opts.date !== false) cols.push({ key: "start", label: "Date", render: (a) => <span>{a.date} <span class="muted">{time(a.start)}</span></span> });
  else cols.push({ key: "start", label: "Start", render: (a) => time(a.start) });
  cols.push(
    { key: "type", label: "Workout", render: (a) => <span title={typeText(a)}><SportDot sport={a.sport} />{what(a)}</span>, sort: (a) => a.sport + what(a) },
    { key: "dist_km", label: "km", num: true, render: (a) => km(a.dist_km) },
    { key: "moving_s", label: "Moving", num: true, render: (a) => dur(a.moving_s ?? a.dur_s) },
    { key: "speed", label: "Pace / speed", num: true, render: speedText, sort: speedSort },
    { key: "hr", label: "HR", num: true, render: (a) => num(a.hr) },
    { key: "elev", label: "Climb", num: true, render: (a) => num(a.elev) },
    { key: "place", label: "Place", render: placeText, sort: (a) => placeText(a) },
    { key: "track", label: "GPS", render: (a) => <span><span class={"dot " + (a.track ? "good" : a.fit ? "partial" : "none")} />{a.track ? "track" : a.fit ? "no GPS" : "no file"}</span> },
  );
  return cols;
}

export function ActivityKV({ a }: { a: Activity }) {
  return (
    <dl class="kv two">
      <dt>Date</dt><dd><a href={`/day/${a.date}`}>{a.date}</a> {time(a.start)}</dd>
      <dt>Type</dt><dd><SportDot sport={a.sport} />{typeText(a)}</dd>
      <dt>Workout</dt><dd>{what(a)}</dd>
      <dt>Distance</dt><dd>{a.dist_km ? `${a.dist_km.toFixed(2)} km` : "—"}</dd>
      <dt>Time</dt><dd>{dur(a.dur_s)} <span class="muted">moving {dur(a.moving_s)}</span></dd>
      <dt>Pace / speed</dt><dd>{speedText(a)}</dd>
      <dt>Heart rate</dt><dd>{num(a.hr)} <span class="muted">max {num(a.maxhr)}</span></dd>
      <dt>Climb</dt><dd>{a.elev !== null ? `${a.elev} m` : "—"}</dd>
      <dt>Place</dt><dd>{a.place ? <a href={placeHref(a.place)}>{placeText(a)}</a> : a.location ?? "—"}</dd>
      <dt>Route</dt><dd>{a.route ? <a href={`/places?route=${a.route}`} title={a.route_name ?? ""}>{a.route}</a> : "—"}</dd>
      <dt>Load / TE</dt><dd>{num(a.load)} / {a.te ?? "—"}</dd>
    </dl>
  );
}
