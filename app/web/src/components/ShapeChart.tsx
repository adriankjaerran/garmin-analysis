// Weekly shape per discipline (mean line + ±1 sd band) over all years, overall % of best (line only) on a
// right axis, and every session's evidence as markers (race = diamond, hard = dot, bound = ring,
// implausible = cross). With `withHr`, the "with HR" model variant (fitness.json variants.hr) is drawn per
// discipline as a dashed line in the same colour, without a band. Hover a marker -> session details, click -> activity. Drag across the plot to zoom.
// Running sessions (interval / long evidence) are drawn on the run line (the distance curve at 10 km) at their
// `score_10k`: the score carried along that day's curve to 10 km, so the gap to the line is the session's gap to the
// curve at its own distance.
// With `races`, official races (fitness.json `races`) are drawn as large stars on the race session's marker: runs belong to
// run, ski to ski (hidden with that discipline); green = faster than the
// pre-race prediction, red = slower, neutral for ski / no prediction. Hover -> name, time, index; click -> activity
// (or the Races page without one).
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { useEffect, useRef } from "preact/hooks";
import { navigate } from "../lib/router";
import { cssVar, dur as fmtDur } from "../lib/format";
import { useTheme } from "../lib/theme";
import { dateAxis } from "../lib/chartAxes";
import { DISC_LABEL, KIND_LABEL, discColor, discOf, unitShort, type Disc, type FitRace, type FitSession, type FitnessDoc } from "../lib/fitness";
import { distLabel, pctText } from "../lib/races";

/** 5-point star path centred on (cx, cy) */
function star(ctx: CanvasRenderingContext2D, cx: number, cy: number, ro: number, ri: number) {
  ctx.beginPath();
  for (let i = 0; i < 10; i++) {
    const r = i % 2 ? ri : ro, a = -Math.PI / 2 + (i * Math.PI) / 5;
    ctx[i ? "lineTo" : "moveTo"](cx + r * Math.cos(a), cy + r * Math.sin(a));
  }
  ctx.closePath();
}
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
  /** also draw the "with HR" variant (dashed, no band) */
  withHr?: boolean;
  /** official race markers (vertical lines) */
  races?: boolean;
  onZoom: (from: string | null, to: string | null) => void;
  height?: number;
}

export function ShapeChart({ doc, discs, overall, bounds, from, to, highlight, withHr = false, races = true, onZoom, height = 380 }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const tip = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const hl = useRef(highlight);
  const zoomRef = useRef(onZoom);
  zoomRef.current = onZoom;
  const theme = useTheme();

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
    const HD = withHr ? doc.variants?.hr?.disciplines : undefined;
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
      if (HD?.[k]) {
        const hm: (number | null)[] = xs.map(() => null);
        for (const w of HD[k].weekly) {
          const i = pos.get(w.date);
          if (i === undefined) continue;
          hm[i] = w.mean;
          lo = Math.min(lo, w.mean); hi = Math.max(hi, w.mean);
        }
        data.push(hm);
        series.push({ label: k + " (with HR)", stroke: c, width: 1.5, dash: [5, 4], scale: "v", points: { show: false } });
      }
    }
    // overall (% of best): one line on its own right-hand scale
    let pctRange: [number, number] | null = null;
    if (overall && D.overall) {
      const m: (number | null)[] = xs.map(() => null);
      // the overall % jumps while disciplines join the model (first weeks); start it once all have data
      const start = Object.entries(D).filter(([k, d]) => k !== "overall" && d?.weekly?.length).map(([, d]) => d.weekly[0].date).sort().pop() ?? "";
      for (const w of D.overall.weekly) { const i = pos.get(w.date); if (i !== undefined && w.date >= start) m[i] = w.mean; }
      const vals = m.filter((v) => v !== null) as number[];
      if (vals.length) {
        pctRange = [Math.floor(Math.min(...vals)) - 4, Math.min(102, Math.ceil(Math.max(...vals) + 2))];
        data.push(m);
        series.push({ label: "overall", stroke: discColor("overall"), width: 1.5, scale: "pct", points: { show: false } });
      }
    }
    const pts: (FitSession & { t: number; y: number })[] = doc.sessions
      .filter((s) => discs.includes(discOf(s.discipline)) && inr(s.date) && (bounds || s.kind !== "bound"))
      .map((s) => ({ ...s, t: ts(s.date), y: s.discipline !== "ski" && s.score_10k != null ? s.score_10k : s.score }));
    // races / hard sessions widen the y range, but at most 3 VDOT beyond the ±1 sd bands; markers further
    // out (and lower bounds) are clipped at the edge, so the trend lines keep the height
    const wlo = lo, whi = hi;
    for (const p of pts) if (p.kind === "race" || p.kind === "hard") { lo = Math.min(lo, Math.max(p.y, wlo - 3)); hi = Math.max(hi, Math.min(p.y, whi + 3)); }
    if (!isFinite(lo)) { lo = 40; hi = 60; }
    // official races: run races on the run line, ski races on ski
    const raceDisc = (r: FitRace): Disc => (r.sport === "ski" ? "ski" : "run");
    const rcs: (FitRace & { t: number; y: number })[] = races ? (doc.races ?? [])
      .filter((r) => discs.includes(raceDisc(r)) && inr(r.date) && r.date >= xs[0] && r.date <= xs[xs.length - 1])
      .map((r) => {
        const sess = r.activity_id ? pts.find((p) => String(p.id) === String(r.activity_id)) : undefined;
        let y = sess?.y;
        if (y === undefined) {   // no scored session (e.g. no recording): on the discipline's line that week
          const wk = (D as any)[raceDisc(r)]?.weekly as { date: string; mean: number }[] | undefined;
          const w = wk?.filter((x) => x.date <= r.date).pop() ?? wk?.[0];
          y = w?.mean ?? NaN;
        }
        return { ...r, t: ts(r.date), y };
      }).filter((r) => isFinite(r.y)) : [];
    const raceIds = new Set(rcs.map((r) => String(r.activity_id)));
    const raceColor = (r: FitRace) => r.pct === undefined ? discColor(raceDisc(r)) : r.pct >= 0 ? cssVar("--state-good") : cssVar("--state-duplicate");
    let rcache: { r: FitRace; x: number; y: number }[] = [];
    const vRange: [number, number] = [Math.floor(lo - 1.5), Math.ceil(hi + 1.5)];

    const muted = cssVar("--text-muted"), grid = cssVar("--hairline"), font = `12px ${cssVar("--font")}`;
    const dpr = devicePixelRatio || 1;
    const ptsXY = () => pts.map((p) => ({ p, x: u.valToPos(p.t, "x"), y: u.valToPos(p.y, "v") }));
    let cache: { p: FitSession; x: number; y: number }[] = [];

    const drawPts = (u: uPlot) => {
      const ctx = u.ctx, { left, top, width, height } = u.bbox;
      ctx.save();
      ctx.beginPath(); ctx.rect(left, top, width, height); ctx.clip();
      const order: Record<string, number> = { bound: 0, implausible: 1, hard: 2, race: 3 };
      for (const p of [...pts].sort((a, b) => order[a.kind] - order[b.kind])) {
        if (raceIds.has(String(p.id))) continue;
        const cx = u.valToPos(p.t, "x", true), cy = u.valToPos(p.y, "v", true);
        if (cy < top - 10 || cy > top + height + 10) continue;
        const c = discColor(p.discipline), w = p.weight ?? 0;
        ctx.lineWidth = 1.25 * dpr;
        if (p.kind === "race") {
          const r = 5.5 * dpr;
          ctx.beginPath(); ctx.moveTo(cx, cy - r); ctx.lineTo(cx + r, cy); ctx.lineTo(cx, cy + r); ctx.lineTo(cx - r, cy); ctx.closePath();
          ctx.fillStyle = c; ctx.fill(); ctx.strokeStyle = cssVar("--surface"); ctx.stroke();
        } else if (p.kind === "hard") {
          ctx.beginPath(); ctx.arc(cx, cy, 3.5 * dpr, 0, 2 * Math.PI);
          ctx.fillStyle = rgba(c, 0.3 + 0.7 * w); ctx.fill(); ctx.strokeStyle = cssVar("--surface"); ctx.stroke();
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
      // official races on top: a star at the race session's score (its 10k equivalent for runs), else on the line
      rcache = [];
      for (const r of rcs) {
        const cx = u.valToPos(r.t, "x", true);
        const cy = u.valToPos(Math.min(Math.max(r.y, vRange[0]), vRange[1]), "v", true);
        star(ctx, cx, cy, 8.5 * dpr, 3.8 * dpr);
        if (r.pct === undefined) {   // ski / no prediction: hollow star in the discipline colour
          ctx.fillStyle = cssVar("--surface"); ctx.fill();
          ctx.lineWidth = 2 * dpr; ctx.strokeStyle = raceColor(r); ctx.stroke();
        } else {
          ctx.fillStyle = raceColor(r); ctx.fill();
          ctx.lineWidth = 1.5 * dpr; ctx.strokeStyle = cssVar("--surface"); ctx.stroke();
        }
        rcache.push({ r, x: cx / dpr - left / dpr, y: cy / dpr - top / dpr });
      }
      ctx.restore();
      cache = ptsXY().filter((c) => !raceIds.has(String(c.p.id)));
    };

    const width = root.clientWidth || 900;
    const u: uPlot = new uPlot({
      width, height,
      padding: [8, 4, 0, 0],
      legend: { show: false },
      cursor: { drag: { x: true, y: false, setScale: false }, points: { show: false }, y: false },
      select: { show: true, left: 0, top: 0, width: 0, height: 0 },
      scales: { x: { time: true }, v: { range: () => vRange }, ...(pctRange ? { pct: { range: () => pctRange! } } : {}) },
      bands: bandsCfg,
      axes: [
        { stroke: muted, grid: { stroke: grid, width: 1 }, ticks: { show: false }, font, size: 26, ...dateAxis(56) },
        { scale: "v", stroke: muted, grid: { stroke: grid, width: 1 }, ticks: { show: false }, font, size: 48, label: "VDOT", labelFont: font, labelSize: 16 },
        ...(pctRange ? [{ scale: "pct", side: 1, stroke: muted, grid: { show: false }, ticks: { show: false }, font, size: 44,
          label: "% of best", labelFont: font, labelSize: 16 } as uPlot.Axis] : []),
      ],
      series,
      hooks: {
        draw: [drawPts],
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

    // marker hover / click (uPlot's cursor only knows the weekly grid)
    const over = u.over;
    let hit: FitSession | null = null, hitRace: FitRace | null = null;
    const onMove = (e: MouseEvent) => {
      const mx = e.offsetX, my = e.offsetY;
      // race stars first (bigger target), then session markers
      let rb: (typeof rcache)[number] | null = null, rd = 121;
      for (const c of rcache) { const d = (c.x - mx) ** 2 + (c.y - my) ** 2; if (d <= rd) { rd = d; rb = c; } }
      let best: (typeof cache)[number] | null = null, bd = 64;
      if (!rb) for (const c of cache) {
        const d = (c.x - mx) ** 2 + (c.y - my) ** 2;
        if (d < bd || (best && d === bd && c.p.kind !== "bound")) { bd = d; best = c; }
      }
      hit = best?.p ?? null;
      hitRace = rb?.r ?? null;
      const el = tip.current!;
      if (!best) {
        over.style.cursor = hitRace ? "pointer" : "crosshair";
        if (!rb) { el.style.display = "none"; return; }
        const r = rb.r;
        el.innerHTML = `<div class="t1"><i class="dot" style="background:${raceColor(r)}"></i><b>${r.date}</b> ${esc(r.name)}</div>
          <div>${distLabel(r.km)} ${r.sport === "ski" ? "ski" : "run"} · official <b>${esc(r.official ?? "")}</b>${r.pct !== undefined ? ` · <span style="color:${raceColor(r)}">${pctText(r.pct)}</span> vs predicted` : ""}</div>
          ${r.predicted_s !== undefined ? `<div class="muted">predicted from data before race day: ${esc(fmtDur(r.predicted_s))}</div>` : ""}
          <div class="muted">click to open ${r.activity_id ? "the activity" : "Races"}</div>`;
        el.style.display = "block";
        const ow = over.clientWidth, tw = el.offsetWidth;
        el.style.left = (rb.x + 12 + tw > ow ? over.offsetLeft + rb.x - tw - 12 : over.offsetLeft + rb.x + 12) + "px";
        el.style.top = Math.max(0, over.offsetTop + rb.y - 20) + "px";
        return;
      }
      over.style.cursor = "pointer";
      const p = best.p;
      const delta = p.shape_at !== null && p.kind !== "implausible" ? p.score - p.shape_at : null;
      el.innerHTML = `<div class="t1"><i class="dot" style="background:${discColor(p.discipline)}"></i><b>${p.date}</b> ${esc(p.name ?? "")}</div>
        <div>${esc(p.explain)}</div>
        <div class="muted">${DISC_LABEL[p.discipline]} · ${KIND_LABEL[p.kind]} · score ${p.score.toFixed(1)} ${unitShort(p.discipline)}${p.shape_at !== null ? ` · shape that day${p.discipline !== "ski" && p.km ? ` at ${p.km.toFixed(1)} km` : ""} ${p.shape_at.toFixed(1)}${delta !== null ? ` (${delta >= 0 ? "+" : "−"}${Math.abs(delta).toFixed(1)})` : ""}` : ""}${p.weight !== null ? ` · weight ${p.weight.toFixed(2)}` : ""}</div>
        ${p.discipline !== "ski" && p.score_10k != null ? `<div class="muted">drawn at its 10k equivalent ${p.score_10k.toFixed(1)}</div>` : ""}
        <div class="muted">click to open</div>`;
      el.style.display = "block";
      const ow = over.clientWidth, left = over.offsetLeft + best.x + 12, tw = el.offsetWidth;
      el.style.left = (best.x + 12 + tw > ow ? over.offsetLeft + best.x - tw - 12 : left) + "px";
      el.style.top = Math.max(0, over.offsetTop + best.y - 20) + "px";
    };
    const onLeave = () => { tip.current!.style.display = "none"; hit = null; hitRace = null; };
    const onClick = () => {
      if (hit) navigate(`/activities/${hit.id}`);
      else if (hitRace) navigate(hitRace.activity_id ? `/activities/${hitRace.activity_id}` : "/races");
    };
    const onDbl = () => zoomRef.current(null, null);
    over.addEventListener("mousemove", onMove);
    over.addEventListener("mouseleave", onLeave);
    over.addEventListener("click", onClick);
    over.addEventListener("dblclick", onDbl);
    const ro = new ResizeObserver(() => { u.setSize({ width: root.clientWidth, height }); });
    ro.observe(root);
    return () => {
      ro.disconnect();
      over.removeEventListener("mousemove", onMove); over.removeEventListener("mouseleave", onLeave);
      over.removeEventListener("click", onClick); over.removeEventListener("dblclick", onDbl);
      u.destroy(); plot.current = null;
    };
  }, [doc, discs.join(","), overall, bounds, withHr, races, from, to, height, theme]);

  return (
    <div class="shape-chart">
      <div class="plotwrap">
        <div ref={box} />
        <div class="chart-tip" ref={tip} />
      </div>
    </div>
  );
}
