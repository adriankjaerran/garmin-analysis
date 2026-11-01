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
  /** distance mode: share of the route covered (route axis), route stretches not covered [from_m, to_m], ran the
   * route reversed, gap at the last position both this session and the reference covered */
  coverage_pct?: N; uncovered_m?: N; uncovered?: [number, number][]; reversed?: boolean;
  gap_end?: { x_m: number; delta_moving_s: N; delta_elapsed_s: N } | null;
  /** common start: the whole-session totals (the row itself is the aligned window from the start line) */
  whole?: { elapsed_s: number; moving_s: number; pause_s: number; dist_m: N; moving_pace_s_km: N; gap_pace_s_km: N; hr_avg: N; climb_m: N };
}
/** common start line, per session: anchor (defines the line), aligned (crosses it; at_line = started <= 20 m past
 *  it), late (its recording begins further along the anchor's path: joins at join_m), excluded (reason) */
export interface AlignSession {
  status: "anchor" | "aligned" | "late" | "excluded"; kind: "anchor" | "crossing" | "at_line" | "join" | "rep1" | null; reason: string | null;
  offset_s?: N; moving_offset_s?: N; offset_m?: N; dist_to_line_m?: N; zone_pause_s?: N; join_m?: N; x_offset_s?: N;
  x_offset_moving_s?: N; closest_m?: N;
  /** start=rep1: the session's own number of its rep 1 and the slot it is in (late: its first rep matches a later slot) */
  rep_n?: number; slot?: string;
}
interface Align {
  applied: boolean; note?: string;
  start?: { kind: "ref" | "session" | "point" | "rep1"; anchor_id: number; lat: N; lon: N; heading_deg: N; radius_m: N;
    t_anchor_s: number; route_m: N };
  finish?: null; pacer_id?: number; sessions?: Record<string, AlignSession>;
  excluded?: { id: number; reason: string; closest_m: N }[]; late?: number[];
}
interface LegRow { moving_s: number; pause_s: number; speed_kmh: number; hr: N; delta_moving_s: N; d_moving_s: N; rank: number; partial?: number }
interface Leg { from_m: number; to_m: number; name: string; climb_m: N; sessions: Record<string, LegRow | null> }
interface RepRow { n: number; start_s: number; time_s: number; dist_m: number; speed_kmh: N; pace_s_km: N; hr: N; hr_max: N;
  hr_start: N; hr_end: N; d_time_s?: N; d_pace_s_km?: N; d_hr?: N; d_hr_max?: N; d_hr_end?: N }
interface BreakRow { start_s: number; dur_s: number; dist_m: number; speed_kmh: number; standing_s: number; kind: string;
  hr_peak: N; hr_30: N; hr_60: N; hr_90: N; drop_30: N; drop_60: N; drop_90: N; hr_min: N; hr_next_start: N;
  after_rep: number; next_slot: string; d_dur_s?: N; d_drop_30?: N; d_drop_60?: N; d_hr_next_start?: N }
interface RepSummary { n_reps: number; rep_time_s: number; rep_dist_m: number; rep_pace_s_km: N; rep_hr: N; rep_hr_max: N;
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
  /** common start line (start=ref|<id>|lat,lon) or t = 0 at rep 1 (start=rep1); intervals / breaks: {applied: false} */
  align?: Align;
  /** intervals with rep_series=1: every rep on its own real-time axis, x = x0 + index (s since the rep start) */
  rep_series?: RepSeries;
}
interface RepSeries {
  lead_s: number; post_s: number;
  slots: { slot: number; width: number; x0: number; n: number;
    series: Record<string, { speed_kmh: N[]; hr: N[]; start_s: number; time_s: number }> }[];
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
// Session identity colours: no pure red / green (those mean slower / faster in the tables and bars).
// Blue, orange, purple, teal, pink, brown, slate, ochre; the reference is neutral (text colour) and drawn thicker.
const PALETTE = ["#2563d6", "#e07b00", "#8e44ad", "#0e8c95", "#d0368a", "#8d5a2b", "#5d6d7e", "#a77c00"];
const REF_COLOR = "#1d2125";
const MUTED = "#b9bfc6";
/** the highlighted session when the others are grey (many sessions): blue, not red */
export const FOCUS_MANY = "#2563d6";
// dark theme (charts, swatches). Maps keep the light set: the basemap tiles are light in both themes.
const PALETTE_DARK = ["#6f9cf0", "#f39a3c", "#b884e0", "#3fbfc8", "#ee6fb0", "#c08a5a", "#9aa8b6", "#d9b23a"];
const REF_COLOR_DARK = "#e3e6ea";
const MUTED_DARK = "#5a616a";
const FOCUS_MANY_DARK = "#6f9cf0";
export const focusColor = (theme: "light" | "dark" = "light") => (theme === "dark" ? FOCUS_MANY_DARK : FOCUS_MANY);
/** few sessions: one colour each (reference dark / light on dark); many (> 6): reference, others grey, focus blue */
export function colorsFor(ids: string[], refId: string | null, theme: "light" | "dark" = "light"): { colors: Record<string, string>; many: boolean } {
  const many = ids.length > 6;
  const colors: Record<string, string> = {};
  const dark = theme === "dark";
  const [pal, ref, muted] = dark ? [PALETTE_DARK, REF_COLOR_DARK, MUTED_DARK] : [PALETTE, REF_COLOR, MUTED];
  let k = 0;
  for (const id of ids) colors[id] = id === refId ? ref : many ? muted : pal[k++ % pal.length];
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
/** seconds with a unit: "7.2 s", "12 s", ≥ 60 s as m:ss */
export const secTxt = (x: number) => (Math.abs(x) >= 59.5 ? dur(Math.abs(x)) : `${Math.abs(x).toFixed(Math.abs(x) < 10 ? 1 : 0)} s`);
/** metres with a unit: "40 m", "1.2 km" */
export const metres = (x: number) => { const a = Math.abs(x); return a >= 1000 ? `${(a / 1000).toFixed(a >= 10000 ? 0 : 1)} km` : `${Math.round(a)} m`; };
/** a gap in words, no sign: "12 s behind", "40 m ahead". `plus` = what a positive value means (time gaps:
 *  "behind", distance ahead: "ahead"). Values that round to zero: "level". */
export function gapTxt(v: N | undefined, f: (x: number) => string = secTxt, plus: "behind" | "ahead" = "behind"): string {
  if (v === null || v === undefined || isNaN(v)) return "—";
  const t = f(Math.abs(v));
  if (Math.abs(v) < 1e-9 || !/[1-9]/.test(t)) return "level";
  return `${t} ${(v > 0) === (plus === "behind") ? "behind" : "ahead"}`;
}
/** axis tick of a gap: "0" at zero, else the gap in words */
export const gapTick = (f: (x: number) => string, plus: "behind" | "ahead" = "behind") => (v: number) => (Math.abs(v) < 1e-9 ? "0" : gapTxt(v, f, plus));

/** rep in the URL: "R3" (as the UI labels it) or "3"; extra reps "R1+1" / "1+1". Returns the slot label ("3") */
export const repFromUrl = (v: string | null) => (v ? v.trim().replace(/^r/i, "") || null : null);
export const repToUrl = (label: string) => `R${label}`;

/** display smoothing of a 1 s speed series: rolling median over 2k+1 points (kills GPS spikes), then a mean over
 *  2k+1 points (k = 3: 7 s + 7 s) */
export function smoothRep(a: N[], k = 3): N[] {
  const med = a.map((v, i) => {
    if (v === null) return null;
    const w: number[] = [];
    for (let j = Math.max(0, i - k); j <= Math.min(a.length - 1, i + k); j++) if (a[j] !== null) w.push(a[j]!);
    w.sort((x, y) => x - y);
    return w[w.length >> 1];
  });
  return med.map((v, i) => {
    if (v === null) return null;
    let s = 0, n = 0;
    for (let j = Math.max(0, i - k); j <= Math.min(med.length - 1, i + k); j++) if (med[j] !== null) { s += med[j]!; n++; }
    return s / n;
  });
}

/** y range from the p-lo..p-hi of the values, padded (per-rep charts: spikes may leave the chart, the reps don't) */
export function pRange(vals: N[], lo = 0.03, hi = 0.97, pad = 0.12): [number, number] | null {
  const a = vals.filter((v): v is number => v !== null && isFinite(v)).sort((x, y) => x - y);
  if (a.length < 3) return null;
  const q = (p: number) => a[Math.round(p * (a.length - 1))];
  const L = q(lo), H = q(hi), d = (H - L) || Math.abs(H) * 0.05 || 1;
  return [L - d * pad, H + d * pad];
}

export const kmhToPace = (v: N) => (v !== null && v >= 3 ? Math.min(3600 / v, 900) : null);

/** rep slot label: anchor slots "Rep 3"; extra reps (a session had more reps than the anchor) "extra after R1" */
export function slotName(s: { label: string; anchor: boolean }, short = false): string {
  if (s.anchor) return short ? `R${s.label}` : `Rep ${s.label}`;
  const [a, j] = s.label.split("+");
  if (a === "0") return short ? `pre-R1${j === "1" ? "" : " " + j}` : `extra${j === "1" ? "" : " " + j} before R1`;
  return short ? `R${a}+${j === "1" ? "" : j}` : `extra${j === "1" ? "" : " " + j} after R${a}`;
}

/** the break after the rep in slot k: "After R3"; after the last rep: "Recovery" (up to 2 min after the session's
 *  last rep, there is no next rep). `short` = for buttons and ticks */
export function breakName(slots: { label: string; anchor: boolean }[], k: number, short = false): string {
  const s = slots[k];
  if (!s) return "";
  if (k === slots.length - 1) return short ? "Recovery" : `Recovery after ${slotName(s, true)} (last rep)`;
  return `After ${slotName(s, true)}`;
}

/** x value -> readable position for the cursor label */
export function xLabel(r: CmpResult, x: number): string {
  const a = r.axis || {};
  if (r.mode === "distance") return `${(x / 1000).toFixed(2)} km`;
  if (r.mode === "moving" || r.mode === "elapsed") return dur(x);
  const slots = r.slots ?? [];
  if (r.mode === "intervals") {
    const x0: number[] = a.slot_x0, w: number[] = a.slot_width, norm = a.rep_axis === "normalized";
    if (x < x0[0]) return `before ${slots[0] ? slotName(slots[0], true) : "rep 1"}`;
    for (let k = 0; k < x0.length; k++) {
      if (x >= x0[k] && x <= x0[k] + w[k]) return `${slotName(slots[k])} · ${norm ? Math.round(x - x0[k]) + " %" : dur(x - x0[k])}`;
      if (x > x0[k] + w[k] && (k + 1 >= x0.length || x < x0[k + 1])) return `break after ${slotName(slots[k], true)} (squeezed)`;
    }
    return "";
  }
  const b0: number[] = a.break_x0, bw: number[] = a.break_width, r0: number[] = a.rep_x0;
  for (let k = 0; k < b0.length; k++) {
    if (x >= r0[k] && x < b0[k]) return `${slotName(slots[k])} (squeezed)`;
    if (x >= b0[k] && x <= b0[k] + bw[k]) return `${breakName(slots, k)} · ${dur(x - b0[k])}`;
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
      t.push([x, breakName(slots, k, true)]);
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
function bisect(ts: number[], v: number): number {
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
