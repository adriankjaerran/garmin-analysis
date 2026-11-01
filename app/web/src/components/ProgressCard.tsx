// "Am I getting better?": workout progression and personal records.
// - WorkoutProgress (activity page): this workout's progression (/api/progress/{id}) + record / route-rank chips.
// - RecordsSection (Fitness page): every distance's record, the top 3 and the PR history (/api/records).
import { useEffect } from "preact/hooks";
import { useApi } from "../lib/api";
import { daysBetween, dur, shortDate, speedText, speedVal, sportColor } from "../lib/format";
import { Table, type Col } from "./Table";
import { navigate } from "../lib/router";
import "./progress.css";

export interface ProgPoint { id: string; date: string; speed: number; hr: number | null; rest_s: number | null; place_name: string | null; label: string | null; together: boolean }
export interface ProgTrend { vs_median_pct: number; n_prev: number; median_speed: number; arrow: string; word: string }
export interface ProgWorkout { key: string; sport: string; signature: string; n: number; first: string; last: string; current: ProgPoint;
  best_before: ProgPoint | null; best: ProgPoint; is_best: boolean; rank: number; trend: ProgTrend | null; points: ProgPoint[] }
export interface RecordRow { id: string; date: string; time_s: number; pace_s_km: number; vdot: number | null; rank?: number; name: string | null;
  workout_label: string | null; place_name: string | null; improved_s?: number | null; reason?: string }
export interface DistRecord { key: string; label: string; dist_m: number; n: number; best: RecordRow; top: RecordRow[]; history: RecordRow[];
  rejected: RecordRow[]; n_rejected: number }
export interface Records { records: DistRecord[]; by_activity: Record<string, { key: string; label: string; rank: number; of: number; time_s: number }[]>;
  vdot_cap: number | null; definition: string }

const NEW_PR_DAYS = 90;
/** 1 -> "1st", 2 -> "2nd", 11 -> "11th" */
export const ordinal = (n: number) => `${n}${n % 100 >= 11 && n % 100 <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] ?? "th"}`;
/** "fastest" / "2nd fastest" */
export const fastest = (rank: number) => (rank === 1 ? "fastest" : `${ordinal(rank)} fastest`);
const trendCls = (t: ProgTrend | null) => (!t ? "muted" : t.arrow === "↗" ? "up" : t.arrow === "↘" ? "down" : "muted");
const trendTip = (t: ProgTrend | null) => (t ? `${Math.abs(t.vs_median_pct).toFixed(1)} % ${t.word === "level" ? "(level)" : t.word} than the median of the ${t.n_prev} before` : "first time");

/** avg rep pace per occurrence (up = faster), the highlighted one as a dot, the best as a ring */
export function ProgSpark({ pts, sport, cur, w = 120, h = 26 }: { pts: ProgPoint[]; sport: string; cur?: string; w?: number; h?: number }) {
  if (pts.length < 2) return null;
  const v = pts.map((p) => p.speed), lo = Math.min(...v), hi = Math.max(...v), r = hi - lo || 1;
  const x = (i: number) => 3 + (i / (pts.length - 1)) * (w - 6), y = (s: number) => h - 3 - ((s - lo) / r) * (h - 6);
  const bi = v.indexOf(hi), ci = cur ? pts.findIndex((p) => p.id === cur) : pts.length - 1;
  return (
    <svg class="spark prog-spark" width={w} height={h} role="img">
      <polyline points={pts.map((p, i) => `${x(i).toFixed(1)},${y(p.speed).toFixed(1)}`).join(" ")} fill="none" stroke={sportColor(sport)} stroke-width={1.5} stroke-opacity={0.8} />
      <circle cx={x(bi)} cy={y(hi)} r={3.5} fill="none" stroke="var(--state-good)" stroke-width={1.5}><title>best {speedText(sport, hi)} · {pts[bi].date}</title></circle>
      {ci >= 0 && <circle cx={x(ci)} cy={y(pts[ci].speed)} r={3} fill="var(--text)"><title>{pts[ci].date}: {speedText(sport, pts[ci].speed)}</title></circle>}
    </svg>
  );
}

function RecordsTable({ rec, anchor }: { rec: Records; anchor: string }) {
  const cols: Col<DistRecord>[] = [
    { key: "label", label: "Distance", sort: (r) => r.dist_m },
    { key: "best", label: "Record", num: true, sort: (r) => r.best.time_s, render: (r) => <b>{dur(r.best.time_s)}</b> },
    { key: "pace", label: "Pace", num: true, sort: () => null, render: (r) => <span class="muted">{dur(r.best.pace_s_km)} /km</span> },
    { key: "date", label: "Set", sort: (r) => r.best.date, render: (r) => {
      const fresh = daysBetween(r.best.date, anchor) <= NEW_PR_DAYS;
      return <a href={`/activities/${r.best.id}`} onClick={(e) => e.stopPropagation()} title={[r.best.workout_label, r.best.place_name, r.best.name].filter(Boolean).join(" · ")}>
        {shortDate(r.best.date, anchor)}{fresh && <span class="muted"> new</span>}</a>; } },
  ];
  cols.push(
    { key: "second", label: "2nd", num: true, sort: () => null, render: (r) => r.top[1] ? <a href={`/activities/${r.top[1].id}`} onClick={(e) => e.stopPropagation()} title={r.top[1].date}>{dur(r.top[1].time_s)}</a> : "—" },
    { key: "third", label: "3rd", num: true, sort: () => null, render: (r) => r.top[2] ? <a href={`/activities/${r.top[2].id}`} onClick={(e) => e.stopPropagation()} title={r.top[2].date}>{dur(r.top[2].time_s)}</a> : "—" },
    { key: "vdot", label: "VDOT", num: true, sort: (r) => r.best.vdot, render: (r) => r.best.vdot?.toFixed(1) ?? "—", title: "Daniels VDOT of the record (the race-shape scale of the Fitness chart)" },
    { key: "prs", label: "PRs", num: true, sort: (r) => r.history.length, render: (r) => r.history.length, title: "times this record was improved (the first one counts)" },
    { key: "n", label: "Runs", num: true, title: "runs with a split of this distance (trusted GPS distance)" },
  );
  return <Table cols={cols} rows={rec.records} rowKey={(r) => r.key} onRow={(r) => navigate(`/activities/${r.best.id}`)} stickyCols={1} />;
}

// ------------------------------------------------------------------ Fitness page section
export function RecordsSection({ anchor }: { anchor: string }) {
  const { data: rec } = useApi<Records>("/api/records");
  // /fitness#records: scroll there once the section exists
  useEffect(() => { if (rec && location.hash === "#records") document.getElementById("records")?.scrollIntoView(); }, [rec]);
  if (!rec || !rec.records.length) return null;
  const rejected = rec.records.reduce((s, r) => s + r.n_rejected, 0);
  return (
    <section id="records">
      <div class="head"><h2>Personal records</h2><span class="sub">Garmin's fastest split of each distance within a run; click a row to open the run</span></div>
      <RecordsTable rec={rec} anchor={anchor} />
      <div class="pr-history">
        {rec.records.map((r) => (
          <details key={r.key}>
            <summary>{r.label}: {r.history.length} {r.history.length === 1 ? "record" : "records"} over time</summary>
            <table class="pr-hist">
              <thead><tr><th>Date</th><th class="num">Time</th><th class="num">Pace</th><th class="num">Better by</th><th>Run</th></tr></thead>
              <tbody>{r.history.map((h) => (
                <tr key={h.id} onClick={() => navigate(`/activities/${h.id}`)}>
                  <td><a href={`/activities/${h.id}`}>{shortDate(h.date, anchor)}</a></td>
                  <td class="num">{dur(h.time_s)}</td><td class="num muted">{dur(h.pace_s_km)} /km</td>
                  <td class="num">{h.improved_s ? `−${dur(h.improved_s)}` : <span class="muted">first</span>}</td>
                  <td class="muted">{[h.workout_label, h.place_name].filter(Boolean).join(" · ")}</td>
                </tr>))}</tbody>
            </table>
          </details>
        ))}
      </div>
      <p class="note">{rec.definition}.{rejected ? ` ${rejected} splits left out (indoor / no GPS, or faster than your shape allows${rec.vdot_cap ? `: VDOT > ${rec.vdot_cap.toFixed(0)}` : ""}).` : ""}</p>
    </section>
  );
}

// ------------------------------------------------------------------ activity page
/** "Fastest 5 km (17:38)" / "2nd fastest 1 km" chips for an activity in the top 3 of a distance */
export function RecordChips({ id }: { id: string }) {
  const { data } = useApi<Records>("/api/records");
  const mine = data?.by_activity[id];
  if (!mine?.length) return null;
  return <>{mine.map((m) => <span key={m.key} class="pr-chip" title={`${fastest(m.rank)} ${m.label} of ${m.of} runs with that split`}>
    {fastest(m.rank)[0].toUpperCase() + fastest(m.rank).slice(1)} {m.label} ({dur(m.time_s)})</span>)}</>;
}

/** "6×1000: 3:27 today · best 3:13 (14 Oct 2025) · 11th of 19 · → level vs the 4 before" + every occurrence */
export function WorkoutProgress({ id }: { id: string }) {
  const { data } = useApi<{ id: string; workout: ProgWorkout | null }>(`/api/progress/${id}`);
  const w = data?.workout;
  if (!w) return null;
  const c = w.current, ref = c.date;
  const prevBest = w.best_before;
  const t = w.trend;
  return (
    <div class="act-progress">
      <span class="lbl">Progress</span>
      <span><b>{w.signature}</b>: <b>{speedText(w.sport, c.speed)}</b> here{c.hr ? <span class="muted"> · rep HR {c.hr}</span> : null}</span>
      {w.is_best ? <span class="pr-chip">your best {w.signature}</span> :
        <span>best <a href={`/activities/${w.best.id}`}>{speedVal(w.sport, w.best.speed)}</a> <span class="muted">({shortDate(w.best.date, ref)})</span></span>}
      {prevBest && !w.is_best && prevBest.id !== w.best.id && <span class="muted">best until then: {speedVal(w.sport, prevBest.speed)}</span>}
      <span class="muted">{ordinal(w.rank)} of {w.n}</span>
      <span class={trendCls(t)} title={trendTip(t)}>{t ? `${t.arrow} ${t.word === "level" ? "level with" : `${Math.abs(t.vs_median_pct).toFixed(1)} % ${t.word} than`} the ${t.n_prev} before` : "first time"}</span>
      <ProgSpark pts={w.points} sport={w.sport} cur={c.id} w={160} />
      <a href={`/activities?signature=${encodeURIComponent(w.signature)}&sport=${w.sport}`} class="more">all {w.n} →</a>
    </div>
  );
}
