// Stacked, cursor-synced uPlot charts for several sessions on one shared x grid (Compare page).
// Reference drawn dark on top, the focused session redrawn thick on top of everything; with many
// sessions the others are grey and the interquartile band (p25-p75) + median line are shaded.
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { useEffect, useRef } from "preact/hooks";
import { cssVar } from "../lib/format";
import { PACE_INCRS, TIME_INCRS, categoryAxis, paceRange, speedRange } from "../lib/chartAxes";

type N = number | null;
export interface CPanel {
  key: string;
  label: string;
  height: number;
  fmt: (v: number) => string;
  /** per session id, aligned to X */
  ys: Record<string, N[]>;
  dir?: 1 | -1;
  band?: { lo: N[]; hi: N[] } | null;
  median?: N[] | null;
  zero?: boolean;
  /** one line only (e.g. route elevation) */
  single?: N[];
  /** only these grid points set the y range (e.g. reps, not breaks) */
  rangeMask?: boolean[] | null;
  /** y axis kind: "pace" (s/km, robust moving range, m:ss steps), "time" (m:ss steps), "num" (default).
   *  "speed" = robust moving range. Without it, a "speed" panel counts as pace when reversed (dir -1), else as
   *  speed, and a fmt that prints m:ss as time. */
  axis?: "pace" | "speed" | "time" | "num";
}

interface Props {
  X: number[];
  panels: CPanel[];
  /** draw order (reference last = on top) */
  order: string[];
  colors: Record<string, string>;
  refId: string | null;
  focus: string | null;
  many: boolean;
  spans?: { a: number; b: number }[];
  ticks?: [number, string][] | null;
  xFmt: (x: number) => string;
  /** nice x tick steps (e.g. whole minutes / km) */
  xIncrs?: number[];
  onCursor?: (i: number | null) => void;
  /** hover over a line picks the nearest session (y distance at the cursor) */
  onPick?: (id: string | null) => void;
  /** label per session for the panel titles */
  names?: Record<string, string>;
}

function range(vals: N[][], pad = 0.05): [number, number] {
  const a: number[] = [];
  for (const v of vals) for (const x of v) if (x !== null && isFinite(x)) a.push(x);
  if (!a.length) return [0, 1];
  a.sort((x, y) => x - y);
  const lo = a[Math.floor(0.005 * (a.length - 1))], hi = a[Math.ceil(0.995 * (a.length - 1))];
  const d = hi - lo || Math.abs(hi) * 0.1 || 1;
  return [lo - d * pad, hi + d * pad];
}

export function CompareCharts({ X, panels, order, colors, refId, focus, many, spans = [], ticks, xFmt, xIncrs, onCursor, onPick, names = {} }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const plots = useRef<uPlot[]>([]);
  const titleFns = useRef<((i: number | null) => void)[]>([]);
  const st = useRef({ focus, colors, onCursor, onPick, names });
  st.current = { focus, colors, onCursor, onPick, names };

  useEffect(() => {
    const root = box.current!;
    root.innerHTML = "";
    const sync = uPlot.sync("cmp-" + Math.random());
    const width = root.clientWidth || 600;
    const list: uPlot[] = [];
    let zooming = false;
    const titles: ((i: number | null) => void)[] = [];
    const vals: HTMLElement[] = [];
    const muted = cssVar("--text-muted"), hair = cssVar("--hairline"), font = `12px ${cssVar("--font")}`;

    panels.forEach((P, pi) => {
      const ids = P.single ? ["_single"] : order.filter((id) => P.ys[id]);
      const data: N[][] = P.single ? [P.single] : ids.map((id) => P.ys[id]);
      if (P.median) { data.push(P.median); }
      const wrap = document.createElement("div");
      wrap.className = "chart";
      const title = document.createElement("div");
      title.className = "title";
      title.innerHTML = `<span>${P.label}</span><span class="val"></span>`;
      vals.push(title.querySelector(".val") as HTMLElement);
      wrap.appendChild(title);
      root.appendChild(wrap);
      const rd = P.band ? [...data, P.band.lo, P.band.hi] : data;
      const masked = P.rangeMask ? rd.map((c) => c.map((v, i) => (P.rangeMask![i] ? v : null))) : rd;
      const kind = P.axis ?? (P.key === "speed" ? (P.dir === -1 ? "pace" : "speed") : /\d:\d\d/.test(P.fmt(61)) ? "time" : "num");
      // pace / speed: robust range of the moving samples (stops, walks and dropouts would stretch it to 15:00 / 0)
      const mv = P.rangeMask ? { moveFrac: 0.5, hi: 0.98 } : {};
      const [lo, hi] = (kind === "pace" && paceRange(masked.flat(), mv)) || (kind === "speed" && speedRange(masked.flat(), mv)) || range(masked);
      const yr: [number, number] = P.zero ? [Math.min(lo, 0), Math.max(hi, 0)] : [lo, hi];
      const isLast = pi === panels.length - 1;
      const setTitle = (i: number | null) => {
        if (i === null) { vals[pi].textContent = ""; return; }
        const f = st.current.focus ?? refId;
        const v = P.single ? P.single[i] : f && P.ys[f] ? P.ys[f][i] : null;
        vals[pi].textContent = v === null || v === undefined ? "" : `${P.fmt(v)}${P.single || !f ? "" : " · " + (st.current.names[f] ?? f)}`;
      };
      titles.push(setTitle);
      const series: uPlot.Series[] = [{}];
      ids.forEach((id) => series.push({
        stroke: P.single ? cssVar("--chart-3") : () => st.current.colors[id] ?? "#888",
        width: P.single ? 1.5 : id === refId ? 2 : many ? 1 : 1.4,
        alpha: !P.single && many && id !== refId ? 0.75 : 1,
        points: { show: false }, spanGaps: false,
      } as uPlot.Series));
      if (P.median) series.push({ stroke: REF_MED, width: 1.5, dash: [5, 4], points: { show: false }, spanGaps: false });
      const u = new uPlot({
        width, height: P.height + (isLast ? 22 : 0),
        padding: [6, 8, 0, 0],
        legend: { show: false },
        cursor: {
          sync: { key: sync.key, setSeries: false },
          drag: { x: true, y: false, setScale: true },
          points: { show: false },
          y: false,
        },
        scales: { x: { time: false }, y: { range: () => yr, dir: P.dir ?? 1 } },
        axes: [
          {
            show: isLast, stroke: muted, grid: { stroke: hair, width: 1 }, ticks: { show: false }, font, size: 22, space: 70,
            ...(ticks ? categoryAxis(ticks) : { values: (_u: uPlot, ts: number[]) => ts.map(xFmt), ...(xIncrs ? { incrs: xIncrs } : {}) }),
          },
          { stroke: muted, grid: { stroke: hair, width: 1 }, ticks: { show: false }, values: (_u, ts) => ts.map((v) => P.fmt(v)), font, size: 58, space: 28,
            ...(kind === "pace" ? { incrs: PACE_INCRS } : kind === "time" ? { incrs: TIME_INCRS } : {}) },
        ],
        series,
        hooks: {
          // drag-zoom on one chart zooms all
          setScale: [(src, k) => {
            if (k !== "x" || zooming) return;
            zooming = true;
            const { min, max } = src.scales.x;
            list.forEach((p) => { if (p !== src) p.setScale("x", { min: min!, max: max! }); });
            zooming = false;
          }],
          drawClear: [(u) => {
            const ctx = u.ctx, { top, height } = u.bbox;
            ctx.save();
            ctx.fillStyle = cssVar("--accent-surface");
            for (const s of spans) {
              const x0 = u.valToPos(s.a, "x", true), x1 = u.valToPos(s.b, "x", true);
              ctx.fillRect(x0, top, Math.max(1, x1 - x0), height);
            }
            // interquartile band
            if (P.band) {
              ctx.fillStyle = "rgba(103,112,122,0.16)";
              const { lo, hi } = P.band;
              let open = false;
              const flush = (a: number, b: number) => {
                ctx.beginPath();
                for (let i = a; i <= b; i++) ctx.lineTo(u.valToPos(X[i], "x", true), u.valToPos(hi[i]!, "y", true));
                for (let i = b; i >= a; i--) ctx.lineTo(u.valToPos(X[i], "x", true), u.valToPos(lo[i]!, "y", true));
                ctx.closePath(); ctx.fill();
              };
              let s0 = 0;
              for (let i = 0; i <= X.length; i++) {
                const ok = i < X.length && lo[i] !== null && hi[i] !== null;
                if (ok && !open) { open = true; s0 = i; } else if (!ok && open) { open = false; if (i - 1 > s0) flush(s0, i - 1); }
              }
            }
            if (P.zero) {
              const y0 = u.valToPos(0, "y", true);
              ctx.strokeStyle = cssVar("--text-muted"); ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
              ctx.beginPath(); ctx.moveTo(u.bbox.left, y0); ctx.lineTo(u.bbox.left + u.bbox.width, y0); ctx.stroke();
            }
            ctx.restore();
          }],
          draw: [(u) => {
            // focused session on top, thick
            const f = st.current.focus;
            if (!f || P.single || !P.ys[f]) return;
            const ys = P.ys[f], ctx = u.ctx;
            ctx.save();
            ctx.beginPath();
            ctx.rect(u.bbox.left, u.bbox.top, u.bbox.width, u.bbox.height); ctx.clip();
            ctx.setLineDash([]); ctx.strokeStyle = "#fff"; ctx.lineWidth = 5 * devicePixelRatio; ctx.lineJoin = "round";
            const path = () => {
              ctx.beginPath();
              let pen = false;
              for (let i = 0; i < X.length; i++) {
                const v = ys[i];
                if (v === null) { pen = false; continue; }
                const x = u.valToPos(X[i], "x", true), y = u.valToPos(v, "y", true);
                if (pen) ctx.lineTo(x, y); else ctx.moveTo(x, y);
                pen = true;
              }
            };
            path(); ctx.globalAlpha = 0.8; ctx.stroke();
            ctx.globalAlpha = 1;
            ctx.strokeStyle = many && f !== refId ? FOCUS_RED : st.current.colors[f]; ctx.lineWidth = 2.4 * devicePixelRatio;
            path(); ctx.stroke();
            ctx.restore();
          }],
          setCursor: [(u) => {
            const i = u.cursor.idx;
            setTitle(i ?? null);
            if (pi === 0) st.current.onCursor?.(i ?? null);
          }],
        },
      }, [X, ...data] as uPlot.AlignedData, wrap);
      // pick the nearest line under the mouse
      u.over.addEventListener("mousemove", (e) => {
        if (P.single || !st.current.onPick) return;
        const i = u.cursor.idx;
        if (i === null || i === undefined) return;
        const yv = u.posToVal(e.offsetY, "y");
        let best: string | null = null, bd = Infinity;
        ids.forEach((id) => {
          const v = P.ys[id][i];
          if (v === null) return;
          const d = Math.abs(u.valToPos(v, "y") - u.valToPos(yv, "y"));
          if (d < bd) { bd = d; best = id; }
        });
        st.current.onPick(bd < 12 ? best : null);
      });
      u.over.addEventListener("mouseleave", () => st.current.onPick?.(null));
      // double click resets a drag zoom on all charts
      u.over.addEventListener("dblclick", () => list.forEach((p) => p.setScale("x", { min: X[0], max: X[X.length - 1] })));
      list.push(u);
    });
    plots.current = list;
    titleFns.current = titles;
    const ro = new ResizeObserver(() => {
      const w = root.clientWidth;
      list.forEach((u) => u.setSize({ width: w, height: u.height }));
    });
    ro.observe(root);
    return () => { ro.disconnect(); list.forEach((u) => u.destroy()); plots.current = []; };
  }, [X, panels, order.join(","), spans, ticks]);

  // focus / colours change: redraw only
  useEffect(() => {
    plots.current.forEach((u) => u.redraw(false));
    const i = plots.current[0]?.cursor.idx;
    titleFns.current.forEach((f) => f(i ?? null));
  }, [focus, colors]);

  return <div class="charts cmp-charts" ref={box} />;
}

const REF_MED = "#1d2125";
const FOCUS_RED = "#c43c3c";
