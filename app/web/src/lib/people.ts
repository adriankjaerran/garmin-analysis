// Display info per person (name, initials, colour) from /api/people `info` (data/people.json or automatic).
// One fixed colour per person everywhere (charts, map dots, initials, table accents); the dark variant on the
// dark theme. Maps keep the light variant: their basemaps are always light.
import { currentTheme } from "./theme";

export interface PersonInfo { name: string; initials: string; colour: string; colour_dark: string; auto?: boolean }

let INFO: Record<string, PersonInfo> = {};
/** called from main.tsx with /api/people `info`; pages may add more (together payloads carry `people`) */
export function setPeopleInfo(info: Record<string, PersonInfo> | null | undefined) {
  if (info) INFO = { ...INFO, ...info };
}

const FALLBACK = ["#2f5fd0", "#c2560c", "#2f8a4c", "#8e44ad", "#1f8fa6", "#b88a00", "#c43c3c", "#5d6d7e"];
const FALLBACK_DARK = ["#6f96ee", "#f0884a", "#5cbf7a", "#b07fd6", "#45b5cc", "#d9ae2a", "#ec6d6d", "#9aa8b6"];

/** "patrik#2" (a 2nd file of the same person) -> "patrik" */
const baseKey = (key: string) => key.split("#")[0];

export function personInfo(key: string): PersonInfo {
  const p = baseKey(key);
  const i = INFO[p];
  if (i) return i;
  const n = Object.keys(INFO).length + [...p].reduce((s, c) => s + c.charCodeAt(0), 0);
  return { name: p ? p[0].toUpperCase() + p.slice(1) : "?", initials: (p[0] ?? "?").toUpperCase(),
    colour: FALLBACK[n % 8], colour_dark: FALLBACK_DARK[n % 8], auto: true };
}
export const personName = (key: string) => personInfo(key).name + (key.includes("#") ? " (2)" : "");
/** colour for UI and charts (theme-aware) */
export const personColour = (key: string, theme = currentTheme()) =>
  theme === "dark" ? personInfo(key).colour_dark : personInfo(key).colour;
/** colour on a map (always the light variant: basemaps are light) */
export const mapColour = (key: string) => personInfo(key).colour;
