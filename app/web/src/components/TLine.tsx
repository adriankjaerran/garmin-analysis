// A small uPlot line chart for the Together pages: one line per person in the person's colour, optional zero
// line, shaded x bands, category ticks (reps), a cursor callback (index) and an externally driven cursor (replay /
// map hover). The values at the cursor are shown by the caller (a legend row above the chart).
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { useEffect, useRef } from "preact/hooks";
import { cssVar } from "../lib/format";
import { useTheme } from "../lib/theme";
import { TIME_INCRS } from "../lib/chartAxes";

type N = number | null;
export interface TSeries { key: string; colour: string; values: N[]; width?: number; dash?: number[]; points?: boolean }

interface Props {
  x: number[];
  series: TSeries[];
  height?: number;
  xFmt?: (v: number) => string;
  yFmt?: (v: number) => string;
  /** category axis: tick label per x value (e.g. rep labels) */
  xLabels?: string[];
  xIncrs?: number[];
  zero?: boolean;
  /** y range always includes 0 */
  from0?: boolean;
  bands?: { a: number; b: number }[];
  onCursor?: (i: number | null) => void;
  /** drive the cursor from outside (x value); null hides it */
  cursorX?: number | null;
  /** y axis direction (-1: larger values lower) */
  dir?: 1 | -1;
  /** fixed y range */
  yRange?: [number, number];
  /** y axis width (px) for long tick labels ("1:20 behind") */
  ySize?: number;
}

export function TLine({ x, series, height = 200, xFmt = String, yFmt = (v) => String(Math.round(v)), xLabels, xIncrs, zero, from0, bands = [], onCursor, cursorX, dir = 1, yRange, ySize = 50 }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const cb = useRef(onCursor);
  cb.current = onCursor;
  const hovering = useRef(false);
  const theme = useTheme();

  useEffect(() => {
    const el = box.current!;
    el.innerHTML = "";
    const muted = cssVar("--text-muted"), hair = cssVar("--hairline"), font = `12px ${cssVar("--font")}`;
    let lo = Infinity, hi = -Infinity;
    for (const s of series) for (const v of s.values) if (v !== null && isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    if (from0 || zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    const pad = (hi - lo || 1) * 0.08;
    const yr: [number, number] = yRange ?? [from0 && lo >= 0 ? 0 : lo - pad, hi + pad];
    const cat = !!xLabels;
    const u = new uPlot({
      width: el.clientWidth || 500, height,
      padding: [8, 22, 0, 0],
      legend: { show: false },
      cursor: { drag: { x: false, y: false }, points: { show: true, size: 7 }, y: false },
      scales: { x: { time: false, ...(cat ? { range: () => [x[0] - 0.3, x[x.length - 1] + 0.3] as [number, number] } : {}) }, y: { range: () => yr, dir } },
      axes: [
        { stroke: muted, grid: { stroke: hair, width: 1 }, ticks: { show: false }, font, size: 24, space: cat ? 18 : 60,
          ...(cat ? { splits: () => x, values: (_u: uPlot, ts: number[]) => ts.map((t) => xLabels![x.indexOf(t)] ?? "") }
            : { values: (_u: uPlot, ts: number[]) => ts.map(xFmt), ...(xIncrs ? { incrs: xIncrs } : {}) }) },
        { stroke: muted, grid: { stroke: hair, width: 1 }, ticks: { show: false }, font, size: ySize, space: 28, values: (_u, ts) => ts.map(yFmt) },
      ],
      series: [{}, ...series.map((s) => ({
        stroke: s.colour, width: s.width ?? 2, dash: s.dash, spanGaps: false,
        points: { show: s.points ?? false, size: 6, fill: s.colour, stroke: s.colour },
      }))],
      hooks: {
        drawClear: [(u) => {
          const ctx = u.ctx, { top, height: h } = u.bbox;
          ctx.save();
          ctx.fillStyle = cssVar("--accent-surface");
          for (const b of bands) {
            const x0 = u.valToPos(b.a, "x", true), x1 = u.valToPos(b.b, "x", true);
            ctx.fillRect(x0, top, Math.max(1, x1 - x0), h);
          }
          if (zero) {
            const y0 = u.valToPos(0, "y", true);
            ctx.strokeStyle = muted; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
            ctx.beginPath(); ctx.moveTo(u.bbox.left, y0); ctx.lineTo(u.bbox.left + u.bbox.width, y0); ctx.stroke();
          }
          ctx.restore();
        }],
        // only real mouse moves report the cursor (not resizes or the externally driven cursor)
        setCursor: [(u) => { if (hovering.current) cb.current?.(u.cursor.idx ?? null); }],
      },
    }, [x, ...series.map((s) => s.values)] as uPlot.AlignedData, el);
    u.over.addEventListener("mouseenter", () => { hovering.current = true; });
    u.over.addEventListener("mouseleave", () => { hovering.current = false; cb.current?.(null); });
    plot.current = u;
    const ro = new ResizeObserver(() => { if (el.clientWidth) u.setSize({ width: el.clientWidth, height }); });
    ro.observe(el);
    return () => { ro.disconnect(); u.destroy(); plot.current = null; };
  }, [x, series, height, theme, JSON.stringify(bands), xLabels?.join("|"), dir, yRange?.join(","), ySize]);

  useEffect(() => {
    const u = plot.current;
    if (!u || cursorX === undefined) return;
    if (hovering.current) return;
    if (cursorX === null) u.setCursor({ left: -10, top: -10 });
    else u.setCursor({ left: u.valToPos(cursorX, "x"), top: u.bbox.height / devicePixelRatio / 2 });
  }, [cursorX]);

  return <div class="tline" ref={box} />;
}

export { TIME_INCRS };
