// Weekly shape per discipline (mean line + ±1 sd band) over all years, overall % of best on a right
// axis, and every session's evidence as markers (race = diamond, hard = dot, bound = ring,
// implausible = cross). Hover a marker -> session details, click -> activity. Drag across the plot to zoom.
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { useEffect, useRef } from "preact/hooks";
import { navigate } from "../lib/router";
import { cssVar } from "../lib/format";
import { DISC_LABEL, KIND_LABEL, discColor, pm, unitShort, type Disc, type FitSession, type FitnessDoc } from "../lib/fitness";

const ts = (d: string) => Date.parse(d + "T12:00:00Z") / 1000;
const isoOf = (t: number) => new Date(t * 1000).toISOString().slice(0, 10);

function rgba(hex: string, a: number) {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));

interface Props {
  doc: FitnessDoc;
  discs: Disc[];
  overall: boolean;
  bounds: boolean;
  from: string | null;
  to: string | null;
  highlight: string | null;
  onZoom: (from: string | null, to: string | null) => void;
  height?: number;
}

export function ShapeChart({ doc, discs, overall, bounds, from, to, highlight, onZoom, height = 380 }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const read = useRef<HTMLDivElement>(null);
  const tip = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const hl = useRef(highlight);
  const zoomRef = useRef(onZoom);
  zoomRef.current = onZoom;

  useEffect(() => {
    hl.current = highlight;
    plot.current?.redraw(false);
  }, [highlight]);

  useEffect(() => {
    const root = box.current!;
    root.innerHTML = "";
    const inr = (d: string) => (!from || d >= from) && (!to || d <= to);
    const D = doc.disciplines;
    // shared weekly x (all weekly series end on the build day, so the dates line up)
    const dates = new Set<string>();
    for (const k of [...discs, ...(overall ? ["overall"] : [])]) for (const w of D[k]?.weekly ?? []) if (inr(w.date)) dates.add(w.date);
    const xs = [...dates].sort();
    if (!xs.length) { root.innerHTML = `<div class="empty">No data in this range.</div>`; return; }
    const x = xs.map(ts);
    const pos = new Map(xs.map((d, i) => [d, i]));
    const data: (number | null)[][] = [x];
    const series: uPlot.Series[] = [{}];
    const bandsCfg: uPlot.Band[] = [];
    const lines: { key: string; idx: number }[] = [];
    let lo = Infinity, hi = -Infinity;
    for (const k of discs) {
      const d = D[k];
      if (!d) continue;
      const m: (number | null)[] = xs.map(() => null), up = [...m], dn = [...m];
      for (const w of d.weekly) {
        const i = pos.get(w.date);
        if (i === undefined) continue;
        m[i] = w.mean; up[i] = w.mean + w.sd; dn[i] = w.mean - w.sd;
        lo = Math.min(lo, w.mean - w.sd); hi = Math.max(hi, w.mean + w.sd);
      }
      const c = discColor(k);
      const base = data.length;
      data.push(m, up, dn);
      series.push({ label: k, stroke: c, width: 1.75, scale: "v", points: { show: false } },
        { stroke: "transparent", width: 0, scale: "v", points: { show: false } },
        { stroke: "transparent", width: 0, scale: "v", points: { show: false } });
      bandsCfg.push({ series: [base + 1, base + 2], fill: rgba(c, 0.13) });
      lines.push({ key: k, idx: base });
    }
    // overall (% of best) goes in its own small panel below, x-synced
    let ov: (number | null)[][] | null = null;
    if (overall && D.overall) {
      const m: (number | null)[] = xs.map(() => null), up = [...m], dn = [...m];
      for (const w of D.overall.weekly) { const i = pos.get(w.date); if (i !== undefined) { m[i] = w.mean; up[i] = w.mean + w.sd; dn[i] = w.mean - w.sd; } }
      ov = [x, m, up, dn];
    }
    const pts: (FitSession & { t: number })[] = doc.sessions
      .filter((s) => discs.includes(s.discipline) && inr(s.date) && (bounds || s.kind !== "bound"))
      .map((s) => ({ ...s, t: ts(s.date) }));
    // hard evidence widens the y range, but at most 6 VDOT below the bands (keeps the trend readable)
    const wlo = lo;
    for (const p of pts) if (p.kind === "race" || p.kind === "hard") { lo = Math.min(lo, Math.max(p.score, wlo - 6)); hi = Math.max(hi, p.score); }
    if (!isFinite(lo)) { lo = 40; hi = 60; }
    const vRange: [number, number] = [Math.floor(lo - 1.5), Math.ceil(hi + 1.5)];

    const muted = cssVar("--text-muted"), grid = cssVar("--hairline"), font = `12px ${cssVar("--font")}`;
    const dpr = devicePixelRatio || 1;
    const ptsXY = () => pts.map((p) => ({ p, x: u.valToPos(p.t, "x"), y: u.valToPos(p.score, "v") }));
    let cache: { p: FitSession; x: number; y: number }[] = [];

    const drawPts = (u: uPlot) => {
      const ctx = u.ctx, { left, top, width, height } = u.bbox;
      ctx.save();
      ctx.beginPath(); ctx.rect(left, top, width, height); ctx.clip();
      const order: Record<string, number> = { bound: 0, implausible: 1, hard: 2, race: 3 };
      for (const p of [...pts].sort((a, b) => order[a.kind] - order[b.kind])) {
        const cx = u.valToPos(p.t, "x", true), cy = u.valToPos(p.score, "v", true);
        if (cy < top - 10 || cy > top + height + 10) continue;
        const c = discColor(p.discipline), w = p.weight ?? 0;
        ctx.lineWidth = 1.25 * dpr;
        if (p.kind === "race") {
          const r = 5.5 * dpr;
          ctx.beginPath(); ctx.moveTo(cx, cy - r); ctx.lineTo(cx + r, cy); ctx.lineTo(cx, cy + r); ctx.lineTo(cx - r, cy); ctx.closePath();
          ctx.fillStyle = c; ctx.fill(); ctx.strokeStyle = "#fff"; ctx.stroke();
        } else if (p.kind === "hard") {
          ctx.beginPath(); ctx.arc(cx, cy, 3.5 * dpr, 0, 2 * Math.PI);
          ctx.fillStyle = rgba(c, 0.3 + 0.7 * w); ctx.fill(); ctx.strokeStyle = "#fff"; ctx.stroke();
        } else if (p.kind === "bound") {
          ctx.beginPath(); ctx.arc(cx, cy, 2.5 * dpr, 0, 2 * Math.PI);
          ctx.strokeStyle = rgba(c, w > 0 ? 0.9 : 0.35); ctx.stroke();
        } else {
          const r = 3.5 * dpr;
          ctx.beginPath(); ctx.moveTo(cx - r, cy - r); ctx.lineTo(cx + r, cy + r); ctx.moveTo(cx + r, cy - r); ctx.lineTo(cx - r, cy + r);
          ctx.strokeStyle = muted; ctx.stroke();
        }
        if (hl.current === p.id) {
          ctx.beginPath(); ctx.arc(cx, cy, 8 * dpr, 0, 2 * Math.PI);
          ctx.lineWidth = 2 * dpr; ctx.strokeStyle = cssVar("--text"); ctx.stroke();
        }
      }
      ctx.restore();
      cache = ptsXY();
    };

    const fmtRead = (i: number | null | undefined) => {
      if (i === null || i === undefined) i = xs.length - 1;
      const parts = [`<span class="muted">week to ${xs[i]}</span>`];
      for (const { key, idx } of lines) {
        const m = data[idx][i], up = data[idx + 1][i];
        if (m === null) continue;
        parts.push(`<span><i class="dot" style="background:${discColor(key)}"></i>${DISC_LABEL[key]} ${pm(m, (up as number) - m)}</span>`);
      }
      if (ov && ov[1][i] !== null) parts.push(`<span><i class="dot" style="background:${discColor("overall")}"></i>Overall ${(ov[1][i] as number).toFixed(1)} %</span>`);
      read.current!.innerHTML = parts.join("");
    };

    const width = root.clientWidth || 900;
    const sync = uPlot.sync("shape-" + Math.random());
    const u: uPlot = new uPlot({
      width, height,
      padding: [8, 4, 0, 0],
      legend: { show: false },
      cursor: { drag: { x: true, y: false, setScale: false }, points: { show: false }, y: false, sync: { key: sync.key, setSeries: false } },
      select: { show: true, left: 0, top: 0, width: 0, height: 0 },
      scales: { x: { time: true }, v: { range: () => vRange } },
      bands: bandsCfg,
      axes: [
        { stroke: muted, grid: { stroke: grid, width: 1 }, ticks: { show: false }, font, size: 26, space: 70 },
        { scale: "v", stroke: muted, grid: { stroke: grid, width: 1 }, ticks: { show: false }, font, size: 48, label: "VDOT", labelFont: font, labelSize: 16 },
      ],
      series,
      hooks: {
        draw: [drawPts],
        setCursor: [(u) => fmtRead(u.cursor.idx)],
        setSelect: [(u) => {
          const s = u.select;
          if (s.width > 6) {
            const a = isoOf(u.posToVal(s.left, "x")), b = isoOf(u.posToVal(s.left + s.width, "x"));
            zoomRef.current(a, b);
          }
          u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
        }],
      },
    }, data as uPlot.AlignedData, root);
    plot.current = u;
    let u2: uPlot | null = null;
    if (ov) {
      const c = discColor("overall");
      const vals = (ov[1].filter((v) => v !== null) as number[]);
      const pr: [number, number] = [Math.floor(Math.min(...vals) - 2), Math.min(102, Math.ceil(Math.max(...vals) + 2))];
      const lab = document.createElement("div");
      lab.className = "subtitle";
      lab.textContent = "Overall, % of personal best (precision-weighted across disciplines)";
      root.appendChild(lab);
      u2 = new uPlot({
        width, height: 120, padding: [6, 4, 0, 0], legend: { show: false },
        cursor: { drag: { x: false, y: false, setScale: false }, points: { show: false }, y: false, sync: { key: sync.key, setSeries: false } },
        scales: { x: { time: true }, y: { range: () => pr } },
        bands: [{ series: [2, 3], fill: rgba(c, 0.15) }],
        axes: [
          { stroke: muted, grid: { stroke: grid, width: 1 }, ticks: { show: false }, font, size: 24, space: 70 },
          { stroke: muted, grid: { stroke: grid, width: 1 }, ticks: { show: false }, font, size: 48, space: 28, label: "%", labelFont: font, labelSize: 16 },
        ],
        series: [{}, { stroke: c, width: 1.5, points: { show: false } },
          { stroke: "transparent", width: 0, points: { show: false } }, { stroke: "transparent", width: 0, points: { show: false } }],
      }, ov as uPlot.AlignedData, root);
    }
    fmtRead(null);

    // marker hover / click (uPlot's cursor only knows the weekly grid)
    const over = u.over;
    let hit: FitSession | null = null;
    const onMove = (e: MouseEvent) => {
      const mx = e.offsetX, my = e.offsetY;
      let best: (typeof cache)[number] | null = null, bd = 64;
      for (const c of cache) {
        const d = (c.x - mx) ** 2 + (c.y - my) ** 2;
        if (d < bd || (best && d === bd && c.p.kind !== "bound")) { bd = d; best = c; }
      }
      hit = best?.p ?? null;
      const el = tip.current!;
      over.style.cursor = hit ? "pointer" : "crosshair";
      if (!best) { el.style.display = "none"; return; }
      const p = best.p;
      const delta = p.shape_at !== null && p.kind !== "implausible" ? p.score - p.shape_at : null;
      el.innerHTML = `<div class="t1"><i class="dot" style="background:${discColor(p.discipline)}"></i><b>${p.date}</b> ${esc(p.name ?? "")}</div>
        <div>${esc(p.explain)}</div>
        <div class="muted">${DISC_LABEL[p.discipline]} · ${KIND_LABEL[p.kind]} · score ${p.score.toFixed(1)} ${unitShort(p.discipline)}${p.shape_at !== null ? ` · shape that day ${p.shape_at.toFixed(1)}${delta !== null ? ` (${delta >= 0 ? "+" : "−"}${Math.abs(delta).toFixed(1)})` : ""}` : ""}${p.weight !== null ? ` · weight ${p.weight.toFixed(2)}` : ""}</div>
        <div class="muted">click to open</div>`;
      el.style.display = "block";
      const ow = over.clientWidth, left = over.offsetLeft + best.x + 12, tw = el.offsetWidth;
      el.style.left = (best.x + 12 + tw > ow ? over.offsetLeft + best.x - tw - 12 : left) + "px";
      el.style.top = Math.max(0, over.offsetTop + best.y - 20) + "px";
    };
    const onLeave = () => { tip.current!.style.display = "none"; hit = null; };
    const onClick = () => { if (hit) navigate(`/activities/${hit.id}`); };
    const onDbl = () => zoomRef.current(null, null);
    over.addEventListener("mousemove", onMove);
    over.addEventListener("mouseleave", onLeave);
    over.addEventListener("click", onClick);
    over.addEventListener("dblclick", onDbl);
    const ro = new ResizeObserver(() => { u.setSize({ width: root.clientWidth, height }); u2?.setSize({ width: root.clientWidth, height: 120 }); });
    ro.observe(root);
    return () => {
      ro.disconnect();
      over.removeEventListener("mousemove", onMove); over.removeEventListener("mouseleave", onLeave);
      over.removeEventListener("click", onClick); over.removeEventListener("dblclick", onDbl);
      u.destroy(); u2?.destroy(); plot.current = null;
    };
  }, [doc, discs.join(","), overall, bounds, from, to, height]);

  return (
    <div class="shape-chart">
      <div class="readout" ref={read} />
      <div class="plotwrap">
        <div ref={box} />
        <div class="chart-tip" ref={tip} />
      </div>
    </div>
  );
}
