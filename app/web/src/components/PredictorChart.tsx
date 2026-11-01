// Race predictor chart: daily VDOT at 5k / 10k / half (line + ±1 sd band) from /api/fitness/predictor, the
// evidence behind it as markers (race = diamond, other evidence = dot, paler = lower weight) and a vertical
// marker on the selected day. Hover a marker -> what it was; click anywhere -> select that day (snapped to the
// nearest day in `daily`).
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { useEffect, useRef } from "preact/hooks";
import { cssVar } from "../lib/format";
import { useTheme } from "../lib/theme";
import { dateAxis } from "../lib/chartAxes";

export interface PredDaily { date: string; vdot_5k: number; vdot_10k: number; vdot_half: number; sd_5k: number; sd_10k: number; sd_half: number }
export interface PredEvidence { date: string; id: string | number; km: number; vdot: number; kind: string; weight: number }
export interface PredictorDoc {
  ref_km: number; evidence_km: [number, number]; daily: PredDaily[]; evidence: PredEvidence[];
  last_evidence_date: string | null; today: string; distance_curve?: boolean;
}

/** the three lines: key suffix in PredDaily, label, colour variable (predictor.css) */
export const PRED_LINES = [
  { k: "5k", label: "5k", color: "--pred-5k" },
  { k: "10k", label: "10k", color: "--pred-10k" },
  { k: "half", label: "Half", color: "--pred-half" },
] as const;

const ts = (d: string) => Date.parse(d + "T12:00:00Z") / 1000;
function rgba(hex: string, a: number) {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));
const kmText = (km: number) => (km >= 10 ? km.toFixed(1) : km.toFixed(2)).replace(/\.?0+$/, "") + " km";

export function PredictorChart({ doc, selected, onSelect, height = 340 }:
  { doc: PredictorDoc; selected: string | null; onSelect: (date: string) => void; height?: number }) {
  const box = useRef<HTMLDivElement>(null);
  const tip = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const sel = useRef(selected);
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;
  const theme = useTheme();

  useEffect(() => {
    sel.current = selected;
    plot.current?.redraw(false);
  }, [selected]);

  useEffect(() => {
    const root = box.current!;
    root.innerHTML = "";
    const days = doc.daily;
    if (!days.length) { root.innerHTML = `<div class="empty">No shape estimate yet.</div>`; return; }
    const x = days.map((d) => ts(d.date));
    const data: (number | null)[][] = [x];
    const series: uPlot.Series[] = [{}];
    const bands: uPlot.Band[] = [];
    let lo = Infinity, hi = -Infinity;
    for (const l of PRED_LINES) {
      const m = days.map((d) => (d as any)["vdot_" + l.k] as number);
      const s = days.map((d) => (d as any)["sd_" + l.k] as number);
      const up = m.map((v, i) => v + s[i]), dn = m.map((v, i) => v - s[i]);
      for (let i = 0; i < m.length; i++) { lo = Math.min(lo, dn[i]); hi = Math.max(hi, up[i]); }
      const c = cssVar(l.color), base = data.length;
      data.push(m, up, dn);
      series.push({ label: l.label, stroke: c, width: 1.75, scale: "v", points: { show: false } },
        { stroke: "transparent", width: 0, scale: "v", points: { show: false } },
        { stroke: "transparent", width: 0, scale: "v", points: { show: false } });
      bands.push({ series: [base + 1, base + 2], fill: rgba(c, 0.09) });
    }
    const pts = doc.evidence.map((e) => ({ e, t: ts(e.date) }));
    // evidence widens the y range at most 3 VDOT beyond the bands; markers further out are clipped at the edge
    const wlo = lo, whi = hi;
    for (const p of pts) { lo = Math.min(lo, Math.max(p.e.vdot, wlo - 3)); hi = Math.max(hi, Math.min(p.e.vdot, whi + 3)); }
    if (!isFinite(lo)) { lo = 40; hi = 60; }
    const vRange: [number, number] = [Math.floor(lo - 1), Math.ceil(hi + 1)];

    const muted = cssVar("--text-muted"), grid = cssVar("--hairline"), ink = cssVar("--text"), font = `12px ${cssVar("--font")}`;
    const dpr = devicePixelRatio || 1;
    let cache: { e: PredEvidence; x: number; y: number }[] = [];

    const draw = (u: uPlot) => {
      const ctx = u.ctx, { left, top, width, height } = u.bbox;
      ctx.save();
      ctx.beginPath(); ctx.rect(left, top, width, height); ctx.clip();
      // selected day
      if (sel.current) {
        const sx = u.valToPos(ts(sel.current), "x", true);
        ctx.beginPath(); ctx.moveTo(sx, top); ctx.lineTo(sx, top + height);
        ctx.lineWidth = 1.5 * dpr; ctx.strokeStyle = ink; ctx.setLineDash([4 * dpr, 3 * dpr]); ctx.stroke(); ctx.setLineDash([]);
      }
      for (const p of [...pts].sort((a, b) => (a.e.kind === "race" ? 1 : 0) - (b.e.kind === "race" ? 1 : 0))) {
        const cx = u.valToPos(p.t, "x", true), cy = u.valToPos(Math.min(Math.max(p.e.vdot, vRange[0]), vRange[1]), "v", true);
        ctx.lineWidth = 1.25 * dpr; ctx.strokeStyle = cssVar("--surface");
        if (p.e.kind === "race") {
          const r = 5.5 * dpr;
          ctx.beginPath(); ctx.moveTo(cx, cy - r); ctx.lineTo(cx + r, cy); ctx.lineTo(cx, cy + r); ctx.lineTo(cx - r, cy); ctx.closePath();
          ctx.fillStyle = ink; ctx.fill(); ctx.stroke();
        } else {
          ctx.beginPath(); ctx.arc(cx, cy, 3.25 * dpr, 0, 2 * Math.PI);
          ctx.globalAlpha = 0.3 + 0.7 * Math.max(0, Math.min(1, p.e.weight ?? 1));
          ctx.fillStyle = muted; ctx.fill(); ctx.stroke(); ctx.globalAlpha = 1;
        }
      }
      ctx.restore();
      cache = pts.map((p) => ({ e: p.e, x: u.valToPos(p.t, "x"), y: u.valToPos(p.e.vdot, "v") }));
    };

    const u: uPlot = new uPlot({
      width: root.clientWidth || 900, height,
      padding: [8, 4, 0, 0],
      legend: { show: false },
      cursor: { drag: { x: false, y: false }, points: { show: false }, y: false },
      scales: { x: { time: true }, v: { range: () => vRange } },
      bands,
      axes: [
        { stroke: muted, grid: { stroke: grid, width: 1 }, ticks: { show: false }, font, size: 26, ...dateAxis(56) },
        { scale: "v", stroke: muted, grid: { stroke: grid, width: 1 }, ticks: { show: false }, font, size: 40, label: "VDOT", labelFont: font, labelSize: 16 },
      ],
      series,
      hooks: { draw: [draw] },
    }, data as uPlot.AlignedData, root);
    plot.current = u;

    // nearest day in `daily` (binary search on the sorted timestamps)
    const nearest = (t: number) => {
      let a = 0, b = x.length - 1;
      while (b - a > 1) { const m = (a + b) >> 1; if (x[m] < t) a = m; else b = m; }
      return days[Math.abs(x[a] - t) <= Math.abs(x[b] - t) ? a : b].date;
    };
    const over = u.over;
    const onMove = (e: MouseEvent) => {
      const mx = e.offsetX, my = e.offsetY;
      let best: (typeof cache)[number] | null = null, bd = 64;
      for (const c of cache) {
        const d = (c.x - mx) ** 2 + (c.y - my) ** 2;
        if (d < bd || (best && d === bd && c.e.kind === "race")) { bd = d; best = c; }
      }
      const el = tip.current!;
      const day = nearest(u.posToVal(mx, "x"));
      const dd = days.find((d) => d.date === day);
      if (best) {
        const p = best.e;
        el.innerHTML = `<div class="t1"><b>${p.date}</b> ${p.kind === "race" ? "race" : esc(p.kind.replace(/_/g, " "))}, ${kmText(p.km)}</div>
          <div class="muted">VDOT ${p.vdot.toFixed(1)} · weight ${(p.weight ?? 0).toFixed(2)}</div><div class="muted">click to select this day</div>`;
      } else if (dd) {
        el.innerHTML = `<div class="t1"><b>${day}</b></div>
          <div class="muted">${PRED_LINES.map((l) => `${l.label} ${((dd as any)["vdot_" + l.k] as number).toFixed(1)}`).join(" · ")}</div>`;
      } else { el.style.display = "none"; return; }
      el.style.display = "block";
      const px = best ? best.x : mx, py = best ? best.y : my;
      const ow = over.clientWidth, tw = el.offsetWidth;
      el.style.left = (px + 12 + tw > ow ? over.offsetLeft + px - tw - 12 : over.offsetLeft + px + 12) + "px";
      el.style.top = Math.max(0, over.offsetTop + py - 20) + "px";
    };
    const onLeave = () => { tip.current!.style.display = "none"; };
    const onClick = (e: MouseEvent) => selectRef.current(nearest(u.posToVal(e.offsetX, "x")));
    over.addEventListener("mousemove", onMove);
    over.addEventListener("mouseleave", onLeave);
    over.addEventListener("click", onClick);
    const ro = new ResizeObserver(() => u.setSize({ width: root.clientWidth, height }));
    ro.observe(root);
    return () => {
      ro.disconnect();
      over.removeEventListener("mousemove", onMove); over.removeEventListener("mouseleave", onLeave); over.removeEventListener("click", onClick);
      u.destroy(); plot.current = null;
    };
  }, [doc, height, theme]);

  return (
    <div class="shape-chart pred-chart">
      <div class="plotwrap">
        <div ref={box} />
        <div class="chart-tip" ref={tip} />
      </div>
    </div>
  );
}
