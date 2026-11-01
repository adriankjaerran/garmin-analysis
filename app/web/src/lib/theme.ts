// Light / dark theme. The preference ("auto" | "light" | "dark") lives in localStorage; the resolved theme is
// written to <html data-theme="light|dark"> (an inline script in index.html does the same before first paint).
// Canvas charts read colours via cssVar(), so they take the resolved theme as an effect dependency (useTheme).
import { useEffect, useState } from "preact/hooks";
import { clearCssCache } from "./format";

export type ThemePref = "auto" | "light" | "dark";
export type Theme = "light" | "dark";

const KEY = "theme";
const mq = window.matchMedia("(prefers-color-scheme: dark)");

export function getPref(): ThemePref {
  const v = localStorage.getItem(KEY);
  return v === "light" || v === "dark" ? v : "auto";
}
const resolve = (p: ThemePref): Theme => (p === "auto" ? (mq.matches ? "dark" : "light") : p);
export const currentTheme = (): Theme => (document.documentElement.dataset.theme === "dark" ? "dark" : "light");

const subs = new Set<() => void>();
function apply() {
  const t = resolve(getPref());
  if (document.documentElement.dataset.theme === t) return;
  document.documentElement.dataset.theme = t;
  clearCssCache();
  subs.forEach((f) => f());
}
mq.addEventListener("change", apply);
apply();

export function setPref(p: ThemePref) {
  if (p === "auto") localStorage.removeItem(KEY); else localStorage.setItem(KEY, p);
  apply();
  subs.forEach((f) => f()); // the pref may change without the resolved theme changing (toggle UI)
}

function useSub<T>(get: () => T): T {
  const [v, set] = useState(get);
  useEffect(() => {
    const f = () => set(get);
    subs.add(f);
    return () => { subs.delete(f); };
  }, []);
  return v;
}
/** resolved theme; use as an effect dependency for anything drawn with cssVar() */
export const useTheme = () => useSub(currentTheme);
export const useThemePref = () => useSub(getPref);
