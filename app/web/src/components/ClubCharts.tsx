// uPlot charts of the Community pages: the club's month series (sessions as bars by category, km as a line),
// a member's form delta per session (stems from 0, green above / red below) and their shape trend (weekly mean ± sd
// per discipline with the session scores as dots).
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { useEffect, useRef, useState } from "preact/hooks";
import { cssVar, shortDate } from "../lib/format";
import { useTheme } from "../lib/theme";
import { dateAxis } from "../lib/chartAxes";
import { discColor, DISC_LABEL } from "../lib/fitness";
import { CATS, dayTs, delta, monthLabel, type ClubMonth, type Member } from "../lib/community";
import { navigate } from "../lib/router";
import "../fitness.css";

const monthTs = (m: string) => Date.UTC(Number(m.slice(0, 4)), Number(m.slice(5, 7)) - 1, 15) / 1000;
function rgba(c: string, a: number) {
  const m = /^#([0-9a-f]{6})$/i.exec(c.trim());
  if (!m) return c;
  const n = parseInt(m[1], 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
}

/** create a uPlot in `el`, resized with it; returns a cleanup */
function mount(el: HTMLDivElement, opts: Omit<uPlot.Options, "width">, data: uPlot.AlignedData): () => void {
  el.innerHTML = "";
  const u = new uPlot({ ...opts, width: el.clientWidth || 600 } as uPlot.Options, data, el);
  const ro = new ResizeObserver(() => { if (el.clientWidth) u.setSize({ width: el.clientWidth, height: opts.height }); });
  ro.observe(el);
  return () => { ro.disconnect(); u.destroy(); };
}
const axisBase = () => {
  const muted = cssVar("--text-muted"), hair = cssVar("--hairline"), font = `12px ${cssVar("--font")}`;
  return { stroke: muted, grid: { stroke: hair, width: 1 }, ticks: { show: false }, font };
};

/** sessions per month as stacked category bars + km line (right axis); hover = readout, click a month = its first session */
export function ClubMonthChart({ months, height = 220 }: { months: ClubMonth[]; height?: number }) {
  const box = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  const [idx, setIdx] = useState<number | null>(null);
  const hl = useRef<number | null>(null);
  useEffect(() => {
    if (!months.length) return;
    const x = months.map((m) => monthTs(m.month));
    const km = months.map((m) => m.km || null);
    const maxS = Math.max(1, ...months.map((m) => m.sessions));
    const maxKm = Math.max(1, ...months.map((m) => m.km));
    const span = x.length > 1 ? (x[x.length - 1] - x[0]) / (x.length - 1) : 30 * 86400;
    const drawBars = (u: uPlot) => {
      const ctx = u.ctx, dpr = devicePixelRatio || 1;
      const w = Math.max(2 * dpr, Math.min(26 * dpr, (u.valToPos(x[0] + span, "x", true) - u.valToPos(x[0], "x", true)) * 0.72));
      ctx.save();
      months.forEach((m, i) => {
        let y0 = 0;
        const cx = u.valToPos(x[i], "x", true);
        for (const c of CATS) {
          const n = m.by_category[c.id] ?? 0;
          if (!n) continue;
          const a = u.valToPos(y0, "s", true), b = u.valToPos(y0 + n, "s", true);
          ctx.fillStyle = cssVar(`--cat-${c.id}`);
          ctx.globalAlpha = hl.current === null || hl.current === i ? 0.92 : 0.5;
          ctx.fillRect(cx - w / 2, b, w, Math.max(1, a - b - 1 * dpr));
          y0 += n;
        }
      });
      ctx.restore();
    };
    return mount(box.current!, {
      height, padding: [10, 6, 0, 0], legend: { show: false },
      cursor: { drag: { x: false, y: false }, points: { show: true, size: 6 }, y: false },
      scales: { x: { time: true, range: () => [x[0] - span, x[x.length - 1] + span] }, s: { range: () => [0, Math.ceil(maxS * 1.15)] }, k: { range: () => [0, maxKm * 1.15] } },
      axes: [
        { ...axisBase(), size: 26, ...dateAxis(48) },
        { ...axisBase(), scale: "s", size: 34, space: 24, incrs: [1, 2, 5, 10, 20], values: (_u, ts) => ts.map((t) => String(t)), label: "sessions", labelSize: 14, labelFont: `11px ${cssVar("--font")}` },
        { ...axisBase(), scale: "k", side: 1, size: 44, grid: { show: false }, values: (_u, ts) => ts.map((t) => `${Math.round(t)}`), label: "km", labelSize: 14, labelFont: `11px ${cssVar("--font")}` },
      ],
      series: [{}, { scale: "s", stroke: "transparent", points: { show: false } },
        { scale: "k", stroke: cssVar("--text-muted"), width: 1.5, dash: [4, 3], spanGaps: false, points: { show: true, size: 4, fill: cssVar("--surface"), stroke: cssVar("--text-muted") } }],
      hooks: {
        drawSeries: [(u, si) => { if (si === 1) drawBars(u); }],
        setCursor: [(u) => { const i = u.cursor.idx ?? null; if (i !== hl.current) { hl.current = i; setIdx(i); u.redraw(false); } }],
        ready: [(u) => {
          u.over.addEventListener("click", () => {
            const i = u.cursor.idx;
            if (i !== null && i !== undefined && months[i]?.gids.length) navigate(`/community?period=${months[i].month}`);
          });
          u.over.addEventListener("mouseleave", () => { hl.current = null; setIdx(null); u.redraw(false); });
        }],
      },
    }, [x, months.map((m) => m.sessions), km]);
  }, [months, height, theme]);
  const m = idx !== null ? months[idx] : null;
  return (
    <div class="cm-chart">
      <div class="cm-readout">
        {m ? <>
          <b>{monthLabel(m.month)}</b>
          <span>{m.sessions} session{m.sessions === 1 ? "" : "s"}</span><span>{m.km.toFixed(1)} km</span><span>{m.hours.toFixed(1)} h</span>
          {CATS.filter((c) => m.by_category[c.id]).map((c) => <span><i class="sw" style={{ background: `var(--cat-${c.id})` }} />{c.label} {m.by_category[c.id]}</span>)}
        </> : <span class="muted">Hover a month · bars = sessions by category · dashed = km (all members)</span>}
      </div>
      <div ref={box} />
    </div>
  );
}

/** a member's form delta per session: stems from 0 (green above, red below), low confidence hollow; click = session.
 *  One slot per session (equal spacing: sessions days apart would overlap on a time axis spanning years); dates
 *  under the slots (thinned), a faint line where the year changes. */
export function ClubFormChart({ series, height = 200 }: { series: Member["form_series"]; height?: number }) {
  const box = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  const [idx, setIdx] = useState<number | null>(null);
  const hl = useRef<number | null>(null);
  useEffect(() => {
    if (!series.length) return;
    const x = series.map((_s, i) => i);
    const v = series.map((s) => s.delta);
    const lim = Math.max(2, ...v.map(Math.abs)) * 1.2;
    const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const lab = (i: number) => { const d = series[i].date; return `${Number(d.slice(8, 10))} ${MON[Number(d.slice(5, 7)) - 1]}`; };
    return mount(box.current!, {
      height, padding: [10, 10, 0, 0], legend: { show: false },
      cursor: { drag: { x: false, y: false }, points: { show: false }, y: false, dataIdx: (_u, _s, i) => i },
      scales: { x: { time: false, range: () => [-0.6, x.length - 0.4] }, y: { range: () => [-lim, lim] } },
      axes: [{ ...axisBase(), size: 38, grid: { show: false },
        splits: (u) => { const step = Math.max(1, Math.ceil(56 / Math.max(1, u.bbox.width / devicePixelRatio / Math.max(1, x.length)))); return x.filter((i) => i % step === 0); },
        values: (_u, ts) => ts.map((t, k) => { const i = Math.round(t); const y = series[i]?.date.slice(0, 4); const py = k ? series[Math.round(ts[k - 1])]?.date.slice(0, 4) : null; return series[i] ? (y !== py ? `${lab(i)}\n${y}` : lab(i)) : ""; }) },
        { ...axisBase(), size: 40, values: (_u, ts) => ts.map((t) => delta(t, 0)) }],
      series: [{}, { stroke: "transparent", points: { show: false } }],
      hooks: {
        draw: [(u) => {
          const ctx = u.ctx, dpr = devicePixelRatio || 1;
          const y0 = u.valToPos(0, "y", true);
          ctx.save();
          ctx.strokeStyle = cssVar("--text-muted"); ctx.setLineDash([3 * dpr, 3 * dpr]); ctx.lineWidth = dpr;
          ctx.beginPath(); ctx.moveTo(u.bbox.left, y0); ctx.lineTo(u.bbox.left + u.bbox.width, y0); ctx.stroke(); ctx.setLineDash([]);
          ctx.strokeStyle = cssVar("--hairline"); ctx.lineWidth = dpr; ctx.fillStyle = cssVar("--text-muted"); ctx.font = `${10 * dpr}px ${cssVar("--font")}`;
          series.forEach((s, i) => {   // year boundaries
            if (!i || s.date.slice(0, 4) === series[i - 1].date.slice(0, 4)) return;
            const bx = u.valToPos(i - 0.5, "x", true);
            ctx.beginPath(); ctx.moveTo(bx, u.bbox.top); ctx.lineTo(bx, u.bbox.top + u.bbox.height); ctx.stroke();
            ctx.fillText(s.date.slice(0, 4), bx + 3 * dpr, u.bbox.top + 10 * dpr);
          });
          series.forEach((s, i) => {
            const cx = u.valToPos(i, "x", true), cy = u.valToPos(s.delta, "y", true);
            const c = cssVar(s.delta >= 0 ? "--state-good" : "--state-duplicate");
            ctx.strokeStyle = c; ctx.lineWidth = 3 * dpr;
            ctx.beginPath(); ctx.moveTo(cx, y0); ctx.lineTo(cx, cy); ctx.stroke();
            ctx.beginPath(); ctx.arc(cx, cy, (hl.current === i ? 7 : 5) * dpr, 0, 2 * Math.PI);
            ctx.lineWidth = 2 * dpr;
            if (s.confidence === "low") { ctx.fillStyle = cssVar("--surface"); ctx.fill(); ctx.stroke(); }
            else { ctx.fillStyle = c; ctx.fill(); ctx.strokeStyle = cssVar("--surface"); ctx.stroke(); }
          });
          ctx.restore();
        }],
        setCursor: [(u) => {
          const l = u.cursor.left;
          let best: number | null = null, bd = Math.max(24, u.bbox.width / devicePixelRatio / Math.max(1, x.length) / 2);
          if (l !== undefined && l >= 0) x.forEach((t, i) => { const d = Math.abs(u.valToPos(t, "x") - l); if (d < bd) { bd = d; best = i; } });
          if (best !== hl.current) { hl.current = best; setIdx(best); u.redraw(false); }
        }],
        ready: [(u) => {
          u.over.style.cursor = "pointer";
          u.over.addEventListener("click", () => {
            const l = u.cursor.left ?? -1;
            let best: number | null = null, bd = Math.max(24, u.bbox.width / devicePixelRatio / Math.max(1, x.length) / 2);
            x.forEach((t, i) => { const d = Math.abs(u.valToPos(t, "x") - l); if (d < bd) { bd = d; best = i; } });
            if (best !== null) navigate(`/community/${series[best].gid}`);
          });
          u.over.addEventListener("mouseleave", () => { hl.current = null; setIdx(null); u.redraw(false); });
        }],
      },
    }, [x, v]);
  }, [series, height, theme]);
  const s = idx !== null ? series[idx] : null;
  return (
    <div class="cm-chart">
      <div class="cm-readout">
        {s ? <><b>{shortDate(s.date, "x")}</b><span class={s.delta >= 0 ? "up" : "down"}>{delta(s.delta)} vs form</span>
          <span>score {s.score.toFixed(1)} · shape {s.shape.toFixed(1)}</span><span class="muted">{DISC_LABEL[s.discipline] ?? s.discipline}{s.confidence ? ` · ${s.confidence} confidence` : ""}</span></>
          : <span class="muted">Each dot = a club session: the session's score minus the shape the day before (VDOT). Hollow = low confidence. Click = open</span>}
      </div>
      <div ref={box} />
    </div>
  );
}

/** shape trend per discipline (weekly mean ± sd) from the member's `shape`, with the club sessions' scores as dots */
export function ClubShapeChart({ shape, sessions, height = 240 }: { shape: NonNullable<Member["shape"]>; sessions: Member["form_series"]; height?: number }) {
  const box = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  const [idx, setIdx] = useState<number | null>(null);
  const discs = Object.keys(shape.disciplines).filter((d) => shape.disciplines[d].weekly?.length);
  const dates = [...new Set(discs.flatMap((d) => shape.disciplines[d].weekly.map((w) => w.date)))].sort();
  useEffect(() => {
    if (!dates.length) return;
    const x = dates.map(dayTs);
    const pos = new Map(dates.map((d, i) => [d, i]));
    const data: (number | null)[][] = [x];
    const ser: uPlot.Series[] = [{}];
    const bands: uPlot.Band[] = [];
    let lo = Infinity, hi = -Infinity;
    for (const d of discs) {
      const m: (number | null)[] = x.map(() => null), up = [...m], dn = [...m];
      for (const w of shape.disciplines[d].weekly) {
        const i = pos.get(w.date)!;
        m[i] = w.mean; up[i] = w.mean + w.sd; dn[i] = w.mean - w.sd;
        lo = Math.min(lo, w.mean - w.sd); hi = Math.max(hi, w.mean + w.sd);
      }
      const c = discColor(d), base = data.length;
      data.push(m, up, dn);
      ser.push({ stroke: c, width: 2, points: { show: false } }, { stroke: "transparent", points: { show: false } }, { stroke: "transparent", points: { show: false } });
      bands.push({ series: [base + 1, base + 2], fill: rgba(c, 0.12) });
    }
    for (const s of sessions) { lo = Math.min(lo, s.score); hi = Math.max(hi, s.score); }
    const yr: [number, number] = [Math.floor(lo - 1), Math.ceil(hi + 1)];
    return mount(box.current!, {
      height, padding: [10, 10, 0, 0], legend: { show: false },
      cursor: { drag: { x: false, y: false }, points: { show: false }, y: false },
      scales: { x: { time: true }, y: { range: () => yr } },
      bands,
      axes: [{ ...axisBase(), size: 26, ...dateAxis(56) }, { ...axisBase(), size: 40, label: "VDOT", labelSize: 14, labelFont: `11px ${cssVar("--font")}` }],
      series: ser,
      hooks: {
        draw: [(u) => {
          const ctx = u.ctx, dpr = devicePixelRatio || 1;
          ctx.save();
          for (const s of sessions) {
            const cx = u.valToPos(dayTs(s.date), "x", true), cy = u.valToPos(s.score, "y", true);
            const sy = u.valToPos(s.shape, "y", true);
            ctx.strokeStyle = cssVar("--text-muted"); ctx.lineWidth = dpr; ctx.setLineDash([2 * dpr, 2 * dpr]);
            ctx.beginPath(); ctx.moveTo(cx, sy); ctx.lineTo(cx, cy); ctx.stroke(); ctx.setLineDash([]);
            ctx.beginPath(); ctx.arc(cx, cy, 4.5 * dpr, 0, 2 * Math.PI);
            ctx.fillStyle = discColor(s.discipline); ctx.fill(); ctx.lineWidth = 1.5 * dpr; ctx.strokeStyle = cssVar("--surface"); ctx.stroke();
          }
          ctx.restore();
        }],
        setCursor: [(u) => setIdx(u.cursor.idx ?? null)],
        ready: [(u) => { u.over.addEventListener("mouseleave", () => setIdx(null)); }],
      },
    }, data as uPlot.AlignedData);
  }, [shape, sessions, height, theme]);
  if (!dates.length) return null;
  const i = idx ?? dates.length - 1;
  return (
    <div class="cm-chart">
      <div class="cm-readout">
        <b>{idx === null ? "Now" : `week to ${shortDate(dates[i], "x")}`}</b>
        {discs.map((d) => {
          const w = shape.disciplines[d].weekly.find((x) => x.date === dates[i]);
          return w ? <span><i class="sw" style={{ background: discColor(d) }} />{DISC_LABEL[d] ?? d} {w.mean.toFixed(1)} ± {w.sd.toFixed(1)}</span> : null;
        })}
        <span class="muted">dots = club sessions' scores</span>
      </div>
      <div ref={box} />
    </div>
  );
}
