// Community layer of one session page (/api/community/sessions/{gid}, docs/community.md): types, a small shared
// store (the hero, the members strip and the photo gallery are separate components on the page but show one state)
// and the edit calls (PATCH meta, kudos, photo upload with progress / delete). Not memoised like lib/api.ts `get`:
// edits refetch the session, so coming back to the page never shows stale metadata.
import { useEffect, useState } from "preact/hooks";
import { apiUrl } from "./api";
import { currentPerson } from "./router";
import type { PersonInfo } from "./people";
import type { TPlace } from "./together";

type N = number | null;

export interface CsCategory { id: string; label: string; emoji: string; description: string; n?: number }
export interface CsPhoto { id: string; url: string; thumb: string; by: string | null; at: string | null; taken: string | null; name: string | null; w: N; h: N }
export interface CsMeta {
  title: string | null; category: string | null; category_auto: string; category_reason: string | null; category_manual: boolean;
  note: string | null; guests: string[]; photos: CsPhoto[]; cover: string | null; kudos: string[]; hidden: boolean;
}
export interface CsForm {
  score: N; shape: N; delta: N; discipline: string | null; kind?: string | null; confidence: "high" | "medium" | "low" | "none" | string;
  text: string; flags?: string[]; z?: N; ref_sd?: N;
}
export interface CsNumbers { km: N; moving_s: N; dur_s: N; climb_m: N; kcal: N; reps: N; avg_hr: N; with_min: N; shared_min: N }
export interface CsRecord {
  kind: "club_best" | "pb"; measure: "rep" | "avg" | "race"; dist_m: number; label: string; person: string; key?: string;
  value_s: number; n?: number; prev_s: N; prev_person?: string | null; prev_gid?: string | null; prev_date?: string | null;
  improvement_s: N; text: string;
}
export interface CsMember {
  key: string; person: string; display: PersonInfo; role: "full" | "partial" | "other"; activity_id: string; workout: string | null;
  type: string | null; has_fit: boolean; timed: boolean; form: CsForm | null; numbers: CsNumbers | null;
  rank: N; rep_wins: N; reps_done: N; of: N; avg_s: N; pace_s_km: N; records: CsRecord[];
}
export interface CsCollective {
  km: N; moving_s: N; hours: N; reps: N; reps_shared: N; climb_m: N; kcal: N; person_min_together: N; person_min_shared: N;
  together_estimated: boolean; n_members: number; per_person?: Record<string, CsNumbers>;
}
export interface CsSession {
  gid: string; requested_gid: string; viewer: string | null; date: string; start_local: string; place: TPlace | null;
  workout: string; kind: string; label: string; label_text: string; people: Record<string, PersonInfo>;
  display_title: string; category_info: CsCategory; categories: CsCategory[]; meta: CsMeta;
  n_timed: number; n_guests: number; kudoed: boolean | null; collective: CsCollective;
  form: Record<string, CsForm | null>; form_available: boolean; members: CsMember[]; records: CsRecord[];
  nav: { newer: string | null; older: string | null };
  same_place: { gid: string; date: string; workout: string; category: string; people: string[] }[];
  store_error: string | null;
}

const base = (gid: string) => `/api/community/sessions/${encodeURIComponent(gid)}`;

// ------------------------------------------------------------------ shared store per gid
type St = { data: CsSession | null; error: string | null };
const store = new Map<string, St>();
const subs = new Map<string, Set<() => void>>();
const inflight = new Map<string, Promise<void>>();
const emit = (gid: string) => subs.get(gid)?.forEach((f) => f());

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : j.detail ? JSON.stringify(j.detail) : msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}

/** (re)load the session's community layer; components using useCommunity(gid) re-render */
export function reload(gid: string): Promise<void> {
  const running = inflight.get(gid);
  if (running) return running;
  const p = fetch(apiUrl(base(gid)), { cache: "no-store" }).then((r) => json<CsSession>(r))
    .then((data) => { store.set(gid, { data, error: null }); },
      (e) => { store.set(gid, { data: store.get(gid)?.data ?? null, error: String(e.message || e) }); })
    .finally(() => { inflight.delete(gid); emit(gid); });
  inflight.set(gid, p);
  return p;
}

/** the community layer of `gid` (fetched on first use per page visit; null while loading or when missing) */
export function useCommunity(gid: string): St {
  const [, tick] = useState(0);
  useEffect(() => {
    const f = () => tick((n) => n + 1);
    if (!subs.has(gid)) subs.set(gid, new Set());
    subs.get(gid)!.add(f);
    // a fresh visit (the first subscriber) refetches: edits from another tab / the list page show up
    if (subs.get(gid)!.size === 1) reload(gid);
    return () => { subs.get(gid)?.delete(f); };
  }, [gid]);
  return store.get(gid) ?? { data: null, error: null };
}

/** the person giving kudos / uploading: the URL's person (the API's viewer) */
export const viewerOf = (s: CsSession | null) => s?.viewer ?? currentPerson();

// ------------------------------------------------------------------ edits
export async function patchSession(gid: string, body: Partial<{ title: string | null; category: string | null; note: string | null;
  guests: string[]; hidden: boolean; cover: string | null }>): Promise<void> {
  const r = await fetch(apiUrl(base(gid)), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  await json<CsMeta>(r);
  await reload(gid);
}

export async function setKudos(gid: string, person: string, on: boolean): Promise<void> {
  const r = on
    ? await fetch(apiUrl(`${base(gid)}/kudos`), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ person }) })
    : await fetch(apiUrl(`${base(gid)}/kudos?person=${encodeURIComponent(person)}`), { method: "DELETE" });
  await json(r);
  await reload(gid);
}

/** upload one photo with progress (0..1); resolves with the stored photo */
export function uploadPhoto(gid: string, file: File, onProgress: (f: number) => void): Promise<CsPhoto> {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", apiUrl(`${base(gid)}/photos`));
    x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    x.onload = () => {
      let j: any = null;
      try { j = JSON.parse(x.responseText); } catch { /* not json */ }
      if (x.status >= 200 && x.status < 300) resolve(j);
      else reject(new Error(x.status === 413 ? "too large (max 15 MB)" : (typeof j?.detail === "string" ? j.detail : null) ?? `${x.status} ${x.statusText}`));
    };
    x.onerror = () => reject(new Error("network error"));
    const fd = new FormData();
    fd.append("file", file, file.name);
    x.send(fd);
  });
}

export async function deletePhoto(gid: string, id: string): Promise<void> {
  await json(await fetch(apiUrl(`${base(gid)}/photos/${encodeURIComponent(id)}`), { method: "DELETE" }));
  await reload(gid);
}

// ------------------------------------------------------------------ wording
/** a known person's name, else the name as given (guests can give kudos too) */
export const someoneName = (s: CsSession | null, p: string, name: (k: string) => string) => (s?.people?.[p] || /^[a-z0-9_]+$/.test(p) ? name(p) : p);

const REFS: [string, number][] = [
  ["a 400 m track", 0.4], ["the Sognsvann loop", 3.3], ["the Holmenkollen climb", 9.1], ["Oslo → Lillestrøm", 20],
  ["Oslo → Drammen", 40], ["Oslo → Hamar", 125], ["Oslo → Lillehammer", 170], ["Oslo → Trondheim", 495],
];
/** "that's Oslo → Drammen halfway" / "that's 2.4 × the Holmenkollen climb" / "that's 44 laps of a 400 m track" */
export function distanceFun(km: N): string | null {
  if (!km || km <= 0) return null;
  if (km < 6) return `that's ${Math.round(km / 0.4)} laps of a 400 m track`;
  // the reference the distance is closest to (log scale), not the track
  let best = REFS[1], bq = Infinity;
  for (const r of REFS.slice(1)) { const q = Math.abs(Math.log(km / r[1])); if (q < bq) { bq = q; best = r; } }
  const q = km / best[1];
  if (q >= 0.9 && q <= 1.12) return `that's about ${best[0]}`;
  if (q >= 0.42 && q < 0.6) return `that's ${best[0]} halfway`;
  if (q >= 0.6 && q < 0.9) return `that's ${Math.round(q * 100)} % of ${best[0]}`;
  if (q < 0.42) return `that's a third of ${best[0]}`;
  return `that's ${q.toFixed(1)} × ${best[0]}`;
}
/** "≈ 9 floors" / "≈ the Holmenkollen climb" for metres climbed */
export function climbFun(m: N): string | null {
  if (!m || m < 6) return null;
  if (m < 300) return `≈ ${Math.round(m / 3)} floors of stairs`;
  const q = m / 400;
  return q < 1.15 ? "≈ the Holmenkollen climb" : `≈ ${q.toFixed(1)} × the Holmenkollen climb`;
}
/** "1 h 37 min" */
export function hoursMin(s: N): string {
  if (!s) return "—";
  const m = Math.round(s / 60), h = Math.floor(m / 60);
  return h ? `${h} h ${String(m % 60).padStart(2, "0")} min` : `${m} min`;
}
