// Types + URL helpers for /api/search, /api/nearest, /api/groups (scripts/similarity.py index).
import type { Sport } from "./api";

export interface SearchRow {
  id: string; date: string; start: string | null; name: string | null; sport: Sport; isport: string; atype: string; gps: boolean;
  dist_km: number | null; dist_trusted: boolean | null; moving_s: number | null; avg_hr: number | null; climb_m: number | null;
  speed_ms: number | null; place: string | null; place_src: string | null; places: string[]; region: string | null; regions: string[];
  route: string | null; route_name: string | null; family: string; family_label: string; category: string | null;
  signature: string | null; main_sig: string | null; exact_key: string | null; rep_count: number | null; reps_total: number | null;
  rep_dist_m: number | null; rep_time_s: number | null; rep_speed_ms: number | null; rest_s: number | null; rest_mode: string | null;
  bbox: [number, number, number, number] | null; poly?: string | null;
}
export interface SearchResult { n: number; truncated: boolean; km: number; filters: Record<string, any>; families: Record<string, number>; rows: SearchRow[] }
export interface Reason { c: string | null; s: number | null; text: string }
export interface NearRow extends SearchRow { score: number; components: Record<string, number>; reasons: Reason[] }
export interface NearestResult { query: SearchRow; preset: string; weights: Record<string, number>; components: string[]; presets: string[]; results: NearRow[] }
export interface Group {
  key: string; label: string; n: number; first: string; last: string; ids: string[]; sports: Record<string, number>;
  lat?: number; lon?: number; kind?: string; src?: string; n_main?: number; n_touched?: number;
  shape?: { kind?: string; circle?: [number, number, number]; poly?: [number, number][] };
  dist_km?: number; climb_m?: number; is_climb?: boolean; poly?: string; start_place?: string; end_place?: string; via?: string[];
  family?: string; places?: Record<string, number>;
}
export interface TreeNode { key: string | null; label?: string; n: number; first: string | null; last: string | null;
  places: Record<string, number>; children?: TreeNode[]; ids?: string[] }
export interface Opt { id: string; n: number; n_main?: number; label?: string; name?: string; kind?: string; src?: string; sport?: string }
export interface SearchOptions {
  sports: Opt[]; places: Opt[]; regions: Opt[]; routes: Opt[]; families: Opt[]; categories: Opt[];
  signatures: Opt[]; exact: Opt[]; rest_modes: Opt[]; presets: string[]; built: string | null;
}

/** URL query keys that are search filters (passed through to /api/search and /api/groups unchanged) */
export const FILTER_KEYS = [
  "q", "sport", "place", "place_mode", "region", "region_mode", "route", "family", "category", "signature", "exact_key",
  "rep_count", "rep_len", "tol", "rest_s", "part", "distance_km", "from", "to",
] as const;

export function filterQuery(query: URLSearchParams): string {
  const q = new URLSearchParams();
  for (const k of FILTER_KEYS) { const v = query.get(k); if (v) q.set(k, v); }
  return q.toString();
}

export const compareUrl = (ids: string[]) => `/compare?ids=${ids.join(",")}`;

/** "1000 m" / "4:00" for a rep */
export function repLen(r: { rep_dist_m: number | null; rep_time_s: number | null; signature: string | null; main_sig?: string | null }): string {
  const s = r.main_sig || r.signature || "";
  if (/×\d+:\d\d/.test(s)) return r.rep_time_s ? mmss(r.rep_time_s) : "—";
  return r.rep_dist_m ? `${Math.round(r.rep_dist_m)} m` : "—";
}
export const mmss = (s: number) => { const r = Math.round(s); return `${Math.floor(r / 60)}:${String(r % 60).padStart(2, "0")}`; };
