import type { Sport } from "./api";

export const SPORT_LABEL: Record<string, string> = { run: "Run", ski: "Ski", hike: "Hike", bike: "Bike", swim: "Swim", other: "Other" };
export const SPORTS: Sport[] = ["run", "ski", "hike", "bike", "swim", "other"];

const cssCache: Record<string, string> = {};
export function cssVar(name: string): string {
  if (!cssCache[name]) cssCache[name] = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
  return cssCache[name];
}
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
