// Typed fetch helpers. GET responses are memoised per URL for the session (data changes rarely;
// reload the page to refetch).
import { useEffect, useState } from "preact/hooks";
import { currentPerson, splitPerson } from "./router";

export type Sport = "run" | "ski" | "row" | "skierg" | "hike" | "bike" | "swim" | "other";
/** one Concept2 split / interval (seconds, metres) */
export interface ErgInterval { type?: string; time_s: number; dist_m?: number; spm?: number; kcal?: number; rest_s?: number; rest_m?: number; hr?: number; target_pace_s?: number; machine?: string }
/** Concept2 erg extras of an activity (store.erg_info) */
export interface ErgInfo {
  machine: string | null; workout_type: string | null; pace_500_s: number | null; pace_unit: string; watts: number | null; spm: number | null;
  drag_factor: number | null; stroke_count: number | null; work_s: number | null; rest_s: number | null; rest_m: number | null;
  comments: string | null; intervals: ErgInterval[] | null; splits: ErgInterval[] | null; logbook_url: string | null;
  /** shared machine (concept2.clean): friends took turns; the activity is this person's turns only */
  shared?: { slot_s: number; pattern: string; on_s: number; off_s: number; others_pace_500_s: number; logbook_time_s: number;
    logbook_dist_m: number; forced: boolean; contrast: number; n_turns: number;
    /** a second, unrecorded machine in the rotation: its Logbook total, this person's metres there (estimated) */
    second_machine?: { logbook_dist_m: number; ratio: number; est_m: number; time_s: number; on_here_s: number; off_here_s: number } | null } | null;
  /** hand-logged session (concept2 manual.json): not recorded, the monitors' totals; no per-person split */
  manual?: { source: string; people: string[]; on_s: number; off_s: number; rounds: number; session_s: number;
    machines: { dist_m: number; time_s: number; pace_500_s: number }[]; total_m: number } | null;
}

export interface Activity {
  id: string; start: string; date: string; name: string | null; type: string; sport: Sport;
  dist_km: number | null; dur_s: number | null; moving_s: number | null; hr: number | null; maxhr: number | null;
  elev: number | null; load: number | null; te: number | null; kcal: number | null; location: string | null;
  lat: number | null; lon: number | null; place: string | null; area: string | null; route: string | null;
  stype: string | null; type_detail: string | null; sig: string | null; fit: boolean; track: boolean;
  // WP-A additions (display names / labels; `place` stays the id for links and filters)
  type_label?: string | null; place_name?: string | null; start_place?: string | null; start_place_name?: string | null;
  region?: string | null; route_name?: string | null; workout_label?: string | null; swim?: SwimSummary | null;
  /** the workout was done with others (data/together.json; /community/<gid>); `workout` = the group's workout */
  together?: TogetherLink | null;
  /** `workout_label` is the one workout name (store.workout_name); detail = same-workout label with rest / route */
  workout_detail?: string | null;
  /** average rep pace of the main reps (total rep distance / total rep time), m/s */
  rep_pace_ms?: number | null;
  /** mean HR over the same main reps */
  rep_hr?: number | null;
  /** false = the watch distance isn't trusted (indoor / no GPS): dist_km is kept but lists show dist_est_km
   *  ("≈ 9.2", lap-based estimate, why in dist_note) or "—"; km totals and pace leave it out */
  dist_trusted?: boolean | null; dist_est_km?: number | null; dist_note?: string | null;
  /** Concept2 erg session (RowErg / SkiErg): machine, pace /500 m, watts, stroke rate, intervals; null otherwise */
  erg?: ErgInfo | null;
}
export interface TogetherLink { gid: string; label: string; kind: string; role: string; with: string[]; workout?: string | null }
interface SwimSummary { pool_m: number | null; lengths?: number; n_lengths?: number; dist_m: number | null; active_s: number | null;
  rest_s: number | null; timer_s?: number | null; elapsed_s?: number | null; pace_100m_s: number | null; strokes_per_length: number | null;
  bad_lengths?: number; n_rests: number | null; longest_rest_s: number | null }
export interface SwimLength { start: number; time: number; strokes: number | null; stroke: number | string | null }
export interface Meta {
  person: string; n_activities: number; n_tracks: number; first: string; last: string; years: string[];
  sports: { id: Sport; n: number; label?: string }[]; types: { id: string; n: number; label?: string }[]; groups_built: string | null;
}
export interface Series { cols: string[]; t: number[]; lat?: (number | null)[]; lon?: (number | null)[];
  hr?: (number | null)[]; speed?: (number | null)[]; alt?: (number | null)[]; dist?: (number | null)[];
  cad?: (number | null)[]; power?: (number | null)[]; start_utc: number }
export interface Lap { start: number | null; time: number | null; timer: number | null; dist: number | null; speed: number | null;
  hr: number | null; maxhr: number | null; climb: number | null; desc: number | null; intensity: string | null; trigger: string | null;
  kind?: "swim" | "rest" | null; swim_s?: number | null; pace_100m_s?: number | null }
export interface Rep { start?: number; end?: number; time?: number; time_s?: number; dist?: number; dist_m?: number;
  speed?: number; speed_ms?: number; hr: number | null; maxhr?: number | null;
  gps_dist?: number; dist_source?: string }  // dist_source "track_set": track rep counted as its set distance, gps_dist = measured
export interface Leg { t: number; dur_s: number; dist_m: number; kmh: number; hr: number | null; hr_max: number | null;
  climb_m: number; desc_m: number; grade_pct: number | null }
interface Pause { t: number; dur_s: number; lat: number | null; lon: number | null; km: number; timer: number }
interface Similar { id: number; score: number; reasons: string }
export interface ActivityDetail {
  activity: Activity; group: Record<string, any>; legs: Leg[]; pauses: Pause[];
  similar: { route?: Similar[]; workout?: Similar[] } | null;
  route: { id: string; name: string; n: number; shape: string; rank?: RouteRank | null } | null;
  laps: Lap[]; reps: Rep[]; breaks: any[]; series: Series | null; newer: string | null; older: string | null;
  swim?: (SwimSummary & { lengths: SwimLength[] }) | null;
}
export interface TrackLite { id: string; date?: string; sport: Sport; type?: string; name?: string | null; km?: number | null;
  bbox: [number, number, number, number]; poly: string }
interface DailySummary { date: string; steps: number | null; rhr: number | null; minhr: number | null; maxhr: number | null;
  stress: number | null; bbmax: number | null; bbmin: number | null; resp: number | null; spo2: number | null;
  mod: number | null; vig: number | null; kcal: number | null; floors: number | null }
interface Sleep { date: string; total: number; deep: number | null; light: number | null; rem: number | null;
  awake: number | null; score: number | null; hr: number | null }
export interface Day { date: string; activities: Activity[]; tracks: TrackLite[]; summary: DailySummary | null;
  sleep: Sleep | null; hr: { rhr: number | null; values: [number, number][] } | null;
  prev_active: string | null; next_active: string | null }
export interface CalDay { date: string; n: number; km: number; s: number; sports: Record<string, number> }
export interface Place { id: string; name: string; lat: number; lon: number; n: number; types: Record<string, number>;
  area: string | null; routes: number; last: string | null; n_main?: number; n_touched?: number; n_start?: number;
  kind?: string | null; src?: string | null }
export interface RouteRow { id: string; name: string; place: string; sport: string; shape: string; dist_km: number;
  n: number; first: string | null; last: string | null; has_legs: boolean; place_name?: string | null; start_place_name?: string | null;
  end_place_name?: string | null; via_names?: string[] | null; climb_m?: number | null; region?: string | null;
  /** fastest time on the route (main direction); null for laps routes */
  pb?: { id: string; route_time_s: number; best_date: string | null; of: number; group: string } | null }
/** time on a route and its rank (places.route_rankings); point-to-point routes rank per direction */
interface RouteRank { route_time_s: number; time_src: string; rank: number; of: number; best_s: number; best_id: string;
  best_date: string | null; delta_s: number; is_pb: boolean; dir: string | null; group: string }
export interface RouteDetail extends RouteRow {
  medoid: number; ref: { id: number; lat: number[]; lon: number[]; km: number[]; alt: (number | null)[] } | null;
  waypoints: { km: number; name: string; lat: number; lon: number; share_stopped: number }[] | null;
  legs_table: any; members: (Activity & { route_dir: string | null; pause_s: number | null; poly: string | null } & Partial<RouteRank>)[];
  ranked?: boolean; pbs?: (RouteRank & { id: string })[]; trend?: { id: string; date: string | null; route_time_s: number; group: string }[];
  rank_definition?: string;
}

const memo = new Map<string, Promise<any>>();

/** "/api/x" -> "/api/<person>/x" for the person in the current URL (the server strips it again, deps.get_store) */
export function apiUrl(url: string, person = currentPerson()): string {
  if (!url.startsWith("/api/") || !person || splitPerson(url.slice(4))[0]) return url;   // not API, or already prefixed
  return `/api/${encodeURIComponent(person)}/${url.slice(5)}`;
}

export function get<T>(url: string): Promise<T> {
  url = apiUrl(url);
  let p = memo.get(url);
  if (!p) {
    p = fetch(url).then(async (r) => {
      if (!r.ok) {
        memo.delete(url);
        let msg = `${r.status} ${r.statusText}`;
        try { const j = await r.json(); msg = j.detail || j.todo || msg; } catch { /* not json */ }
        throw new Error(msg);
      }
      return r.json();
    });
    memo.set(url, p);
  }
  return p;
}

export function qs(params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? "?" + s : "";
}

export function useApi<T>(url: string | null): { data: T | null; error: string | null; loading: boolean } {
  url = url && apiUrl(url);   // key on the person too, so a person switch refetches
  const [st, set] = useState<{ data: T | null; error: string | null; loading: boolean; url: string | null }>(
    { data: null, error: null, loading: !!url, url });
  useEffect(() => {
    if (!url) { set({ data: null, error: null, loading: false, url }); return; }
    let live = true;
    set((s) => ({ ...s, loading: true, error: null, url }));
    get<T>(url).then((data) => live && set({ data, error: null, loading: false, url }),
      (e) => live && set({ data: null, error: String(e.message || e), loading: false, url }));
    return () => { live = false; };
  }, [url]);
  return st;
}

// ------------------------------------------------------------------ erg trend (Concept2 RowErg / SkiErg)
// GET /api/fitness/erg -> data/<p>/erg_fitness.json (scripts/erg_fitness.py, docs/erg.md). Paces in s /500 m
// (2k-equivalent by Paul's law), watts = 2.80 / (s per m)³. `machines` is empty without Concept2 data.
export type ErgMachine = "row" | "skierg";
export type ErgKind = "test" | "piece" | "intervals" | "warmup" | "ignored";
export interface ErgPoint { date: string; pace: number; sd: number; pace_fast: number; pace_slow: number; watts: number }
export interface ErgCurrent extends ErgPoint {
  pace_text: string; watts_fast: number; watts_slow: number; time_2k_s: number;
  last_evidence_date: string | null; days_since_evidence: number | null; stale: boolean; trend_90d_s: number | null;
}
export interface ErgPbDistance { m: number; pace: number; time_s: number; watts: number; id: string; date: string; label: string;
  what: string; piece_m: number; piece_s: number; exact: boolean }
export interface ErgPbDuration { s: number; watts: number; pace: number; id: string; date: string; label: string; what: string;
  piece_s: number; exact: boolean }
export interface ErgMachineDoc {
  label: string; unit: string; current: ErgCurrent; daily: ErgPoint[]; weekly: ErgPoint[];
  personal_bests: { best_2k_equivalent: { pace: number; watts: number; id: string; date: string; label: string; kind: ErgKind } | null;
    distances: ErgPbDistance[]; durations: ErgPbDuration[] };
  n_sessions: number; n_scored: number; first_date: string; last_date: string;
}
export interface ErgSession {
  id: string; date: string; start: string; machine: ErgMachine; label: string; workout_type: string | null; kind: ErgKind;
  reason?: string; work_s: number; work_m: number; rest_s?: number; n_reps?: number; work_share?: number;
  pace?: number; watts?: number; d_eff_s?: number; d_eff_m?: number; pace_2k?: number; watts_2k?: number;
  pace_text?: string; pace_2k_text?: string; sigma?: number; weight?: number; trend_pace?: number; delta_s?: number;
  drag_factor?: number | null; stroke_rate?: number | null;
}
export interface ErgDoc {
  built: string | null; today?: string; model_version?: number; params?: Record<string, unknown>; scale?: string;
  machines: Partial<Record<ErgMachine, ErgMachineDoc>>; sessions: ErgSession[];
}
