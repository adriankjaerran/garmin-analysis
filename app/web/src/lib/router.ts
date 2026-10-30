// Tiny history router: path + query are the state; every view is URL-addressable.
import { useEffect, useState } from "preact/hooks";

type Loc = { path: string; query: URLSearchParams };
const read = (): Loc => ({ path: location.pathname.replace(/\/+$/, "") || "/", query: new URLSearchParams(location.search) });
const EVT = "app:navigate";

export function navigate(url: string, replace = false) {
  if (url === location.pathname + location.search) return;
  history[replace ? "replaceState" : "pushState"](null, "", url);
  window.dispatchEvent(new Event(EVT));
}

/** Merge `patch` into the current query (null/"" removes a key). Replaces history by default. */
export function setQuery(patch: Record<string, string | number | null | undefined>, replace = true) {
  const q = new URLSearchParams(location.search);
  for (const [k, v] of Object.entries(patch)) {
    if (v === null || v === undefined || v === "") q.delete(k);
    else q.set(k, String(v));
  }
  const s = q.toString();
  navigate(location.pathname + (s ? "?" + s : ""), replace);
}

export function useLocation(): Loc {
  const [loc, set] = useState(read);
  useEffect(() => {
    const on = () => set(read());
    window.addEventListener("popstate", on);
    window.addEventListener(EVT, on);
    return () => { window.removeEventListener("popstate", on); window.removeEventListener(EVT, on); };
  }, []);
  return loc;
}

/** Match "/activities/:id" against a path; returns params or null. */
export function match(pattern: string, path: string): Record<string, string> | null {
  const a = pattern.split("/"), b = path.split("/");
  if (a.length !== b.length) return null;
  const out: Record<string, string> = {};
  for (let i = 0; i < a.length; i++) {
    if (a[i].startsWith(":")) out[a[i].slice(1)] = decodeURIComponent(b[i]);
    else if (a[i] !== b[i]) return null;
  }
  return out;
}

/** Plain <a href="/..."> links navigate client-side (except /api, new-tab clicks, external). */
export function interceptLinks() {
  document.addEventListener("click", (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = (e.target as Element).closest?.("a");
    if (!a || a.target || a.hasAttribute("download")) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin || url.pathname.startsWith("/api")) return;
    e.preventDefault();
    navigate(url.pathname + url.search);
  });
}
