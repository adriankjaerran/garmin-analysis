// Race replay for a Together session: everyone's dot running on the map, two modes.
// - Rep / km mode (a chip per rep, or per km along the shared route for continuous runs): every circle starts at
//   their own start of that rep ("Own start") or at their real clock time ("Clock", the session page's toggle) and
//   they run it on one clock; live order with metres behind the leader, rep time, each person's own time so far; a
//   passes feed ("R4 · 210 m: Patrik passes Adrian", from the server's along-track crossings with hysteresis) and
//   overall rank changes (only while everyone ran the same reps); a finish card with the rep's order; auto-advance.
// - Whole session: the group clock from the first to the last rep (or the joint stretch), "reps only" skips breaks.
// Play / pause, speeds, a scrub bar with rep / pass / finish markers, keys: space, ← → (rep), shift ← → (5 s),
// 1–9 / 0 (rep 1–10), W (whole session). Data: /api/together/<gid>/replay (segments, passes; docs/together.md).
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { dur, repLabel } from "../lib/format";
import { mapColour, personColour, personInfo, personName } from "../lib/people";
import { setQuery, useLocation } from "../lib/router";
import { useTheme } from "../lib/theme";
import { behind, pathBetween, posAt, repTime, valAt, type Replay as ReplayData, type ReplayPerson, type TSession } from "../lib/together";
import { TogetherMap, type MapDot, type MapLine } from "./TogetherMap";

type N = number | null;
interface SegPass { x_s: number; at_m: N; who: string; whom: string }
interface SessPass { t_s: number; at_m: N; who: string; whom: string; seg: number | null; label: string | null }
interface SegPerson { start_s: number; time_s: number; scale: number; partial: boolean; result_s: N; rank: N }
interface Segment {
  i: number; kind: "rep" | "km"; slot: number; label: string; title: string; target_m: N; from_m?: number;
  start_s: number; end_s: number; people: Record<string, SegPerson>; passes: { own: SegPass[]; clock: SegPass[] };
  /** each person's own time so far (reps: rank / gap only while everyone in it ran the same reps) */
  total: Record<string, { total_s: number; gap_s: N; rank: N; n?: number }> | null;
  moves: { person: string; from_rank: number; to_rank: number }[] | null;
}
/** the replay payload with the segment fields (docs/together.md) */
type RX = ReplayData & { segment_kind?: "rep" | "km" | null; segments?: Segment[]; passes?: SessPass[];
  people: Record<string, ReplayPerson & { route_m?: N[] }> };

const SPEEDS = [1, 2, 4, 8, 16];
const SPEEDS_LONG = [1, 2, 4, 8, 16, 32, 64];   // whole continuous sessions (an hour at 16× is still 4 min)
const LEAD = 4;   // s before a rep start when "reps only" jumps there / a rep starts
const HOLD_MS = 2600;   // the finish card stays this long (wall time) before auto-advance
const TRAIL_S = 20;

/** local wall-clock "20:16:32" for a group-clock second */
export function clockText(s: TSession, t: number): string {
  const ms = Date.parse(s.group.start_local_iso + "Z") + (Date.parse(s.t0_utc) + t * 1000 - Date.parse(s.group.start_utc));
  const d = new Date(ms);
  return [d.getUTCHours(), d.getUTCMinutes(), d.getUTCSeconds()].map((x) => String(x).padStart(2, "0")).join(":");
}

interface Standing { key: string; cells: string[]; done?: boolean; hr?: number | null }
interface Board { title: string; cols: string[]; rows: Standing[] }

/** live standings at clock time t, one unit per column: in a rep [distance, metres behind, time]; in a break the
 *  last rep's [time, gap]; continuous [distance, metres behind] */
function standingsAt(s: TSession, r: RX, t: number): Board {
  const iv = s.intervals;
  const keys = Object.keys(r.people);
  const hrOf = (k: string) => { const v = valAt(r.people[k].t, r.people[k].hr, t); return v === null ? null : Math.round(v); };
  if (iv) {
    // inside a rep window (any participant's cell running)?
    const slotAt = iv.slots.find((sl) => {
      const cells = Object.values(iv.reps).map((c) => c[sl.slot]).filter(Boolean);
      return cells.some((c) => t >= c!.clock_start_s - 1 && t <= c!.clock_start_s + (c!.own_time_s ?? c!.time_s ?? 0) + 1);
    });
    if (slotAt) {
      const rows = keys.filter((k) => iv.reps[k]?.[slotAt.slot]).map((k) => {
        const c = iv.reps[k][slotAt.slot]!;
        const p = r.people[k];
        const t0 = c.clock_start_s, own = c.own_time_s ?? c.time_s ?? 0;
        const d0 = valAt(p.t, p.dist_m, t0), d1 = valAt(p.t, p.dist_m, Math.min(t, t0 + own));
        const scale = c.scale ?? 1;
        const d = t < t0 || d0 === null || d1 === null ? 0 : Math.max(0, (d1 - d0) * scale);
        const done = t > t0 + own;
        return { k, c, d, done, el: Math.max(0, t - t0) };
      });
      const lead = Math.max(0, ...rows.map((x) => x.d));
      rows.sort((a, b) => (a.done && b.done ? (a.c.result_s ?? a.c.time_s ?? 0) - (b.c.result_s ?? b.c.time_s ?? 0) : a.done ? -1 : b.done ? 1 : b.d - a.d));
      const target = slotAt.target?.kind === "dist" ? slotAt.target.value : null;
      return {
        title: `Rep ${slotAt.label}${slotAt.target ? " · " + slotAt.target.text : ""}`,
        cols: ["Distance", "Behind", "Time"],
        rows: rows.map((x, i) => ({
          key: x.k, done: x.done, hr: hrOf(x.k),
          cells: [
            `${Math.round(x.done && target ? target : x.d)} m`,
            x.done ? "finished" : t < x.c.clock_start_s ? "start…" : i === 0 || x.d >= lead - 0.5 ? "leads" : `${Math.round(lead - x.d)} m`,
            x.done ? repTime(x.c.time_s ?? x.c.own_time_s) : dur(x.el),
          ],
        })),
      };
    }
    // a break: the last finished rep's order
    const prev = [...iv.standings].reverse().find((st) => iv.slots[st.slot].start_s < t && st.order.length);
    const next = iv.slots.find((sl) => sl.start_s > t);
    return {
      title: next ? `Break · Rep ${next.label} in ${dur(next.start_s - t)}` : prev ? "Finished" : "Warm-up",
      cols: prev ? [`Rep ${prev.label}`, "Gap"] : [],
      rows: prev ? prev.order.map((o) => ({ key: o.person, hr: hrOf(o.person),
        cells: [repTime(o.result_s), o.rank === 1 && !o.tie ? "fastest" : o.tie ? "dead heat" : behind(o.gap_s)] }))
        : keys.map((k) => ({ key: k, cells: [], hr: hrOf(k) })),
    };
  }
  // continuous: metres behind the front, along the shared route (route_m, as the passes) when there is one, else
  // each person's own distance since the start of the joint window
  const w0 = s.continuous?.window_s?.[0] ?? 0;
  const rows = keys.map((k) => {
    const p = r.people[k];
    if (p.route_m) { const d = valAt(p.t, p.route_m, t); return { k, d }; }
    const a = valAt(p.t, p.dist_m, Math.max(w0, p.t[0])), b = valAt(p.t, p.dist_m, t);
    return { k, d: a === null || b === null ? null : b - a };
  });
  const front = Math.max(...rows.map((x) => x.d ?? -Infinity));
  rows.sort((a, b) => (b.d ?? -1e9) - (a.d ?? -1e9));
  const ap = s.continuous?.apart?.[0];
  const apart = ap ? valAt(ap.t_s, ap.apart_m, t, 15) : null;
  return {
    title: apart === null ? "Distance" : apart <= 20 ? `Side by side · ${Math.round(apart)} m apart` : `${Math.round(apart)} m apart`,
    cols: ["Distance", "Behind"],
    rows: rows.map((x) => ({ key: x.k, hr: hrOf(x.k),
      cells: [x.d === null ? "—" : `${(x.d / 1000).toFixed(2)} km`, x.d === null ? "—" : x.d >= front - 0.5 ? "in front" : `${Math.round(front - x.d)} m`] })),
  };
}

/** the side panel under the live standings: the reps finished so far (intervals) or the stretch so far */
function SoFar({ s, t, w0 }: { s: TSession; t: number; w0: number }) {
  const iv = s.intervals;
  if (iv) {
    // every ranked rep: the finished ones with the fastest / time / the next person's gap, the rest still to come
    const isDone = (slot: number) => {
      const cells = Object.values(iv.reps).map((c) => c[slot]).filter(Boolean);
      return cells.length > 0 && cells.every((c) => t > c!.clock_start_s + (c!.own_time_s ?? c!.time_s ?? 0));
    };
    const ranked = iv.standings.filter((st) => st.order.length);
    if (!ranked.length) return null;
    return (
      <div class="tg-sofar">
        <div class="tg-live-h">Reps so far</div>
        <table class="tg-tbl sofar">
          <thead><tr><th>Rep</th><th>Fastest</th><th class="num">Time</th><th class="num">Next</th></tr></thead>
          <tbody>{ranked.map((st) => {
            const w = st.order[0], n2 = st.order[1];
            if (!isDone(st.slot)) return <tr key={st.slot} class="todo"><td>{repLabel(st.label, true)}</td><td colSpan={3} class="muted">—</td></tr>;
            return (
              <tr key={st.slot}><td>{repLabel(st.label, true)}</td>
                <td><i class="sw" style={{ background: personColour(w.person) }} />{w.tie ? "dead heat" : personInfo(w.person).name}</td>
                <td class="num">{repTime(w.result_s)}</td><td class="num muted">{n2 ? behind(n2.gap_s) : ""}</td></tr>
            );
          })}</tbody>
        </table>
      </div>
    );
  }
  const ap = s.continuous?.apart?.[0];
  if (!ap) return null;
  let n = 0, w = 0;
  for (let i = 0; i < ap.t_s.length && ap.t_s[i] <= t; i++) if (ap.apart_m[i] !== null) { n++; if (ap.with[i]) w++; }
  return (
    <div class="tg-sofar">
      <div class="tg-live-h">So far</div>
      <div class="tg-sofar-kv"><span class="muted">Time</span><b>{dur(Math.max(0, t - w0))}</b></div>
      {n > 0 && <div class="tg-sofar-kv"><span class="muted">Side by side</span><b>{Math.round((100 * w) / n)} %</b></div>}
    </div>
  );
}

const ord = (n: number) => `${n}${n % 10 === 1 && n % 100 !== 11 ? "st" : n % 10 === 2 && n % 100 !== 12 ? "nd" : n % 10 === 3 && n % 100 !== 13 ? "rd" : "th"}`;
const segShort = (g: Segment) => (g.kind === "km" ? `km ${g.label}` : repLabel(g.label, true));
/** "+9.1 s", "+2:04.4" */
const plus = (v: number) => `+${repTime(v)}`;
const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));
type Align = "own" | "clock";

/** a segment on the chosen alignment: axis x = s since each person's own start (own) or the earliest start (clock);
 *  off = a person's start on the axis, fin = the last finish */
interface Axis { s0: number; off: Record<string, number>; fin: number }
function segAxis(g: Segment, align: Align): Axis {
  const off: Record<string, number> = {};
  let fin = 0;
  for (const [k, p] of Object.entries(g.people)) { off[k] = align === "own" ? 0 : p.start_s - g.start_s; fin = Math.max(fin, off[k] + p.time_s); }
  return { s0: g.start_s, off, fin };
}
/** group-clock time of person k at axis x */
const clockAt = (g: Segment, ax: Axis, align: Align, k: string, x: number) => (align === "own" && g.people[k] ? g.people[k].start_s + x : ax.s0 + x);

/** metres into the segment after e s of it (own distance x scale for reps, metres along the shared route for km) */
function segDist(r: RX, g: Segment, k: string, e: number): N {
  const p = r.people[k], c = g.people[k];
  if (!p || !c) return null;
  const v = g.kind === "km" && p.route_m ? p.route_m : p.dist_m;
  const a = valAt(p.t, v, c.start_s), b = valAt(p.t, v, c.start_s + e);
  if (a === null || b === null) return null;
  const d = Math.max(0, (b - a) * (c.scale || 1));
  return g.target_m ? Math.min(d, g.target_m) : d;
}

interface LiveRow { k: string; d: N; e: number; done: boolean; started: boolean; finX: number; total: N; c: SegPerson }
function segRows(r: RX, g: Segment, ax: Axis, x: number, prevTot: Record<string, number>): LiveRow[] {
  const rows = Object.keys(g.people).map((k) => {
    const c = g.people[k];
    const e = clamp(x - ax.off[k], 0, c.time_s);
    const done = x - ax.off[k] >= c.time_s - 0.05;
    let d = segDist(r, g, k, e);
    if (done && !c.partial && g.target_m) d = g.target_m;
    const tot = g.total?.[k] ? (done ? g.total[k].total_s : (prevTot[k] ?? 0) + e) : null;
    return { k, d, e, done, started: x >= ax.off[k], finX: ax.off[k] + c.time_s, total: tot, c };
  });
  rows.sort((a, b) => (a.done && b.done ? (a.c.partial ? 1 : 0) - (b.c.partial ? 1 : 0) || a.finX - b.finX : a.done ? -1 : b.done ? 1 : (b.d ?? -1) - (a.d ?? -1)));
  return rows;
}

interface Feed { key: string; seg: Segment; kind: "pass" | "move"; who: string; whom?: string; at_m?: N; rank?: number; route?: boolean }
function FeedList({ items, empty }: { items: Feed[]; empty: string }) {
  return (
    <div class="tg-feed">
      <div class="tg-live-h">Passes &amp; moves</div>
      {!items.length ? <div class="muted small">{empty}</div> : (
        <ul>{items.map((f, n) => (
          <li key={f.key} class={(f.kind === "move" ? "move" : "") + (n === 0 ? " new" : "")}>
            <span class="where">{f.route ? (f.at_m !== null && f.at_m !== undefined ? `${(f.at_m / 1000).toFixed(2)} km` : segShort(f.seg)) : `${segShort(f.seg)}${f.kind === "pass" && f.at_m !== null && f.at_m !== undefined ? ` · ${Math.round(f.at_m)} m` : ""}`}</span>
            <span class="what">
              <i class="sw" style={{ background: personColour(f.who) }} /><b>{personName(f.who)}</b>
              {f.kind === "pass" ? <> passes <i class="sw" style={{ background: personColour(f.whom!) }} />{personName(f.whom!)}</> : <> moves up to <b>{ord(f.rank!)}</b> overall</>}
            </span>
          </li>
        ))}</ul>
      )}
    </div>
  );
}

const movesOf = (g: Segment): Feed[] => (g.moves ?? []).filter((m) => m.to_rank < m.from_rank)
  .map((m) => ({ key: `m${g.i}:${m.person}`, seg: g, kind: "move" as const, who: m.person, rank: m.to_rank }));

/** the finish card on the map: rep ranking, total so far, rank changes */
function FinishCard({ g, ax, whole }: { g: Segment; ax: Axis; whole?: boolean }) {
  const rows = Object.entries(g.people).map(([k, c]) => ({ k, c, fx: ax.off[k] + c.time_s }))
    .sort((a, b) => (a.c.partial ? 1 : 0) - (b.c.partial ? 1 : 0) || a.fx - b.fx);
  const tot = g.total ? Object.entries(g.total).sort((a, b) => (a[1].rank ?? 0) - (b[1].rank ?? 0) || a[1].total_s - b[1].total_s) : [];
  const ranked = tot.every(([, v]) => v.rank !== null);
  const ups = (g.moves ?? []).filter((m) => m.to_rank < m.from_rank);
  return (
    <div class={"overlay tg-flash" + (whole ? " small" : "")} role="status">
      <div class="h">{g.title} · finish</div>
      <ol>{rows.map((x, n) => (
        <li key={x.k}><span class="rk">{n + 1}</span><i class="sw" style={{ background: personColour(x.k) }} /><span class="nm">{personName(x.k)}</span>
          <span class="v">{x.c.partial ? <span class="muted">partial</span> : repTime(x.c.result_s ?? x.c.time_s)}</span>
          <span class="g muted">{n === 0 || x.c.partial ? "" : plus(x.fx - rows[0].fx)}</span></li>
      ))}</ol>
      {tot.length > 1 && (
        <div class="tot"><span class="muted">{ranked ? `Total after ${segShort(g)}` : `Own time so far (after ${segShort(g)})`}</span>{tot.map(([k, v]) => (
          <span key={k}><i class="sw" style={{ background: personColour(k) }} />{personInfo(k).initials} <b>{repTime(v.total_s)}</b>{ranked && (v.rank ?? 1) > 1 && v.gap_s !== null ? <span class="muted"> {plus(v.gap_s)}</span> : !ranked && v.n ? <span class="muted"> · {v.n} reps</span> : ""}</span>
        ))}</div>
      )}
      {ups.map((m) => <div key={m.person} class="up">▲ {personName(m.person)} moves up to {ord(m.to_rank)} overall</div>)}
    </div>
  );
}

export function ReplayPanel({ s, r: r0, hr, startRep, onClose }: { s: TSession; r: ReplayData; hr: boolean; startRep?: string | null; onClose: () => void }) {
  useTheme();
  const r = r0 as RX;
  const { query } = useLocation();
  const align: Align = query.get("align") === "clock" ? "clock" : "own";
  const keys = Object.keys(r.people);
  const segs = r.segments ?? [];
  const kmMode = r.segment_kind === "km";
  const [tmin, tmax] = useMemo(() => {
    let a = Infinity, b = -Infinity;
    for (const k of keys) { const t = r.people[k].t; if (t.length) { a = Math.min(a, t[0]); b = Math.max(b, t[t.length - 1]); } }
    return [isFinite(a) ? a : 0, isFinite(b) ? b : 0];
  }, [r]);
  const reps = r.reps;
  const isIv = !!s.intervals && reps.length > 0;
  const startAt = isIv ? Math.max(tmin, reps[0].start_s - LEAD) : Math.max(tmin, s.continuous?.window_s?.[0] ?? tmin);
  const segIdx = (label: string | null | undefined) => (label ? segs.findIndex((g) => g.label === label) : -1);
  const init = segIdx(startRep);
  const axes = useMemo(() => segs.map((g) => segAxis(g, align)), [r, align]);
  // total so far before each segment (the last earlier segment with a total, per person)
  const prevTots = useMemo(() => {
    const out: Record<string, number>[] = [];
    let acc: Record<string, number> = {};
    for (const g of segs) { out.push({ ...acc }); if (g.total) for (const [k, v] of Object.entries(g.total)) acc[k] = v.total_s; }
    return out;
  }, [r]);
  const segSpeed = useMemo(() => {
    const f = axes.map((a) => a.fin).sort((a, b) => a - b);
    const med = f.length ? f[f.length >> 1] : 80;
    return SPEEDS.find((v) => med / v <= 30) ?? 16;
  }, [axes]);
  const allSpeeds = isIv ? SPEEDS : SPEEDS_LONG;

  const [sel, setSel] = useState<number | null>(init >= 0 ? init : null);
  const [pos, setPos] = useState(init >= 0 ? -LEAD : startAt);
  const [playing, setPlaying] = useState(init >= 0);
  const [speedSeg, setSpeedSeg] = useState(segSpeed);
  const [speedAll, setSpeedAll] = useState(isIv ? 8 : 32);
  const [repsOnly, setRepsOnly] = useState(isIv);
  const [auto, setAuto] = useState(true);
  const speed = sel === null ? speedAll : speedSeg;
  const st = useRef({ pos, speed, repsOnly, auto, sel, axes, hold: 0 });
  Object.assign(st.current, { speed, repsOnly, auto, sel, axes });

  const seek = (x: number) => { st.current.pos = x; st.current.hold = 0; setPos(x); };
  const goSeg = (n: number | null, play?: boolean) => {
    st.current.sel = n; st.current.hold = 0;
    setSel(n);
    seek(n === null ? startAt : -LEAD);
    if (play !== undefined) setPlaying(play);
  };
  // the page's ▶ on a rep row (or ?rrep=) while the replay is open
  useEffect(() => { const n = segIdx(startRep); if (n >= 0 && n !== st.current.sel) goSeg(n, true); }, [startRep]);
  // switching Own start / Clock keeps the rep, restarts it
  useEffect(() => { if (st.current.sel !== null) seek(Math.min(st.current.pos, axes[st.current.sel].fin)); }, [align]);

  useEffect(() => {
    if (!playing) return;
    let raf = 0, last = performance.now(), shown = 0;
    const stepFn = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const c = st.current;
      if (c.sel !== null) {
        const ax = c.axes[c.sel];
        let x = c.pos + dt * c.speed;
        if (x >= ax.fin) {
          x = Math.max(c.pos, ax.fin);
          if (!c.hold) c.hold = now + HOLD_MS;
          else if (now >= c.hold) {
            c.hold = 0;
            const nx = c.auto ? segs.findIndex((g, j) => j > c.sel! && Object.keys(g.people).length >= 2) : -1;
            if (nx >= 0) { c.sel = nx; setSel(nx); x = -LEAD; }
            else { c.pos = x; setPos(x); setPlaying(false); return; }
          }
        }
        c.pos = x;
      } else {
        let x = c.pos + dt * c.speed;
        if (c.repsOnly && reps.length) {
          const inRep = reps.some((w) => x >= w.start_s - LEAD && x <= w.end_s + 2);
          if (!inRep) {
            const nx = reps.find((w) => w.start_s - LEAD > x);
            if (nx) x = nx.start_s - LEAD; else { x = Math.min(tmax, reps[reps.length - 1].end_s + 2); setPlaying(false); }
          }
        }
        if (x >= tmax) { x = tmax; setPlaying(false); }
        c.pos = x;
      }
      if (now - shown > 33) { shown = now; setPos(c.pos); }
      raf = requestAnimationFrame(stepFn);
    };
    raf = requestAnimationFrame(stepFn);
    return () => cancelAnimationFrame(raf);
  }, [playing]);

  const togglePlay = () => {
    const c = st.current;
    if (!playing) {
      if (c.sel !== null && c.pos >= c.axes[c.sel].fin - 0.05) seek(-LEAD);
      else if (c.sel === null && c.pos >= tmax - 0.5) seek(startAt);
    }
    setPlaying((p) => !p);
  };
  const step = (dir: 1 | -1) => {
    const c = st.current;
    if (c.sel !== null) { const n = c.sel + dir; if (n >= 0 && n < segs.length) goSeg(n); return; }
    const t = c.pos;
    const list = dir > 0 ? segs.filter((g) => g.start_s - LEAD > t + 0.5) : segs.filter((g) => g.start_s - LEAD < t - 1).reverse();
    if (list[0]) seek(list[0].start_s - LEAD);
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (el && (el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable || (el.tagName === "INPUT" && (el as HTMLInputElement).type !== "range" && (el as HTMLInputElement).type !== "checkbox"))) return;
      if (e.key === " ") { e.preventDefault(); togglePlay(); }
      else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        const d = e.key === "ArrowRight" ? 1 : -1;
        if (e.shiftKey) { const c = st.current; const hi = c.sel !== null ? c.axes[c.sel].fin : tmax; seek(clamp(c.pos + d * 5, c.sel !== null ? -LEAD : tmin, hi)); }
        else step(d);
      } else if (/^[0-9]$/.test(e.key) && segs.length) {
        const n = segIdx(e.key === "0" ? "10" : e.key);
        if (n >= 0) { e.preventDefault(); goSeg(n, true); }
      } else if (e.key === "w" || e.key === "W") goSeg(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [playing, r]);

  const g = sel !== null ? segs[sel] : null;
  const ax = sel !== null ? axes[sel] : null;
  const segKeys = g ? keys.filter((k) => g.people[k]) : keys;
  // in a rep each dot stops at its finish line (waits there for the others)
  const clockOfK = (k: string) => (g && ax ? Math.min(clockAt(g, ax, align, k, pos), g.people[k] ? g.people[k].start_s + g.people[k].time_s : Infinity) : pos);
  const mainClock = g && ax ? ax.s0 + (align === "own" ? pos : pos) : pos;

  const tracks = useMemo<MapLine[]>(() => {
    if (g) return segKeys.map((k) => ({ key: k, colour: mapColour(k), faint: true, opacity: 0.55, width: 2,
      coords: pathBetween(r.people[k], g.people[k].start_s, g.people[k].start_s + g.people[k].time_s) }));
    return keys.map((k) => ({ key: k, colour: mapColour(k), faint: true, coords: pathBetween(r.people[k], tmin, tmax) }));
  }, [r, sel]);
  // fit to where it happens: the rep (rep mode), the reps (intervals) or the joint stretch, not each person's way there
  const fitTo = useMemo(() => {
    if (g) return segKeys.flatMap((k) => pathBetween(r.people[k], g.people[k].start_s - 2, g.people[k].start_s + g.people[k].time_s));
    const win = isIv ? reps.map((w) => [w.start_s, w.end_s]) : [s.continuous?.window_s ?? [tmin, tmax]];
    return keys.flatMap((k) => win.flatMap(([a, b]) => pathBetween(r.people[k], a, b)));
  }, [r, sel]);
  const trail = segKeys.map((k) => {
    const ct = clockOfK(k);
    const from = g ? Math.max(g.people[k].start_s, ct - TRAIL_S) : Math.max(tmin, ct - TRAIL_S);
    return { key: k + ":trail", colour: mapColour(k), width: 3.5, coords: ct > from ? pathBetween(r.people[k], from, ct) : [] };
  });
  const dots: MapDot[] = segKeys.map((k) => ({ key: k, colour: mapColour(k), initials: personInfo(k).initials, pos: posAt(r.people[k], clockOfK(k)), title: personName(k), strong: k === s.viewer }));
  const hrOf = (k: string) => { const v = valAt(r.people[k].t, r.people[k].hr, clockOfK(k)); return v === null ? null : Math.round(v); };

  // feed: passes + rank changes so far (newest first)
  const feed: Feed[] = useMemo(() => {
    const out: Feed[] = [];
    if (g && ax && sel !== null) {
      for (let j = 0; j <= sel; j++) {
        const h = segs[j];
        const fin = j < sel || pos >= axes[j].fin - 0.05;
        for (const p of h.passes?.[align] ?? []) if (j < sel || p.x_s <= pos) out.push({ key: `p${j}:${p.x_s}:${p.who}:${p.whom}`, seg: h, kind: "pass", who: p.who, whom: p.whom, at_m: p.at_m });
        if (fin) out.push(...movesOf(h));
      }
    } else {
      const ends = segs.filter((h) => h.end_s <= pos);
      const ps = (r.passes ?? []).filter((p) => p.t_s <= pos);
      const ev: { t: number; f: Feed[] }[] = [
        ...ps.map((p) => ({ t: p.t_s, f: [{ key: `P${p.t_s}:${p.who}:${p.whom}`, seg: segs[p.seg ?? 0] ?? segs[0], kind: "pass" as const, who: p.who, whom: p.whom, at_m: p.at_m, route: kmMode }] })),
        ...ends.map((h) => ({ t: h.end_s, f: movesOf(h) })),
      ].sort((a, b) => a.t - b.t);
      for (const e of ev) out.push(...e.f);
    }
    return out.reverse();
  }, [r, sel, align, Math.floor(pos)]);

  // scrub geometry
  const lo = g ? -LEAD : tmin, hi = g && ax ? ax.fin : tmax;
  const pct = (x: number) => `${((x - lo) / (hi - lo || 1)) * 100}%`;
  const wholeFlash = !g ? segs.find((h) => pos >= h.end_s && pos <= h.end_s + (kmMode ? 25 : 12) && Object.keys(h.people).length >= 2) : null;
  const finished = !!(g && ax && pos >= ax.fin - 0.05);
  const repLabel = kmMode ? "km" : "rep";

  return (
    <div class="tg-replay">
      {segs.length > 0 && (
        <div class="tg-rp-chips" role="group" aria-label={`Replay a ${repLabel}`}>
          <button class={"chip" + (sel === null ? " on" : "")} onClick={() => goSeg(null, false)} title="The whole session on the group clock (W)">Whole session</button>
          {segs.map((h, j) => {
            const n = Object.keys(h.people).length;
            const np = (h.passes?.[align] ?? []).length;
            return (
              <button key={h.i} class={"chip" + (sel === j ? " on" : "") + (n < 2 ? " solo" : "")} onClick={() => goSeg(j, true)}
                title={`${h.title}${n < 2 ? ` (only ${personName(Object.keys(h.people)[0])})` : ""}${np ? ` · ${np} pass${np > 1 ? "es" : ""}` : ""}${/^\d$|^10$/.test(h.label) ? ` (key ${h.label === "10" ? "0" : h.label})` : ""}`}>
                {segShort(h)}{np > 0 && <span class="np" aria-label={`${np} passes`}>⇄</span>}</button>
            );
          })}
          {g && (
            <div class="seg tg-rp-align" role="group" aria-label="Start alignment">
              <button class={align === "own" ? "on" : ""} onClick={() => setQuery({ align: null })} title="Everyone from their own start of the rep: who's faster, start delay left out">Own start</button>
              <button class={align === "clock" ? "on" : ""} onClick={() => setQuery({ align: "clock" })} title="Real clock time: a late start counts">Clock</button>
            </div>
          )}
        </div>
      )}
      <div class="tg-replay-map">
        <TogetherMap lines={[...tracks, ...trail]} dots={dots} fitKey={s.gid + ":replay:" + (sel ?? "all")} fitTo={fitTo}>
          <div class="overlay tr tg-clock"><b>{g ? `${segShort(g)} · ${dur(Math.max(0, pos))}` : clockText(s, pos)}</b>{g && <span class="muted"> {clockText(s, mainClock)}</span>}</div>
          {g && ax && finished && <FinishCard g={g} ax={ax} />}
          {wholeFlash && <FinishCard g={wholeFlash} ax={segAxis(wholeFlash, "own")} whole />}
        </TogetherMap>
      </div>
      <div class="tg-replay-side">
        {g && ax ? (
          <div class="tg-live">
            <div class="tg-live-h">{g.title}{align === "clock" ? " · clock" : ""}</div>
            <table class="tg-tbl live">
              <thead><tr><th></th><th>Person</th><th class="num" title="Metres behind the leader along the track (after the finish: time behind the first finisher)">Behind</th>
                <th class="num" title={`Time in this ${repLabel}`}>{kmMode ? "Km" : "Rep"}</th>
                <th class="num" title={kmMode ? "Time since the first km started" : "Each person's own time so far: their reps up to and including this one"}>Total</th>{hr && <th class="num">HR</th>}</tr></thead>
              <tbody>{(() => {
                const rows = segRows(r, g, ax, pos, prevTots[sel!] ?? {});
                const lead = Math.max(0, ...rows.map((x) => x.d ?? 0));
                const first = rows.find((x) => x.done && !x.c.partial);
                return rows.map((x, i) => (
                  <tr key={x.k} class={x.done ? "done" : ""}>
                    <td class="pos">{i + 1}</td>
                    <td><i class="sw" style={{ background: personColour(x.k) }} /><span class={x.k === s.viewer ? "me" : ""}>{personName(x.k)}</span></td>
                    <td class="num">{x.done ? (x.c.partial ? "partial" : x === first ? "1st" : first ? plus(x.finX - first.finX) : "finished")
                      : !x.started ? "start…" : x.d === null ? "—" : i === 0 || x.d >= lead - 0.5 ? "leads" : `${Math.round(lead - x.d)} m`}</td>
                    <td class="num v">{x.done ? repTime(x.c.partial ? x.c.time_s : x.c.result_s ?? x.c.time_s) : dur(x.e)}</td>
                    <td class="num">{x.total === null ? <span class="muted">—</span> : x.done ? repTime(x.total) : dur(x.total)}</td>
                    {hr && <td class="num muted">{hrOf(x.k) ? `♥ ${hrOf(x.k)}` : "—"}</td>}
                  </tr>
                ));
              })()}</tbody>
            </table>
            {g.target_m && <div class="muted small tg-rp-dist">{Object.keys(g.people).length < 2 ? "only one person ran this rep" : `${Math.round(g.target_m)} m${kmMode && g.from_m !== undefined ? ` from ${(g.from_m / 1000).toFixed(1)} km on the shared route` : ""}`}</div>}
          </div>
        ) : (() => {
          const stand = standingsAt(s, r, pos);
          return (
            <div class="tg-live">
              <div class="tg-live-h">{stand.title}</div>
              {stand.cols.length > 0 && (
                <table class="tg-tbl live">
                  <thead><tr><th></th><th>Person</th>{stand.cols.map((c) => <th key={c} class="num">{c}</th>)}{hr && <th class="num">HR</th>}</tr></thead>
                  <tbody>{stand.rows.map((x, i) => (
                    <tr key={x.key} class={x.done ? "done" : ""}>
                      <td class="pos">{i + 1}</td>
                      <td><i class="sw" style={{ background: personColour(x.key) }} /><span class={x.key === s.viewer ? "me" : ""}>{personName(x.key)}</span></td>
                      {x.cells.map((c, j) => <td key={j} class={"num" + (j === 0 ? " v" : "")}>{c}</td>)}
                      {hr && <td class="num muted">{x.hr ? `♥ ${x.hr}` : "—"}</td>}
                    </tr>
                  ))}</tbody>
                </table>
              )}
            </div>
          );
        })()}
        {segs.length > 0 && <FeedList items={feed} empty={g ? `No passes${finished ? "" : " yet"} in ${segShort(g)}.` : "No passes yet."} />}
        {!g && <SoFar s={s} t={pos} w0={startAt} />}
        <button class="link tg-close" onClick={onClose}>✕ Close replay</button>
      </div>
      <div class="tg-controls">
        <button class="play" onClick={togglePlay} aria-label={playing ? "Pause" : "Play"} title="Play / pause (space)">{playing ? "❚❚" : "▶"}</button>
        {segs.length > 0 && <button onClick={() => step(-1)} title={`Previous ${repLabel} (←)`} aria-label={`Previous ${repLabel}`}>⏮</button>}
        {segs.length > 0 && <button onClick={() => step(1)} title={`Next ${repLabel} (→)`} aria-label={`Next ${repLabel}`}>⏭</button>}
        <div class="seg" role="group" aria-label="Speed">
          {(g ? SPEEDS : allSpeeds).map((x) => <button key={x} class={speed === x ? "on" : ""} onClick={() => (g ? setSpeedSeg(x) : setSpeedAll(x))}>{x}×</button>)}
        </div>
        {g ? <label class="tg-check" title={`After the finish, go on to the next ${repLabel}`}><input type="checkbox" checked={auto} onChange={(e) => setAuto((e.target as HTMLInputElement).checked)} /> Auto-advance</label>
          : isIv && <label class="tg-check"><input type="checkbox" checked={repsOnly} onChange={(e) => setRepsOnly((e.target as HTMLInputElement).checked)} /> Reps only</label>}
        <div class="tg-scrub">
          <div class="bands" aria-hidden="true">
            {g && ax ? Object.entries(g.people).map(([k, c]) => <i key={k} class="fin" style={{ left: pct(ax.off[k] + c.time_s), background: personColour(k) }} title={`${personName(k)} finishes`} />)
              : kmMode ? segs.map((h) => <i key={h.i} class="km" style={{ left: pct(h.start_s) }} title={h.title} />)
              : reps.map((w) => <i key={w.slot} style={{ left: pct(w.start_s), width: `calc(${pct(w.end_s)} - ${pct(w.start_s)})` }} title={`Rep ${w.label}`} />)}
          </div>
          <div class="marks" aria-hidden="true">
            {(g ? (g.passes?.[align] ?? []).map((p) => ({ x: p.x_s, p })) : (r.passes ?? []).map((p) => ({ x: p.t_s, p })))
              .map(({ x, p }) => <i key={`${x}:${p.who}`} style={{ left: pct(x), borderTopColor: personColour(p.who) }} title={`${personName(p.who)} passes ${personName(p.whom)}`} />)}
          </div>
          <input type="range" min={lo} max={hi} step={g ? 0.5 : 1} value={pos} aria-label="Replay position"
            onInput={(e) => seek(Number((e.target as HTMLInputElement).value))} />
        </div>
        <span class="muted tg-tt">{g && ax ? `${dur(Math.max(0, pos))} / ${dur(ax.fin)}` : `${dur(pos - tmin)} / ${dur(tmax - tmin)}`}</span>
      </div>
      <div class="tg-rp-keys muted small">space play / pause · ← → {repLabel} · shift ← → 5 s{segs.length ? ` · 1–9, 0 ${repLabel} 1–10 · W whole session` : ""}</div>
    </div>
  );
}
