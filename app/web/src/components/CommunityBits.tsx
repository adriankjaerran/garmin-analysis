// Small shared pieces of the Community pages: avatars (Av), category chips, form-delta chips, stat tiles, the
// pair card (sessions together), a debounced search box and a tile-free route sketch (SVG of the members' tracks).
import type { ComponentChildren } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import { get } from "../lib/api";
import { setQuery } from "../lib/router";
import { decodePolyline } from "../lib/geo";
import { isoLocal, shortDate } from "../lib/format";
import { mapColour, personColour, personInfo, personName } from "../lib/people";
import { useTheme } from "../lib/theme";
import { catInfo, catVar, delta, deltaCls, type FormInfo } from "../lib/community";

type N = number | null;

/** The one avatar of the Community pages: initials on the person colour. `href` makes it a link (member page),
 *  `role` "partial" adds ◐ (part of the workout), "other" fades it (another workout). */
export function Av({ p, size, dim, title, href, role }:
  { p: string; size?: "xs" | "sm" | "lg" | "xl"; dim?: boolean; title?: string; href?: string; role?: string }) {
  useTheme();
  const i = personInfo(p);
  const cls = "cm-av" + (size ? " " + size : "") + (dim || role === "other" ? " dim" : "");
  const style = { background: personColour(p) };
  const roleText = role === "partial" ? " · partial (part of the workout)" : role === "other" ? " · other workout" : "";
  const tip = (title ?? personName(p)) + roleText;
  const mark = role === "partial" && <i class="role" aria-hidden="true">◐</i>;
  return href
    ? <a class={cls} style={style} href={href} title={tip} onClick={(e) => e.stopPropagation()}>{i.initials}{mark}</a>
    : <span class={cls} style={style} title={tip}>{i.initials}{mark}</span>;
}
/** overlapping avatars */
export const AvStack = ({ people, size, max = 6 }: { people: string[]; size?: "sm" | "lg"; max?: number }) => (
  <span class="cm-avs">
    {people.slice(0, max).map((p) => <Av key={p} p={p} size={size} />)}
    {people.length > max && <span class={"cm-av more" + (size ? " " + size : "")}>+{people.length - max}</span>}
  </span>
);
/** avatar + name, linking to the member page */
export const PersonLink = ({ p, size = "sm" }: { p: string; size?: "xs" | "sm" | "lg" }) => (
  <a class="cm-pl" href={`/community/members/${encodeURIComponent(p)}`}><Av p={p} size={size} />{personName(p)}</a>
);

export function CatChip({ id, label, solid }: { id: string; label?: string; solid?: boolean }) {
  const c = catInfo(id);
  return <span class={"cm-cat" + (solid ? " solid" : "")} style={{ "--c": catVar(id) } as any}><span class="e" aria-hidden="true">{c.emoji}</span>{label ?? c.label}</span>;
}

/** "+2.4" green / "−1.6" red; low confidence greyed; nothing for null (unless `showNone`) */
export function FormChip({ v, info, p, showNone }: { v: N | undefined; info?: FormInfo | null; p?: string; showNone?: boolean }) {
  if (v === null || v === undefined) {
    return showNone ? <span class="cm-form none" title={(p ? personName(p) + ": " : "") + "no form score for this session (easy run, hike, no watch data)"}>{p && <Av p={p} size="xs" />}—</span> : null;
  }
  if (info?.confidence === "none") {   // rejected by the fitness model itself: no number, like the session page
    return <span class="cm-form unrel" title={(p ? personName(p) + ": " : "") + `score unreliable (${delta(v)} vs form, far off and rejected by the fitness model — e.g. HR not maximal, a mislabelled session)`}>{p && <Av p={p} size="xs" />}?</span>;
  }
  const low = info?.confidence === "low";
  const tip = [
    p ? personName(p) : null,
    info?.text ?? `${delta(v)} vs form`,
    info?.score !== undefined && info?.score !== null && info?.shape !== undefined && info?.shape !== null ? `score ${info.score.toFixed(1)} vs shape ${info.shape.toFixed(1)} the day before` : null,
    info?.discipline ? `${info.discipline} discipline` : null,
    info?.confidence ? `${info.confidence} confidence${low ? " — take with a grain of salt" : ""}` : null,
  ].filter(Boolean).join(" · ");
  return <span class={"cm-form " + deltaCls(v) + (low ? " low" : "")} title={tip}>{p && <Av p={p} size="xs" />}{delta(v)}</span>;
}

/** big number tile */
export const Stat = ({ v, label, sub, accent, title }: { v: ComponentChildren; label: ComponentChildren; sub?: ComponentChildren; accent?: string; title?: string }) => (
  <div class="cm-stat" style={accent ? ({ "--c": accent } as any) : undefined} title={title}>
    <b>{v}</b><span class="l">{label}</span>{sub !== undefined && sub !== null && sub !== "" && <span class="s">{sub}</span>}
  </div>
);

/** a pair of members: how often they trained together (no score), viewer first */
export function PairCard({ a: a0, b: b0, viewer, sessions, last }: { a: string; b: string; viewer: string | null; sessions: number; last?: string | null }) {
  const [a, b] = viewer === b0 ? [b0, a0] : [a0, b0];
  return (
    <div class="cm-pair">
      <div class="top">
        <PersonLink p={a} size="lg" />
        <b class="score" title="Sessions together">{sessions}</b>
        <span class="rt"><PersonLink p={b} size="lg" /></span>
      </div>
      <div class="sub">
        <span>{sessions === 1 ? "session" : "sessions"} together</span>
        {last && <span>last {shortDate(last, isoLocal())}</span>}
      </div>
    </div>
  );
}

export function SearchBox({ value, placeholder = "Search…" }: { value: string; placeholder?: string }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  useEffect(() => {
    if (v === value) return;
    const t = setTimeout(() => setQuery({ q: v.trim() }), 350);
    return () => clearTimeout(t);
  }, [v]);
  return <input type="search" value={v} placeholder={placeholder} class="cm-q" aria-label="Search"
    onInput={(e) => setV((e.target as HTMLInputElement).value)} onKeyDown={(e) => e.key === "Enter" && setQuery({ q: v.trim() })} />;
}

// ---------------------------------------------------------------- route sketch
interface TrackRow { id: string; poly: string; bbox: [number, number, number, number] }
const trackCache = new Map<string, Map<string, string>>();

/** activity id -> encoded polyline (20 m simplified) for these people, from /api/<person>/map/tracks (one request each, memoised) */
export function usePeopleTracks(people: string[]): Map<string, string> {
  const key = [...new Set(people)].sort().join(",");
  const [tracks, setTracks] = useState<Map<string, string>>(() => merged(key));
  useEffect(() => {
    if (!key) return;
    let live = true;
    Promise.all(key.split(",").map(async (p) => {
      if (trackCache.has(p)) return;
      try {
        const r = await get<{ tracks: TrackRow[] }>(`/api/${encodeURIComponent(p)}/map/tracks`);
        trackCache.set(p, new Map(r.tracks.map((t) => [t.id, t.poly])));
      } catch { trackCache.set(p, new Map()); }
    })).then(() => live && setTracks(merged(key)));
    return () => { live = false; };
  }, [key]);
  return tracks;
}
function merged(key: string): Map<string, string> {
  const m = new Map<string, string>();
  for (const p of key.split(",")) for (const [k, v] of trackCache.get(p) ?? []) m.set(k, v);
  return m;
}

/** the members' tracks as lines on a plain background (no tiles): a cheap "mini map" per card */
export function RouteSketch({ lines, w = 320, h = 140 }: { lines: { person: string; poly: string; dim?: boolean }[]; w?: number; h?: number }) {
  const paths = useMemo(() => {
    const dec = lines.map((l) => ({ ...l, c: decodePolyline(l.poly) })).filter((l) => l.c.length > 1);
    if (!dec.length) return null;
    let w0 = Infinity, s0 = Infinity, e0 = -Infinity, n0 = -Infinity;
    for (const l of dec) for (const [lo, la] of l.c) { w0 = Math.min(w0, lo); e0 = Math.max(e0, lo); s0 = Math.min(s0, la); n0 = Math.max(n0, la); }
    const k = Math.cos(((s0 + n0) / 2) * Math.PI / 180);
    const dx = Math.max((e0 - w0) * k, 1e-5), dy = Math.max(n0 - s0, 1e-5);
    const pad = 14, sc = Math.min((w - 2 * pad) / dx, (h - 2 * pad) / dy);
    const ox = (w - dx * sc) / 2, oy = (h - dy * sc) / 2;
    const P = ([lo, la]: [number, number]) => `${(ox + (lo - w0) * k * sc).toFixed(1)},${(oy + (n0 - la) * sc).toFixed(1)}`;
    return dec.map((l) => ({ ...l, d: "M" + l.c.map(P).join("L"), start: P(l.c[0]) }));
  }, [lines.map((l) => l.poly.length + l.person).join("|"), w, h]);
  if (!paths) return null;
  return (
    <svg class="cm-sketch" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      {paths.map((p) => <path d={p.d} class="casing" />)}
      {paths.map((p, i) => <path d={p.d} stroke={mapColour(p.person)} class={"ln" + (p.dim ? " dim" : "")} style={{ strokeDasharray: i ? "none" : undefined }} />)}
      {paths.length > 0 && (() => { const [x, y] = paths[0].start.split(","); return <circle cx={x} cy={y} r="4" class="start" />; })()}
    </svg>
  );
}
