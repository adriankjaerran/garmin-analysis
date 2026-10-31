// Shared uPlot axis helpers: nice m:ss steps, robust pace ranges, calendar date ticks, label thinning.
import type uPlot from "uplot";

type N = number | null | undefined;

/** tick steps for elapsed / clock time axes in seconds (m:ss and h:mm friendly) */
export const TIME_INCRS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1200, 1800, 3600, 7200, 10800, 21600, 43200, 86400];
/** tick steps for pace axes in seconds per km: 0:05 … 5:00 */
export const PACE_INCRS = [5, 10, 15, 30, 60, 120, 300, 600];
/** distance axes in metres */
export const DIST_INCRS = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000];

function q(a: number[], p: number) {
  return a[Math.min(a.length - 1, Math.max(0, Math.round(p * (a.length - 1))))];
}

/**
 * Robust y range for a pace axis (seconds per km, slow = large).
 * Only "moving" samples count: speed at least `moveFrac` of the median speed (drops stops, walking
 * breaks and GPS dropouts), then [p2, p92] of those plus a small pad (≥ `minPad` s): the slow tail (recovery
 * jogs, climbs) may be clipped so the fast part keeps its resolution.
 */
export function paceRange(vals: Iterable<N>, opts: { lo?: number; hi?: number; moveFrac?: number; minPad?: number } = {}): [number, number] | null {
  const { lo = 0.02, hi = 0.92, moveFrac = 0.6, minPad = 10 } = opts;
  const a: number[] = [];
  for (const v of vals) if (v !== null && v !== undefined && isFinite(v) && v > 0) a.push(v);
  if (a.length < 3) return null;
  a.sort((x, y) => x - y);
  const med = q(a, 0.5);                         // median pace == 1 / median speed
  const m = a.filter((p) => p <= med / moveFrac); // speed >= moveFrac * median speed
  const L = q(m, lo), H = q(m, hi);
  const pad = Math.max(minPad, (H - L) * 0.08);
  return [Math.max(60, L - pad), H + pad];
}

/** Robust y range for a speed axis: like paceRange, samples below `moveFrac` × median speed are ignored. */
export function speedRange(vals: Iterable<N>, opts: { lo?: number; hi?: number; moveFrac?: number } = {}): [number, number] | null {
  const { lo = 0.02, hi = 0.98, moveFrac = 0.5 } = opts;
  const a: number[] = [];
  for (const v of vals) if (v !== null && v !== undefined && isFinite(v) && v > 0) a.push(v);
  if (a.length < 3) return null;
  a.sort((x, y) => x - y);
  const m = a.filter((v) => v >= q(a, 0.5) * moveFrac);
  const L = q(m, lo), H = q(m, hi), pad = (H - L) * 0.08 || H * 0.05 || 1;
  return [Math.max(0, L - pad), H + pad];
}

/** percentile range for any axis */
export function pctRange(vals: Iterable<N>, lo = 0.01, hi = 0.99, pad = 0.05): [number, number] | null {
  const a: number[] = [];
  for (const v of vals) if (v !== null && v !== undefined && isFinite(v)) a.push(v);
  if (!a.length) return null;
  a.sort((x, y) => x - y);
  const L = q(a, lo), H = q(a, hi);
  const d = H - L || Math.abs(H) * 0.1 || 1;
  return [L - d * pad, H + d * pad];
}

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * Calendar ticks for a uPlot time axis (x in unix seconds). Steps: 1/2 weeks, 1/2/3/6 months, 1/2 years,
 * chosen so labels are ≥ `space` px apart. January ticks read as the year ("2025"), other months
 * "Jun"; if no January is visible the first tick also carries the year ("Jun 2024").
 */
export function dateAxis(space = 56): Pick<uPlot.Axis, "splits" | "values" | "space"> {
  const splits = (u: uPlot, _ai: number, min: number, max: number) => {
    const px = u.bbox.width / (devicePixelRatio || 1) || 800;
    const days = (max - min) / 86400;
    const maxTicks = Math.max(2, Math.floor(px / space));
    const out: number[] = [];
    if (days / 7 <= maxTicks || days < 45) {
      const step = days / 7 <= maxTicks ? 7 : 14;
      const d = new Date(min * 1000);
      d.setUTCHours(0, 0, 0, 0);
      while (d.getUTCDay() !== 1) d.setUTCDate(d.getUTCDate() + 1);   // Mondays
      for (let t = d.getTime() / 1000; t <= max; t += step * 86400) out.push(t);
      return out;
    }
    const months = days / 30.44;
    const step = [1, 2, 3, 6, 12, 24].find((s) => months / s <= maxTicks) ?? 48;
    const d0 = new Date(min * 1000);
    let y = d0.getUTCFullYear(), m = d0.getUTCMonth();
    // align to the step (months 0, step, 2·step … of the year)
    while (m % Math.min(step, 12) !== 0 || (step > 12 && y % (step / 12) !== 0)) { m++; if (m > 11) { m = 0; y++; } }
    for (;;) {
      const t = Date.UTC(y, m, 1) / 1000;
      if (t > max) break;
      if (t >= min) out.push(t);
      m += step; while (m > 11) { m -= 12; y++; }
    }
    return out;
  };
  const values = (_u: uPlot, ts: number[]) => {
    const ds = ts.map((t) => new Date(t * 1000));
    const weekly = ts.length > 1 && ts[1] - ts[0] < 20 * 86400;
    const anyJan = ds.some((d) => d.getUTCMonth() === 0 && d.getUTCDate() === 1);
    return ds.map((d, i) => {
      if (weekly) return `${d.getUTCDate()} ${MON[d.getUTCMonth()]}${i === 0 || (d.getUTCMonth() === 0 && d.getUTCDate() <= 7) ? " " + d.getUTCFullYear() : ""}`;
      if (d.getUTCMonth() === 0) return String(d.getUTCFullYear());
      return i === 0 && !anyJan ? `${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}` : MON[d.getUTCMonth()];
    });
  };
  return { splits: splits as uPlot.Axis.Splits, values, space };
}

/**
 * Category ticks (e.g. "Rep 3" at each slot start) thinned so labels don't overlap. Primary labels
 * (matching `primary`) are placed first, left to right with a minimum gap; secondary ones ("Rep 2+3",
 * "1:00" inside breaks) only fill free space. When even primary labels are cramped, `compact` shortens
 * them ("Rep 12" -> "R12").
 */
export function categoryAxis(ticks: [number, string][], primary: RegExp = /^(Rep|B)\s?\d+$/): Pick<uPlot.Axis, "splits" | "values"> {
  const charPx = 7, gap = 10;
  let shown = new Map<number, string>();
  const compact = (s: string) => s.replace(/^Rep\s?/, "R");
  const splits = (u: uPlot, _ai: number, min: number, max: number) => {
    const px = u.bbox.width / (devicePixelRatio || 1) || 600;
    const pos = (x: number) => ((x - min) / (max - min || 1)) * px;
    const vis = ticks.filter((t) => t[0] >= min && t[0] <= max);
    const prim = vis.filter((t) => primary.test(t[1]));
    // compact when the average primary slot is narrower than its full label
    const avg = prim.length > 1 ? (pos(prim[prim.length - 1][0]) - pos(prim[0][0])) / (prim.length - 1) : Infinity;
    const longest = Math.max(0, ...prim.map((t) => t[1].length));
    const useCompact = avg < longest * charPx + gap;
    const lab = (s: string) => (useCompact ? compact(s) : s);
    const placed: { a: number; b: number }[] = [];
    const fits = (x: number, s: string) => {
      const w = lab(s).length * charPx, a = pos(x) - w / 2 - gap / 2, b = pos(x) + w / 2 + gap / 2;
      if (a < -w / 2 || b > px + w / 2) return false;
      return !placed.some((p) => a < p.b && b > p.a) && (placed.push({ a, b }), true);
    };
    shown = new Map();
    for (const group of [prim, vis.filter((t) => !primary.test(t[1]))]) {
      for (const [x, s] of group) if (fits(x, s)) shown.set(x, lab(s));
    }
    return [...shown.keys()].sort((a, b) => a - b);
  };
  const values = (_u: uPlot, ts: number[]) => ts.map((x) => shown.get(x) ?? "");
  return { splits: splits as uPlot.Axis.Splits, values };
}
