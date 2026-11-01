// Community layer of one session page (/api/community/sessions/{gid}, docs/community.md): types, a small shared
// store (the hero, the members strip and the photo gallery are separate components on the page but show one state)
// and the edit calls (PATCH meta, kudos, photo upload with progress / delete). Not memoised like lib/api.ts `get`:
// edits refetch the session, so coming back to the page never shows stale metadata.
import { useEffect, useState } from "preact/hooks";
import { apiUrl } from "./api";
import { currentPerson } from "./router";
import type { PersonInfo } from "./people";
import type { TPlace, TSession, TargetAvg } from "./together";

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
export interface CsNumbers { km: N; moving_s: N; dur_s: N; climb_m: N; kcal: N; reps: N; avg_hr: N }
export interface CsMember {
  key: string; person: string; display: PersonInfo; role: "full" | "partial" | "other"; activity_id: string; workout: string | null;
  type: string | null; has_fit: boolean; timed: boolean; form: CsForm | null; numbers: CsNumbers | null;
  rank: N; reps_done: N; n_counted: N; of: N; avg_s: N; pace_s_km: N;
  by_target: TargetAvg[] | null; best: { slot: number; label: string; result_s: number } | null;
}
export interface CsCollective {
  km: N; moving_s: N; hours: N; reps: N; climb_m: N; kcal: N; n_members: number; per_person?: Record<string, CsNumbers>;
}
export interface CsSession {
  gid: string; requested_gid: string; viewer: string | null; date: string; start_local: string; place: TPlace | null;
  workout: string; kind: string; label: string; label_text: string; people: Record<string, PersonInfo>;
  display_title: string; category_info: CsCategory; categories: CsCategory[]; meta: CsMeta;
  n_timed: number; n_guests: number; kudoed: boolean | null; collective: CsCollective;
  form: Record<string, CsForm | null>; form_available: boolean; members: CsMember[];
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
  const j = await json<{ kudos: string[]; kudoed: boolean | null }>(r);
  kudosOverride.set(gid, { kudos: j.kudos, kudoed: j.kudoed ?? j.kudos.includes(person) });
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

// ------------------------------------------------------------------ kudos from the list page
/** kudos changed in this page visit (the list API response is memoised): gid -> latest state */
export const kudosOverride = new Map<string, { kudos: string[]; kudoed: boolean }>();
/** POST / DELETE kudos as `person`; returns the new list (also remembered in kudosOverride) */
export async function toggleKudos(gid: string, person: string, on: boolean): Promise<{ kudos: string[]; kudoed: boolean }> {
  const r = on
    ? await fetch(apiUrl(`${base(gid)}/kudos`), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ person }) })
    : await fetch(apiUrl(`${base(gid)}/kudos?person=${encodeURIComponent(person)}`), { method: "DELETE" });
  const j = await json<{ kudos: string[]; kudoed: boolean | null }>(r);
  const st = { kudos: j.kudos, kudoed: j.kudoed ?? j.kudos.includes(person) };
  kudosOverride.set(gid, st);
  return st;
}

// ------------------------------------------------------------------ facts: story, highlights, per-person badges
type Names = (k: string) => string;
const WEEKDAY = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
export const partOfDay = (hhmm: string) => { const h = Number(hhmm.slice(0, 2)); return h < 5 ? "night" : h < 12 ? "morning" : h < 17 ? "afternoon" : h < 22 ? "evening" : "night"; };
export const andList = (xs: string[]) => (xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);
const signed = (d: number) => `${d > 0 ? "+" : d < 0 ? "−" : "±"}${Math.abs(d).toFixed(1)}`;
const sd = (xs: number[]) => { const m = xs.reduce((a, b) => a + b, 0) / xs.length; return Math.sqrt(xs.reduce((a, b) => a + (b - m) ** 2, 0) / xs.length); };
const t1 = (s: number) => (s < 59.95 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`);

export interface Highlight { id: string; emoji: string; title: string; person: string | null; value: string; sub: string; rep?: string }
export interface PersonFacts { bestRep: { label: string; s: number } | null; recoveries: number; breaks: number; even: number | null; strongFinish: number | null }
export interface Facts { story: string[]; highlights: Highlight[]; people: Record<string, PersonFacts>; sameTarget: boolean }

/** everything the session page says in words, from the together session (+ the club layer when loaded) */
export function sessionFacts(s: TSession, c: CsSession | null, name: Names): Facts {
  const iv = s.intervals, cont = s.continuous, g = s.group;
  const members = s.participants.filter((p) => p.role !== "other");
  const people: Record<string, PersonFacts> = {};
  for (const p of members) people[p.key] = { bestRep: null, recoveries: 0, breaks: 0, even: null, strongFinish: null };
  const hl: Highlight[] = [];
  const story: string[] = [];
  const who = andList([...new Set(members.map((p) => name(p.key)))]);
  const guests = c?.meta.guests ?? [];
  const d = new Date(g.date + "T12:00:00");
  const when = `On a ${WEEKDAY[d.getDay()]} ${partOfDay(g.start_local)}`;
  const place = c?.place?.name ?? g.place?.name;
  const verb = g.sport === "hike" ? "hiked" : g.sport === "bike" ? "rode" : g.sport === "ski" ? "skied" : g.sport === "swim" ? "swam" : "ran";
  const kmTot = c?.collective.km ?? null;
  const raceKm = cont?.race?.[0]?.dist_km;
  const adj = ({ easy: "an easy ", long: "a long ", tempo: "a tempo " } as Record<string, string>)[c?.category_info.id ?? ""] ?? "";
  const what = iv ? g.workout : s.kind === "race" ? (raceKm ? `${raceKm.toFixed(1)} km` : "together")
    : kmTot && members.length ? `${adj}${(kmTot / members.length).toFixed(1)} km` : g.workout.toLowerCase();
  story.push(`${when}${place ? ` at ${place}` : ""}, ${who}${guests.length ? ` (with ${andList(guests)})` : ""} ${s.kind === "race" ? "raced" : verb} ${what}.`);

  let sameTarget = false;
  if (iv && !g.no_track) {
    // every rep a person ran counts (no "reps everyone ran" subset)
    const full = (k: string) => iv.slots.map((sl) => ({ sl, c: iv.reps[k]?.[sl.slot] })).filter((x) => x.c && !x.c.partial && !x.c.suspect && !x.c.warmup && x.c.result_s);
    const tg = new Set(members.flatMap((p) => full(p.key).map((x) => x.sl.target?.text)));
    const anyFull = members.find((p) => full(p.key).length)?.key;
    const t0 = anyFull ? full(anyFull)[0].sl.target : null;
    sameTarget = tg.size === 1 && t0?.kind === "dist" && iv.slots.length >= 3;
    const done = iv.leaderboard.filter((l) => l.reps_done > 0 && people[l.person]);
    const nReps = done.reduce((a, l) => a + l.reps_done, 0);
    if (done.length > 4) story.push(`${done.length} members ran ${nReps} reps between them.`);
    else if (done.length > 1 && new Set(done.map((l) => l.reps_done)).size === 1) story.push(`${done.length === 2 ? "Both" : "Everyone"} ran ${done[0].reps_done} reps.`);
    else if (done.length > 1) story.push(`${andList(done.map((l, i) => `${name(l.person)} ${i === 0 ? `ran ${done[0].reps_done} reps` : l.reps_done}`))}.`);
    // rep of the day: the closest rep anyone ran together
    const cl = closestRep(iv);
    if (cl) {
      const st = iv.standings[cl.slot];
      const [a, b] = st.order;
      const sl = iv.slots[cl.slot];
      if (iv.standings.filter((x) => x.order.length >= 2).length >= 3) {
        hl.push({ id: "closest", emoji: "🎯", title: "Rep of the day", person: null, rep: cl.label,
          value: `Rep ${cl.label} · ${cl.gap_s.toFixed(1)} s`, sub: `apart: ${name(a.person)} ${t1(a.result_s)} · ${name(b.person)} ${t1(b.result_s)}${sl?.target ? ` (${sl.target.text})` : ""}` });
        story.push(`Rep ${cl.label} was the closest: ${cl.gap_s.toFixed(1)} s between the first two.`);
      }
    }
    for (const l of iv.leaderboard) {
      const pf = people[l.person];
      if (!pf) continue;
      if (l.best) pf.bestRep = { label: l.best.label, s: l.best.result_s };
      const f = l.fade_s ?? null;
      if (f !== null) pf.strongFinish = f;
      if (sameTarget) {
        const xs = full(l.person).map((x) => x.c!.result_s!);
        if (xs.length >= 4) pf.even = sd(xs);
      }
    }
    if (sameTarget) {
      const best = iv.leaderboard.filter((l) => l.best).sort((a, b) => a.best!.result_s - b.best!.result_s)[0];
      if (best?.best) hl.push({ id: "fastest", emoji: "🏎️", title: "Fastest rep", person: best.person, rep: best.best.label,
        value: `${name(best.person)} · ${t1(best.best.result_s)}`, sub: `Rep ${best.best.label}, ${t0!.text}` });
    }
    // recovery: who's HR dropped more, per break
    const rf = iv.recovered_faster;
    if (rf.length >= 2) {
      for (const r of rf) if (people[r.person]) people[r.person].recoveries++;
      for (const k of Object.keys(people)) people[k].breaks = rf.length;
      const top = Object.entries(people).sort((a, b) => b[1].recoveries - a[1].recoveries)[0];
      if (top && top[1].recoveries > rf.length / 2) {
        hl.push({ id: "recovery", emoji: "💚", title: "Faster recovery", person: top[0], value: `${name(top[0])} · ${top[1].recoveries} of ${rf.length}`,
          sub: "breaks with the bigger heart-rate drop in the first 30 s" });
        if (top[1].recoveries / rf.length >= 0.6) story.push(`${name(top[0])} recovered faster in ${top[1].recoveries} of the ${rf.length} breaks.`);
      }
    }
    const fin = Object.entries(people).filter(([, v]) => v.strongFinish !== null && v.strongFinish < -0.5).sort((a, b) => a[1].strongFinish! - b[1].strongFinish!)[0];
    if (fin) hl.push({ id: "finish", emoji: "📈", title: "Strongest finish", person: fin[0], value: `${name(fin[0])} · ${Math.abs(fin[1].strongFinish!).toFixed(1)} s faster`,
      sub: "the last 3 reps vs the first 3, per rep" });
    const even = Object.entries(people).filter(([, v]) => v.even !== null).sort((a, b) => a[1].even! - b[1].even!)[0];
    if (even && members.length > 1) hl.push({ id: "even", emoji: "🎚️", title: "Most even", person: even[0], value: `${name(even[0])} · ±${even[1].even!.toFixed(1)} s`,
      sub: "the smallest spread of their rep times" });
  } else if (cont?.available) {
    const race = cont.race ?? [];
    if (race.length >= 2) {
      const [a] = race;
      story.push(race.length > 4 ? `${race.length} members finished, from ${hms(a.time_s)} to ${hms(race[race.length - 1].time_s)}.`
        : `${andList(race.map((r, i) => `${name(r.person)} ${i === 0 ? "finished in " : ""}${hms(r.time_s)}`))}.`);
      hl.push({ id: "winner", emoji: "🏁", title: "First across the line", person: a.person, value: `${name(a.person)} · ${hms(a.time_s)}`, sub: `${race.length} finishers` });
    }
    const ap = cont.apart?.[0];
    if (ap?.with_pct && ap.with_pct >= 20) {
      if (!race.length) story.push(`Side by side ${ap.with_pct} % of the time${ap.median_apart_m !== null ? `, typically ${ap.median_apart_m} m apart` : ""}.`);
      hl.push({ id: "together", emoji: "👥", title: "Side by side", person: null, value: `${ap.with_pct} % of the time`, sub: ap.within_20m_pct !== null ? `within 20 m ${ap.within_20m_pct} %` : "" });
    }
    const led: Record<string, number> = {};
    for (const l of cont.led ?? []) if (l.person) led[l.person] = (led[l.person] ?? 0) + (l.to_m - l.from_m);
    const front = Object.entries(led).sort((a, b) => b[1] - a[1])[0];
    if (front && front[1] >= 300 && front[0] !== race[0]?.person) hl.push({ id: "front", emoji: "🧭", title: "Most in front", person: front[0], value: `${name(front[0])} · ${(front[1] / 1000).toFixed(1)} km`, sub: "in front by more than 2 s" });
    let fk: { k: string; km: number; t: number } | null = null;
    for (const r of cont.km_splits ?? []) for (const [k, v] of Object.entries(r.people)) if (v.time_s && (!fk || v.time_s < fk.t)) fk = { k, km: r.km, t: v.time_s };
    if (fk && (cont.km_splits?.length ?? 0) >= 2) hl.push({ id: "fastkm", emoji: "🏎️", title: "Fastest km", person: fk.k, value: `${name(fk.k)} · ${hms(fk.t)}`, sub: `km ${fk.km}` });
  }
  // form: how each did against their current shape
  if (c) {
    const fs = c.members.filter((m) => m.role !== "other" && m.form && m.form.delta !== null && m.form.confidence !== "none").map((m) => ({ k: m.key, d: m.form!.delta! }));
    const up = fs.filter((f) => f.d >= 0.5);
    if (fs.length && up.length === fs.length && fs.length > 1) story.push(`${fs.length === 2 ? "Both" : `All ${fs.length}`} ran above their current form (${andList(fs.map((f) => signed(f.d)))}).`);
    else if (up.length) story.push(`${andList(up.map((f) => `${name(f.k)} (${signed(f.d)})`))} ran above current form.`);
    const jump = [...fs].sort((a, b) => b.d - a.d)[0];
    if (jump && jump.d >= 1 && fs.length > 1) hl.push({ id: "form", emoji: "🚀", title: "Biggest form jump", person: jump.k, value: `${name(jump.k)} · ${signed(jump.d)}`, sub: "vs their shape the day before" });
  }
  return { story, highlights: hl, people, sameTarget };
}
/** "39:08" / "1:02:10" / "45 s" */
/** the slot with the smallest gap between the first two (any rep two or more people ran) */
export function closestRep(iv: NonNullable<TSession["intervals"]>): { slot: number; label: string; gap_s: number } | null {
  let out: { slot: number; label: string; gap_s: number } | null = null;
  for (const st of iv.standings) if (st.order.length >= 2 && (!out || st.order[1].gap_s < out.gap_s)) out = { slot: st.slot, label: st.label, gap_s: st.order[1].gap_s };
  return out;
}
export function hms(s: number): string {
  s = Math.round(s);
  if (s < 60) return `${s} s`;
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`;
}
