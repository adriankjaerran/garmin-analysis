// Types + URL helpers for /api/search, /api/nearest, /api/groups (scripts/similarity.py index).
import type { Sport } from "./api";

export interface SearchRow {
  id: string; date: string; start: string | null; name: string | null; sport: Sport; isport: string; atype: string; gps: boolean;
  dist_km: number | null; dist_trusted: boolean | null; dist_est_km?: number | null; dist_note?: string | null; moving_s: number | null; avg_hr: number | null; climb_m: number | null;
  speed_ms: number | null; place: string | null; place_src: string | null; places: string[]; region: string | null; regions: string[];
  route: string | null; route_name: string | null; family: string; family_label: string; category: string | null;
  signature: string | null; main_sig: string | null; exact_key: string | null; rep_count: number | null; reps_total: number | null;
  rep_dist_m: number | null; rep_time_s: number | null; rep_speed_ms: number | null; rest_s: number | null; rest_mode: string | null;
  /** WP-A: display names (place / start_place / places stay ids for links and filters), labels, same-workout rest */
  sport_label?: string | null; place_name?: string | null; start_place?: string | null; start_place_name?: string | null;
  place_names?: string[]; exact_label?: string | null; rest_group_s?: number | null;
  bbox: [number, number, number, number] | null; poly?: string | null;
  /** done with others (data/together.json) */
  together?: { gid: string; label: string; kind: string; role: string; with: string[]; workout?: string | null } | null;
  /** app-row fields (routers/search.app_fields): the one workout name, its detail, avg rep pace (m/s) */
  workout_label?: string | null; workout_detail?: string | null; rep_pace_ms?: number | null;
}
export interface SearchResult { n: number; truncated: boolean; km: number; filters: Record<string, any>; families: Record<string, number>; rows: SearchRow[] }
interface Reason { c: string | null; s: number | null; text: string }
export interface NearRow extends SearchRow { score: number; components: Record<string, number>; reasons: Reason[] }
export interface NearestResult { query: SearchRow; preset: string; weights: Record<string, number>; components: string[]; presets: string[]; results: NearRow[] }
export interface Group {
  key: string; label: string; n: number; first: string; last: string; ids: string[]; sports: Record<string, number>;
  lat?: number; lon?: number; kind?: string; src?: string; n_main?: number; n_touched?: number; n_start?: number; name?: string;
  shape?: { kind?: string; circle?: [number, number, number]; poly?: [number, number][] };
  dist_km?: number; climb_m?: number; is_climb?: boolean; poly?: string; start_place?: string; end_place?: string; via?: string[];
  place_name?: string; start_place_name?: string; end_place_name?: string; via_names?: string[];
  family?: string; places?: Record<string, number>;
}
export interface TreeNode { key: string | null; label?: string; n: number; first: string | null; last: string | null;
  places: Record<string, number>; children?: TreeNode[]; ids?: string[] }
interface Opt { id: string; n: number; n_main?: number; n_touched?: number; n_start?: number; label?: string; name?: string; kind?: string; src?: string; sport?: string }
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
const mmss = (s: number) => { const r = Math.round(s); return `${Math.floor(r / 60)}:${String(r % 60).padStart(2, "0")}`; };

/** "94 · 108 touched": main-place count first, touched count when it differs (Place model, docs/app.md) */
export function placeCount(nMain: number | null | undefined, nTouched: number | null | undefined): string {
  const m = nMain ?? 0, t = nTouched ?? m;
  return t > m ? `${m} · ${t} touched` : String(m);
}

type BB = [number, number, number, number];
/** Bounding box of the "home" cluster of a set of boxes (tracks / places): when the boxes spread over more than
 *  ~150 km (trips abroad, other towns), fit to the densest group instead of the whole world. Returns the box to fit
 *  and how many boxes lie outside it. */
export function coreBBox(boxes: BB[], spanKm = 150): { fit: BB | null; out: number } {
  if (!boxes.length) return { fit: null, out: 0 };
  const all = boxes.reduce((a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])] as BB);
  const kmx = (lat: number) => 111.32 * Math.cos((lat * Math.PI) / 180);
  const midLat = (all[1] + all[3]) / 2;
  if ((all[3] - all[1]) * 110.5 < spanKm && (all[2] - all[0]) * kmx(midLat) < spanKm) return { fit: all, out: 0 };
  const c = boxes.map((b) => [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2] as [number, number]);
  const R = 40;   // km: neighbours of a centre
  const d = (a: [number, number], b: [number, number]) => Math.hypot((a[0] - b[0]) * kmx((a[1] + b[1]) / 2), (a[1] - b[1]) * 110.5);
  // densest centre (O(n²) on ≤ ~1000 boxes; sample when larger)
  const step = Math.max(1, Math.floor(c.length / 400));
  let best = 0, bn = -1;
  for (let i = 0; i < c.length; i += step) {
    let n = 0;
    for (let j = 0; j < c.length; j += step) if (d(c[i], c[j]) < R) n++;
    if (n > bn) { bn = n; best = i; }
  }
  let keep = boxes.filter((_b, i) => d(c[i], c[best]) < spanKm / 2);
  // and drop the few stragglers at the cluster's edge (outer 3 % of centres on each side) so it isn't fitted to them
  if (keep.length >= 20) {
    const q = (a: number[], p: number) => a.sort((x, y) => x - y)[Math.floor(p * (a.length - 1))];
    const kc = keep.map((b) => [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]);
    const [w, e] = [q(kc.map((x) => x[0]), 0.03), q(kc.map((x) => x[0]), 0.97)];
    const [so, no] = [q(kc.map((x) => x[1]), 0.03), q(kc.map((x) => x[1]), 0.97)];
    keep = keep.filter((_b, i) => kc[i][0] >= w && kc[i][0] <= e && kc[i][1] >= so && kc[i][1] <= no);
  }
  const fit = keep.reduce((a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])] as BB);
  return { fit, out: boxes.length - keep.length };
}

