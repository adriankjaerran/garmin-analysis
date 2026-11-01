// Race replay for a Together session: everyone's dot running on the map on the group clock, play / pause, speed,
// a scrub bar with the reps shaded, "reps only" (skips the breaks) and live standings next to the map.
// Positions come from /api/together/<gid>/replay (loaded by the page when the replay or a map is opened).
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { dur } from "../lib/format";
import { mapColour, personColour, personInfo, personName } from "../lib/people";
import { useTheme } from "../lib/theme";
import { behind, pathBetween, posAt, repTime, valAt, type Replay as ReplayData, type TSession } from "../lib/together";
import { TogetherMap, type MapDot, type MapLine } from "./TogetherMap";

const SPEEDS = [1, 5, 20, 60];
const LEAD = 4;   // s before a rep start when "reps only" jumps there

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
function standingsAt(s: TSession, r: ReplayData, t: number): Board {
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
        cells: [repTime(o.result_s), o.rank === 1 && !o.tie ? "won" : o.tie ? "dead heat" : behind(o.gap_s)] }))
        : keys.map((k) => ({ key: k, cells: [], hr: hrOf(k) })),
    };
  }
  // continuous: metres behind the front (distance since the start of the joint window)
  const w0 = s.continuous?.window_s?.[0] ?? 0;
  const rows = keys.map((k) => {
    const p = r.people[k];
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
    // every ranked rep: the finished ones with winner / time / the next person's gap, the rest still to come
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
          <thead><tr><th>Rep</th><th>Winner</th><th class="num">Time</th><th class="num">Next</th></tr></thead>
          <tbody>{ranked.map((st) => {
            const w = st.order[0], n2 = st.order[1];
            if (!isDone(st.slot)) return <tr key={st.slot} class="todo"><td>R{st.label}</td><td colSpan={3} class="muted">—</td></tr>;
            return (
              <tr key={st.slot}><td>R{st.label}</td>
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

export function ReplayPanel({ s, r, hr, onClose }: { s: TSession; r: ReplayData; hr: boolean; onClose: () => void }) {
  useTheme();
  const keys = Object.keys(r.people);
  const [tmin, tmax] = useMemo(() => {
    let a = Infinity, b = -Infinity;
    for (const k of keys) { const t = r.people[k].t; if (t.length) { a = Math.min(a, t[0]); b = Math.max(b, t[t.length - 1]); } }
    return [isFinite(a) ? a : 0, isFinite(b) ? b : 0];
  }, [r]);
  const reps = r.reps;
  const isIv = !!s.intervals && reps.length > 0;
  const startAt = isIv ? Math.max(tmin, reps[0].start_s - LEAD) : Math.max(tmin, s.continuous?.window_s?.[0] ?? tmin);
  const [t, setT] = useState(startAt);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(isIv ? 5 : 20);
  const [repsOnly, setRepsOnly] = useState(isIv);
  const st = useRef({ t, speed, repsOnly });
  st.current.speed = speed; st.current.repsOnly = repsOnly;

  useEffect(() => {
    if (!playing) return;
    let raf = 0, last = performance.now(), shown = 0;
    const step = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      let x = st.current.t + dt * st.current.speed;
      if (st.current.repsOnly && reps.length) {
        const inRep = reps.some((w) => x >= w.start_s - LEAD && x <= w.end_s + 2);
        if (!inRep) {
          const nx = reps.find((w) => w.start_s - LEAD > x);
          if (nx) x = nx.start_s - LEAD; else { x = Math.min(tmax, reps[reps.length - 1].end_s + 2); setPlaying(false); }
        }
      }
      if (x >= tmax) { x = tmax; setPlaying(false); }
      st.current.t = x;
      if (now - shown > 33) { shown = now; setT(x); }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [playing]);

  const seek = (x: number) => { st.current.t = x; setT(x); };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT" && (e.target as HTMLInputElement).type !== "range") return;
      if (e.key === " ") { e.preventDefault(); setPlaying((p) => !p); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const tracks = useMemo<MapLine[]>(() => keys.map((k) => ({ key: k, colour: mapColour(k), faint: true,
    coords: pathBetween(r.people[k], tmin, tmax) })), [r]);
  // fit to where it happens: the reps (intervals) or the joint stretch, not each person's way there
  const fitTo = useMemo(() => {
    const win = isIv ? reps.map((w) => [w.start_s, w.end_s]) : [s.continuous?.window_s ?? [tmin, tmax]];
    return keys.flatMap((k) => win.flatMap(([a, b]) => pathBetween(r.people[k], a, b)));
  }, [r]);
  const trail = keys.map((k) => ({ key: k + ":trail", colour: mapColour(k), width: 3.5, coords: pathBetween(r.people[k], Math.max(tmin, t - 20), t) }));
  const dots: MapDot[] = keys.map((k) => ({ key: k, colour: mapColour(k), initials: personInfo(k).initials, pos: posAt(r.people[k], t), title: personName(k), strong: k === s.viewer }));
  const stand = standingsAt(s, r, t);
  const pct = (x: number) => `${((x - tmin) / (tmax - tmin || 1)) * 100}%`;
  const nextRep = (dir: 1 | -1) => {
    const list = dir > 0 ? reps.filter((w) => w.start_s - LEAD > t + 0.5) : reps.filter((w) => w.start_s - LEAD < t - 1).reverse();
    if (list[0]) seek(list[0].start_s - LEAD);
  };

  return (
    <div class="tg-replay">
      <div class="tg-replay-map">
        <TogetherMap lines={[...tracks, ...trail]} dots={dots} fitKey={s.gid + ":replay"} fitTo={fitTo}>
          <div class="overlay tr tg-clock"><b>{clockText(s, t)}</b></div>
        </TogetherMap>
      </div>
      <div class="tg-replay-side">
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
        <SoFar s={s} t={t} w0={startAt} />
        <button class="link tg-close" onClick={onClose}>✕ Close replay</button>
      </div>
      <div class="tg-controls">
        <button class="play" onClick={() => { if (t >= tmax - 0.5) seek(startAt); setPlaying(!playing); }} aria-label={playing ? "Pause" : "Play"} title="Play / pause (space)">
          {playing ? "❚❚" : "▶"}</button>
        {isIv && <button onClick={() => nextRep(-1)} title="Previous rep" aria-label="Previous rep">⏮</button>}
        {isIv && <button onClick={() => nextRep(1)} title="Next rep" aria-label="Next rep">⏭</button>}
        <div class="seg" role="group" aria-label="Speed">
          {SPEEDS.map((x) => <button key={x} class={speed === x ? "on" : ""} onClick={() => setSpeed(x)}>{x}×</button>)}
        </div>
        {isIv && <label class="tg-check"><input type="checkbox" checked={repsOnly} onChange={(e) => setRepsOnly((e.target as HTMLInputElement).checked)} /> Reps only</label>}
        <div class="tg-scrub">
          <div class="bands" aria-hidden="true">
            {reps.map((w) => <i key={w.slot} style={{ left: pct(w.start_s), width: `calc(${pct(w.end_s)} - ${pct(w.start_s)})` }} title={`Rep ${w.label}`} />)}
          </div>
          <input type="range" min={tmin} max={tmax} step={1} value={t} aria-label="Replay position"
            onInput={(e) => seek(Number((e.target as HTMLInputElement).value))} />
        </div>
        <span class="muted tg-tt">{dur(t - tmin)} / {dur(tmax - tmin)}</span>
      </div>
    </div>
  );
}
