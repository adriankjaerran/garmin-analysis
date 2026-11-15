// Official races with the pre-race prediction (/api/races, /api/community/races; backend app/server/routers/races.py,
// precomputed in fitness.json `races`). Shared by pages/RacesPage.tsx and pages/CommunityRacesPage.tsx.
import { useState } from "preact/hooks";
import { dur } from "./format";

export interface FieldResult { person: string; official_s: number; official: string | null; gap_s: number }
export interface RaceRow {
  name: string; date: string; sport: "run" | "ski"; km: number; official_s: number; official: string | null;
  activity_id: number | null; source: "chip" | "watch";
  /** run only, from data before race day (absent for ski, or with no running data before the race) */
  predicted_s?: number; predicted_range_s?: [number, number]; predicted_vdot?: number; predicted_sd?: number;
  actual_vdot?: number;
  /** (predicted − official) / predicted · 100: > 0 = faster than predicted */
  pct?: number;
  /** official − predicted (s): < 0 = faster than predicted */
  delta_s?: number;
  field: FieldResult[];
}
export interface RacesSummary {
  n_races: number; n_run: number; n_ski: number; n_beat: number; avg_pct: number | null; avg_delta_s: number | null;
  best: { name: string; date: string; km: number; official_s: number; predicted_s: number; pct: number; delta_s: number } | null;
}
export interface RacesDoc { person: string; races: RaceRow[]; summary: RacesSummary; predictions: boolean }

export interface H2HResult extends FieldResult {
  activity_id: number | null; source: "chip" | "watch"; predicted_s?: number; pct?: number; delta_s?: number;
}
export interface H2HRace { name: string; date: string; sport: "run" | "ski"; km: number; results: H2HResult[] }
export interface H2HDoc { people: string[]; races: H2HRace[] }

/** % | time: how the index (and ski gaps) are shown; remembered app-wide */
export type IdxMode = "pct" | "time";
export function useIdxMode(): [IdxMode, (m: IdxMode) => void] {
  const key = "races.index";
  const [m, setM] = useState<IdxMode>(() => (localStorage.getItem(key) as IdxMode | null) === "time" ? "time" : "pct");
  return [m, (v: IdxMode) => { setM(v); localStorage.setItem(key, v); }];
}

/** 5 -> "5k", 21.0975 -> "Half", 54 -> "54 km" */
export function distLabel(km: number): string {
  if (Math.abs(km - 21.0975) < 0.05) return "Half";
  if (Math.abs(km - 42.195) < 0.05) return "Marathon";
  if (km <= 15 && Number.isInteger(km)) return `${km}k`;
  return `${Math.round(km * 10) / 10} km`;
}

/** "+1.4 %" / "−0.5 %" */
export const pctText = (p: number) => `${p >= 0 ? "+" : "−"}${Math.abs(p).toFixed(1)} %`;
/** "−0:34" / "+0:12": official − predicted */
export const deltaText = (s: number) => (Math.round(s) === 0 ? "±0:00" : (s < 0 ? "−" : "+") + dur(Math.abs(s)));

/** colour class for an index: green = faster than predicted (stronger = more), red = slower, neutral near 0 */
export function idxClass(pct: number): string {
  const a = Math.abs(pct);
  if (a < 0.5) return "rx-idx n";
  const lvl = a < 2 ? 1 : a < 4 ? 2 : 3;
  return `rx-idx ${pct > 0 ? "g" : "r"}${lvl}`;
}
export const idxTitle = (pct: number, delta: number) =>
  `${Math.abs(pct).toFixed(1)} % (${dur(Math.abs(delta))}) ${pct >= 0 ? "faster" : "slower"} than predicted`;
