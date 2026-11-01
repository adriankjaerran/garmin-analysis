import type { Sport } from "./api";

export const SPORT_LABEL: Record<string, string> = { run: "Run", ski: "Ski", hike: "Hike", bike: "Bike", swim: "Swim", other: "Other" };
export const SPORTS: Sport[] = ["run", "ski", "hike", "bike", "swim", "other"];

const cssCache: Record<string, string> = {};
export function cssVar(name: string): string {
  if (!cssCache[name]) cssCache[name] = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
  return cssCache[name];
}
export function clearCssCache() { for (const k in cssCache) delete cssCache[k]; }
export const sportColor = (s: string) => cssVar(`--sport-${SPORT_LABEL[s] ? s : "other"}`);

/** seconds -> "m:ss" / "h:mm:ss"; negative values get a leading minus sign ("−1:06:42") */
export function dur(s: number | null | undefined): string {
  if (s === null || s === undefined || !isFinite(s)) return "—";
  s = Math.round(s);
  const neg = s < 0;
  s = Math.abs(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  const t = h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`;
  return neg ? "−" + t : t;
}
/** signed duration: "+0:34", "−1:02", "±0:00" */
export function sdur(s: number | null | undefined): string {
  if (s === null || s === undefined || !isFinite(s)) return "—";
  const r = Math.round(s);
  return r === 0 ? "±" + dur(0) : (r > 0 ? "+" : "−") + dur(Math.abs(r));
}
/** m/s -> "m:ss" per 100 m (swim pace) */
export function pace100(ms: number | null | undefined): string {
  if (!ms || ms <= 0.1) return "—";
  return dur(100 / ms);
}
/** m/s -> "m:ss" per km */
export function pace(ms: number | null | undefined): string {
  if (!ms || ms <= 0.3) return "—";
  return dur(1000 / ms);
}
export const kmh = (ms: number | null | undefined) => (ms ? (ms * 3.6).toFixed(1) : "—");
export const num = (v: number | null | undefined, d = 0) =>
  v === null || v === undefined || isNaN(v) ? "—" : v.toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d });
export const km = (v: number | null | undefined) => (v ? v.toFixed(v >= 100 ? 0 : 1) : "—");

const WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
export function dayLabel(date: string): string {
  const d = new Date(date + "T12:00:00");
  return `${WD[d.getDay()]} ${date}`;
}
export function addDays(date: string, n: number): string {
  const d = new Date(date + "T12:00:00");
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}
export const time = (start: string) => start.slice(11, 16);

/** Session type ids from groups.json -> readable */
export function stypeLabel(t: string | null | undefined): string {
  if (!t) return "";
  return t.replace("intervals_", "intervals ").replace("ski_", "ski ").replace("run_", "").replace(/_/g, " ").replace("?", "");
}
export const typeLabel = (t: string) => t.replace("_ws", "").replace("_v2", "").replace(/_/g, " ");
/** seconds -> "h:mm" (rounded to whole minutes; negative -> "−h:mm") */
export function hm(s: number | null | undefined): string {
  if (s === null || s === undefined || !isFinite(s)) return "—";
  const m = Math.round(Math.abs(s) / 60);
  return `${s < 0 && m ? "−" : ""}${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
}
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** "Sat 31 Oct" (+ " 2025" when `year` or another year than `ref`) */
export function shortDate(date: string, ref?: string): string {
  const d = new Date(date + "T12:00:00");
  const y = ref && ref.slice(0, 4) !== date.slice(0, 4) ? ` ${date.slice(0, 4)}` : "";
  return `${WD[d.getDay()]} ${d.getDate()} ${MON[d.getMonth()]}${y}`;
}
/** local calendar date "YYYY-MM-DD" */
export const isoLocal = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
/** whole days from a to b (dates) */
export const daysBetween = (a: string, b: string) => Math.round((new Date(b + "T12:00:00").getTime() - new Date(a + "T12:00:00").getTime()) / 864e5);

// ------------------------------------------------------------------ shared vocabulary (docs/app.md#vocabulary)
/** sports shown as pace (m:ss /km): on foot (run, walk, hike). Ski, bike and other are km/h, swims /100 m. */
export const onFoot = (sport: string | null | undefined) => sport === "run" || sport === "hike" || sport === "walk";
/** unit of the speed / pace of a sport: "/km", "/100 m", "km/h" */
export const speedUnit = (sport: string | null | undefined) => (onFoot(sport) ? "/km" : sport === "swim" ? "/100 m" : "km/h");
/** speed / pace value without the unit, in the sport's unit: "3:29" (/km), "1:52" (/100 m), "12.1" (km/h) */
export function speedVal(sport: string | null | undefined, ms: number | null | undefined): string {
  if (!ms || !isFinite(ms) || ms <= 0) return "—";
  return onFoot(sport) ? pace(ms) : sport === "swim" ? pace100(ms) : kmh(ms);
}
/** speed / pace with its unit: "3:29 /km" for run / walk / hike, "1:52 /100 m" for swims, "12.1 km/h" else */
export function speedText(sport: string | null | undefined, ms: number | null | undefined): string {
  const v = speedVal(sport, ms);
  return v === "—" ? v : `${v} ${speedUnit(sport)}`;
}
/** sort key so that "faster" sorts the same way for pace and speed (higher = faster) */
export const speedSortKey = (ms: number | null | undefined) => (ms && isFinite(ms) ? ms : null);
/** "Pace" or "Speed" for a column / axis header */
export const speedLabel = (sport: string | null | undefined) => (onFoot(sport) || sport === "swim" ? "Pace" : "Speed");
/** seconds with a unit: "45 s", "9.4 s" (d decimals below 60 s), ≥ 60 s as "1:17" / "1:02:03" */
export function secs(s: number | null | undefined, d = 0): string {
  if (s === null || s === undefined || !isFinite(s)) return "—";
  const a = Math.abs(s);
  return a < 59.5 ? `${s < 0 ? "−" : ""}${a.toFixed(d)} s` : `${s < 0 ? "−" : ""}${dur(a)}`;
}
/** a gap as words, no sign: gapText(12) = "12 s behind", gapText(-40, "m") = "40 m ahead" (positive = behind) */
export function gapText(v: number | null | undefined, unit: "s" | "m" = "s", d = 0): string {
  if (v === null || v === undefined || !isFinite(v)) return "—";
  const r = Number(Math.abs(v).toFixed(d));
  if (r === 0) return "level";
  const mag = unit === "s" ? secs(r, d) : `${r.toLocaleString("en-GB")} m`;
  return `${mag} ${v > 0 ? "behind" : "ahead"}`;
}
/** "Rep 3" (room) / "R3" (compact); never a bare number */
export const repLabel = (n: number | string, compact = false) => (compact ? `R${n}` : `Rep ${n}`);
/** "Sat 31 Oct 2026" (dayLabel's ISO form is for ids and inputs) */
export const longDate = (date: string) => `${shortDate(date)} ${date.slice(0, 4)}`;
