// Types and small helpers for the Together pages (/api/together…, docs/together.md).
import type { PersonInfo } from "./people";
import { gapText as gapWords, speedText } from "./format";

type N = number | null;

export interface TogetherSpan { from_min: number; to_min: number; segments: [number, number][]; text: string }
export interface TPlace { name: string; name_local?: string; lat: number; lon: number; person: string; id: string; src: string }
export interface TListParticipant {
  person: string; activity_id: string; role: "full" | "partial" | "other"; workout: string; signature: string | null;
  n_reps: number | null; dist_km: number | null; dur_s: number | null; together: TogetherSpan | null;
  reps_done?: number; of?: number;
}
/** neutral one-liner: "15×400 · 2 members · fastest rep 1:15.6" (no win counts) */
export interface Headline {
  kind: "intervals" | "race" | "together" | "unverified"; text: string; n_members?: number; reps?: number;
  fastest?: { kind: "rep"; value_s: number; person: string } | { kind: "pace"; value_s_km: number; person: string } | null;
  order?: string[]; with_pct?: number;
}
export interface TGroup {
  gid: string; date: string; start_local: string; start_utc: string; sport: string; sports: string[];
  kind: string; label: string; label_text: string; score: number; hidden: boolean; no_track: boolean;
  place: TPlace | null; workout: string; people: string[]; n_people: number;
  participants: TListParticipant[]; headline: Headline;
}
export interface TList { n: number; viewer: string | null; built: string; people: Record<string, PersonInfo>; groups: TGroup[] }

export interface TParticipant {
  key: string; person: string; activity_id: string; role: "full" | "partial" | "other"; anchor: boolean; name: string;
  type: string; sport: string; signature: string | null; workout: string; dist_km: number | null; dur_s: number | null;
  start_utc: string; start_local: string; offset_s: number; clock_fixed: boolean; has_fit: boolean; has_gps: boolean;
  product: number | null; sample_s: number | null; sample_p90_s: number | null; together: TogetherSpan | null;
  display: PersonInfo; hr_norm: { max: number | null; rest: number | null; mode: string; source: string; warn: string | null } | null;
  shape: { score: number; discipline: string; kind: string } | null;
}
export interface Slot { slot: number; label: string; anchor: boolean; owner: string; target: { kind: "dist" | "time"; value: number; text: string } | null;
  start_s: number; n_people: number; warmup?: "warm-up" | "cool-down" | null }
export interface Cell {
  source: "detector" | "onset"; method: string; partial: boolean; result_s: N; time_s: N; dist_m: N; own_time_s: N; own_dist_m: N;
  start_s: number; clock_start_s: number; scale: N; rank: N; gap_s: N; tie: boolean; tie_s?: number; suspect?: string; warmup?: "warm-up" | "cool-down"; warmup_reason?: string;
  hr: N; hr_peak: N; hr_start: N; effort_pct: N; effort_mode: string | null;
}
export interface Standing { slot: number; label: string; partial: string[]; missing: string[];
  order: { person: string; result_s: number; rank: number; gap_s: number; gap_m: N; tie: boolean; tie_s: number }[] }
/** per set distance (or set time) of one person's reps: count, average, best */
export interface TargetAvg { kind: "dist" | "time"; value: number; text: string; n: number; avg_s?: N; best_s?: N; avg_m?: N; best_m?: N }
/** every rep a person ran counts; ranked by the distance-weighted pace over their own full reps (suspect ones left out) */
export interface LeaderRow {
  rank: number | null; person: string; reps_done: number; n_counted: number; n_partial: number; anchor_done: number;
  extra_reps: number; n_warmup?: number; of: number; n_slots: number; reps_ranked: number; total_s: N; avg_s: N; pace_s_km: N; gap_s_km: N;
  by_target: TargetAvg[]; suspect: string[]; best: { slot: number; label: string; result_s: number } | null;
  fade_s: N; fade_s_km: N; avg_effort_pct: N; effort_mode: string | null;
}
export interface Break {
  after_slot: number; after_label: string; next_slot: number; next_label: string; start_s: number; dur_s: number; dist_m: N;
  kind: string; hr_peak: N; hr_30: N; drop_30: N; hr_next_start: N; drop_30_pct: N; hr_next_start_pct: N;
}
export interface CloseAlign { x0: number; n: number; people: Record<string, { d_m: N[]; behind_m: N[]; start_delta_s: number }> }
export interface Closeup { slot: string; available: boolean; leader?: string; target_m?: number; own?: CloseAlign; clock?: CloseAlign }
export interface Intervals {
  anchor: string; gps_scale: Record<string, number>; slots: Slot[];
  reps: Record<string, (Cell | null)[]>; standings: Standing[]; leaderboard: LeaderRow[];
  /** each person's own time run so far after each slot (null: no full rep in that slot) */
  cumulative: { slots: number[]; labels: string[]; people: Record<string, ({ total_s: number; n: number } | null)[]> };
  breaks: Record<string, Break[]>; recovered_faster: { after_slot: number; after_label: string; person: string }[];
  rep_closeups: Closeup[];
}
export interface Apart { a: string; b: string; t_s: number[]; apart_m: N[]; with: boolean[]; with_pct: N; within_20m_pct: N; median_apart_m: N }
export interface Continuous {
  available: boolean; why?: string | null; reference?: string; window_s?: [number, number]; ref_length_m?: number;
  clock?: "moving" | "elapsed"; stopped_s?: N; split_at_m?: N;
  gap_over_distance?: { x_m: number[]; x0_route_m: number; people: Record<string, N[]>; flagged?: number } | null;
  led?: { from_m: number; to_m: number; person: string | null }[];
  km_splits?: { km: number; people: Record<string, { time_s: N; rank: N; gap_s: N }> }[];
  finish?: { person: string; rank: number; time_s: number; gap_s: number; clock_s: number }[] | null; common_m?: N;
  race?: { person: string; rank: number; time_s: number; dist_km: N; gap_s: number }[] | null;
  apart?: Apart[];
}
export interface Caveat { kind: string; person: string | null; text: string }
export interface TSession {
  gid: string; requested_gid: string; viewer: string | null; kind: string; t0_utc: string;
  group: TGroup & { pairs: { why: string; label: string; with_pct: N; start_dist_m?: N; dstart_s?: N; overlap_s?: N }[]; start_local_iso: string; joint_start_utc: string; end_utc: string };
  participants: TParticipant[]; people: Record<string, PersonInfo>; caveats: Caveat[]; headline: Headline;
  intervals: Intervals | null; continuous: Continuous | null;
}
export interface ReplayPerson { person: string; role: string; t: number[]; lat: N[]; lon: N[]; hr: N[]; dist_m: N[] }
export interface Replay { gid: string; available: boolean; t0_utc: string; step_s: number; note?: string;
  people: Record<string, ReplayPerson>; reps: { slot: number; label: string; start_s: number; end_s: number }[] }

export const LABELS: { id: string; label: string; hint: string }[] = [
  { id: "same_workout", label: "Same workout", hint: "same reps, started together" },
  { id: "together", label: "Side by side", hint: "side by side most of the time (a steady run or hike together)" },
  { id: "partly_together", label: "Partly", hint: "part of the session side by side" },
  { id: "same_race", label: "Race", hint: "same race start" },
  { id: "unverified", label: "Unverified", hint: "one of you has no watch file: same start place and time, but nothing to compare" },
];
export const labelShort = (id: string) => LABELS.find((l) => l.id === id)?.label ?? (id === "same_place_diff_workout" ? "Near-miss" : id);

export const KIND_LABEL: Record<string, string> = { intervals: "Intervals", race: "Race", continuous: "Run", hike: "Hike", ride: "Ride", ski: "Ski", swim: "Swim", other: "Other" };

/** index of the last t <= x (binary search); -1 before the first */
export function idxAt(ts: number[], x: number): number {
  let lo = 0, hi = ts.length - 1;
  if (!ts.length || x < ts[0]) return -1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (ts[m] <= x) lo = m; else hi = m - 1; }
  return lo;
}
/** linearly interpolated value of `v` at time x (null outside, or across a gap > maxGap s) */
export function valAt(ts: number[], v: N[], x: number, maxGap = 12): N {
  const i = idxAt(ts, x);
  if (i < 0) return null;
  const a = v[i];
  if (i + 1 >= ts.length) return x - ts[i] <= maxGap / 2 ? a : null;
  const b = v[i + 1];
  if (a === null || b === null) return a ?? (ts[i + 1] - x < 1 ? b : null);
  const dt = ts[i + 1] - ts[i];
  if (dt > maxGap) return x - ts[i] < 2 ? a : ts[i + 1] - x < 2 ? b : null;
  return a + ((b - a) * (x - ts[i])) / (dt || 1);
}
/** [lon, lat] of a replay person at group-clock time x */
export function posAt(p: ReplayPerson | undefined, x: number): [number, number] | null {
  if (!p) return null;
  const la = valAt(p.t, p.lat, x), lo = valAt(p.t, p.lon, x);
  return la === null || lo === null ? null : [lo, la];
}
/** the path [lon, lat][] between clock times a and b */
export function pathBetween(p: ReplayPerson | undefined, a: number, b: number): [number, number][] {
  if (!p) return [];
  const out: [number, number][] = [];
  const s = posAt(p, a);
  if (s) out.push(s);
  for (let i = Math.max(0, idxAt(p.t, a) + 1); i < p.t.length && p.t[i] < b; i++) if (p.lat[i] !== null && p.lon[i] !== null) out.push([p.lon[i]!, p.lat[i]!]);
  const e = posAt(p, b);
  if (e) out.push(e);
  return out;
}

/** "75.6" (tenths) */
export const sec1 = (s: N | undefined) => (s === null || s === undefined ? "—" : s.toFixed(1));
/** rep time with its unit: "45.3 s" under a minute, else "1:17.2" / "12:52.3" */
export function repTime(s: N | undefined): string {
  if (s === null || s === undefined) return "—";
  if (s < 59.95) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60), x = s - m * 60;
  return `${m}:${x.toFixed(1).padStart(4, "0")}`;
}
/** a gap in words, no sign: "10.9 s behind" (positive = behind), "1:18 behind", "40 m ahead" */
export const behind = (v: N | undefined, unit: "s" | "m" = "s", d = unit === "s" ? 1 : 0) => gapWords(v ?? null, unit, d);
/** average pace from s per km in the sport's unit: "3:13 /km" on foot, "18.6 km/h" else */
export const paceText = (sKm: N | undefined, sport: string | null | undefined) => (sKm ? speedText(sport, 1000 / sKm) : "—");
/** centred rolling median over k points (nulls skipped; null where the point itself is null) */
export function rollingMedian(v: N[], k = 5): N[] {
  const h = Math.floor(k / 2);
  return v.map((x, i) => {
    if (x === null) return null;
    const w = v.slice(Math.max(0, i - h), i + h + 1).filter((y): y is number => y !== null).sort((a, b) => a - b);
    return w.length ? w[Math.floor(w.length / 2)] : null;
  });
}
/** a place name, with the local-script original when the name is a Latin fallback ("Huayin City (青柯坪)") */
export const placeText = (p: TPlace | null | undefined) => (!p ? "" : p.name_local ? `${p.name} (${p.name_local})` : p.name);
export const MEDAL = ["🥇", "🥈", "🥉"];
