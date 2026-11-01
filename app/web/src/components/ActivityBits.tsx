// Small shared pieces for activities: sport dot, 👥 marker, workout name, speed/pace cell, table column set, summary kv.
import type { Activity, TogetherLink } from "../lib/api";
import { SPORT_LABEL, dur, km, num, sportColor, stypeLabel, typeLabel, time, speedText as speedOf, speedLabel } from "../lib/format";
import type { Col } from "./Table";
import { personName } from "../lib/people";

/** "Together with Patrik" */
export const togetherWith = (t: TogetherLink) => `Together with ${t.with.map(personName).join(", ")}`;
/** 👥 marker for an activity done with others: a link to the Together session (title = who + the shared workout) */
export function TogetherMark({ a }: { a: { together?: TogetherLink | null } }) {
  const t = a.together;
  if (!t) return null;
  return (
    <a class="tg-mark" href={`/together/${t.gid}`} onClick={(e) => e.stopPropagation()} aria-label={togetherWith(t)}
       title={`${togetherWith(t)}${t.workout ? ` · ${t.workout}` : ""} — open the session`}>👥</a>
  );
}
export const SportDot = ({ sport }: { sport: string }) => <span class="dot" style={{ background: sportColor(sport) }} title={SPORT_LABEL[sport]} />;

/** moving pace / speed in the sport's unit (lib/format speedText): /km on foot, /100 m for pool swims (swimming time
 *  only), km/h otherwise */
export function speedText(a: Activity): string {
  if (a.swim?.pace_100m_s) return dur(a.swim.pace_100m_s) + " /100 m";
  const s = a.dist_km && (a.moving_s || a.dur_s) ? (a.dist_km * 1000) / (a.moving_s || a.dur_s)! : null;
  return speedOf(a.sport, s);
}
/** sort key for the pace / speed column (m/s) */
export const speedSort = (a: Activity) => (a.swim?.pace_100m_s ? 100 / a.swim.pace_100m_s : a.dist_km && a.moving_s ? (a.dist_km * 1000) / a.moving_s : null);

/** "Track run" (Garmin type, humanised server-side) */
export const typeText = (a: Activity) => a.type_label || stypeLabel(a.stype) || typeLabel(a.type);
/** what the session was: the one workout name (`workout_label`, store.workout_name: "15×400", "1×1300 + 2×3000",
 *  "Easy run"), else the type */
export const what = (a: Pick<Activity, "workout_label" | "stype" | "type" | "sig">) => a.workout_label || [stypeLabel(a.stype) || typeLabel(a.type), a.sig].filter(Boolean).join(" · ");
/** same (the workout name no longer carries a route suffix) */
export const whatShort = what;
/** the workout name with the avg rep pace for intervals: "6×1000 @ 3:29 /km" */
export function workoutText(r: { sport: string; workout_label?: string | null; rep_pace_ms?: number | null; family_label?: string | null;
  type_label?: string | null; type?: string; stype?: string | null; sig?: string | null }): string {
  const w = r.workout_label || r.family_label || r.type_label || (r.type ? what(r as any) : "—");
  return r.rep_pace_ms ? `${w} @ ${speedOf(r.sport, r.rep_pace_ms)}` : w;
}
/** tooltip for a workout cell: same-workout detail, name, who with */
export const workoutTip = (r: { workout_detail?: string | null; name?: string | null; together?: TogetherLink | null; rep_pace_ms?: number | null }) =>
  [r.workout_detail, r.rep_pace_ms ? "@ = avg rep pace (total rep distance / total rep time)" : "", r.name,
    r.together ? togetherWith(r.together) : ""].filter(Boolean).join(" · ");
/** place display name (id stays in links) */
export const placeText = (a: Activity) => a.place_name ?? a.place ?? a.location ?? "—";
export const placeHref = (id: string) => `/places?place=${encodeURIComponent(id)}`;

export function activityCols(opts: { date?: boolean } = {}): Col<Activity>[] {
  const cols: Col<Activity>[] = [];
  if (opts.date !== false) cols.push({ key: "start", label: "Date", render: (a) => <span>{a.date} <span class="muted">{time(a.start)}</span></span> });
  else cols.push({ key: "start", label: "Start", render: (a) => time(a.start) });
  cols.push(
    { key: "type", label: "Workout", render: (a) => <span class="clip" style={{ maxWidth: "210px" }} title={[workoutTip(a), typeText(a)].filter(Boolean).join(" · ")}><SportDot sport={a.sport} /><TogetherMark a={a} />{workoutText(a)}</span>, sort: (a) => a.sport + what(a) },
    { key: "dist_km", label: "km", num: true, render: (a) => km(a.dist_km) },
    { key: "moving_s", label: "Moving", num: true, render: (a) => dur(a.moving_s ?? a.dur_s) },
    { key: "speed", label: "Pace / speed", num: true, render: speedText, sort: speedSort, title: "Moving pace: min/km on foot (run, walk, hike), /100 m for swims, km/h for ski, bike and other" },
    { key: "hr", label: "HR", num: true, render: (a) => num(a.hr), title: "Average heart rate, whole activity (bpm)" },
    { key: "elev", label: "Climb", num: true, render: (a) => (a.elev ? `${num(a.elev)} m` : "—") },
    { key: "place", label: "Place", render: (a) => <span class="clip" style={{ maxWidth: "140px" }} title={placeText(a)}>{placeText(a)}</span>, sort: (a) => placeText(a) },
    // dot only (green = GPS track, amber = FIT without GPS, grey = no file); the text is the tooltip
    { key: "track", label: "GPS", title: "green = GPS track, amber = FIT file without GPS, grey = no file (hover a dot)",
      render: (a) => <span class={"dot " + (a.track ? "good" : a.fit ? "partial" : "none")} title={a.track ? "GPS track" : a.fit ? "no GPS" : "no file"} />,
      sort: (a) => (a.track ? 2 : a.fit ? 1 : 0) },
  );
  return cols;
}

export function ActivityKV({ a }: { a: Activity }) {
  return (
    <dl class="kv two">
      <dt>Date</dt><dd><a href={`/day/${a.date}`}>{a.date}</a> {time(a.start)}</dd>
      <dt>Type</dt><dd><SportDot sport={a.sport} />{typeText(a)}</dd>
      <dt>Workout</dt><dd title={workoutTip(a)}>{workoutText(a)}{a.together && <> · <a href={`/together/${a.together.gid}`}>👥 {togetherWith(a.together)}</a></>}</dd>
      <dt>Distance</dt><dd>{a.dist_km ? `${a.dist_km.toFixed(2)} km` : "—"}</dd>
      <dt>Time</dt><dd>{dur(a.dur_s)} <span class="muted">moving {dur(a.moving_s)}</span></dd>
      <dt>{speedLabel(a.sport)}</dt><dd>{speedText(a)} <span class="muted">moving</span></dd>
      <dt>Heart rate</dt><dd>{a.hr ? <>{num(a.hr)} bpm <span class="muted">max {num(a.maxhr)}</span></> : "—"}</dd>
      <dt>Climb</dt><dd>{a.elev !== null ? `${a.elev} m` : "—"}</dd>
      <dt>Place</dt><dd>{a.place ? <a href={placeHref(a.place)}>{placeText(a)}</a> : a.location ?? "—"}</dd>
      <dt>Route</dt><dd>{a.route ? <a href={`/places?route=${a.route}`} title={a.route_name ?? ""}>{a.route}</a> : "—"}</dd>
      <dt title="Garmin training load / aerobic training effect (0–5)">Training load / effect</dt><dd>{num(a.load)} / {a.te ?? "—"}</dd>
    </dl>
  );
}
