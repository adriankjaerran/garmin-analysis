import type { Sport } from "./api";

export const SPORT_LABEL: Record<string, string> = { run: "Run", ski: "Ski", hike: "Hike", bike: "Bike", swim: "Swim", other: "Other" };
export const SPORTS: Sport[] = ["run", "ski", "hike", "bike", "swim", "other"];

const cssCache: Record<string, string> = {};
export function cssVar(name: string): string {
  if (!cssCache[name]) cssCache[name] = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
  return cssCache[name];
}
export const sportColor = (s: string) => cssVar(`--sport-${SPORT_LABEL[s] ? s : "other"}`);

export function dur(s: number | null | undefined): string {
  if (s === null || s === undefined || isNaN(s)) return "—";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`;
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
/** seconds -> "h:mm" */
export const hm = (s: number | null | undefined) =>
  s === null || s === undefined ? "—" : `${Math.floor(s / 3600)}:${String(Math.round((s % 3600) / 60)).padStart(2, "0")}`;
