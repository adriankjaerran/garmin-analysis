// Shared-machine erg sessions (Concept2, several people taking turns on one machine; docs/concept2.md):
// - `ownSeries`: this person's series inside their own turns only, each turn's handover cut (adaptive, 8-10 s, from
//   /api/activities/{id}/shared-turns) and the last second; with `noBreaks` the turns are joined end to end (x = work time).
// - `SharedTurns`: "Pace per turn" (steady pace after the handover, a line per person) and "Pace curve" (every
//   person's stroke pace, turns joined, x = turn number), for "Me" or "Everyone", in each person's colour.
import { useEffect, useRef, useState } from "preact/hooks";
import type { Rep, Series } from "../lib/api";
import { pace500 } from "../lib/format";
import { personColour, personName } from "../lib/people";
import { useTheme } from "../lib/theme";

export interface Turn { n: number; start_s: number; time_s: number; dist_m: number; spm: number | null; pace_s: number | null;
  cut_s: number; steady_pace_s: number | null; t: number[]; p: number[] }
export interface SharedPerson { person: string; turns: Turn[]; on_s: number; off_s: number; steady_pace_s: number | null;
  second_machine?: { est_m: number; logbook_dist_m: number } | null }
export interface SharedTurnsResp { activity_id: string; people: SharedPerson[] }

const MIN_CUT_S = 8;     // fallback when the turn profiles aren't loaded (the measured flywheel carry-over)
const END_CUT_S = 1;     // the last second before the swap sags
const p500 = (s: number) => pace500(500 / s);

/** the series inside this person's turns only (others' turns = gaps), each turn's handover cut (per turn from
 *  `turns`, else 8 s) and its last second; `noBreaks`: turns joined end to end, t = work seconds, a gap between turns */
export function ownSeries(s: Series, reps: Rep[], turns: Turn[] | null, noBreaks: boolean): { series: Series; bands: { a: number; b: number }[] } {
  const iv = reps.filter((r) => r.start !== undefined && r.end !== undefined).map((r, k) => {
    const cut = turns?.find((t) => Math.abs(t.start_s - r.start!) < 2)?.cut_s ?? turns?.[k]?.cut_s ?? MIN_CUT_S;
    return [r.start! + cut, r.end! - END_CUT_S] as const;
  });
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

/** x = turn 1..n, y = steady pace (faster up), a line per person */
function PerTurn({ people }: { people: SharedPerson[] }) {
  const [box, W] = useWidth();
  const n = Math.max(...people.map((p) => p.turns.length));
  const vals = people.flatMap((p) => p.turns.map((t) => t.steady_pace_s)).filter((x): x is number => !!x);
  if (!vals.length) return null;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = Math.max((hi - lo) * 0.08, 1.5);
  lo -= pad; hi += pad;
  const H = 220, L = 50, R = 12, T = 8, B = 22;
  const x = (k: number) => L + 16 + (n === 1 ? 0 : (k * (W - L - R - 32)) / (n - 1));
  const y = (v: number) => T + ((v - lo) / (hi - lo)) * (H - T - B);
  return (
    <div class="ladder" ref={box}>
      <div class="title">Pace per turn (/500 m, steady: handover cut) · faster is up</div>
      <svg width={W} height={H}>
        {ticks(lo, hi).map((v) => <g key={v}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} class="grid" /><text x={L - 6} y={y(v) + 4} class="ax" text-anchor="end">{p500(v)}</text></g>)}
        {Array.from({ length: n }, (_, k) => <text key={k} x={x(k)} y={H - 6} class="ax" text-anchor="middle">{(n > 20 || W / n < 44) && k % 2 ? "" : `T${k + 1}`}</text>)}
        {people.map((p) => {
          const c = personColour(p.person);
          const pts = p.turns.map((t, k) => (t.steady_pace_s ? [x(k), y(t.steady_pace_s), t] as const : null)).filter((q) => q !== null);
          const d = pts.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join("");
          return (
            <g key={p.person}>
              <path d={d} fill="none" stroke={c} stroke-width={2.2} />
              {pts.map(([px, py, t]) => (
                <circle key={t.n} cx={px} cy={py} r={3.5} fill={c} style={{ stroke: "var(--surface)" }} stroke-width={1}>
                  <title>{`${personName(p.person)} · turn ${t.n}: ${p500(t.steady_pace_s!)} /500 m steady (first ${Math.round(t.cut_s)} s cut) · ${p500(t.pace_s!)} over the whole turn · ${Math.round(t.dist_m)} m${t.spm ? ` · ${t.spm} spm` : ""}`}</title>
                </circle>))}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** every person's stroke pace inside their turns, handover cut, turns joined: x = turn number (+ fraction of it) */
function Curve({ people }: { people: SharedPerson[] }) {
  const [box, W] = useWidth();
  const [hover, setHover] = useState<string | null>(null);
  const n = Math.max(...people.map((p) => p.turns.length));
  const all = people.flatMap((p) => p.turns.flatMap((t) => t.p)).sort((a, b) => a - b);
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
          const d = p.turns.map((t, k) => {
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
  return (
    <section>
      <div class="head">
        <h2 title="each person's own turns on the recorded machine; the first 8-10 s of every turn (the flywheel still carries the previous person's pace) and the last second are left out">Turns</h2>
        {resp.people.length > 1 && <div class="tools"><div class="seg">
          <button class={who === "me" ? "on" : ""} onClick={() => setWho("me")}>Me</button>
          <button class={who === "all" ? "on" : ""} onClick={() => setWho("all")}>Everyone</button>
        </div></div>}
      </div>
      <p class="muted small">
        {resp.people.map((p, i) => <span key={p.person}>{i ? " · " : ""}<b style={{ color: personColour(p.person) }}>{personName(p.person)}</b> {p.steady_pace_s ? p500(p.steady_pace_s) : "—"}
          {fastest && p.steady_pace_s && i ? <span> (+{(p.steady_pace_s - fastest).toFixed(1)} s)</span> : null}</span>)}
        {" "}/500 m steady average over {resp.people[0]?.turns.length} turns
      </p>
      <PerTurn people={shown} />
      <Curve people={shown} />
    </section>
  );
}
