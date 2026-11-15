// Fitness page · Erg: the Concept2 trend per machine (RowErg "Row", SkiErg), from /api/fitness/erg
// (scripts/erg_fitness.py -> erg_fitness.json; docs/erg.md). Per machine: the current 2k-equivalent pace /500 m and
// watts, a uPlot chart of the smoothed line with a ±1 sd band and every session's 2k-equivalent as a marker (test =
// diamond, continuous piece / interval session = dot, paler = lower weight, warm-up = ring; faster = up; hover = details,
// click = activity), and a small personal-best table. Own scale: not VDOT, not part of Overall. Hidden without erg data.
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { useEffect, useRef } from "preact/hooks";
import { useApi, type ErgDoc, type ErgKind, type ErgMachine, type ErgMachineDoc, type ErgSession } from "../lib/api";
import { navigate } from "../lib/router";
import { cssVar } from "../lib/format";
import { useTheme } from "../lib/theme";
import { dateAxis } from "../lib/chartAxes";
import "../fitness.css";

const MACHINES: ErgMachine[] = ["row", "skierg"];
/** machine colour: the sport tokens (--sport-row / --sport-skierg) */
const ergColor = (m: ErgMachine) => cssVar(`--sport-${m}`) || cssVar("--chart-muted");
const KIND: Record<ErgKind, [string, string]> = {
  test: ["test piece", "a single all-out-looking piece (1:30–10:00 or a standard distance) at or above the trend: full evidence"],
  piece: ["continuous piece", "a continuous piece: its average pace; a lower bound (below the trend it counts weakly)"],
  intervals: ["interval session", "time-weighted work watts of the work intervals, as a continuous effort between one rep and all the work; robust like a piece"],
  warmup: ["warm-up / extra", "a same-day extra piece clearly slower than the day's best: ignored unless far above the trend"],
  ignored: ["ignored", "too short (< 60 s or < 250 m) or broken"],
};

/** s /500 m -> "1:48.3 /500 m" (unit=false: "1:48.3") */
export function ergPace(s: number | null | undefined, unit = true): string {
  if (s === null || s === undefined || !isFinite(s)) return "—";
  const t = Math.round(s * 10) / 10, m = Math.floor(t / 60), x = t - 60 * m;
  return `${m}:${x.toFixed(1).padStart(4, "0")}${unit ? " /500 m" : ""}`;
}
/** seconds -> "6:59" / "1:02:03" */
const clock = (s: number) => {
  const r = Math.round(s), h = Math.floor(r / 3600), m = Math.floor((r % 3600) / 60), x = r % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`;
};
const ts = (d: string) => Date.parse(d + "T12:00:00Z") / 1000;
function rgba(hex: string, a: number) {
  const h = hex.trim().replace("#", "");
  const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
  return isFinite(n) ? `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})` : hex;
}
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));

/** smoothed 2k-equivalent pace (± 1 sd band) and session markers; y = −pace so faster is up */
function ErgChart({ m, d, sessions, height = 240 }: { m: ErgMachine; d: ErgMachineDoc; sessions: ErgSession[]; height?: number }) {
  const box = useRef<HTMLDivElement>(null);
  const tip = useRef<HTMLDivElement>(null);
  const theme = useTheme();

  useEffect(() => {
    const root = box.current!;
    root.innerHTML = "";
    const days = d.daily;
    if (!days.length) return;
    const x = days.map((p) => ts(p.date));
    const mean = days.map((p) => -p.pace), fast = days.map((p) => -p.pace_fast), slow = days.map((p) => -p.pace_slow);
    const pts = sessions.filter((s) => s.kind !== "ignored" && s.pace_2k !== undefined).map((s) => ({ s, t: ts(s.date), y: -(s.pace_2k as number) }));
    let lo = Math.min(...slow), hi = Math.max(...fast);
    const blo = lo, bhi = hi;   // markers widen the range by at most 4 s beyond the band; further out they're clipped
    for (const p of pts) if (p.s.kind !== "warmup") { lo = Math.min(lo, Math.max(p.y, blo - 4)); hi = Math.max(hi, Math.min(p.y, bhi + 4)); }
    const range: [number, number] = [Math.floor(lo - 1), Math.ceil(hi + 1)];
    const c = ergColor(m), muted = cssVar("--text-muted"), grid = cssVar("--hairline"), font = `12px ${cssVar("--font")}`;
    const dpr = devicePixelRatio || 1;
    let cache: { s: ErgSession; x: number; y: number }[] = [];

    const drawPts = (u: uPlot) => {
      const ctx = u.ctx, { left, top, width, height: h } = u.bbox;
      ctx.save();
      ctx.beginPath(); ctx.rect(left, top, width, h); ctx.clip();
      const order: Record<string, number> = { warmup: 0, piece: 1, intervals: 1, test: 2 };
      cache = [];
      for (const p of [...pts].sort((a, b) => order[a.s.kind] - order[b.s.kind])) {
        const cx = u.valToPos(p.t, "x", true), cy = u.valToPos(Math.min(Math.max(p.y, range[0]), range[1]), "y", true);
        const w = p.s.weight ?? 0;
        ctx.lineWidth = 1.25 * dpr;
        if (p.s.kind === "test") {
          const r = 5.5 * dpr;
          ctx.beginPath(); ctx.moveTo(cx, cy - r); ctx.lineTo(cx + r, cy); ctx.lineTo(cx, cy + r); ctx.lineTo(cx - r, cy); ctx.closePath();
          ctx.fillStyle = c; ctx.fill(); ctx.strokeStyle = cssVar("--surface"); ctx.stroke();
        } else if (p.s.kind === "warmup") {
          ctx.beginPath(); ctx.arc(cx, cy, 2.5 * dpr, 0, 2 * Math.PI);
          ctx.strokeStyle = rgba(c, 0.5); ctx.stroke();
        } else {
          ctx.beginPath(); ctx.arc(cx, cy, 3.5 * dpr, 0, 2 * Math.PI);
          ctx.fillStyle = rgba(c, 0.3 + 0.7 * w); ctx.fill(); ctx.strokeStyle = cssVar("--surface"); ctx.stroke();
        }
        cache.push({ s: p.s, x: cx / dpr - left / dpr, y: cy / dpr - top / dpr });
      }
      ctx.restore();
    };

    const u = new uPlot({
      width: root.clientWidth || 900, height,
      padding: [8, 4, 0, 0],
      legend: { show: false },
      cursor: { points: { show: false }, y: false, drag: { x: false, y: false } },
      scales: { x: { time: true }, y: { range: () => range } },
      bands: [{ series: [2, 3], fill: rgba(c, 0.13) }],
      axes: [
        { stroke: muted, grid: { stroke: grid, width: 1 }, ticks: { show: false }, font, size: 26, ...dateAxis(56) },
        { stroke: muted, grid: { stroke: grid, width: 1 }, ticks: { show: false }, font, size: 52, label: "2k-eq. /500 m", labelFont: font, labelSize: 16,
          incrs: [1, 2, 5, 10, 15, 30, 60], values: (_u, vals) => vals.map((v) => ergPace(-v, false).replace(/\.0$/, "")) },
      ],
      series: [{}, { stroke: c, width: 1.75, points: { show: false } },
        { stroke: "transparent", width: 0, points: { show: false } }, { stroke: "transparent", width: 0, points: { show: false } }],
      hooks: { draw: [drawPts] },
    }, [x, mean, fast, slow] as uPlot.AlignedData, root);

    const over = u.over;
    let hit: ErgSession | null = null;
    const onMove = (e: MouseEvent) => {
      let best: (typeof cache)[number] | null = null, bd = 64;
      for (const q of cache) { const dd = (q.x - e.offsetX) ** 2 + (q.y - e.offsetY) ** 2; if (dd < bd) { bd = dd; best = q; } }
      hit = best?.s ?? null;
      const el = tip.current!;
      if (!best) {
        over.style.cursor = "default";
        const i = u.cursor.idx;
        if (i === null || i === undefined || !days[i]) { el.style.display = "none"; return; }
        const p = days[i];
        el.innerHTML = `<div class="t1"><b>${p.date}</b> ${esc(d.label)} trend</div>
          <div>${ergPace(p.pace)} ± ${p.sd.toFixed(1)} s · ${Math.round(p.watts)} W · 2k ≈ ${clock(p.pace * 4)}</div>`;
        el.style.display = "block";
        const lx = u.valToPos(x[i], "x"), ly = u.valToPos(mean[i], "y");
        const ow = over.clientWidth, tw = el.offsetWidth;
        el.style.left = (lx + 12 + tw > ow ? over.offsetLeft + lx - tw - 12 : over.offsetLeft + lx + 12) + "px";
        el.style.top = Math.max(0, over.offsetTop + ly - 20) + "px";
        return;
      }
      over.style.cursor = "pointer";
      const s = best.s;
      const delta = s.delta_s;
      el.innerHTML = `<div class="t1"><i class="dot" style="background:${c}"></i><b>${s.date}</b> ${esc(d.label)} ${esc(s.label)}</div>
        <div>work ${ergPace(s.pace)} · ${Math.round(s.watts ?? 0)} W${s.n_reps && s.n_reps > 1 ? ` (time-weighted over ${s.n_reps} reps)` : ""}</div>
        <div>2k-equivalent <b>${ergPace(s.pace_2k)}</b>${delta !== undefined ? ` · ${Math.abs(delta).toFixed(1)} s ${delta <= 0 ? "faster" : "slower"} than the trend` : ""}</div>
        <div class="muted">${KIND[s.kind][0]}${s.weight !== undefined ? ` · weight ${s.weight.toFixed(2)}` : ""}${s.d_eff_s ? ` · counted as ${clock(s.d_eff_s)} continuous` : ""}</div>
        <div class="muted">click to open</div>`;
      el.style.display = "block";
      const ow = over.clientWidth, tw = el.offsetWidth;
      el.style.left = (best.x + 12 + tw > ow ? over.offsetLeft + best.x - tw - 12 : over.offsetLeft + best.x + 12) + "px";
      el.style.top = Math.max(0, over.offsetTop + best.y - 20) + "px";
    };
    const onLeave = () => { tip.current!.style.display = "none"; hit = null; };
    const onClick = () => { if (hit) navigate(`/activities/${hit.id}`); };
    over.addEventListener("mousemove", onMove);
    over.addEventListener("mouseleave", onLeave);
    over.addEventListener("click", onClick);
    const ro = new ResizeObserver(() => u.setSize({ width: root.clientWidth, height }));
    ro.observe(root);
    return () => {
      ro.disconnect();
      over.removeEventListener("mousemove", onMove); over.removeEventListener("mouseleave", onLeave); over.removeEventListener("click", onClick);
      u.destroy();
    };
  }, [d, sessions, m, height, theme]);

  return (
    <div class="shape-chart">
      <div class="plotwrap">
        <div ref={box} />
        <div class="chart-tip" ref={tip} />
      </div>
    </div>
  );
}

const distLabel = (m: number) => (m >= 1000 ? `${m / 1000} km` : `${m} m`);

function PbTables({ d }: { d: ErgMachineDoc }) {
  const pb = d.personal_bests;
  const note = (exact: boolean, what: string) => exact ? "" : ` (${what === "whole" ? "a longer piece" : `${what} of a longer piece`})`;
  return (
    <div class="cols erg-pbs" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))" }}>
      <div class="tablewrap">
        <table>
          <thead><tr><th title="best average pace over a piece (whole piece, rep or split) of at least this distance">Distance</th><th class="num">Pace</th><th class="num">Time</th><th>Session</th></tr></thead>
          <tbody>
            {pb.best_2k_equivalent && <tr>
              <td title="the best session score: 2k-equivalent by Paul's law">2k-equivalent</td>
              <td class="num"><b>{ergPace(pb.best_2k_equivalent.pace, false)}</b></td>
              <td class="num">{clock(pb.best_2k_equivalent.pace * 4)}</td>
              <td><a href={`/activities/${pb.best_2k_equivalent.id}`}>{pb.best_2k_equivalent.date}</a> <span class="muted">{pb.best_2k_equivalent.label}</span></td>
            </tr>}
            {pb.distances.map((x) => (
              <tr key={x.m}>
                <td title={x.exact ? "a piece of this distance" : `fastest piece of at least ${distLabel(x.m)}: ${distLabel(x.piece_m)} in ${clock(x.piece_s)}`}>
                  {x.exact ? distLabel(x.m) : `≥ ${distLabel(x.m)}`}</td>
                <td class="num">{ergPace(x.pace, false)}</td>
                <td class="num" title={`${Math.round(x.watts)} W`}>{x.exact ? clock(x.time_s) : <span class="muted">{clock(x.time_s)}</span>}</td>
                <td title={`${x.label}${note(x.exact, x.what)}`}><a href={`/activities/${x.id}`}>{x.date}</a> <span class="muted">{x.label}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div class="tablewrap">
        <table>
          <thead><tr><th title="best average watts over a piece (whole piece, rep or split) of at least this duration">Duration</th><th class="num">Watts</th><th class="num">Pace</th><th>Session</th></tr></thead>
          <tbody>
            {pb.durations.map((x) => (
              <tr key={x.s}>
                <td title={x.exact ? "a piece of this duration" : `best piece of at least ${clock(x.s)}: ${clock(x.piece_s)}`}>{x.exact ? clock(x.s) : `≥ ${clock(x.s)}`}</td>
                <td class="num"><b>{Math.round(x.watts)} W</b></td>
                <td class="num">{ergPace(x.pace, false)}</td>
                <td title={`${x.label}${note(x.exact, x.what)}`}><a href={`/activities/${x.id}`}>{x.date}</a> <span class="muted">{x.label}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MachineBlock({ m, d, sessions }: { m: ErgMachine; d: ErgMachineDoc; sessions: ErgSession[] }) {
  const c = d.current;
  const tr = c.trend_90d_s;
  return (
    <div class="erg-machine">
      <p class="shape-line">
        <span class="dot" style={{ background: ergColor(m), marginRight: "6px" }} /><b>{d.label}</b>{" "}
        <span class={c.stale ? "muted" : undefined} title={`2k-equivalent pace today ± 1 sd (${ergPace(c.pace_fast, false)}–${ergPace(c.pace_slow, false)}; ${Math.round(c.watts_slow)}–${Math.round(c.watts_fast)} W)${c.stale ? `\nno scored session for ${c.days_since_evidence ?? "ever"} days: the value is carried forward` : ""}`}>
          <b>{ergPace(c.pace)}</b> ± {c.sd.toFixed(1)} s · {Math.round(c.watts)} W · 2k ≈ {clock(c.time_2k_s)}
        </span>
        {tr !== null && <span class="muted" title="trend pace now minus 90 days ago (negative = faster)"> · 90 d: {tr <= 0 ? "−" : "+"}{Math.abs(tr).toFixed(1)} s</span>}
        <span class="muted"> · {d.n_scored} sessions{c.last_evidence_date ? ` · last ${c.last_evidence_date}` : ""}</span>
      </p>
      <ErgChart m={m} d={d} sessions={sessions} />
      <PbTables d={d} />
    </div>
  );
}

/** the Fitness page's erg section; renders nothing while loading, on error, or without Concept2 data */
export function ErgSection() {
  const { data } = useApi<ErgDoc>("/api/fitness/erg");
  const ms = MACHINES.filter((m) => data?.machines?.[m]);
  useEffect(() => { if (ms.length && location.hash === "#erg") document.getElementById("erg")?.scrollIntoView(); }, [data]);
  if (!data || !ms.length) return null;
  return (
    <section id="erg">
      <div class="head">
        <h2>Erg</h2>
        <span class="sub">Concept2 · 2k-equivalent pace /500 m and watts, ± 1 sd band · faster is up</span>
      </div>
      {ms.map((m) => <MachineBlock key={m} m={m} d={data.machines[m]!} sessions={data.sessions.filter((s) => s.machine === m)} />)}
      <div class="legend-row muted">
        {(["test", "intervals", "warmup"] as ErgKind[]).map((k) => <span key={k} title={KIND[k][1]}>
          <span class="dot" style={k === "test" ? { background: "var(--text-muted)", transform: "rotate(45deg)", borderRadius: 0, width: "7px", height: "7px" }
            : k === "warmup" ? { border: "1.5px solid var(--text-muted)", background: "transparent" } : { background: "var(--text-muted)" }} />
          {k === "intervals" ? "piece / interval session (paler = lower weight)" : KIND[k][0]}</span>)}
      </div>
      <details class="howto">
        <summary><h3 style={{ margin: 0 }}>How the erg trend is computed</h3></summary>
        <div class="prose">
          <ul>
            <li>Every session gets a <b>2k-equivalent pace</b>: continuous pieces from their average pace; interval sessions from the
              time-weighted average watts of the work intervals (watts = 2.80 / (s per m)³), counted as a continuous effort between one rep and
              all the work (more rest → closer to a single rep).</li>
            <li>Distances are scaled to 2000 m with <b>Paul&apos;s law</b>: +5 s /500 m per doubling of distance.</li>
            <li>There is no heart rate, so most sessions only show a lower bound: a smoothed line (daily random walk, robust weights) follows
              the upper edge of the sessions; a session far below the line counts weakly. A single all-out-looking piece (1:30–10:00 or a
              standard test distance) at or above the line is a test and counts fully.</li>
            <li>Own scale per machine: not VDOT, not part of Overall. Details: <span class="mono">docs/erg.md</span>.</li>
          </ul>
        </div>
      </details>
    </section>
  );
}
