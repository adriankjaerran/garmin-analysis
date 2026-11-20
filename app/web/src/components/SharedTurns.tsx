// Shared-machine erg sessions (Concept2, several people taking turns on one machine; docs/concept2.md):
// - `ownSeries`: this person's series inside their own turns only, each turn's handover cut (adaptive, 8-10 s, from
//   /api/activities/{id}/shared-turns) and the last second; with `noBreaks` the turns are joined end to end (x = work time).
// - `SharedTurns`: "Who rowed when" (a lane per machine, a cell per minute in the rower's colour), "Rounds" (metres per
//   round and person, coloured against the person's own average; A + B with two machines), "Pace per turn" (steady
//   pace after the handover on the session clock, a line per person; B turns as squares) and "Pace curve" (every
//   person's stroke pace, turns joined), for "Me" or "Everyone", in each person's colour.
//   Two machines (2:00 on = 1:00 on each): machine A is this recording, B another Logbook merged by the rotation
//   (docs/concept2.md); without B's recording its lane says so.
import { useEffect, useRef, useState } from "preact/hooks";
import type { Rep, Series } from "../lib/api";
import { pace500 } from "../lib/format";
import { personColour, personInfo, personName } from "../lib/people";
import { useTheme } from "../lib/theme";

export interface Turn { n: number; start_s: number; time_s: number; dist_m: number; spm: number | null; pace_s: number | null;
  cut_s: number; steady_pace_s: number | null; t: number[]; p: number[]; machine?: "A" | "B" }
export interface Round { start_s: number; time_s: number; dist_m: number; spm: number | null;
  parts: { start_s: number; time_s: number; dist_m: number; machine: "A" | "B" }[] }
export interface SharedPerson { person: string; turns: Turn[]; turns_b?: Turn[]; rounds?: Round[]; on_s: number; off_s: number;
  steady_pace_s: number | null; pattern?: string; slot_s?: number;
  second_machine?: { est_m: number; logbook_dist_m: number } | null; machine_b?: { id: number; from?: string; shift: number; scale: number };
  /** two recorded machines: own strokes on both, [t (session s), speed m/s, spm, watts, machine] */
  strokes?: [number, number, number | null, number, "A" | "B"][] }
export interface Machine { id: string; label: "A" | "B"; dist_m: number | null; time_s: number | null; drag_factor: number | null; from?: string }
export interface SharedTurnsResp { activity_id: string; people: SharedPerson[]; machines?: Machine[] }

/** every turn of a person on both machines, in time order */
export const allTurns = (p: SharedPerson) => [...p.turns, ...(p.turns_b ?? [])].sort((a, b) => a.start_s - b.start_s);
const twoMachines = (people: SharedPerson[]) => people.some((p) => p.turns_b?.length);

const MIN_CUT_S = 8;     // fallback when the turn profiles aren't loaded (the measured flywheel carry-over)
const END_CUT_S = 1;     // the last second before the swap sags
const p500 = (s: number) => pace500(500 / s);

/** the series inside this person's turns only (others' turns = gaps), each turn's handover cut (per turn from
 *  `turns`, else 8 s) and its last second; `noBreaks`: turns joined end to end, t = work seconds, a gap between turns */
export function ownSeries(s0: Series, reps: Rep[], turns: Turn[] | null, noBreaks: boolean,
  strokes?: SharedPerson["strokes"]): { series: Series; bands: { a: number; b: number }[] } {
  // two recorded machines: the series from the person's strokes on both (this recording's FIT has machine A only)
  const s: Series = strokes?.length
    ? { ...s0, t: strokes.map((x) => x[0]), speed: strokes.map((x) => x[1]), cad: strokes.map((x) => x[2]),
        power: strokes.map((x) => x[3]), hr: undefined, lat: undefined, lon: undefined, alt: undefined, dist: undefined }
    : s0;
  // the turns on this recording (machine A) when known: with two machines a rep (round) spans A and B, and only
  // A's minute is in this series
  const iv = turns?.length
    ? turns.map((t) => [t.start_s + t.cut_s, t.start_s + t.time_s - END_CUT_S] as const)
    : reps.filter((r) => r.start !== undefined && r.end !== undefined).map((r) => [r.start! + MIN_CUT_S, r.end! - END_CUT_S] as const);
  const n = s.t.length;
  const keys = (Object.keys(s) as (keyof Series)[]).filter((k) => Array.isArray(s[k]) && (s[k] as unknown[]).length === n && k !== "cols");
  if (!noBreaks) {
    const keep = s.t.map((t) => iv.some(([a, b]) => t >= a && t <= b));
    const out: Series = { ...s };
    for (const k of ["hr", "speed", "cad", "power"] as const) { const v = s[k]; if (v) out[k] = v.map((x, i) => (keep[i] ? x : null)); }
    return { series: out, bands: iv.map(([a, b]) => ({ a, b })) };
  }
  // joined: copy the samples of each turn, shift t so the turns follow each other; one null sample between turns
  const out: Record<string, unknown[]> = Object.fromEntries(keys.map((k) => [k, []]));
  const bands: { a: number; b: number }[] = [];
  let off = 0;
  iv.forEach(([a, b], j) => {
    const idx: number[] = [];
    for (let i = 0; i < n; i++) if (s.t[i] >= a && s.t[i] <= b) idx.push(i);
    if (!idx.length) return;
    if (j > 0) for (const k of keys) out[k].push(k === "t" ? off + 0.5 : null);   // break the line between turns
    const t0 = s.t[idx[0]];
    for (const i of idx) for (const k of keys) out[k].push(k === "t" ? off + 1 + (s.t[i] - t0) : (s[k] as unknown[])[i]);
    bands.push({ a: off + 1, b: off + 1 + (s.t[idx[idx.length - 1]] - t0) });
    off += 1 + (s.t[idx[idx.length - 1]] - t0);
  });
  return { series: { ...s, ...(out as unknown as Partial<Series>) } as Series, bands };
}

function useWidth(min = 320) {
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(800);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(min, el.clientWidth)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [box, W] as const;
}

/** nice pace ticks (s /500 m) between lo and hi */
function ticks(lo: number, hi: number) {
  const step = [1, 2, 5, 10, 15, 20, 30].find((s) => (hi - lo) / s <= 6) ?? 30;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(v);
  return out;
}

const mm = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

/** x = the session clock (minutes), y = steady pace (faster up), a line per person; B turns as squares */
function PerTurn({ people }: { people: SharedPerson[] }) {
  const [box, W] = useWidth();
  const hasRounds = people.some((p) => p.rounds?.length);
  const [mode, setMode] = useState<"turns" | "rounds">("turns");
  if (hasRounds && mode === "rounds") return <PerRound people={people} setMode={setMode} />;
  const pts0 = people.flatMap((p) => allTurns(p));
  const vals = pts0.map((t) => t.steady_pace_s).filter((x): x is number => !!x);
  if (!vals.length) return null;
  const end = Math.max(...pts0.map((t) => t.start_s + t.time_s));
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = Math.max((hi - lo) * 0.08, 1.5);
  lo -= pad; hi += pad;
  const H = 230, L = 50, R = 12, T = 8, B = 22;
  const x = (sec: number) => L + 8 + (sec / end) * (W - L - R - 16);
  const y = (v: number) => T + ((v - lo) / (hi - lo)) * (H - T - B);
  const two = twoMachines(people);
  const step = end > 2400 ? 600 : 300;
  return (
    <div class="ladder" ref={box}>
      <div class="title chart-head"><span>Pace per turn (/500 m, steady: handover cut) · faster is up{two ? " · ○ machine A · ● machine B" : ""}</span>
        {hasRounds && <ModeSeg mode={mode} setMode={setMode} />}</div>
      <svg width={W} height={H}>
        {ticks(lo, hi).map((v) => <g key={v}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} class="grid" /><text x={L - 6} y={y(v) + 4} class="ax" text-anchor="end">{p500(v)}</text></g>)}
        {Array.from({ length: Math.floor(end / step) + 1 }, (_, k) => <text key={k} x={x(k * step)} y={H - 6} class="ax" text-anchor="middle">{mm(k * step)}</text>)}
        {people.map((p) => {
          const c = personColour(p.person);
          const pts = allTurns(p).filter((t) => t.steady_pace_s).map((t) => [x(t.start_s + t.time_s / 2), y(t.steady_pace_s!), t] as const);
          const d = pts.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join("");
          return (
            <g key={p.person}>
              <path d={d} fill="none" stroke={c} stroke-width={2} stroke-opacity={0.85} />
              {pts.map(([px, py, t]) => {
                const tip = `${personName(p.person)} · ${mm(t.start_s)}${two ? ` on ${t.machine}` : ""}: ${p500(t.steady_pace_s!)} /500 m steady (first ${Math.round(t.cut_s)} s cut) · ${p500(t.pace_s!)} over the whole turn · ${Math.round(t.dist_m)} m${t.spm ? ` · ${t.spm} spm` : ""}`;
                // machine A: a hollow ring, machine B: a filled dot (one machine only: filled)
                return two && t.machine !== "B"
                  ? <circle key={`A${t.start_s}`} cx={px} cy={py} r={4.6} style={{ fill: "var(--surface)" }} stroke={c} stroke-width={2.2}><title>{tip}</title></circle>
                  : <circle key={`B${t.start_s}`} cx={px} cy={py} r={4.2} fill={c} style={{ stroke: "var(--surface)" }} stroke-width={1.2}><title>{tip}</title></circle>;
              })}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function ModeSeg({ mode, setMode }: { mode: "turns" | "rounds"; setMode: (m: "turns" | "rounds") => void }) {
  return <div class="seg small">
    <button class={mode === "turns" ? "on" : ""} onClick={() => setMode("turns")} title="every 1:00 turn on the session clock">Turns</button>
    <button class={mode === "rounds" ? "on" : ""} onClick={() => setMode("rounds")} title="one point per round (A + B), like the Rounds table">Rounds</button>
  </div>;
}

/** x = round R1..Rn (as in the Rounds table), y = the round's steady pace (time-weighted over its turns on A and B) */
function PerRound({ people, setMode }: { people: SharedPerson[]; setMode: (m: "turns" | "rounds") => void }) {
  const [box, W] = useWidth();
  const per = people.map((p) => {
    const ts = allTurns(p);
    return { p, pts: (p.rounds ?? []).map((r, k) => {
      const parts = r.parts.map((q) => ts.find((t) => Math.abs(t.start_s - q.start_s) < 2)).filter((t): t is Turn => !!t?.steady_pace_s);
      const w = parts.reduce((a, t) => a + t.time_s, 0);
      const v = w ? parts.reduce((a, t) => a + t.steady_pace_s! * t.time_s, 0) / w : null;
      return { k, v, r, parts };
    }) };
  });
  const n = Math.max(1, ...per.map((x) => x.pts.length));
  const vals = per.flatMap((x) => x.pts.map((q) => q.v)).filter((v): v is number => !!v);
  if (!vals.length) return null;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = Math.max((hi - lo) * 0.08, 1.5);
  lo -= pad; hi += pad;
  const H = 230, L = 50, R = 12, T = 8, B = 22;
  const x = (k: number) => L + 20 + (n === 1 ? 0 : (k * (W - L - R - 40)) / (n - 1));
  const y = (v: number) => T + ((v - lo) / (hi - lo)) * (H - T - B);
  return (
    <div class="ladder" ref={box}>
      <div class="title chart-head"><span>Pace per round (/500 m, steady, A + B) · faster is up · a hollow ring = a 1:00 round on one machine</span>
        <ModeSeg mode="rounds" setMode={setMode} /></div>
      <svg width={W} height={H}>
        {ticks(lo, hi).map((v) => <g key={v}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} class="grid" /><text x={L - 6} y={y(v) + 4} class="ax" text-anchor="end">{p500(v)}</text></g>)}
        {Array.from({ length: n }, (_, k) => <text key={k} x={x(k)} y={H - 6} class="ax" text-anchor="middle">R{k + 1}</text>)}
        {per.map(({ p, pts }) => {
          const c = personColour(p.person);
          const ok = pts.filter((q) => q.v);
          const d = ok.map((q, i) => `${i ? "L" : "M"}${x(q.k).toFixed(1)},${y(q.v!).toFixed(1)}`).join("");
          return <g key={p.person}>
            <path d={d} fill="none" stroke={c} stroke-width={2.2} />
            {ok.map((q) => {
              const tip = `${personName(p.person)} · R${q.k + 1} (${mm(q.r.start_s)}): ${p500(q.v!)} /500 m steady · ${Math.round(q.r.dist_m)} m in ${mm(q.r.time_s)}` +
                (q.r.parts.length > 1 ? " · " + q.r.parts.map((m) => `${m.machine} ${Math.round(m.dist_m)} m`).join(" + ") : ` · machine ${q.r.parts[0].machine} only`);
              return q.r.parts.length > 1
                ? <circle key={q.k} cx={x(q.k)} cy={y(q.v!)} r={4.6} fill={c} style={{ stroke: "var(--surface)" }} stroke-width={1.2}><title>{tip}</title></circle>
                : <circle key={q.k} cx={x(q.k)} cy={y(q.v!)} r={4.6} style={{ fill: "var(--surface)" }} stroke={c} stroke-width={2.2}><title>{tip}</title></circle>;
            })}
          </g>;
        })}
      </svg>
    </div>
  );
}

/** a lane per machine, a cell per slot in the colour of whoever rowed it (initials + metres), the resting lane below */
function Timeline({ people, machines }: { people: SharedPerson[]; machines: Machine[] }) {
  const [box, W] = useWidth();
  const slot = people[0]?.slot_s ?? 60;
  const lanes: { label: string; cells: Map<number, { who: string; m: number; pace: number | null }> }[] = [
    { label: "A", cells: new Map() }, { label: "B", cells: new Map() }];
  for (const p of people) {
    for (const t of p.turns) lanes[0].cells.set(Math.round(t.start_s / slot), { who: p.person, m: t.dist_m, pace: t.pace_s });
    for (const t of p.turns_b ?? []) lanes[1].cells.set(Math.round(t.start_s / slot), { who: p.person, m: t.dist_m, pace: t.pace_s });
  }
  const n = Math.max(people[0]?.pattern?.length ?? 0, ...lanes.flatMap((l) => [...l.cells.keys()].map((k) => k + 1)));
  if (!n) return null;
  const two = lanes[1].cells.size > 0;
  const expectB = two || people.some((p) => p.on_s >= 2 * slot || p.second_machine);
  const rows = expectB ? [lanes[0], lanes[1]] : [lanes[0]];
  const L = 74, R = 8, rowH = 34, gap = 4, T = 4;
  const cw = (W - L - R) / n;
  const ev = cw * 5 >= 60 ? 5 : 10;   // a time label every 5 (narrow: 10) slots
  const off = (k: number) => people.filter((p) => !rows.some((l) => l.cells.get(k)?.who === p.person)).map((p) => p.person);
  const H = T + (rows.length + (two ? 1 : 0)) * (rowH + gap) + 20;
  const recBy = machines.find((x) => x.label === "B")?.from;
  return (
    <div class="ladder" ref={box}>
      <div class="title">Who rowed when · a cell = {mm(slot)}{expectB ? (two ? ` · both machines recorded${recBy ? ` (B by ${personName(recBy)})` : ""}` : " · machine B not recorded yet") : ""}</div>
      <svg width={W} height={H}>
        {rows.map((l, r) => {
          const y0 = T + r * (rowH + gap);
          return <g key={l.label}>
            <text x={L - 8} y={y0 + rowH / 2 + 4} class="ax" text-anchor="end">Machine {l.label}</text>
            {l.label === "B" && !two && <rect x={L} y={y0} width={W - L - R} height={rowH} rx={4} fill="none" style={{ stroke: "var(--text-muted)" }} stroke-dasharray="4 4" />}
            {[...l.cells.entries()].map(([k, c]) => {
              const col = personColour(c.who);
              return <g key={k}>
                <rect x={L + k * cw + 1} y={y0} width={Math.max(1, cw - 2)} height={rowH} rx={3} fill={col} fill-opacity={0.85}>
                  <title>{`${mm(k * slot)}–${mm((k + 1) * slot)} · machine ${l.label} · ${personName(c.who)} · ${Math.round(c.m)} m${c.pace ? ` · ${p500(c.pace)} /500 m` : ""}`}</title>
                </rect>
                {cw > 26 && <text x={L + k * cw + cw / 2} y={y0 + 14} text-anchor="middle" class="cellt">{personInfo(c.who).initials}</text>}
                {cw > 26 && <text x={L + k * cw + cw / 2} y={y0 + 27} text-anchor="middle" class="cellm">{Math.round(c.m)}</text>}
              </g>;
            })}
          </g>;
        })}
        {(() => {
          const y0 = T + rows.length * (rowH + gap);   // resting: only with both machines recorded (else "off" may be on B)
          return <g>
            {two && <text x={L - 8} y={y0 + 14} class="ax" text-anchor="end">Resting</text>}
            {two && Array.from({ length: n }, (_, k) => off(k).map((w, i, a) => (
              <rect key={`${k}${w}`} x={L + k * cw + 1} y={y0 + 4 + i * (16 / a.length)} width={Math.max(1, cw - 2)} height={16 / a.length - 1}
                fill={personColour(w)} fill-opacity={0.3}><title>{`${mm(k * slot)} · ${personName(w)} resting`}</title></rect>)))}
            {Array.from({ length: Math.floor(n / ev) + 1 }, (_, k) => k * ev).map((k) => k / ev).map((k) => <text key={k} x={L + k * ev * cw} y={H - 4} class="ax" text-anchor={k * ev >= n ? "end" : k ? "middle" : "start"}>{mm(k * ev * slot)}</text>)}
          </g>;
        })()}
      </svg>
    </div>
  );
}

/** metres per round and person (A + B with two machines), coloured against the person's own average pace */
function Rounds({ people }: { people: SharedPerson[] }) {
  const two = twoMachines(people);
  const rowsOf = (p: SharedPerson) => (p.rounds?.length ? p.rounds : p.turns.map((t) => ({ start_s: t.start_s, time_s: t.time_s, dist_m: t.dist_m, spm: t.spm,
    parts: [{ start_s: t.start_s, time_s: t.time_s, dist_m: t.dist_m, machine: "A" as const }] })));
  const n = Math.max(...people.map((p) => rowsOf(p).length));
  const tot = (p: SharedPerson) => {
    const rs = rowsOf(p);
    const m = rs.reduce((a, r) => a + r.dist_m, 0), s = rs.reduce((a, r) => a + r.time_s, 0);
    return { m, s, pace: m ? (s / m) * 500 : null };
  };
  const byMachine = (p: SharedPerson, mach: "A" | "B") => {
    const ps = rowsOf(p).flatMap((r) => r.parts).filter((q) => q.machine === mach);
    const m = ps.reduce((a, q) => a + q.dist_m, 0), s = ps.reduce((a, q) => a + q.time_s, 0);
    return m ? (s / m) * 500 : null;
  };
  return (
    <div class="ladder rounds">
      <div class="title">{two ? "Rounds (A + B, metres; colour: faster / slower than the person's own average)" : "Turns (metres; colour: faster / slower than the person's own average)"}</div>
      <div class="scroll">
        <table class="rounds-t">
          <thead><tr><th></th>{Array.from({ length: n }, (_, k) => <th key={k}>{two ? "R" : "T"}{k + 1}</th>)}<th>Total</th><th>/500 m</th>{two && <th>A</th>}{two && <th>B</th>}</tr></thead>
          <tbody>
            {people.map((p) => {
              const rs = rowsOf(p), t = tot(p);
              return <tr key={p.person}>
                <th style={{ color: personColour(p.person) }}>{personName(p.person)}</th>
                {Array.from({ length: n }, (_, k) => {
                  const r = rs[k];
                  if (!r) return <td key={k} />;
                  const pace = (r.time_s / r.dist_m) * 500, dv = t.pace ? pace / t.pace - 1 : 0;
                  const bg = dv < 0 ? `rgba(46,154,90,${Math.min(0.55, -dv * 9)})` : `rgba(196,60,60,${Math.min(0.55, dv * 9)})`;
                  const tip = `${mm(r.start_s)} · ${Math.round(r.dist_m)} m in ${mm(r.time_s)} · ${p500(500 / (r.dist_m / r.time_s))} /500 m` +
                    (two ? " · " + r.parts.map((q) => `${q.machine} ${Math.round(q.dist_m)} m`).join(" + ") : "");
                  return <td key={k} style={{ background: bg }} title={tip}>
                    <b>{Math.round(r.dist_m)}</b>
                    {two && <div class="parts">{r.parts.map((q) => <span key={q.machine}>{q.machine}{Math.round(q.dist_m)}</span>)}</div>}
                  </td>;
                })}
                <td><b>{Math.round(t.m).toLocaleString("en")}</b> m<div class="parts">{mm(t.s)}</div></td>
                <td>{t.pace ? p500(500 / (t.m / t.s)) : "—"}</td>
                {two && <td>{byMachine(p, "A") ? p500(byMachine(p, "A")!) : "—"}</td>}
                {two && <td>{byMachine(p, "B") ? p500(byMachine(p, "B")!) : "—"}</td>}
              </tr>;
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** every person's stroke pace inside their turns, handover cut, turns joined: x = turn number (+ fraction of it) */
function Curve({ people }: { people: SharedPerson[] }) {
  const [box, W] = useWidth();
  const [hover, setHover] = useState<string | null>(null);
  const n = Math.max(...people.map((p) => allTurns(p).length));
  const all = people.flatMap((p) => allTurns(p).flatMap((t) => t.p)).sort((a, b) => a - b);
  if (!all.length) return null;
  // robust range: 1st-99th percentile (a sprint finish or a stumble shouldn't squash the rest)
  let lo = all[Math.floor(all.length * 0.01)], hi = all[Math.floor(all.length * 0.99)];
  const pad = Math.max((hi - lo) * 0.06, 1.5);
  lo -= pad; hi += pad;
  const H = 240, L = 50, R = 12, T = 8, B = 22;
  const x = (u: number) => L + (u / n) * (W - L - R);   // u in turns (0..n)
  const y = (v: number) => T + ((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo)) * (H - T - B);
  return (
    <div class="ladder" ref={box}>
      <div class="title">Pace curve (/500 m per stroke, handover cut, breaks removed) · faster is up</div>
      <svg width={W} height={H}>
        {ticks(lo, hi).map((v) => <g key={v}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} class="grid" /><text x={L - 6} y={y(v) + 4} class="ax" text-anchor="end">{p500(v)}</text></g>)}
        {Array.from({ length: n }, (_, k) => <g key={k}>
          {k > 0 && <line x1={x(k)} x2={x(k)} y1={T} y2={H - B} class="grid" stroke-dasharray="2 3" />}
          <text x={x(k + 0.5)} y={H - 6} class="ax" text-anchor="middle">{(n > 20 || W / n < 44) && k % 2 ? "" : `T${k + 1}`}</text></g>)}
        {people.map((p) => {
          const c = personColour(p.person);
          const d = allTurns(p).map((t, k) => {
            const span = Math.max(1, t.time_s - t.cut_s - 1);
            return t.t.map((tt, i) => `${i ? "L" : "M"}${x(k + 0.04 + 0.92 * (tt - t.cut_s) / span).toFixed(1)},${y(t.p[i]).toFixed(1)}`).join("");
          }).join("");
          const on = hover === p.person;
          return <path key={p.person} d={d} fill="none" stroke={c} stroke-width={on ? 2.6 : 1.6} stroke-opacity={hover && !on ? 0.35 : 1}
            onMouseEnter={() => setHover(p.person)} onMouseLeave={() => setHover(null)}><title>{personName(p.person)}</title></path>;
        })}
      </svg>
    </div>
  );
}

export function SharedTurns({ resp, me }: { resp: SharedTurnsResp; me: string }) {
  useTheme();   // re-render on theme switch (person colours have a dark variant)
  const [who, setWho] = useState<"me" | "all">(resp.people.length > 1 ? "all" : "me");
  const mine = resp.people.filter((p) => p.person === me);
  const shown = who === "me" && mine.length ? mine : resp.people;
  const fastest = resp.people[0]?.steady_pace_s;
  const two = twoMachines(resp.people);
  return (
    <section>
      <div class="head">
        <h2 title="each person's own turns (on both machines when the second one was recorded too); the first 8-10 s of every turn (the flywheel still carries the previous person's pace) and the last second are left out">Turns</h2>
        {resp.people.length > 1 && <div class="tools"><div class="seg">
          <button class={who === "me" ? "on" : ""} onClick={() => setWho("me")}>Me</button>
          <button class={who === "all" ? "on" : ""} onClick={() => setWho("all")}>Everyone</button>
        </div></div>}
      </div>
      <p class="muted small">
        {resp.people.map((p, i) => <span key={p.person}>{i ? " · " : ""}<b style={{ color: personColour(p.person) }}>{personName(p.person)}</b> {p.steady_pace_s ? p500(p.steady_pace_s) : "—"}
          {fastest && p.steady_pace_s && i ? <span> (+{(p.steady_pace_s - fastest).toFixed(1)} s)</span> : null}</span>)}
        {" "}/500 m steady average over {allTurns(resp.people[0]).length} turns{two ? " on two machines" : ""}
      </p>
      <Timeline people={resp.people} machines={resp.machines ?? []} />
      <Rounds people={shown} />
      <PerTurn people={shown} />
      <Curve people={shown} />
    </section>
  );
}
