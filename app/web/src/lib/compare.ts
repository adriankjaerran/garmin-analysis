// Types and pure helpers for the Compare page (/api/compare, /api/compare/suggest): common x grid,
// interpolation, median / quartile band, colours, x-axis labels per mode.
import type { Activity } from "./api";
import { dur } from "./format";

export type Mode = "distance" | "moving" | "elapsed" | "intervals" | "breaks";
export const MODES: { id: Mode; label: string; title: string }[] = [
  { id: "distance", label: "Route position", title: "Ghost race: x = position along the route (or own distance); who was ahead, where" },
  { id: "moving", label: "Moving time", title: "x = moving time (stops removed)" },
  { id: "elapsed", label: "Elapsed", title: "x = clock time since start (stops included)" },
  { id: "intervals", label: "Intervals", title: "Reps aligned on their start, breaks squeezed to a fixed width: compares only the running" },
  { id: "breaks", label: "Breaks", title: "Breaks aligned on their start, reps squeezed: compares the recoveries" },
];

type N = number | null;
export interface CmpSession {
  id: number; date: string; sport: string; type: string | null; sig: string | null; route: string | null; place: string | null;
  elapsed_s: number; moving_s: number; pause_s: number; n_pauses: number; dist_m: number; moving_kmh: N; moving_pace_s_km: N;
  gap_pace_s_km: N; hr_avg: N; hr_max: N; climb_m: N; eff_m_per_beat: N; delta: Record<string, N>;
  /** hr_avg = moving seconds only (= hr_avg_moving); hr_avg_activity = whole activity (Garmin, the activity header) */
  hr_avg_moving?: N; hr_avg_activity?: N;
}
export interface LegRow { moving_s: number; pause_s: number; speed_kmh: number; hr: N; delta_moving_s: N; d_moving_s: N; rank: number; partial?: number }
export interface Leg { from_m: number; to_m: number; name: string; climb_m: N; sessions: Record<string, LegRow | null> }
export interface RepRow { n: number; start_s: number; time_s: number; dist_m: number; speed_kmh: N; pace_s_km: N; hr: N; hr_max: N;
  hr_start: N; hr_end: N; d_time_s?: N; d_pace_s_km?: N; d_hr?: N; d_hr_max?: N; d_hr_end?: N }
export interface BreakRow { start_s: number; dur_s: number; dist_m: number; speed_kmh: number; standing_s: number; kind: string;
  hr_peak: N; hr_30: N; hr_60: N; hr_90: N; drop_30: N; drop_60: N; drop_90: N; hr_min: N; hr_next_start: N;
  after_rep: number; next_slot: string; d_dur_s?: N; d_drop_30?: N; d_drop_60?: N; d_hr_next_start?: N }
export interface RepSummary { n_reps: number; rep_time_s: number; rep_dist_m: number; rep_pace_s_km: N; rep_hr: N; rep_hr_max: N;
  break_total_s: N; break_median_s: N; drop_60_median: N; matched_slots?: number; d_rep_pace_matched_s_km?: N; d_rep_hr_matched?: N;
  det_mode: string; n_detected: number; n_used: number; match: string }
export interface Track { t: number[]; lat: number[]; lon: number[] }
export interface CmpResult {
  mode: Mode; ids: number[]; skipped: { id: number; why: string }[];
  reference: { kind: "session" | "median"; id?: number; n?: number; summary?: Record<string, N> };
  sessions: CmpSession[]; axis: any; series: Record<string, Record<string, any>>;
  legs?: Leg[]; legs_kind?: string; route_ref?: { id: number; m: number[]; alt: N[]; lat: number[]; lon: number[]; waypoints: { km: number; name: string; lat: number; lon: number }[] };
  median?: Record<string, N[]>;
  slots?: { slot: number; label: string; anchor: boolean; n_sessions: number }[]; anchor_id?: number;
  rep_summary?: Record<string, RepSummary>; no_reps?: number[];
  reps?: Record<string, (RepRow | null)[]>; reps_ref?: (Record<string, N> | null)[] | null;
  breaks?: Record<string, (BreakRow | null)[]>; breaks_ref?: (Record<string, N> | null)[] | null;
  activities: Record<string, Activity | null>; tracks?: Record<string, Track>; compute_s: number;
}
export interface SuggestSet { key: string; label: string; n_total: number; ids: string[]; mode: Mode; why: string }
export interface Suggest { id: string; date: string; mode: Mode; sets: SuggestSet[]; activities: Record<string, Activity | null>;
  /** modes the engine can run for this session ("distance" = Route position); sessions without distance: time / HR modes only */
  modes?: Mode[]; no_distance?: boolean; comparable?: boolean; why_not?: string | null }

// ---------------------------------------------------------------- grid + interpolation
/** common x grid: the distance bins as they are, else n evenly spaced points over the union of x ranges */
export function buildGrid(r: CmpResult, n = 1500): number[] {
  let lo = Infinity, hi = -Infinity;
  for (const s of Object.values(r.series)) {
    const x = s.x as N[];
    for (const v of x) if (v !== null) { if (v < lo) lo = v; if (v > hi) hi = v; }
  }
  if (!isFinite(lo)) return [];
  if (r.mode === "distance" && r.axis?.step_m) {
    const st = r.axis.step_m as number, X: number[] = [];
    for (let v = lo; v <= hi + 1e-6; v += st) X.push(Math.round(v));
    return X;
  }
  const k = Math.max(2, Math.min(n, Math.ceil(hi - lo) + 1));
  return Array.from({ length: k }, (_, i) => lo + ((hi - lo) * i) / (k - 1));
}

function medianStep(x: N[]): number {
  const d: number[] = [];
  for (let i = 1; i < x.length; i++) if (x[i] !== null && x[i - 1] !== null) d.push(x[i]! - x[i - 1]!);
  d.sort((a, b) => a - b);
  return d.length ? d[d.length >> 1] : 1;
}

/** linear interpolation of (x, y) onto X; null outside, across null neighbours or gaps > maxGap */
export function interp(x: N[], y: N[] | null | undefined, X: number[], maxGap?: number): N[] {
  const out: N[] = new Array(X.length).fill(null);
  if (!y) return out;
  const g = maxGap ?? Math.max(4 * medianStep(x), 1e-9);
  let j = 0;
  for (let i = 0; i < X.length; i++) {
    const v = X[i];
    while (j + 1 < x.length && (x[j + 1] === null || x[j + 1]! <= v)) j++;
    const x0 = x[j], x1 = x[j + 1];
    if (x0 === null || x0 > v) continue;
    if (x0 === v || x1 === undefined || x1 === null) { if (Math.abs(x0 - v) < 1e-9) out[i] = y[j]; continue; }
    const y0 = y[j], y1 = y[j + 1];
    if (y0 === null || y1 === null || x1 - x0 > g) continue;
    out[i] = y0 + ((y1 - y0) * (v - x0)) / (x1 - x0);
  }
  return out;
}

/** per-x median and quartiles over the sessions (needs >= 3 values at that x) */
export function stats(cols: N[][], minN = 3): { med: N[]; lo: N[]; hi: N[] } {
  const n = cols[0]?.length ?? 0;
  const med: N[] = new Array(n).fill(null), lo: N[] = new Array(n).fill(null), hi: N[] = new Array(n).fill(null);
  const q = (a: number[], p: number) => { const f = p * (a.length - 1), i = Math.floor(f); return a[i] + (a[Math.min(i + 1, a.length - 1)] - a[i]) * (f - i); };
  for (let i = 0; i < n; i++) {
    const v: number[] = [];
    for (const c of cols) if (c[i] !== null && c[i] !== undefined) v.push(c[i]!);
    if (v.length < minN) continue;
    v.sort((a, b) => a - b);
    med[i] = q(v, 0.5); lo[i] = q(v, 0.25); hi[i] = q(v, 0.75);
  }
  return { med, lo, hi };
}

// ---------------------------------------------------------------- colours
export const PALETTE = ["#2f5fd0", "#c43c3c", "#2f8a4c", "#b88a00", "#1f8fa6", "#8e44ad", "#d35400", "#5d6d7e"];
export const REF_COLOR = "#1d2125";
export const MUTED = "#b9bfc6";
export const FOCUS_MANY = "#c43c3c";
/** few sessions: one colour each (reference dark); many (> 6): reference dark, others grey, focus red */
export function colorsFor(ids: string[], refId: string | null): { colors: Record<string, string>; many: boolean } {
  const many = ids.length > 6;
  const colors: Record<string, string> = {};
  let k = 0;
  for (const id of ids) colors[id] = id === refId ? REF_COLOR : many ? MUTED : PALETTE[k++ % PALETTE.length];
  return { colors, many };
}

// ---------------------------------------------------------------- formatting
export const sdur = (s: N | undefined) => (s === null || s === undefined ? "—" : (s < 0 ? "−" : "") + dur(Math.abs(s)));
/** signed delta with a + for positive values */
export function sgn(v: N | undefined, f: (x: number) => string = (x) => String(Math.round(x))): string {
  if (v === null || v === undefined || isNaN(v)) return "—";
  const t = f(Math.abs(v));
  if (Math.abs(v) < 1e-9 || !/[1-9]/.test(t)) return "±0";   // rounds to zero
  return (v > 0 ? "+" : "−") + t;
}
export const kmhToPace = (v: N) => (v !== null && v >= 3 ? Math.min(3600 / v, 900) : null);

/** rep slot label: anchor slots "Rep 3"; extra reps (a session had more reps than the anchor) "extra after R1" */
export function slotName(s: { label: string; anchor: boolean }, short = false): string {
  if (s.anchor) return short ? `R${s.label}` : `Rep ${s.label}`;
  const [a, j] = s.label.split("+");
  if (a === "0") return short ? `pre-R1${j === "1" ? "" : " " + j}` : `extra${j === "1" ? "" : " " + j} before R1`;
  return short ? `R${a}+${j === "1" ? "" : j}` : `extra${j === "1" ? "" : " " + j} after R${a}`;
}

/** x value -> readable position for the cursor label */
export function xLabel(r: CmpResult, x: number): string {
  const a = r.axis || {};
  if (r.mode === "distance") return `${(x / 1000).toFixed(2)} km`;
  if (r.mode === "moving" || r.mode === "elapsed") return dur(x);
  const slots = r.slots ?? [];
  if (r.mode === "intervals") {
    const x0: number[] = a.slot_x0, w: number[] = a.slot_width, norm = a.rep_axis === "normalized";
    if (x < x0[0]) return `before rep ${slots[0]?.label}`;
    for (let k = 0; k < x0.length; k++) {
      if (x >= x0[k] && x <= x0[k] + w[k]) return `${slotName(slots[k])} · ${norm ? Math.round(x - x0[k]) + " %" : dur(x - x0[k])}`;
      if (x > x0[k] + w[k] && (k + 1 >= x0.length || x < x0[k + 1])) return `break after ${slotName(slots[k], true)} (squeezed)`;
    }
    return "";
  }
  const b0: number[] = a.break_x0, bw: number[] = a.break_width, r0: number[] = a.rep_x0;
  for (let k = 0; k < b0.length; k++) {
    if (x >= r0[k] && x < b0[k]) return `rep ${slots[k].label} (squeezed)`;
    if (x >= b0[k] && x <= b0[k] + bw[k]) return `Break after rep ${slots[k].label} · ${dur(x - b0[k])}`;
  }
  return "";
}

/** custom ticks for the aligned modes: [x, label] */
export function alignedTicks(r: CmpResult): [number, string][] | null {
  const a = r.axis || {}, slots = r.slots ?? [];
  if (r.mode === "intervals") return (a.slot_x0 as number[]).map((x, k) => [x, slotName(slots[k])]);
  if (r.mode === "breaks") {
    const t: [number, string][] = [];
    (a.break_x0 as number[]).forEach((x, k) => {
      const sn = slotName(slots[k], true);
      t.push([x, sn.startsWith("R") ? `B${sn.slice(1)}` : `B ${sn}`]);
      for (let s = 60; s < a.break_width[k] - 20; s += 60) t.push([x + s, dur(s)]);
    });
    return t;
  }
  return null;
}

/** shaded spans on the charts: reps in the intervals view, squeezed reps in the breaks view */
export function spans(r: CmpResult): { a: number; b: number }[] {
  const a = r.axis || {};
  if (r.mode === "intervals") return (a.slot_x0 as number[]).map((x, k) => ({ a: x, b: x + a.slot_width[k] }));
  if (r.mode === "breaks") return (a.rep_x0 as number[]).map((x, k) => ({ a: x, b: a.break_x0[k] }));
  return [];
}

/** index of the largest t <= v (binary search), -1 if before */
export function bisect(ts: number[], v: number): number {
  let lo = 0, hi = ts.length - 1;
  if (!ts.length || v < ts[0]) return -1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (ts[m] <= v) lo = m; else hi = m - 1; }
  return lo;
}

/** position of a session at elapsed second t (lerp along its track) */
export function posAt(tr: Track | undefined, t: N): [number, number] | null {
  if (!tr || t === null || !tr.t.length) return null;
  const i = bisect(tr.t, t);
  if (i < 0) return null;
  if (i >= tr.t.length - 1) return t - tr.t[tr.t.length - 1] < 30 ? [tr.lon[tr.t.length - 1], tr.lat[tr.t.length - 1]] : null;
  const f = (t - tr.t[i]) / Math.max(1e-9, tr.t[i + 1] - tr.t[i]);
  if (tr.t[i + 1] - tr.t[i] > 120) return [tr.lon[i], tr.lat[i]];
  return [tr.lon[i] + (tr.lon[i + 1] - tr.lon[i]) * f, tr.lat[i] + (tr.lat[i + 1] - tr.lat[i]) * f];
}

/** Time gap inside each rep (intervals view, rep axis in seconds): for every grid point of a rep slot, the time
 *  this session needed to cover the distance the reference had covered at that second, minus that second
 *  (+ = behind). Distances are integrated from the speed series from the rep start, so every rep starts at 0
 *  and the value at the rep end ≈ the rep time difference. `refSpeed` = the reference's (or the median's)
 *  speed on the grid (km/h). Null outside reps, where either session has no data, or once a session's rep
 *  is over before it reached the reference's distance. */
export function repGap(X: number[], x0: number[], w: number[], speed: N[], refSpeed: N[]): N[] {
  const out: N[] = new Array(X.length).fill(null);
  for (let k = 0; k < x0.length; k++) {
    const idx: number[] = [];
    for (let i = 0; i < X.length; i++) if (X[i] >= x0[k] && X[i] <= x0[k] + w[k]) idx.push(i);
    if (idx.length < 2) continue;
    const cum = (v: N[]) => {
      const D: N[] = [];
      let d = 0, started = false, ended = false;
      for (let n = 0; n < idx.length; n++) {
        const i = idx[n], a = v[i];
        if (a === null) { if (started) ended = true; D.push(null); continue; }
        if (ended) { D.push(null); continue; }
        if (started) { const p = v[idx[n - 1]]; d += ((((p ?? a) + a) / 2) / 3.6) * (X[i] - X[idx[n - 1]]); }
        started = true;
        D.push(d);
      }
      return D;
    };
    const Ds = cum(speed), Dr = cum(refSpeed);
    let j = 0;
    for (let n = 0; n < idx.length; n++) {
      const d = Dr[n];
      if (d === null) continue;
      while (j < idx.length && (Ds[j] === null || Ds[j]! < d)) {
        if (Ds[j] === null && j > 0 && Ds[j - 1] !== null) break;   // session's rep over
        j++;
      }
      if (j >= idx.length || Ds[j] === null) continue;
      let t = X[idx[j]] - x0[k];
      if (j > 0 && Ds[j - 1] !== null && Ds[j]! > Ds[j - 1]!) {
        const f = (d - Ds[j - 1]!) / (Ds[j]! - Ds[j - 1]!);
        t = X[idx[j - 1]] - x0[k] + f * (X[idx[j]] - X[idx[j - 1]]);
      }
      out[idx[n]] = t - (X[idx[n]] - x0[k]);
    }
  }
  return out;
}
