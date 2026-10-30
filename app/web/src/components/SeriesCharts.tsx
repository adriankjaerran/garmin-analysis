// Stacked, cursor-synced uPlot charts for one activity (pace/speed, HR, elevation, cadence, power).
// uPlot: ~50 kB, canvas, handles 10k+ points per series and has cursor sync + hooks built in, which
// makes chart <-> map hover linking a few lines instead of a hand-rolled SVG hit-test.
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { useEffect, useRef } from "preact/hooks";
import type { Series } from "../lib/api";
import { cssVar, dur } from "../lib/format";

export type PanelKey = "pace" | "speed" | "hr" | "alt" | "cad" | "power";
export interface Band { a: number; b: number; kind: "rep" | "pause" | "lap" }

const PANELS: Record<PanelKey, { label: string; color: string; fmt: (v: number) => string; height: number }> = {
  pace: { label: "Pace (min/km)", color: "--chart-1", fmt: (v) => dur(v), height: 150 },
  speed: { label: "Speed (km/h)", color: "--chart-1", fmt: (v) => v.toFixed(1), height: 150 },
  hr: { label: "Heart rate (bpm)", color: "--chart-2", fmt: (v) => String(Math.round(v)), height: 130 },
  alt: { label: "Elevation (m)", color: "--chart-3", fmt: (v) => String(Math.round(v)), height: 110 },
  cad: { label: "Cadence (spm)", color: "--chart-4", fmt: (v) => String(Math.round(v)), height: 100 },
  power: { label: "Power (W)", color: "--chart-4", fmt: (v) => String(Math.round(v)), height: 100 },
};

function values(s: Series, k: PanelKey): (number | null)[] | null {
  const sp = s.speed;
  if (k === "pace") return sp ? sp.map((v) => (v && v > 0.8 ? Math.min(1000 / v, 900) : null)) : null;
  if (k === "speed") return sp ? sp.map((v) => (v === null ? null : v * 3.6)) : null;
  const v = (s as any)[k] as (number | null)[] | undefined;
  if (!v || !v.some((x) => x !== null && x !== 0)) return null;
  return v;
}

function pct(xs: (number | null)[], q: number) {
  const a = xs.filter((x): x is number => x !== null).sort((x, y) => x - y);
  return a.length ? a[Math.floor(q * (a.length - 1))] : 0;
}

export const tLabel = (t: number) => (t >= 3600 ? dur(t).slice(0, -3) : `${Math.round(t / 60)} min`);

interface Props {
  series: Series;
  panels: PanelKey[];
  bands?: Band[];
  /** chart hover -> index into series.t (null on leave) */
  onHover?: (i: number | null) => void;
  /** parent gets a function to move the cursor from outside (map hover) */
  cursorRef?: { current: ((i: number | null) => void) | null };
  /** x tick / hover label (default: elapsed time) */
  xFmt?: (t: number) => string;
}

export function SeriesCharts({ series, panels, bands = [], onHover, cursorRef, xFmt }: Props) {
  const xf = xFmt ?? tLabel;
  const xh = xFmt ?? dur;
  const box = useRef<HTMLDivElement>(null);
  const hoverRef = useRef(onHover);
  hoverRef.current = onHover;

  useEffect(() => {
    const root = box.current!;
    root.innerHTML = "";
    const plots: uPlot[] = [];
    const sync = uPlot.sync("act-" + Math.random());
    const x = series.t;
    const width = root.clientWidth || 600;
    let external = false;
    const vals: HTMLElement[] = [];
    const active = panels.map((k) => [k, values(series, k)] as const).filter(([, v]) => v !== null) as [PanelKey, (number | null)[]][];

    active.forEach(([k, ys], pi) => {
      const P = PANELS[k];
      const wrap = document.createElement("div");
      wrap.className = "chart";
      const title = document.createElement("div");
      title.className = "title";
      title.innerHTML = `<span>${P.label}</span><span class="val"></span>`;
      vals.push(title.querySelector(".val") as HTMLElement);
      wrap.appendChild(title);
      root.appendChild(wrap);
      const lo = pct(ys, 0.01), hi = pct(ys, 0.99);
      const range: uPlot.Range.MinMax = k === "pace" ? [Math.max(120, lo - 15), Math.min(hi + 15, 720)] :
        k === "hr" ? [Math.max(40, lo - 5), hi + 5] :
        k === "alt" && hi - lo < 20 ? [(hi + lo) / 2 - 10, (hi + lo) / 2 + 10] : [lo - (hi - lo) * 0.05, hi + (hi - lo) * 0.05 || hi + 1];
      const color = cssVar(P.color);
      const isLast = pi === active.length - 1;
      const u = new uPlot({
        width, height: P.height + (isLast ? 20 : 0),
        padding: [6, 8, 0, 0],
        legend: { show: false },
        cursor: {
          sync: { key: sync.key, setSeries: false },
          drag: { x: false, y: false, setScale: false },
          points: { size: 7, fill: color },
          y: false,
        },
        scales: { x: { time: false }, y: { range: () => range, dir: k === "pace" ? -1 : 1 } },
        axes: [
          { show: isLast, stroke: cssVar("--text-muted"), grid: { stroke: cssVar("--hairline"), width: 1 }, ticks: { show: false },
            values: (_u, ts) => ts.map(xf), font: `12px ${cssVar("--font")}`, size: 22, space: 80 },
          { stroke: cssVar("--text-muted"), grid: { stroke: cssVar("--hairline"), width: 1 }, ticks: { show: false },
            values: (_u, ts) => ts.map((v) => P.fmt(v)), font: `12px ${cssVar("--font")}`, size: 48, space: 30 },
        ],
        series: [{}, { stroke: color, width: 1.25, points: { show: false }, spanGaps: false }],
        hooks: {
          drawClear: [(u) => {
            const ctx = u.ctx, { top, height } = u.bbox;
            for (const b of bands) {
              const x0 = u.valToPos(b.a, "x", true), x1 = u.valToPos(b.b, "x", true);
              ctx.fillStyle = b.kind === "rep" ? cssVar("--accent-surface") : b.kind === "pause" ? "rgba(138,145,153,0.16)" : "rgba(47,95,208,0.06)";
              ctx.fillRect(x0, top, Math.max(1, x1 - x0), height);
            }
          }],
          setCursor: [(u) => {
            const i = u.cursor.idx;
            active.forEach(([kk, yy], j) => {
              const v = i === null || i === undefined ? null : yy[i];
              vals[j].textContent = v === null || v === undefined ? "" : `${PANELS[kk].fmt(v)} at ${xh(x[i!])}`;
            });
            if (pi === 0 && !external) hoverRef.current?.(i ?? null);
          }],
        },
      }, [x, ys] as uPlot.AlignedData, wrap);
      plots.push(u);
    });

    if (cursorRef) cursorRef.current = (i) => {
      const u = plots[0];
      if (!u) return;
      external = true;
      const set = u.setCursor as (o: { left: number; top: number }, fire?: boolean, pub?: boolean) => void;
      if (i === null) set({ left: -10, top: -10 }, true, true);
      else set({ left: u.valToPos(x[i], "x"), top: 10 }, true, true);
      external = false;
    };
    const ro = new ResizeObserver(() => {
      const w = root.clientWidth;
      plots.forEach((u) => u.setSize({ width: w, height: u.height }));
    });
    ro.observe(root);
    return () => { ro.disconnect(); plots.forEach((u) => u.destroy()); if (cursorRef) cursorRef.current = null; };
  }, [series, panels.join(","), bands]);

  return <div class="charts" ref={box} />;
}
